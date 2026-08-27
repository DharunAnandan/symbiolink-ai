"""
Tests for the trained ML layer (ml_text.py / ml_models.py / train_ml_models.py)
introduced to replace the original hand-coded heuristics with real, trained
models -- see the module docstrings in ml_models.py and ml_text.py for the
full "why" (TF-IDF instead of sentence-transformers, Ridge instead of
RandomForest for the forecaster, etc).

These tests assume `python train_ml_models.py` has already been run and
ml_artifacts/*.joblib exist (true in this repo -- they're committed the same
way any other generated-once artifact would be). The graceful-fallback tests
at the bottom simulate the "not trained yet" case directly, without needing
to delete the real artifacts.
"""

import ml_models
import ml_text
import matching
import predictive
import whatsapp_stub


# ---------------------------------------------------------------------------
# ml_text.py -- TF-IDF material matching
# ---------------------------------------------------------------------------

def test_best_material_match_handles_typo():
    """Char n-grams should survive a misspelling that shares no exact word
    with the canonical name or any alias."""
    key, score = ml_text.best_material_match("we have some sheet trimings for sale")
    assert key == "sheet_trimmings"
    assert score > 0


def test_best_material_match_handles_synonym_alias():
    key, score = ml_text.best_material_match("looking for some iron filings")
    assert key == "metal_shavings"


def test_best_material_match_rejects_noise():
    """Messages with no real material signal must not force a match --
    see ml_text.py's threshold=0.25 default and the noise-phrase bug this
    guards against (documented in ml_text.py's development history)."""
    for noise in ["hello how are you doing today", "this is just a test message", "good morning everyone"]:
        key, score = ml_text.best_material_match(noise)
        assert key is None, f"expected no match for noise phrase {noise!r}, got {key!r} ({score})"


def test_material_similarity_identical_is_exactly_one():
    assert ml_text.material_similarity("metal_shavings", "metal_shavings") == 1.0


# ---------------------------------------------------------------------------
# ml_models.py -- intake classifier
# ---------------------------------------------------------------------------

def test_intake_classifier_available_and_confident_on_clear_examples():
    assert ml_models.intake_classifier_available(), "run `python train_ml_models.py` before testing"
    label, conf = ml_models.predict_intake_type("we have 200kg of scrap metal to give away")
    assert label == "waste"
    assert conf >= 0.60
    label, conf = ml_models.predict_intake_type("urgently need 100kg of cardboard for packaging")
    assert label == "need"
    assert conf >= 0.60


# ---------------------------------------------------------------------------
# ml_models.py -- forecast regressor
# ---------------------------------------------------------------------------

def test_forecast_model_beats_baseline_on_held_out_data():
    """The whole point of training a model instead of keeping the plain
    moving average is that it's demonstrably better -- this is the same
    number predictive.benchmark_vs_baseline() surfaces to the UI, checked
    here so a future retrain can't silently regress below the baseline."""
    assert ml_models.forecast_model_available(), "run `python train_ml_models.py` before testing"
    bench = predictive.benchmark_vs_baseline()
    assert bench is not None
    assert bench["model_mae"] < bench["baseline_mae"]
    assert bench["improvement_pct"] > 0


# ---------------------------------------------------------------------------
# ml_models.py -- match ranker
# ---------------------------------------------------------------------------

def test_match_ranker_available_and_feature_weights_exposed():
    """explain_match() leans on this to stay 'not a black box' -- see
    matching.py's explain_match() docstring."""
    assert ml_models.match_ranker_available(), "run `python train_ml_models.py` before testing"
    weights = ml_models.match_ranker_feature_weights()
    assert weights
    expected = {"normalized_saving", "distance_factor", "confidence_factor", "similarity", "hops"}
    assert set(weights.keys()) == expected


def test_match_ranker_probability_increases_with_saving():
    """Sanity/monotonicity check: all else equal, a chain that saves more
    money should never be scored as less likely to be accepted."""
    low = ml_models.predict_match_accept_probability(
        normalized_saving=0.1, distance_factor=0.8, confidence_factor=0.8, similarity=1.0, hops=1)
    high = ml_models.predict_match_accept_probability(
        normalized_saving=0.9, distance_factor=0.8, confidence_factor=0.8, similarity=1.0, hops=1)
    assert low is not None and high is not None
    assert high > low


def test_match_ranker_probability_decreases_with_distance_penalty():
    """distance_factor is "closer is better" (1 - distance/MAX_RADIUS), so a
    higher distance_factor (closer) should never score lower than a farther
    chain with everything else equal."""
    far = ml_models.predict_match_accept_probability(
        normalized_saving=0.5, distance_factor=0.1, confidence_factor=0.8, similarity=1.0, hops=1)
    near = ml_models.predict_match_accept_probability(
        normalized_saving=0.5, distance_factor=0.9, confidence_factor=0.8, similarity=1.0, hops=1)
    assert far is not None and near is not None
    assert near > far


# ---------------------------------------------------------------------------
# ml_models.py -- order-risk model
# ---------------------------------------------------------------------------

def test_order_risk_model_available_and_benchmark_shaped():
    assert ml_models.order_risk_model_available(), "run `python train_ml_models.py` before testing"
    bench = ml_models.order_risk_model_benchmark()
    assert bench is not None
    assert 0.0 <= bench["test_accuracy"] <= 1.0
    assert 0.0 <= bench["test_auc"] <= 1.0


def test_order_success_probability_increases_with_trust():
    """Sanity/monotonicity check: all else equal, higher trust on either side
    should never make an order look riskier."""
    low = ml_models.predict_order_success_probability(
        order_value_factor=0.3, distance_factor=0.7, seller_trust_factor=0.5,
        buyer_trust_factor=0.5, qty_factor=0.3)
    high = ml_models.predict_order_success_probability(
        order_value_factor=0.3, distance_factor=0.7, seller_trust_factor=1.0,
        buyer_trust_factor=1.0, qty_factor=0.3)
    assert low is not None and high is not None
    assert high > low


def test_order_success_probability_decreases_with_value_and_qty():
    """A bigger order (higher value and quantity), everything else equal,
    should never be scored as safer than a smaller one."""
    small = ml_models.predict_order_success_probability(
        order_value_factor=0.1, distance_factor=0.6, seller_trust_factor=0.8,
        buyer_trust_factor=0.8, qty_factor=0.1)
    large = ml_models.predict_order_success_probability(
        order_value_factor=0.9, distance_factor=0.6, seller_trust_factor=0.8,
        buyer_trust_factor=0.8, qty_factor=0.9)
    assert small is not None and large is not None
    assert small > large


def test_order_success_probability_decreases_with_distance():
    """distance_factor is "closer is better" (1 - distance/MAX_RADIUS), so a
    farther-apart pair (lower distance_factor) should never score safer than
    a nearby pair with everything else equal."""
    far = ml_models.predict_order_success_probability(
        order_value_factor=0.4, distance_factor=0.1, seller_trust_factor=0.8,
        buyer_trust_factor=0.8, qty_factor=0.4)
    near = ml_models.predict_order_success_probability(
        order_value_factor=0.4, distance_factor=0.9, seller_trust_factor=0.8,
        buyer_trust_factor=0.8, qty_factor=0.4)
    assert far is not None and near is not None
    assert near > far


# ---------------------------------------------------------------------------
# Integration: score_chain / explain_match surface the ranker correctly
# ---------------------------------------------------------------------------

def test_score_chain_uses_ml_when_available_and_explain_match_names_it():
    edges = matching.build_edges()
    chains = matching.find_chains(edges)
    assert chains
    score, saving, all_exact, avg_trust, score_method = matching.score_chain(chains[0])
    assert score_method == "ml"
    reason = matching.explain_match(chains[0], avg_trust, score_method)
    assert "AI-ranked" in reason


# ---------------------------------------------------------------------------
# Graceful fallback -- every ML call must degrade to the pre-ML heuristic,
# never raise, when the artifact isn't available (fresh clone before anyone
# has run train_ml_models.py). Simulated here by monkeypatching _load()
# rather than deleting the real artifacts other tests rely on.
# ---------------------------------------------------------------------------

def test_predict_functions_return_none_when_artifacts_missing(monkeypatch):
    monkeypatch.setattr(ml_models, "_load", lambda name: None)
    assert ml_models.predict_intake_type("we have scrap metal to give away") == (None, 0.0)
    assert ml_models.predict_next_listing(5, 100, 1.0, 1.0, 2) is None
    assert ml_models.forecast_model_benchmark() is None
    assert ml_models.predict_match_accept_probability(0.5, 0.5, 0.5, 1.0, 1) is None
    assert ml_models.match_ranker_feature_weights() is None
    assert ml_models.predict_order_success_probability(0.5, 0.5, 0.5, 0.5, 0.5) is None
    assert ml_models.order_risk_model_benchmark() is None


def test_whatsapp_stub_falls_back_to_keyword_detection_without_ml(monkeypatch):
    """detect_type()/detect_material() must still work end to end via the
    original keyword/alias logic if the ML layer is unavailable."""
    monkeypatch.setattr(ml_models, "predict_intake_type", lambda text, min_confidence=0.60: (None, 0.0))
    monkeypatch.setattr(ml_text, "best_material_match", lambda text, threshold=0.25: (None, 0.0))
    result = whatsapp_stub.parse_message("U1", "we have 220kg metal shavings to give away this week")
    assert result["ok"] is True
    assert result["type"] == "waste"
    assert result["material"] == "metal_shavings"
    assert result["material_method"] == "keyword"
    assert result["type_method"] == "keyword"


def test_score_chain_falls_back_to_heuristic_without_ranker(monkeypatch):
    monkeypatch.setattr(ml_models, "predict_match_accept_probability",
                         lambda **kwargs: None)
    edges = matching.build_edges()
    chains = matching.find_chains(edges)
    score, saving, all_exact, avg_trust, score_method = matching.score_chain(chains[0])
    assert score_method == "heuristic"
    reason = matching.explain_match(chains[0], avg_trust, score_method)
    assert "AI-ranked" not in reason
