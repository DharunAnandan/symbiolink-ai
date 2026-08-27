"""
Multi-hop matching engine.

The cluster is modeled as a directed graph: an edge X -> Y (for material M) exists when
unit X has a WASTE listing for M and unit Y has a NEED listing for the same M, within a
practical transport radius. A "chain" is simply a path through this graph of 2-3 hops,
e.g. A -> B -> C, where A's waste satisfies B's need, and (separately) B's own waste
satisfies C's need. This surfaces indirect symbiosis opportunities that a simple
one-to-one marketplace (which only looks at direct edges) would never see.
"""

import math

import data
import ml_models
import ml_text
from maps_service import maps_service

# LISTINGS, PRICE_TABLE_RS_PER_KG, and unit_by_id must stay data.X attribute lookups,
# not bare "from data import ..." names. app.py's data_access layer reassigns these
# data.* attributes at startup (to database-backed values) and again whenever a unit
# registers a new listing -- a bare import freezes a reference to whatever object
# existed at import time, so newly registered units/listings would silently never show
# up in search, matches, or pooling. Always read through the data module, every call.

MAX_RADIUS_KM = 3.0   # candidate edges only within a practical transport radius
MAX_HOPS = 3          # per Section 6 of the concept note: 2-3 hop chains

# Below this similarity, two differently-named materials are treated as
# genuinely unrelated -- see material_similarity() / build_edges().
#
# Recalibrated for the TF-IDF cosine metric (see ml_text.py) -- it sits on a
# different scale than the old difflib.SequenceMatcher ratio this threshold
# was originally tuned against, so 0.55 stopped being meaningful once
# material_similarity() switched implementations. 0.30 was picked by checking
# every same-category material pair actually present in the seed data: the
# one genuine relationship worth surfacing (metal_dust <-> metal_shavings,
# both "fine metal particles" by alias) scores 0.331, while every other
# same-category pair -- which really are unrelated materials, e.g.
# cast_offcuts vs sheet_trimmings -- tops out at 0.144. 0.30 sits cleanly
# between the two, same as 0.55 used to for the old metric.
FUZZY_MATCH_THRESHOLD = 0.30


def material_similarity(material_a, material_b):
    """Similarity between two material names (0..1), used to surface "semantic"
    matches the old exact-string engine couldn't see -- e.g. a waste listing
    named "metal_shavings" and a need listing named "metal_turnings" describe
    materials a human would recognize as the same thing, but a plain `==`
    never would. Backed by ml_text.material_similarity() -- a TF-IDF
    vector-space model over material names + known aliases (see ml_text.py's
    docstring for why that, and not a difflib string-ratio or a neural
    embedding model, is what's actually used here)."""
    return ml_text.material_similarity(material_a, material_b)


def distance_km_with_source(u1, u2):
    """Calculate distance between two units using Google Maps API if available, otherwise
    Euclidean. Returns (distance_km, source) so callers/templates can show the user
    whether a figure is a real road-distance lookup or just a straight-line estimate --
    the API key is an external credential this deployment doesn't have configured
    (see .env.example / GOOGLE_MAPS_API_KEY), so today "estimated" is what every
    distance in this app actually is; the Google Maps path activates automatically the
    moment a key is configured, with zero code changes needed elsewhere."""
    # Try Google Maps API first
    if hasattr(maps_service, 'api_available') and maps_service.api_available:
        # Convert location coordinates to lat/lng (assuming simple mapping for demo)
        # In production, you'd need proper coordinate conversion
        origin = (u1["loc"][0], u1["loc"][1])
        destination = (u2["loc"][0], u2["loc"][1])

        result = maps_service.get_distance(origin, destination)
        if result['status'] == 'OK':
            return result['distance_km'], "google_maps"

    # Fallback to Euclidean distance
    (x1, y1), (x2, y2) = u1["loc"], u2["loc"]
    return math.hypot(x2 - x1, y2 - y1), "estimated"


def distance_km(u1, u2):
    """Thin wrapper over distance_km_with_source() for callers (e.g. pooling.py's
    clustering) that only need the number, not the source."""
    dist, _source = distance_km_with_source(u1, u2)
    return dist


def estimate_saving_rs(material, qty_kg):
    prices = data.PRICE_TABLE_RS_PER_KG.get(material)
    if not prices:
        return 0
    return round((prices["new_material"] - prices["byproduct"]) * qty_kg, 2)


def estimate_co2_saved_kg(material, qty_kg):
    return round(data.co2_factor_for(material) * qty_kg, 2)


def build_edges():
    """Return candidate edges: (from_unit_id, to_unit_id, material, qty_kg, distance_km, saving_rs).

    Two kinds of edge:
    - "exact": waste and need list the identical material key -- the original
      behavior, unchanged.
    - "similar": the material *names* differ but are close enough (same
      category + high string similarity) that they're almost certainly the
      same underlying material described differently -- e.g. "metal_dust"
      vs "metal_shavings". These are new opportunities the old exact-only
      matcher silently missed; see material_similarity() above.
    """
    waste_listings = [l for l in data.LISTINGS if l["type"] == "waste"]
    need_listings = [l for l in data.LISTINGS if l["type"] == "need"]

    edges = []
    for w in waste_listings:
        w_unit = data.unit_by_id(w["unit_id"])
        for n in need_listings:
            if n["unit_id"] == w["unit_id"]:
                continue
            exact = n["material"] == w["material"]
            if exact:
                similarity = 1.0
            else:
                n_unit_for_cat = data.unit_by_id(n["unit_id"])
                if not w_unit or not n_unit_for_cat or w_unit["category"] != n_unit_for_cat["category"]:
                    continue
                similarity = material_similarity(w["material"], n["material"])
                if similarity < FUZZY_MATCH_THRESHOLD:
                    continue
            n_unit = data.unit_by_id(n["unit_id"])
            dist, dist_source = distance_km_with_source(w_unit, n_unit)
            if dist > MAX_RADIUS_KM:
                continue
            qty = min(w["qty_kg"], n["qty_kg"])
            saving = estimate_saving_rs(w["material"], qty)
            co2_saved = estimate_co2_saved_kg(w["material"], qty)
            edges.append({
                "from": w["unit_id"],
                "to": n["unit_id"],
                "material": w["material"],
                "matched_material": n["material"],
                "exact_match": exact,
                "similarity": round(similarity, 2),
                "qty_kg": qty,
                "distance_km": round(dist, 2),
                "distance_source": dist_source,
                "saving_rs": saving,
                "co2_saved_kg": co2_saved,
            })
    return edges


def network_gap_report():
    """Which listings build_edges() found NO edge for at all -- a waste
    listing nobody nearby needs, or a need listing nobody nearby can supply.
    This is the direct "where should we grow the cluster" answer: since the
    platform's entire value is network density, a waste/need pair with zero
    edges is exactly the gap worth recruiting a new unit to fill, not
    something a ranking or optimization pass over EXISTING edges could ever
    surface (there's nothing to rank -- the opportunity doesn't exist yet).

    Returns (unmet_supply, unmet_demand), each a list of
    {"unit_id", "unit_name", "material", "qty_kg"} dicts, sorted largest
    quantity first."""
    edges = build_edges()
    supplied_waste_keys = {(e["from"], e["material"]) for e in edges}
    served_need_keys = {(e["to"], e["matched_material"]) for e in edges}

    unmet_supply = []
    unmet_demand = []
    for listing in data.LISTINGS:
        unit = data.unit_by_id(listing["unit_id"])
        if not unit:
            continue
        row = {
            "unit_id": listing["unit_id"],
            "unit_name": unit["name"],
            "material": listing["material"],
            "qty_kg": listing["qty_kg"],
        }
        if listing["type"] == "waste" and (listing["unit_id"], listing["material"]) not in supplied_waste_keys:
            unmet_supply.append(row)
        elif listing["type"] == "need" and (listing["unit_id"], listing["material"]) not in served_need_keys:
            unmet_demand.append(row)

    unmet_supply.sort(key=lambda r: r["qty_kg"], reverse=True)
    unmet_demand.sort(key=lambda r: r["qty_kg"], reverse=True)
    return unmet_supply, unmet_demand


def edges_by_source(edges):
    idx = {}
    for e in edges:
        idx.setdefault(e["from"], []).append(e)
    return idx


def find_chains(edges, max_hops=MAX_HOPS):
    """DFS over the edge list to find direct matches (1 hop) and chains (2-3 hops).
    A chain is a sequence of edges where edge[i].to == edge[i+1].from, and no unit
    repeats within the same chain (avoids trivial cycles)."""
    by_source = edges_by_source(edges)
    chains = []

    def dfs(path):
        chains.append(list(path))
        if len(path) >= max_hops:
            return
        last = path[-1]
        visited = {path[0]["from"]} | {e["to"] for e in path}
        for nxt in by_source.get(last["to"], []):
            if nxt["to"] in visited:
                continue
            dfs(path + [nxt])

    for e in edges:
        dfs([e])

    return chains


def score_chain(chain, trust_scores=None):
    """Score a chain by how likely it is to actually be accepted. Primary path:
    a trained ranker (LogisticRegression -- see train_ml_models.py /
    ml_models.py) predicts an accept-probability from
    [normalized_saving, distance_factor, confidence_factor, similarity, hops],
    with weights learned from data instead of hand-picked. Falls back to the
    original fixed 60/25/15 weighted heuristic if the ranker hasn't been
    trained yet (see ml_models.py's docstring for why every ML call in this
    project degrades like that instead of raising)."""
    trust_scores = trust_scores or {}
    total_saving = sum(e["saving_rs"] for e in chain)
    avg_distance = sum(e["distance_km"] for e in chain) / len(chain)
    distance_factor = max(0.0, 1 - (avg_distance / MAX_RADIUS_KM))  # closer is better
    units_in_chain = {chain[0]["from"]} | {e["to"] for e in chain}
    if units_in_chain:
        avg_trust = sum(trust_scores.get(u, 3.5) for u in units_in_chain) / len(units_in_chain)
    else:
        avg_trust = 3.5
    confidence_factor = avg_trust / 5.0
    normalized_saving = min(total_saving / 10000, 1.0)  # cap for scoring purposes
    all_exact = all(e.get("exact_match", True) for e in chain)
    avg_similarity = sum(e.get("similarity", 1.0) for e in chain) / len(chain)

    ml_score = ml_models.predict_match_accept_probability(
        normalized_saving=normalized_saving,
        distance_factor=distance_factor,
        confidence_factor=confidence_factor,
        similarity=avg_similarity,
        hops=len(chain),
    )
    if ml_score is not None:
        score = ml_score
        score_method = "ml"
    else:
        # Fallback: original fixed weighted composite (60% savings-normalized,
        # 25% distance, 15% trust confidence), with the same fuzzy-match
        # discount it always had.
        score = 0.60 * normalized_saving + 0.25 * distance_factor + 0.15 * confidence_factor
        if not all_exact:
            score *= (0.7 + 0.3 * avg_similarity)
        score_method = "heuristic"

    return round(score, 4), round(total_saving, 2), all_exact, round(avg_trust, 2), score_method


def explain_match(chain, avg_trust, score_method="heuristic"):
    """Short, human-readable reason a chain ranked where it did -- built
    deterministically from the same signals score_chain() already computed
    (distance, saving, trust, exact-vs-similar material), not a black-box or
    LLM call, so it's fast, free, and always consistent with the score.

    When the trained ranker (see ml_models.match_ranker_feature_weights())
    produced the score, this also names the single learned feature that
    weighs heaviest in that model -- so upgrading score_chain() to a real,
    trained LogisticRegression doesn't turn it into a black box: the reason
    string still traces back to a concrete, inspectable number, it's just
    "the model's #1 learned factor is X" instead of "60% is saving by
    hand-picked rule." Falls back silently to the plain heuristic phrasing
    (no feature-weight sentence) if the ranker isn't trained yet."""
    total_saving = sum(e["saving_rs"] for e in chain)
    avg_distance = sum(e["distance_km"] for e in chain) / len(chain)
    all_exact = all(e.get("exact_match", True) for e in chain)

    parts = []
    if not all_exact:
        pairs = sorted({
            f"“{e['material'].replace('_', ' ')}” ≈ “{e['matched_material'].replace('_', ' ')}”"
            for e in chain if not e.get("exact_match", True)
        })
        parts.append("Similar-material match: " + "; ".join(pairs))
    parts.append(f"{avg_distance:.1f}km avg hop distance")
    if total_saving > 0:
        parts.append(f"₹{total_saving:,.0f} cheaper than buying new material")
    parts.append(f"{avg_trust:.1f}★ avg trust across the chain")

    if score_method == "ml":
        weights = ml_models.match_ranker_feature_weights()
        if weights:
            top_feature, top_coef = max(weights.items(), key=lambda kv: abs(kv[1]))
            label = {
                "normalized_saving": "cost saving",
                "distance_factor": "short hop distance",
                "confidence_factor": "unit trust",
                "similarity": "material match quality",
                "hops": "chain length",
            }.get(top_feature, top_feature)
            direction = "pushes the score up" if top_coef > 0 else "pulls the score down"
            parts.append(f"AI-ranked (trained model) -- strongest learned factor: {label} {direction}")

    return " · ".join(parts)


def rank_matches(trust_scores=None, top_n=15):
    edges = build_edges()
    chains = find_chains(edges)

    ranked = []
    for chain in chains:
        score, total_saving, all_exact, avg_trust, score_method = score_chain(chain, trust_scores)
        path_units = [chain[0]["from"]] + [e["to"] for e in chain]
        total_co2_saved = round(sum(e.get("co2_saved_kg", 0) for e in chain), 2)
        ranked.append({
            "path": path_units,
            "path_names": [data.unit_by_id(u)["name"] for u in path_units],
            "hops": len(chain),
            "materials": [e["material"] for e in chain],
            "total_saving_rs": total_saving,
            "total_co2_saved_kg": total_co2_saved,
            "score": score,
            "score_method": score_method,
            "edges": chain,
            "match_type": "exact" if all_exact else "similar",
            "reason": explain_match(chain, avg_trust, score_method),
        })

    ranked.sort(key=lambda m: m["score"], reverse=True)
    return ranked[:top_n]


def notify_new_matches_for_unit(unit_id, unit_name=None):
    """Alert every counterpart unit that now has a live match edge with `unit_id`.

    Call this right after ANY new listing is created (not just at initial
    registration) -- a web-form listing, a WhatsApp/voice intake, an IoT
    bin sensor auto-listing -- so that if company B already has an open
    NEED for material M and company A just posted a WASTE listing for M
    (or vice versa), B gets a bell + email + WhatsApp notification the
    moment the match appears, not only if B happens to revisit the
    /matches page.

    This re-derives the whole edge set via build_edges() and filters down
    to edges touching `unit_id`, the same approach /register-unit already
    used for a brand-new unit's first listing -- this function just makes
    that same behavior reusable for every other listing-creation path
    (WhatsApp, IoT auto-listing, and any future one), instead of only
    firing once at signup. Cheap enough for this cluster's scale (see
    build_edges()'s own docstring); a real deployment at 1000s of units
    would want an incremental/indexed version instead of a full rebuild.

    Safe to call even if `unit_id` turns out to have zero new edges (a
    plain quantity edit, a listing for a material nobody needs yet) --
    it just notifies nobody in that case. Notification failures are
    already best-effort inside notifications.notify_unit() itself, so a
    broken notify never blocks the listing creation that triggered it.

    WhatsApp fires through whatsapp_service.send_match_notification(),
    which already existed fully written and correct (it's what powers the
    /whatsapp/test-notification/<unit_id> dev endpoint) but was never
    actually wired to a real "a new match was just found" event -- only
    the bell and email calls below were. For this app's actual target
    user (an MSME owner who intakes over WhatsApp and may not open the
    web dashboard for days) a bell-only alert is close to worthless; this
    closes that gap using the exact channel the rest of the intake story
    already runs on. Like the bell/email calls, this is best-effort --
    send_match_notification() catches its own exceptions and returns
    False rather than raising, and simply no-ops (logging "Simulation
    mode: would send...") when Twilio credentials aren't configured, so
    this never blocks the listing creation that triggered it either.
    """
    import notifications as notifications_module
    from email_service import email_service
    from whatsapp_service import whatsapp_service
    from flask import url_for

    unit = data.unit_by_id(unit_id)
    if not unit:
        return []
    if unit_name is None:
        unit_name = unit["name"]

    edges = build_edges()
    new_matches = [e for e in edges if e["from"] == unit_id or e["to"] == unit_id]

    for e in new_matches:
        other_id = e["to"] if e["from"] == unit_id else e["from"]
        other_unit = data.unit_by_id(other_id)
        if other_unit:
            match_title = f"New match: {unit_name}"
            match_message = f"{e['material'].replace('_', ' ').title()} · est. saving ₹{e['saving_rs']:.0f}"
            notifications_module.notify_unit(
                other_id, "match", match_title, message=match_message, link=url_for("matches_page"),
            )
            email_service.notify_unit(other_id, match_title, match_message)
            whatsapp_service.send_match_notification(other_id, {
                "partner_name": unit_name,
                "material": e["material"].replace("_", " ").title(),
                "savings": f"{e['saving_rs']:.0f}",
                "distance": f"{e['distance_km']:.1f}",
                "partner_phone": unit.get("phone", "not on file"),
            })

    return new_matches


if __name__ == "__main__":
    for m in rank_matches():
        arrow = " -> ".join(m["path_names"])
        tag = "" if m["match_type"] == "exact" else " [similar-material]"
        print(f"[{m['hops']}-hop{tag}] score={m['score']} saving=Rs.{m['total_saving_rs']}  {arrow}  ({', '.join(m['materials'])})")
