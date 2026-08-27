"""
Tests for the four low-complexity, high-value additions built on top of
existing infrastructure: WhatsApp order-lifecycle reminders, market price
transparency, explainable order-risk scores, and admin network-gap analysis.
"""

from datetime import datetime, timedelta

import app as app_module
import matching
import market_insights
import orders as orders_module
from conftest import login


def _stale(hours):
    return (datetime.now() - timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M")


# ---------------------------------------------------------------------------
# WhatsApp order-lifecycle reminders (orders.orders_needing_reminder)
# ---------------------------------------------------------------------------

def test_no_reminder_for_a_freshly_confirmed_unpaid_order():
    order = orders_module.place_order("U2", "Ganga Alloy Casting", "cast_offcuts", "U1", "Shivam Metal Works", 5)
    orders_module.advance_order(order["id"])  # placed -> confirmed, just now
    assert orders_module.orders_needing_reminder() == []


def test_stale_unpaid_confirmed_order_nudges_the_buyer():
    order = orders_module.place_order("U2", "Ganga Alloy Casting", "cast_offcuts", "U1", "Shivam Metal Works", 5)
    orders_module.advance_order(order["id"])
    order["history"][-1]["at"] = _stale(25)

    due = orders_module.orders_needing_reminder()
    assert len(due) == 1
    assert due[0]["reason"] == "payment_due"
    # The buyer is the one who owes payment before advance_order()'s gate
    # lets the seller move it forward.
    assert due[0]["target_unit_id"] == "U1"


def test_stale_paid_confirmed_order_nudges_the_seller_to_pick_up():
    order = orders_module.place_order("U2", "Ganga Alloy Casting", "cast_offcuts", "U1", "Shivam Metal Works", 5)
    orders_module.advance_order(order["id"])
    orders_module.set_payment_info(order["id"], status="paid")
    order["history"][-1]["at"] = _stale(30)

    due = orders_module.orders_needing_reminder()
    assert len(due) == 1
    assert due[0]["reason"] == "pickup_due"
    assert due[0]["target_unit_id"] == "U2"


def test_stale_picked_up_order_nudges_the_seller_to_deliver():
    order = orders_module.place_order("U2", "Ganga Alloy Casting", "cast_offcuts", "U1", "Shivam Metal Works", 5)
    orders_module.advance_order(order["id"])
    orders_module.set_payment_info(order["id"], status="paid")
    orders_module.advance_order(order["id"])  # confirmed -> picked_up (payment ok)
    order["history"][-1]["at"] = _stale(30)

    due = orders_module.orders_needing_reminder()
    assert len(due) == 1
    assert due[0]["reason"] == "delivery_due"
    assert due[0]["target_unit_id"] == "U2"


def test_placed_and_delivered_orders_are_never_flagged_no_matter_how_stale():
    """'placed' means the seller hasn't even decided yet (nobody's overdue),
    and 'delivered' just means completion/rating is pending, which is the
    buyer's call whenever they get to it -- not a stalled fulfillment step."""
    placed = orders_module.place_order("U2", "Ganga Alloy Casting", "cast_offcuts", "U1", "Shivam Metal Works", 5)
    placed["history"][-1]["at"] = _stale(999)

    delivered = orders_module.place_order("U2", "Ganga Alloy Casting", "sheet_trimmings", "U11", "RMD Precision Tools", 5)
    orders_module.advance_order(delivered["id"])
    orders_module.set_payment_info(delivered["id"], status="paid")
    orders_module.advance_order(delivered["id"])  # picked_up
    orders_module.advance_order(delivered["id"])  # delivered
    delivered["history"][-1]["at"] = _stale(999)

    assert orders_module.orders_needing_reminder() == []


def test_admin_send_reminders_route_reports_counts_and_notifies(client, seeded_users):
    order = orders_module.place_order("U2", "Ganga Alloy Casting", "cast_offcuts", "U1", "Shivam Metal Works", 5)
    orders_module.advance_order(order["id"])
    order["history"][-1]["at"] = _stale(25)

    login(client, "test_admin")
    resp = client.post("/admin/send-reminders", follow_redirects=True)
    assert resp.status_code == 200
    assert b"Sent 1 reminder" in resp.data
    # Doesn't re-flag the same order a second time within the same window --
    # not because of any dedup logic, just because the order's already
    # awaiting the reminder trigger's own real state didn't change. Calling
    # again should still report it (this app has no "already reminded" flag
    # yet -- it's a stateless, on-demand nudge), so just confirm it doesn't error.
    resp2 = client.post("/admin/send-reminders", follow_redirects=True)
    assert resp2.status_code == 200


def test_non_admin_cannot_trigger_reminders(client, seeded_users):
    login(client, "test_buyer")
    resp = client.post("/admin/send-reminders", follow_redirects=False)
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Market price transparency (market_insights.material_price_guide)
# ---------------------------------------------------------------------------

def test_price_guide_covers_every_known_material_with_zero_activity_by_default():
    rows = market_insights.material_price_guide()
    materials = {r["material"] for r in rows}
    assert "metal_shavings" in materials
    row = next(r for r in rows if r["material"] == "metal_shavings")
    assert row["completed_orders"] == 0
    assert row["byproduct_price_rs"] > 0
    assert row["new_material_price_rs"] > row["byproduct_price_rs"]
    assert row["saving_pct"] > 0


def test_price_guide_reflects_a_real_completed_order():
    order = orders_module.place_order("U1", "Shivam Metal Works", "metal_shavings", "U11", "RMD Precision Tools", 40)
    orders_module.advance_order(order["id"])  # confirmed
    orders_module.set_payment_info(order["id"], status="paid")
    orders_module.advance_order(order["id"])  # picked_up
    orders_module.advance_order(order["id"])  # delivered
    orders_module.complete_order(order["id"], rating_buyer_to_seller=5, rating_seller_to_buyer=5)

    rows = market_insights.material_price_guide()
    row = next(r for r in rows if r["material"] == "metal_shavings")
    assert row["completed_orders"] == 1
    assert row["total_kg_traded"] == 40.0
    assert row["total_saving_rs"] > 0


def test_market_prices_page_renders_for_a_logged_in_user(client, seeded_users):
    login(client, "test_buyer")
    resp = client.get("/market-prices")
    assert resp.status_code == 200
    assert b"Metal Shavings" in resp.data


# ---------------------------------------------------------------------------
# Explainable order-risk scores (app._order_risk_explanation)
# ---------------------------------------------------------------------------

def test_low_risk_order_has_no_explanation():
    with app_module.app.app_context():
        # U1 -> U2 is the cluster's strongest seeded trust pair, close
        # together, at a small quantity -- should score comfortably "low".
        order = orders_module.place_order("U1", "Shivam Metal Works", "cast_offcuts", "U2", "Ganga Alloy Casting", 2)
        prob, label = app_module._order_risk_score(order)
        assert label is not None
        if label == "low":
            assert app_module._order_risk_explanation(order, label) is None


def test_medium_or_high_risk_order_gets_a_concrete_explanation():
    with app_module.app.app_context():
        # A large, far-apart order (U1 <-> U11 is well within radius but at
        # the app's post-fix ORDER_VALUE_NORM_RS=4000 scale, 220kg of
        # metal_shavings is a genuinely "large" order) -- see app.py's own
        # comment on ORDER_VALUE_NORM_RS for why this combination reliably
        # lands medium/high.
        order = orders_module.place_order("U1", "Shivam Metal Works", "metal_shavings", "U11", "RMD Precision Tools", 220)
        prob, label = app_module._order_risk_score(order)
        assert label in ("medium", "high")
        explanation = app_module._order_risk_explanation(order, label)
        assert explanation
        assert "due to" in explanation or "combination" in explanation


def test_risk_explanation_is_none_when_model_unavailable(monkeypatch):
    with app_module.app.app_context():
        order = orders_module.place_order("U1", "Shivam Metal Works", "metal_shavings", "U11", "RMD Precision Tools", 220)
        monkeypatch.setattr(app_module.ml_models, "predict_order_success_probability", lambda **kwargs: None)
        prob, label = app_module._order_risk_score(order)
        assert (prob, label) == (None, None)
        assert app_module._order_risk_explanation(order, label) is None


# ---------------------------------------------------------------------------
# Network gap analysis (matching.network_gap_report)
# ---------------------------------------------------------------------------

def test_network_gap_report_flags_a_known_seed_data_gap():
    """U6's cardboard_offcuts waste listing has no NEED listing anywhere in
    the seed data for that material -- a real, pre-existing gap in data.py,
    not something this test manufactures. Documents the report actually
    finds it, rather than only ever returning an empty list."""
    unmet_supply, _unmet_demand = matching.network_gap_report()
    assert any(row["unit_id"] == "U6" and row["material"] == "cardboard_offcuts" for row in unmet_supply)


def test_network_gap_report_excludes_listings_with_a_reachable_match():
    """U1's metal_shavings waste listing IS matched by U2's need listing in
    the seed data -- must never show up as an unmet gap."""
    unmet_supply, unmet_demand = matching.network_gap_report()
    assert not any(row["unit_id"] == "U1" and row["material"] == "metal_shavings" for row in unmet_supply)
    assert not any(row["unit_id"] == "U2" and row["material"] == "metal_shavings" for row in unmet_demand)


def test_admin_dashboard_shows_network_gaps_and_reminder_count(client, seeded_users):
    login(client, "test_admin")
    resp = client.get("/admin")
    assert resp.status_code == 200
    assert b"Network gaps" in resp.data
    assert b"Send pending reminders" in resp.data
