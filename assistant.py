"""
SymbioLink AI assistant: the chat panel available on every signed-in page.

It answers questions about the user's own company and the cluster: who can
buy their waste, where to source a material they need, prices, the status of
their orders, and how to do things in the app. It analyses the request
against a live snapshot of the platform's data (build_context) and replies
with links to the right page. It never changes anything itself.

Backed by Google Gemini's free tier through its REST API (plain `requests`,
no SDK). Without GEMINI_API_KEY, or if the API call fails (for example the
free quota is used up), a rule-based responder answers common questions from
the same snapshot, so the chat always works.
"""

import logging
import re

import requests

import data
import market_insights
import matching
import orders as orders_module
from config import Config

logger = logging.getLogger("symbiolink.assistant")

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
MAX_TURNS = 12
MAX_MESSAGE_CHARS = 1500

# Pages the assistant may link to, with what each is for. Paths, not
# url_for(), so the model sees exactly the links it should produce.
PAGES = [
    ("/dashboard", "Dashboard: cluster overview and top matches"),
    ("/register-unit", "Register a Listing: post waste you have or material you need"),
    ("/search", "Search Marketplace: browse available waste; /search?q=<material> filters"),
    ("/market-prices", "Market Prices: new vs by-product price per material"),
    ("/matches", "Matching Engine: direct and multi-hop matches"),
    ("/map", "Cluster Map: companies on a map"),
    ("/units", "Units Directory; /units/<unit_id> shows one company"),
    ("/orders", "Orders: accept, pay, track and complete orders"),
    ("/orders/history", "Order History"),
    ("/pooling", "Logistics Pooling: share trucks for small loads"),
    ("/predictions", "Predictive: forecast of upcoming waste"),
    ("/trust", "Trust scores"),
    ("/carbon-credits", "Carbon Credits"),
    ("/report/brsr", "BRSR sustainability report"),
    ("/iot", "IoT bin sensors"),
]


def _nice(material):
    return str(material or "").replace("_", " ")


def _unit_name(unit_id):
    unit = data.unit_by_id(unit_id)
    return unit["name"] if unit else unit_id


def _order_link(unit_id, material, qty):
    return f"/order/new?unit_id={unit_id}&material={material}&qty={int(qty)}"


def _opportunities(unit_id, limit=6):
    """Concrete deals for this unit: buyers for its waste, sellers for its needs."""
    me = data.unit_by_id(unit_id)
    if not me:
        return [], []
    mine = [l for l in data.LISTINGS if l["unit_id"] == unit_id]
    others = [l for l in data.LISTINGS if l["unit_id"] != unit_id]
    buyers, sellers = [], []
    for my in mine:
        want_type = "need" if my["type"] == "waste" else "waste"
        for other in others:
            if other["type"] != want_type:
                continue
            sim = matching.material_similarity(my["material"], other["material"])
            if sim < matching.FUZZY_MATCH_THRESHOLD:
                continue
            unit = data.unit_by_id(other["unit_id"])
            if not unit:
                continue
            try:
                dist = matching.distance_km(me, unit)
            except Exception:
                dist = None
            row = {
                "unit_id": unit["id"], "unit": unit["name"],
                "material": other["material"], "my_material": my["material"],
                "qty_kg": other["qty_kg"], "similarity": round(sim, 2),
                "distance_km": round(dist, 1) if dist is not None else None,
            }
            (buyers if my["type"] == "waste" else sellers).append(row)
    key = lambda r: (-r["similarity"], r["distance_km"] if r["distance_km"] is not None else 99)
    return sorted(buyers, key=key)[:limit], sorted(sellers, key=key)[:limit]


def build_context(user, unit_id):
    """Plain-text snapshot of what the assistant is allowed to know."""
    lines = []
    unit = data.unit_by_id(unit_id) if unit_id else None
    lines.append(f"USER: {user.username} (role: {user.role})")
    if unit:
        lines.append(f"COMPANY: {unit['name']} (id {unit['id']}, category {unit.get('category')})")
        mine = [l for l in data.LISTINGS if l["unit_id"] == unit["id"]]
        lines.append("MY LISTINGS: " + ("; ".join(
            f"{l['type']} {_nice(l['material'])} {l['qty_kg']:.0f} kg" for l in mine) or "none yet"))

        my_orders = orders_module.orders_for_unit(unit["id"])[-10:]
        if my_orders:
            lines.append("MY ORDERS:")
            for o in my_orders:
                side = "selling to " + o["buyer_name"] if o["seller_unit_id"] == unit["id"] else "buying from " + o["seller_name"]
                lines.append(f"- {o['id']}: {_nice(o['material'])} {o['qty_kg']:.0f} kg, {side}, "
                             f"status {orders_module.STAGE_LABELS.get(o['status'], o['status'])}, "
                             f"payment {o.get('payment_status')}")
        else:
            lines.append("MY ORDERS: none")

        buyers, sellers = _opportunities(unit["id"])
        if buyers:
            lines.append("COMPANIES THAT NEED MY WASTE:")
            lines += [f"- {b['unit']} ({b['unit_id']}) needs {_nice(b['material'])} {b['qty_kg']:.0f} kg"
                      f" (matches my {_nice(b['my_material'])}, {b['distance_km']} km away) -> /units/{b['unit_id']}"
                      for b in buyers]
        if sellers:
            lines.append("COMPANIES SELLING WHAT I NEED:")
            lines += [f"- {s['unit']} ({s['unit_id']}) has {_nice(s['material'])} {s['qty_kg']:.0f} kg"
                      f" ({s['distance_km']} km away) -> request it: "
                      f"{_order_link(s['unit_id'], s['material'], s['qty_kg'])}"
                      for s in sellers]

    try:
        ranked = matching.rank_matches(top_n=30)
        if unit:
            ranked = [m for m in ranked if unit["id"] in m["path"]]
        if ranked:
            lines.append("TOP MATCHES" + (" INVOLVING MY COMPANY:" if unit else " IN THE CLUSTER:"))
            for m in ranked[:5]:
                lines.append(f"- {' -> '.join(m['path_names'])}: {', '.join(_nice(x) for x in m['materials'])}, "
                             f"saves Rs {m['total_saving_rs']:,.0f}, {m['total_co2_saved_kg']:,.0f} kg CO2")
    except Exception:
        logger.exception("rank_matches failed while building assistant context")

    try:
        lines.append("PRICES (Rs/kg, new vs by-product):")
        lines += [f"- {_nice(p['material'])}: new {p['new_material_price_rs']:.0f}, "
                  f"by-product {p['byproduct_price_rs']:.0f} (save {p['saving_pct']}%)"
                  for p in market_insights.material_price_guide()]
    except Exception:
        logger.exception("price guide failed while building assistant context")

    lines.append("ALL WASTE AVAILABLE IN THE CLUSTER:")
    for l in data.LISTINGS:
        if l["type"] == "waste":
            lines.append(f"- {_unit_name(l['unit_id'])} ({l['unit_id']}): {_nice(l['material'])} {l['qty_kg']:.0f} kg"
                         f" -> {_order_link(l['unit_id'], l['material'], l['qty_kg'])}")
    lines.append("ALL NEEDS IN THE CLUSTER:")
    for l in data.LISTINGS:
        if l["type"] == "need":
            lines.append(f"- {_unit_name(l['unit_id'])} ({l['unit_id']}): needs {_nice(l['material'])} {l['qty_kg']:.0f} kg")

    lines.append("APP PAGES:")
    lines += [f"- {path}: {desc}" for path, desc in PAGES]
    return "\n".join(lines)


SYSTEM_PROMPT = """You are the SymbioLink AI assistant, built into an industrial-symbiosis marketplace where \
MSMEs in a cluster trade one factory's waste as another's raw material.

Help the signed-in user: analyse what they ask, then answer using ONLY the live data below. You can:
- find buyers for their waste and sellers for what they need, and compare distance, quantity and price
- estimate savings (by-product vs new price x quantity) and explain CO2 benefit
- explain the status of their orders and what to do next (accept, pay, mark picked up, complete)
- explain how to use the app and point to the right page

Rules:
- Never invent companies, prices, quantities or orders that are not in the data. If something isn't there, say so \
and suggest the page where they can check or post it.
- You cannot change anything yourself. Never claim you placed an order, posted a listing or accepted anything; \
instead give the link that lets them do it, as a markdown link like [Request 150 kg from Ganga Alloy](/order/new?...) \
or [Open Orders](/orders). Only link to paths from the data below.
- Be concise and practical: short paragraphs or a few bullets, under about 120 words unless asked for detail. \
Replies may be read aloud, so avoid tables and long lists of numbers.
- Reply in {language}. If the user writes in Hindi or Hinglish, reply in the same style.

LIVE DATA:
{context}"""


# Tried in order after Config.GEMINI_MODEL when a model is overloaded (503),
# rate-limited on the free tier (429) or retired for new keys (404). Google
# retires and throttles free models often, so one model name is fragile.
FALLBACK_MODELS = ["gemini-flash-latest", "gemini-3.5-flash", "gemini-flash-lite-latest"]
_RETRYABLE = {404, 429, 500, 503}


def _post(model, body):
    return requests.post(
        GEMINI_URL.format(model=model),
        headers={"x-goog-api-key": Config.GEMINI_API_KEY, "Content-Type": "application/json"},
        json=body, timeout=45,
    )


def _gemini(messages, system_text):
    body = {
        "system_instruction": {"parts": [{"text": system_text}]},
        "contents": [
            {"role": "model" if m["role"] == "assistant" else "user", "parts": [{"text": m["text"]}]}
            for m in messages
        ],
        # The newer Gemini models "think" before answering and those hidden
        # tokens count against maxOutputTokens -- with a small cap the reply
        # was cut off mid-sentence. Low thinking keeps replies fast; the
        # generous cap leaves room for the answer itself.
        "generationConfig": {"temperature": 0.4, "maxOutputTokens": 4096,
                             "thinkingConfig": {"thinkingLevel": "low"}},
    }
    models = list(dict.fromkeys([Config.GEMINI_MODEL] + FALLBACK_MODELS))
    last_error = None
    for model in models:
        try:
            resp = _post(model, body)
            if resp.status_code == 400 and "thinking" in resp.text.lower():
                # A model that doesn't support thinkingLevel: ask again without it.
                plain = dict(body, generationConfig={k: v for k, v in body["generationConfig"].items()
                                                     if k != "thinkingConfig"})
                resp = _post(model, plain)
        except requests.RequestException as e:
            last_error = f"{model}: {e}"
            continue
        if resp.status_code == 200:
            payload = resp.json()
            parts = (payload.get("candidates") or [{}])[0].get("content", {}).get("parts") or []
            text = "".join(p.get("text", "") for p in parts if not p.get("thought")).strip()
            if text:
                return text
            last_error = f"{model}: no text in response {str(payload)[:200]}"
            continue
        last_error = f"{model}: HTTP {resp.status_code} {resp.text[:200]}"
        if resp.status_code in (400, 401, 403):
            break  # bad key or request: another model won't help
        if resp.status_code in _RETRYABLE:
            logger.warning("Gemini model unavailable, trying next: %s", last_error)
    raise RuntimeError(f"All Gemini models failed. Last error: {last_error}")


# ------------------------------------------------------------ rule-based fallback

_HINDI = re.compile(r"[ऀ-ॿ]")
# Keyword intents for the fallback. English words are matched on word
# boundaries (so "rs" doesn't fire inside "orders"); Devanagari as substrings.
def _kw(*words):
    latin = [w for w in words if w.isascii()]
    other = [w for w in words if not w.isascii()]
    rx = re.compile(r"\b(" + "|".join(re.escape(w) for w in latin) + r")\b") if latin else None
    return lambda q: bool((rx and rx.search(q)) or any(w in q for w in other))


_is_price = _kw("price", "prices", "rate", "rates", "cost", "kimat", "keemat", "daam", "bhav", "rs",
                "कीमत", "दाम", "भाव", "₹")
_is_order = _kw("order", "orders", "status", "delivery", "payment", "ऑर्डर", "आर्डर", "स्थिति")
_is_sell = _kw("buy", "buyer", "buyers", "sell", "khareed", "kharid", "bech", "becho", "waste",
               "खरीद", "बेच", "ग्राहक", "कचरा")
_is_need = _kw("need", "source", "supplier", "suppliers", "chahiye", "zaroorat", "get",
               "चाहिए", "ज़रूरत", "जरूरत")


def _material_in(text):
    """Which known material the question is about, if any."""
    lowered = text.lower()
    # Direct mention first ("sawdust", "metal shavings"), longest name wins so
    # "metal dust" doesn't shadow "metal dust sludge"-style names.
    for key in sorted(data.PRICE_TABLE_RS_PER_KG, key=len, reverse=True):
        if key.replace("_", " ") in lowered or key in lowered:
            return key
    try:
        import ml_text
        key, _score = ml_text.best_material_match(text, threshold=0.2)
        return key
    except Exception:
        return None


def _away(row):
    return f", {row['distance_km']} km away" if row["distance_km"] is not None else ""


def _fallback(question, user, unit_id, hindi):
    q = question.lower()
    unit = data.unit_by_id(unit_id) if unit_id else None

    if _is_price(q):
        material = _material_in(question)
        prices = {p["material"]: p for p in market_insights.material_price_guide()}
        if material and material in prices:
            p = prices[material]
            if hindi:
                return (f"**{_nice(material)}**: नया ₹{p['new_material_price_rs']:.0f}/kg, बाय-प्रोडक्ट "
                        f"₹{p['byproduct_price_rs']:.0f}/kg, यानी {p['saving_pct']}% बचत। "
                        "[बाज़ार भाव देखें](/market-prices)")
            return (f"**{_nice(material)}** costs Rs {p['new_material_price_rs']:.0f}/kg new vs "
                    f"Rs {p['byproduct_price_rs']:.0f}/kg as a by-product: a {p['saving_pct']}% saving. "
                    "See all materials on [Market Prices](/market-prices).")
        return ("सभी सामग्रियों के भाव [बाज़ार भाव](/market-prices) पर देखें।" if hindi else
                "You can compare new vs by-product prices for every material on [Market Prices](/market-prices). "
                "Ask me about a specific one, e.g. \"price of sawdust\".")

    if _is_order(q) and unit:
        mine = orders_module.orders_for_unit(unit["id"])
        if not mine:
            return ("आपका अभी कोई ऑर्डर नहीं है। [बाज़ार खोजें](/search)" if hindi else
                    "You don't have any orders yet. Find material on [Search Marketplace](/search).")
        rows = [f"- **{o['id']}** {_nice(o['material'])} {o['qty_kg']:.0f} kg: "
                f"{orders_module.STAGE_LABELS.get(o['status'], o['status'])}" for o in mine[-5:]]
        return "\n".join(rows) + ("\n\n[ऑर्डर खोलें](/orders)" if hindi else "\n\nManage them on [Orders](/orders).")

    if unit and (_is_need(q) or _is_sell(q)):
        buyers, sellers = _opportunities(unit["id"], limit=3)
        wants_sellers = _is_need(q) and not _is_sell(q)
        rows = sellers if wants_sellers else buyers
        if not rows:
            return ("अभी कोई मिलान नहीं मिला। अपनी सामग्री [सूची दर्ज करें](/register-unit) पर पोस्ट करें।" if hindi else
                    "I couldn't find a match right now. Post what you have or need on "
                    "[Register a Listing](/register-unit) and the matching engine will pick it up.")
        if wants_sellers:
            out = [f"- **{r['unit']}** has {r['qty_kg']:.0f} kg {_nice(r['material'])}"
                   f"{_away(r)}: [request it]({_order_link(r['unit_id'], r['material'], r['qty_kg'])})" for r in rows]
            head = "ये कंपनियां आपकी ज़रूरत की सामग्री बेच रही हैं:" if hindi else "These companies have what you need:"
        else:
            out = [f"- **{r['unit']}** needs {r['qty_kg']:.0f} kg {_nice(r['material'])}"
                   f"{_away(r)}: [view company](/units/{r['unit_id']})" for r in rows]
            head = "ये कंपनियां आपका कचरा खरीद सकती हैं:" if hindi else "These companies could use your waste:"
        return head + "\n" + "\n".join(out)

    if hindi:
        return ("मैं आपकी मदद कर सकता हूं: आपका कचरा कौन खरीदेगा, किसी सामग्री का भाव, और आपके ऑर्डर की स्थिति। "
                "जैसे पूछें: \"मेरा कचरा कौन खरीदेगा?\"")
    return ("I can help you find buyers for your waste, suppliers for what you need, material prices and "
            "your order status. Try: \"Who can buy my waste?\" or \"What's the price of sawdust?\"")


def chat(messages, lang, user, unit_id):
    """Answer the last user message. Returns (reply_text, mode) where mode is
    "ai" (Gemini) or "basic" (rule-based fallback)."""
    clean = []
    for m in (messages or [])[-MAX_TURNS:]:
        text = str(m.get("text") or "").strip()[:MAX_MESSAGE_CHARS]
        if text:
            clean.append({"role": "assistant" if m.get("role") == "assistant" else "user", "text": text})
    # Gemini requires the conversation to start with a user turn.
    while clean and clean[0]["role"] == "assistant":
        clean.pop(0)
    if not clean or clean[-1]["role"] != "user":
        return "Ask me anything about your listings, orders, prices or matches.", "basic"

    question = clean[-1]["text"]
    hindi = lang == "hi" or bool(_HINDI.search(question))

    if Config.GEMINI_API_KEY:
        try:
            system_text = SYSTEM_PROMPT.format(
                language="Hindi (Devanagari)" if hindi else "English",
                context=build_context(user, unit_id),
            )
            return _gemini(clean, system_text), "ai"
        except Exception:
            logger.exception("Gemini request failed; using the rule-based fallback")

    return _fallback(question, user, unit_id, hindi), "basic"
