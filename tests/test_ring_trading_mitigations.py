"""
Tests for the ring-trading mitigations added to trust.py, the
_trust_badge_info() diversity gate in app.py, and the retrain-readiness gate
in model_promotion.py -- responses to review feedback that named these as
real, unmitigated risks.

Also covers the bug this work surfaced along the way: the /order/<id>/complete
route used to write every completed order's two trust ratings TWICE (once via
TrustLedger.record_exchange(), once more via the now-removed _log_trust_rating()
helper), silently doubling completed_exchanges and letting a unit reach the
"Highly recommended" exchange-count threshold in half the real number of
actual exchanges -- exactly the kind of gap a ring-trading pair could exploit.
"""

import app as app_module
import model_promotion
from conftest import login
from trust import (
    TrustLedger,
    MAX_COUNTED_RATINGS_PER_COUNTERPARTY,
    MIN_DISTINCT_COUNTERPARTIES_FOR_TOP_TIER,
    CONCENTRATION_FLAG_MIN_EXCHANGES,
    CONCENTRATION_FLAG_RATIO,
)


# ---------------------------------------------------------------------------
# trust.py -- capped score, distinct-counterparty count, concentration report
# ---------------------------------------------------------------------------

def test_score_caps_ratings_from_a_single_repeat_counterparty(flask_app):
    with flask_app.app_context():
        ledger = TrustLedger()
        # U2 rates U1 five times in a row, always 5 stars -- without a cap,
        # score would just be 5.0. With MAX_COUNTED_RATINGS_PER_COUNTERPARTY,
        # only the first N of those count.
        for _ in range(5):
            ledger.record_exchange(from_id="U2", to_id="U1", rating_from_gives_to_to=5, rating_to_gives_to_from=5)
        # U3 gives one honest 1-star rating.
        ledger.record_exchange(from_id="U3", to_id="U1", rating_from_gives_to_to=1, rating_to_gives_to_from=5)

        counted = ledger._counted_ratings("U1")
        # From U2: capped at MAX_COUNTED_RATINGS_PER_COUNTERPARTY fives. From U3: one 1.
        assert counted.count(5) == MAX_COUNTED_RATINGS_PER_COUNTERPARTY
        assert counted.count(1) == 1
        assert len(counted) == MAX_COUNTED_RATINGS_PER_COUNTERPARTY + 1

        # The raw completed_exchanges count (used for the badge's exchange
        # threshold) must still reflect all 6 real ratings -- capping affects
        # the score, not the honest history.
        summary_row = next(r for r in ledger.summary() if r["unit_id"] == "U1")
        assert summary_row["completed_exchanges"] == 6


def test_distinct_counterparties_counts_unique_partners_not_total_ratings(flask_app):
    with flask_app.app_context():
        ledger = TrustLedger()
        for _ in range(4):
            ledger.record_exchange(from_id="U2", to_id="U1", rating_from_gives_to_to=5, rating_to_gives_to_from=5)
        assert ledger.distinct_counterparties("U1") == 1

        ledger.record_exchange(from_id="U3", to_id="U1", rating_from_gives_to_to=5, rating_to_gives_to_from=5)
        assert ledger.distinct_counterparties("U1") == 2


def test_concentrated_pairs_report_flags_a_ring_trading_pair(flask_app):
    with flask_app.app_context():
        ledger = TrustLedger()
        # U1 and U2 trade only with each other, enough times to clear the
        # minimum-exchanges floor and blow past the concentration ratio.
        for _ in range(CONCENTRATION_FLAG_MIN_EXCHANGES + 2):
            ledger.record_exchange(from_id="U2", to_id="U1", rating_from_gives_to_to=5, rating_to_gives_to_from=5)

        flagged = ledger.concentrated_pairs_report()
        flagged_ids = {row["unit_id"] for row in flagged}
        assert "U1" in flagged_ids
        assert "U2" in flagged_ids
        row = next(r for r in flagged if r["unit_id"] == "U1")
        assert row["partner_id"] == "U2"
        assert row["concentration_pct"] >= round(CONCENTRATION_FLAG_RATIO * 100)


def test_concentrated_pairs_report_does_not_flag_diverse_trading(flask_app):
    with flask_app.app_context():
        ledger = TrustLedger()
        # U1 trades with three different counterparties roughly evenly --
        # no single partner dominates, so nothing should be flagged.
        ledger.record_exchange(from_id="U2", to_id="U1", rating_from_gives_to_to=5, rating_to_gives_to_from=5)
        ledger.record_exchange(from_id="U3", to_id="U1", rating_from_gives_to_to=5, rating_to_gives_to_from=5)
        ledger.record_exchange(from_id="U6", to_id="U1", rating_from_gives_to_to=5, rating_to_gives_to_from=5)

        flagged_ids = {row["unit_id"] for row in ledger.concentrated_pairs_report()}
        assert "U1" not in flagged_ids


def test_concentrated_pairs_report_skips_units_below_the_minimum_exchange_floor(flask_app):
    with flask_app.app_context():
        ledger = TrustLedger()
        # Only one exchange ever -- trivially "100% with one partner", but
        # there's no real history to judge concentration from yet.
        ledger.record_exchange(from_id="U2", to_id="U1", rating_from_gives_to_to=5, rating_to_gives_to_from=5)
        assert CONCENTRATION_FLAG_MIN_EXCHANGES > 1
        flagged_ids = {row["unit_id"] for row in ledger.concentrated_pairs_report()}
        assert "U1" not in flagged_ids


# ---------------------------------------------------------------------------
# app.py -- "Highly recommended" now requires distinct-counterparty diversity
# ---------------------------------------------------------------------------

def test_highly_recommended_requires_more_than_one_counterparty(flask_app):
    with flask_app.app_context():
        # app_module.ledger reads live from the TrustRating table (see
        # trust.py's _RatingsReceivedView), so using the app's own shared
        # ledger instance here is equivalent to a fresh one -- no cached
        # state to worry about, and it exercises the exact object
        # _trust_badge_info() itself uses.
        # U1 racks up plenty of 5-star exchanges and a high exchange count,
        # but ALL from the same single counterparty (U2) -- a ring-trading
        # shape. Score and exchange-count thresholds alone would pass this.
        for _ in range(5):
            app_module.ledger.record_exchange(from_id="U2", to_id="U1", rating_from_gives_to_to=5, rating_to_gives_to_from=5)

        info = app_module._trust_badge_info("U1")
        assert info["trust_score"] >= app_module.HIGHLY_RECOMMENDED_MIN_SCORE
        assert info["completed_exchanges"] >= app_module.HIGHLY_RECOMMENDED_MIN_EXCHANGES
        assert MIN_DISTINCT_COUNTERPARTIES_FOR_TOP_TIER > 1
        # ...but it's still not "Highly recommended", because every one of
        # those exchanges is with the same single counterparty.
        assert info["highly_recommended"] is False

        # Once a second, distinct counterparty rates U1 well too, the badge
        # becomes available.
        app_module.ledger.record_exchange(from_id="U3", to_id="U1", rating_from_gives_to_to=5, rating_to_gives_to_from=5)
        info = app_module._trust_badge_info("U1")
        assert info["highly_recommended"] is True


# ---------------------------------------------------------------------------
# BUG FIX regression: completing an order via the real HTTP route must write
# exactly ONE TrustRating row per direction (2 total), not two per direction
# (4 total) -- the double-write bug found and fixed this round.
# ---------------------------------------------------------------------------

def test_completing_an_order_records_exactly_one_rating_per_direction(client, seeded_users):
    import orders as orders_module

    login(client, "test_buyer")
    client.post("/act-as", data={"unit_id": "U1"})
    order = orders_module.place_order("U2", "Ganga Alloy Casting", "cast_offcuts", "U1", "Shivam Metal Works", 5)
    orders_module.advance_order(order["id"])  # placed -> confirmed
    orders_module.set_payment_info(order["id"], status="paid")
    orders_module.advance_order(order["id"])  # confirmed -> picked_up
    orders_module.advance_order(order["id"])  # picked_up -> delivered

    resp = client.post(
        f"/order/{order['id']}/complete",
        data={"rating_buyer_to_seller": "5", "rating_seller_to_buyer": "4"},
        follow_redirects=True,
    )
    assert resp.status_code == 200

    ledger = app_module.ledger
    # Exactly one rating in each direction -- not two.
    seller_ratings = ledger.ratings_received.get("U2", [])
    buyer_ratings = ledger.ratings_received.get("U1", [])
    assert seller_ratings.count(5) == 1, f"expected exactly one 5-star rating for U2, got {seller_ratings}"
    assert buyer_ratings.count(4) == 1, f"expected exactly one 4-star rating for U1, got {buyer_ratings}"


# ---------------------------------------------------------------------------
# admin dashboard -- the new "Trading concentration flags" section actually
# renders, both when there's nothing to flag and when there is.
# ---------------------------------------------------------------------------

def test_admin_dashboard_renders_without_concentration_flags(client, seeded_users):
    login(client, "test_admin")
    resp = client.get("/admin")
    assert resp.status_code == 200
    assert b"Trading concentration flags" not in resp.data


def test_admin_dashboard_shows_a_concentration_flag_when_one_exists(client, seeded_users, flask_app):
    with flask_app.app_context():
        ledger = TrustLedger()
        for _ in range(CONCENTRATION_FLAG_MIN_EXCHANGES + 2):
            ledger.record_exchange(from_id="U2", to_id="U1", rating_from_gives_to_to=5, rating_to_gives_to_from=5)

    login(client, "test_admin")
    resp = client.get("/admin")
    assert resp.status_code == 200
    assert b"Trading concentration flags" in resp.data
    assert b"Shivam Metal Works" in resp.data


# ---------------------------------------------------------------------------
# model_promotion.py -- the enforced retrain-readiness gate
# ---------------------------------------------------------------------------

def test_promotion_blocked_below_minimum_real_examples():
    ready, reason = model_promotion.is_ready_to_promote(
        real_example_count=model_promotion.MIN_REAL_EXAMPLES - 1,
        candidate_holdout_score=0.95,
        fallback_holdout_score=0.60,
    )
    assert ready is False
    assert "need at least" in reason


def test_promotion_blocked_when_candidate_does_not_beat_fallback():
    ready, reason = model_promotion.is_ready_to_promote(
        real_example_count=model_promotion.MIN_REAL_EXAMPLES,
        candidate_holdout_score=0.55,
        fallback_holdout_score=0.60,
    )
    assert ready is False
    assert "doesn't beat" in reason


def test_promotion_allowed_once_both_conditions_are_met():
    ready, reason = model_promotion.is_ready_to_promote(
        real_example_count=model_promotion.MIN_REAL_EXAMPLES,
        candidate_holdout_score=0.72,
        fallback_holdout_score=0.60,
    )
    assert ready is True
    assert "ready to promote" in reason
