"""
WhatsApp / voice-note listing intake (simulated for this demo).

Real integration path (not wired up here, since it needs live credentials):
  - Twilio WhatsApp Sandbox: add a Flask route  POST /whatsapp/webhook  that reads
    request.form['From'] and request.form['Body'] (Twilio's inbound webhook payload).
  - For voice notes: Twilio gives a MediaUrl; download the audio and run it through a
    speech-to-text API (e.g. OpenAI Whisper or Google Speech-to-Text) to get `Body`-style
    text, then feed it into parse_message() exactly like a typed message.
  - See README.md for the free-tier setup steps for both.

Everything below simulates that inbound message already being converted to plain text
(as it would be after Twilio + speech-to-text), and parses it into a structured listing.

Two real, trained ML models do the understanding (see ml_models.py / train_ml_models.py):
  - a TF-IDF vector-space material matcher (ml_text.best_material_match) instead of an
    exact-substring alias dict, so typos/spacing variants and any phrase close to a
    known alias are recognized, not just an exact hit;
  - a trained text classifier (LogisticRegression over TF-IDF) for waste-vs-need,
    instead of a fixed "these exact words mean waste, those exact words mean need"
    list, so phrasing that doesn't contain an obvious trigger word can still be
    read correctly (see ml_training_data.py's HARD_WASTE_EXAMPLES/HARD_NEED_EXAMPLES).

Both models gracefully fall back to a simple keyword/alias check (the original
approach) if ml_artifacts/ hasn't been generated yet -- see ml_models.py's docstring
-- so a fresh clone still works before anyone runs `python train_ml_models.py`.
Quantity extraction stays a plain regex on purpose: "find a number followed by a
weight unit" is a deterministic parsing task with no ambiguity to learn from data,
not something that benefits from a trained model.
"""

import re

import data
import ml_models
import ml_text
import ml_multilingual

# PRICE_TABLE_RS_PER_KG/unit_by_id must stay data.X attribute lookups, not bare
# "from data import ..." -- see the note in matching.py for why.

QTY_PATTERN = re.compile(r"(\d+(?:\.\d+)?)\s*(?:kg|kgs|kilograms?|kilos?)\b", re.IGNORECASE)


def _detect_material_keyword_fallback(text_lower):
    """Original exact-substring alias lookup, used only when the ML matcher
    (ml_text.best_material_match) isn't available or isn't confident."""
    for key in data.PRICE_TABLE_RS_PER_KG:
        if key.replace("_", " ") in text_lower:
            return key
    for key, aliases in ml_text.MATERIAL_ALIASES.items():
        for phrase in aliases:
            if phrase in text_lower:
                return key
    return None


def _detect_type_keyword_fallback(text_lower):
    if any(w in text_lower for w in ml_text.NEED_WORDS):
        return "need"
    if any(w in text_lower for w in ml_text.WASTE_WORDS):
        return "waste"
    return None


def detect_material(text_lower):
    """Material detection, three layers deep: MuRIL cross-lingual embedding
    match first if it's been opted into and installed (see
    ml_multilingual.py -- inactive by default, so this is normally a no-op
    check that returns instantly); then the always-on TF-IDF vector-space
    match (typo/synonym/Hinglish-alias robust -- see ml_text.py); then
    exact-substring alias lookup as a last resort if neither model is
    trained/confident about this particular message."""
    if ml_multilingual.available():
        material_key, _score = ml_multilingual.best_material_match_muril(text_lower)
        if material_key:
            return material_key
    material_key, score = ml_text.best_material_match(text_lower)
    if material_key:
        return material_key
    return _detect_material_keyword_fallback(text_lower)


def detect_type(text_lower):
    """Waste-vs-need detection: trained classifier first, keyword list as a
    fallback. See module docstring for why both exist."""
    predicted, confidence = ml_models.predict_intake_type(text_lower)
    if predicted:
        return predicted
    return _detect_type_keyword_fallback(text_lower)


def parse_message(unit_id, raw_text):
    """Turn one inbound WhatsApp/voice-note-transcribed message into a listing dict,
    or return None with a reason if it couldn't be understood (in which case the real
    system would ask a short WhatsApp follow-up question, e.g. 'How many kg?')."""
    text_lower = raw_text.lower()

    qty_match = QTY_PATTERN.search(text_lower)

    material, material_score, material_method = None, 0.0, None
    if ml_multilingual.available():
        material, material_score = ml_multilingual.best_material_match_muril(text_lower)
        if material:
            material_method = "muril"
    if not material:
        material, material_score = ml_text.best_material_match(text_lower)
        if material:
            material_method = "ml"
    if not material:
        material = _detect_material_keyword_fallback(text_lower)
        material_method = "keyword" if material else None

    listing_type, type_confidence = ml_models.predict_intake_type(text_lower)
    type_method = "ml"
    if not listing_type:
        listing_type = _detect_type_keyword_fallback(text_lower)
        type_method = "keyword" if listing_type else None

    missing = []
    if not qty_match:
        missing.append("quantity")
    if not material:
        missing.append("material")
    if not listing_type:
        missing.append("whether this is waste to give or a need to fill")

    if missing:
        return {
            "ok": False,
            "unit_id": unit_id,
            "raw_text": raw_text,
            "reason": f"Could not understand: {', '.join(missing)}. Would send a WhatsApp "
                      f"follow-up question asking for the missing detail.",
        }

    unit = data.unit_by_id(unit_id)
    return {
        "ok": True,
        "unit_id": unit_id,
        "unit_name": unit["name"] if unit else unit_id,
        "type": listing_type,
        "material": material,
        "qty_kg": float(qty_match.group(1)),
        "raw_text": raw_text,
        "material_confidence": round(material_score, 2) if material_method in ("ml", "muril") else None,
        "material_method": material_method,
        "type_confidence": round(type_confidence, 2) if type_method == "ml" else None,
        "type_method": type_method,
    }


# Simulated inbound messages, as if received via Twilio WhatsApp sandbox / a transcribed voice note
SIMULATED_INBOX = [
    ("U1", "Hi, we have 220kg metal shavings to give away this week"),
    ("U2", "We need 150kg metal shavings for our casting line"),
    ("U9", "extra 45kg process effluent solids, byproduct from our unit"),
    ("U6", "looking for cardboard offcuts, around 35kg"),
    ("U7", "Voice note transcript: we have some fabric offcuts, about 60 kilograms"),
    ("U3", "we produce sheet trimmings sometimes"),  # deliberately incomplete -> triggers follow-up
]


if __name__ == "__main__":
    print("Simulated WhatsApp/voice-note intake:\n")
    for unit_id, msg in SIMULATED_INBOX:
        result = parse_message(unit_id, msg)
        if result["ok"]:
            print(f"[OK] {result['unit_name']}: \"{msg}\"\n     -> {result['type'].upper()} "
                  f"{result['qty_kg']}kg {result['material']}\n")
        else:
            print(f"[NEEDS FOLLOW-UP] unit {unit_id}: \"{msg}\"\n     -> {result['reason']}\n")
