"""
Runtime loader for the trained models saved by train_ml_models.py into
ml_artifacts/*.joblib. Every function here is non-raising by design (same
philosophy as email_service.py/whatsapp_service.py's "real if configured,
fall back otherwise" pattern): if a model hasn't been trained yet (fresh
clone, before anyone has run `python train_ml_models.py`), every predict_*
function returns None instead of crashing the request, and callers fall back
to the original hand-coded heuristic they had before -- so the app still
works end to end even without the ML artifacts present, it just runs the
pre-ML behavior until training happens once.
"""

import os

import joblib

ARTIFACT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ml_artifacts")

# Shared with ml_training_data.py (which trains the forecast model against
# synthetic profiles built using these same factors) and predictive.py (which
# computes this same feature live from a real listing's category at inference
# time) -- one definition so training and inference can't silently drift apart.
CATEGORY_INTERVAL_FACTOR = {
    "metal": 1.0, "sugar": 0.85, "paper": 1.1, "packaging": 0.9, "textile": 1.05,
    "chemical": 1.2, "food": 0.7, "plastic": 1.0, "wood": 1.15, "rubber": 1.1,
    "leather": 1.25, "electronics": 1.4, "glass": 1.3, "agro": 0.8, "other": 1.0,
}


def category_interval_factor(category):
    return CATEGORY_INTERVAL_FACTOR.get(category, 1.0)


_cache = {}


def _load(name):
    if name in _cache:
        return _cache[name]
    path = os.path.join(ARTIFACT_DIR, name)
    try:
        obj = joblib.load(path)
    except (FileNotFoundError, OSError, EOFError, Exception) as e:  # noqa: BLE001 -- deliberately broad, see module docstring
        print(f"Info: ML artifact '{name}' not available yet ({e}). "
              f"Run `python train_ml_models.py` to enable it -- falling back to the non-ML logic for now.")
        obj = None
    _cache[name] = obj
    return obj


def intake_classifier_available():
    return _load("intake_classifier.joblib") is not None


def predict_intake_type(text, min_confidence=0.60):
    """Returns ("waste"|"need", confidence) or (None, 0.0) if the model isn't
    available or isn't confident enough to commit to an answer (mirrors the
    old code's behavior of treating an unclear message as "needs a follow-up
    question" rather than guessing)."""
    bundle = _load("intake_classifier.joblib")
    if bundle is None:
        return None, 0.0
    vectorizer, model = bundle["vectorizer"], bundle["model"]
    X = vectorizer.transform([text.lower()])
    proba = model.predict_proba(X)[0]
    classes = model.classes_
    best_idx = proba.argmax()
    confidence = float(proba[best_idx])
    if confidence < min_confidence:
        return None, confidence
    return classes[best_idx], confidence


def forecast_model_available():
    return _load("forecast_model.joblib") is not None


def predict_next_listing(avg_interval_recent, avg_qty_recent, std_interval, category_factor, last_weekday):
    """Returns (predicted_interval_days, predicted_qty_kg) or None if the
    model isn't available yet -- see predictive.py for the moving-average
    fallback this backs off to."""
    bundle = _load("forecast_model.joblib")
    if bundle is None:
        return None
    model = bundle["model"]
    pred = model.predict([[avg_interval_recent, avg_qty_recent, std_interval, category_factor, last_weekday]])[0]
    predicted_interval = max(1.0, float(pred[0]))
    predicted_qty = max(1.0, float(pred[1]))
    return predicted_interval, predicted_qty


def forecast_model_benchmark():
    """Returns {"baseline_mae": ..., "model_mae": ..., "improvement_pct": ...}
    computed once at training time (see train_ml_models.py) and persisted
    alongside the model, so the UI can show "our model beats the naive
    baseline by X%" without re-running the benchmark on every page load."""
    bundle = _load("forecast_model.joblib")
    if bundle is None:
        return None
    baseline = bundle["baseline_test_mae"]
    model_mae = bundle["model_test_mae"]
    improvement = (baseline - model_mae) / baseline * 100 if baseline else 0
    return {"baseline_mae": round(baseline, 2), "model_mae": round(model_mae, 2), "improvement_pct": round(improvement, 1)}


def match_ranker_available():
    return _load("match_ranker.joblib") is not None


def predict_match_accept_probability(normalized_saving, distance_factor, confidence_factor, similarity, hops):
    """Returns a 0..1 probability the match would be accepted, or None if the
    ranker isn't available yet -- see matching.py's score_chain() for the
    fixed-weight fallback formula this backs off to."""
    bundle = _load("match_ranker.joblib")
    if bundle is None:
        return None
    model = bundle["model"]
    proba = model.predict_proba([[normalized_saving, distance_factor, confidence_factor, similarity, hops]])[0]
    # class 1 == "accepted" (see ml_training_data.generate_match_outcomes)
    return float(proba[1])


def match_ranker_feature_weights():
    """Returns {feature_name: coefficient} for the trained ranker, or None --
    used to keep explain_match() honestly reflecting what actually drives the
    score, the same "not a black box" property the original fixed-weight
    formula had."""
    bundle = _load("match_ranker.joblib")
    if bundle is None:
        return None
    model, names = bundle["model"], bundle["feature_names"]
    return dict(zip(names, model.coef_[0].tolist()))


def order_risk_model_available():
    return _load("order_risk_model.joblib") is not None


def predict_order_success_probability(order_value_factor, distance_factor, seller_trust_factor,
                                       buyer_trust_factor, qty_factor):
    """Returns a 0..1 probability the order completes successfully (as opposed
    to being cancelled or disputed), or None if the model isn't available yet
    -- see app.py's _order_risk_score() for the "don't show a risk badge at
    all" fallback this backs off to."""
    bundle = _load("order_risk_model.joblib")
    if bundle is None:
        return None
    model = bundle["model"]
    proba = model.predict_proba([[order_value_factor, distance_factor, seller_trust_factor,
                                   buyer_trust_factor, qty_factor]])[0]
    # class 1 == "completed successfully" (see ml_training_data.generate_order_outcomes)
    return float(proba[1])


def order_risk_model_benchmark():
    """Returns {"test_accuracy": ..., "test_auc": ...} computed once at
    training time (see train_ml_models.py) and persisted alongside the model."""
    bundle = _load("order_risk_model.joblib")
    if bundle is None:
        return None
    return {"test_accuracy": round(bundle["test_accuracy"], 4), "test_auc": round(bundle["test_auc"], 4)}
