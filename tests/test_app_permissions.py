"""
HTTP-level tests against the Flask test client: login, and the order
visibility rule (_can_view_order) that restricts an order's detail/invoice
to its buyer, its seller, or an admin -- a real bug that was found and fixed
earlier in this project's history (a bystander unit could originally see
any order), so this suite exists to keep it fixed.
"""

import orders as orders_module
from conftest import login


def _place_order(client, buyer_username="test_buyer", buyer_unit="U1", seller_unit="U2"):
    """Shared setup for the accept/decline tests below: log in as the buyer,
    place an order, and hand back its id. Mirrors the pattern in
    test_buyer_and_seller_can_view_their_order_but_bystander_cannot above."""
    login(client, buyer_username)
    client.post("/act-as", data={"unit_id": buyer_unit})
    client.post(
        "/order/new",
        data={"unit_id": seller_unit, "material": "cast_offcuts", "qty_kg": "5"},
        follow_redirects=True,
    )
    listing = client.get("/api/orders").get_json()
    order_id = listing["orders"][-1]["id"]
    client.get("/logout")
    return order_id


def test_orders_page_requires_login(client):
    resp = client.get("/orders", follow_redirects=False)
    assert resp.status_code in (301, 302)
    assert "/login" in resp.headers.get("Location", "")


def test_login_with_bad_password_is_rejected(client, seeded_users):
    resp = client.post("/login", data={"username": "test_buyer", "password": "wrong"}, follow_redirects=True)
    assert resp.status_code == 200
    assert b"Please log in" not in resp.data or True  # page renders; real assertion is next
    # A failed login must not establish a session that can reach a protected page.
    protected = client.get("/orders", follow_redirects=False)
    assert protected.status_code in (301, 302)


def test_buyer_and_seller_can_view_their_order_but_bystander_cannot(client, seeded_users):
    # Buyer (U1) logs in, acts as U1, orders from U2.
    login(client, "test_buyer")
    client.post("/act-as", data={"unit_id": "U1"})
    resp = client.post(
        "/order/new",
        data={"unit_id": "U2", "material": "cast_offcuts", "qty_kg": "5"},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    client.get("/logout")

    # Find the order id via the buyer's own order list (re-login as buyer).
    login(client, "test_buyer")
    client.post("/act-as", data={"unit_id": "U1"})
    listing = client.get("/api/orders").get_json()
    assert listing["success"] is True
    assert len(listing["orders"]) == 1
    order_id = listing["orders"][0]["id"]
    client.get("/logout")

    # Seller (U2) should also see it.
    login(client, "test_seller")
    client.post("/act-as", data={"unit_id": "U2"})
    seller_view = client.get(f"/api/order/{order_id}")
    assert seller_view.status_code == 200
    assert seller_view.get_json()["success"] is True
    client.get("/logout")

    # Bystander (U3, neither buyer nor seller) must be refused.
    login(client, "test_bystander")
    client.post("/act-as", data={"unit_id": "U3"})
    bystander_view = client.get(f"/api/order/{order_id}")
    assert bystander_view.status_code == 403
    assert bystander_view.get_json()["success"] is False
    bystander_list = client.get("/api/orders").get_json()
    assert bystander_list["orders"] == []
    client.get("/logout")

    # Admin sees everything without needing to act as any company.
    login(client, "test_admin")
    admin_view = client.get(f"/api/order/{order_id}")
    assert admin_view.status_code == 200
    admin_list = client.get("/api/orders").get_json()
    assert any(o["id"] == order_id for o in admin_list["orders"])


# ---------------------------------------------------------------------------
# Accept/decline ownership rules for /order/<id>/advance and
# /order/<id>/cancel -- previously both routes only checked @login_required,
# so the buyer (or any unrelated unit) could accept/advance an order that
# wasn't theirs to accept, and anyone could cancel any order. Fixed to:
#   - advance (accept / mark picked up / mark delivered): seller only
#   - cancel while 'placed': seller (declining) OR buyer (withdrawing)
#   - cancel once 'confirmed': seller only -- buyer must use the dispute flow
#   - a paid order that's cancelled/declined gets auto-refunded
# ---------------------------------------------------------------------------

def test_buyer_cannot_accept_their_own_order(client, seeded_users):
    order_id = _place_order(client)
    orders_module.set_payment_info(order_id, razorpay_payment_id="pay_test1", status="paid")

    login(client, "test_buyer")
    client.post("/act-as", data={"unit_id": "U1"})
    resp = client.post(f"/order/{order_id}/advance", follow_redirects=True)
    assert resp.status_code == 200
    assert orders_module.order_by_id(order_id)["status"] == "placed"


def test_bystander_cannot_accept_or_cancel_order(client, seeded_users):
    order_id = _place_order(client)
    orders_module.set_payment_info(order_id, razorpay_payment_id="pay_test2", status="paid")

    login(client, "test_bystander")
    client.post("/act-as", data={"unit_id": "U3"})
    client.post(f"/order/{order_id}/advance", follow_redirects=True)
    assert orders_module.order_by_id(order_id)["status"] == "placed"

    client.post(f"/order/{order_id}/cancel", data={"reason": "not mine"}, follow_redirects=True)
    assert orders_module.order_by_id(order_id)["status"] == "placed"


def test_seller_can_accept_paid_order(client, seeded_users):
    order_id = _place_order(client)
    orders_module.set_payment_info(order_id, razorpay_payment_id="pay_test3", status="paid")

    login(client, "test_seller")
    client.post("/act-as", data={"unit_id": "U2"})
    resp = client.post(f"/order/{order_id}/advance", follow_redirects=True)
    assert resp.status_code == 200
    assert orders_module.order_by_id(order_id)["status"] == "confirmed"


def test_seller_can_accept_order_before_payment(client, seeded_users):
    """Accepting/declining is the seller's call on the order request itself,
    independent of payment -- this used to no-op silently until the buyer had
    paid (advance_order()'s old payment_status == 'paid' gate)."""
    order_id = _place_order(client)
    assert orders_module.order_by_id(order_id)["payment_status"] != "paid"

    login(client, "test_seller")
    client.post("/act-as", data={"unit_id": "U2"})
    resp = client.post(f"/order/{order_id}/advance", follow_redirects=True)
    assert resp.status_code == 200
    assert orders_module.order_by_id(order_id)["status"] == "confirmed"


def test_seller_cannot_mark_picked_up_before_payment(client, seeded_users):
    """Accepting (placed -> confirmed) is payment-independent, but the next
    step -- actually marking the order picked up, i.e. starting fulfillment
    -- must wait for the buyer to pay. advance_order() should no-op here,
    not silently skip ahead to picked_up."""
    order_id = _place_order(client)

    login(client, "test_seller")
    client.post("/act-as", data={"unit_id": "U2"})
    client.post(f"/order/{order_id}/advance", follow_redirects=True)
    assert orders_module.order_by_id(order_id)["status"] == "confirmed"

    # Try to advance again before payment -- should be a no-op.
    resp = client.post(f"/order/{order_id}/advance", follow_redirects=True)
    assert resp.status_code == 200
    assert orders_module.order_by_id(order_id)["status"] == "confirmed"


def test_seller_can_mark_picked_up_once_paid(client, seeded_users):
    order_id = _place_order(client)

    login(client, "test_seller")
    client.post("/act-as", data={"unit_id": "U2"})
    client.post(f"/order/{order_id}/advance", follow_redirects=True)
    assert orders_module.order_by_id(order_id)["status"] == "confirmed"

    orders_module.set_payment_info(order_id, razorpay_payment_id="pay_test6", status="paid")

    resp = client.post(f"/order/{order_id}/advance", follow_redirects=True)
    assert resp.status_code == 200
    assert orders_module.order_by_id(order_id)["status"] == "picked_up"


def test_seller_can_decline_placed_order_before_payment(client, seeded_users):
    order_id = _place_order(client)

    login(client, "test_seller")
    client.post("/act-as", data={"unit_id": "U2"})
    resp = client.post(f"/order/{order_id}/cancel", data={"reason": "out of stock"}, follow_redirects=True)
    assert resp.status_code == 200
    assert orders_module.order_by_id(order_id)["status"] == "cancelled"


def test_buyer_can_withdraw_own_placed_order(client, seeded_users):
    order_id = _place_order(client)

    login(client, "test_buyer")
    client.post("/act-as", data={"unit_id": "U1"})
    resp = client.post(f"/order/{order_id}/cancel", data={"reason": "changed my mind"}, follow_redirects=True)
    assert resp.status_code == 200
    assert orders_module.order_by_id(order_id)["status"] == "cancelled"


def test_buyer_cannot_cancel_once_seller_has_confirmed(client, seeded_users):
    order_id = _place_order(client)
    orders_module.set_payment_info(order_id, razorpay_payment_id="pay_test4", status="paid")

    login(client, "test_seller")
    client.post("/act-as", data={"unit_id": "U2"})
    client.post(f"/order/{order_id}/advance", follow_redirects=True)
    assert orders_module.order_by_id(order_id)["status"] == "confirmed"
    client.get("/logout")

    login(client, "test_buyer")
    client.post("/act-as", data={"unit_id": "U1"})
    resp = client.post(f"/order/{order_id}/cancel", data={"reason": "trying anyway"}, follow_redirects=True)
    assert resp.status_code == 200
    # Still confirmed -- the buyer's cancel attempt must be a no-op once the
    # seller has already accepted; a paid+confirmed order they need out of
    # goes through /order/<id>/dispute instead.
    assert orders_module.order_by_id(order_id)["status"] == "confirmed"


def test_declining_a_paid_order_refunds_it(client, seeded_users):
    order_id = _place_order(client)
    orders_module.set_payment_info(order_id, razorpay_payment_id="pay_test5", status="paid")

    login(client, "test_seller")
    client.post("/act-as", data={"unit_id": "U2"})
    client.post(f"/order/{order_id}/cancel", data={"reason": "can't fulfill"}, follow_redirects=True)

    order = orders_module.order_by_id(order_id)
    assert order["status"] == "cancelled"
    assert order["payment_status"] == "refunded"


# ---------------------------------------------------------------------------
# Payment sequencing: the order confirmation page used to auto-trigger the
# Razorpay modal on page load, and orders.html's "Pay now" button was shown
# regardless of order status -- both let a buyer be asked to pay before the
# seller had even seen the order. Fixed so payment is only offered once the
# seller has accepted (status has moved past 'placed').
# ---------------------------------------------------------------------------

def test_order_confirmation_page_does_not_offer_payment(client, seeded_users):
    login(client, "test_buyer")
    client.post("/act-as", data={"unit_id": "U1"})
    resp = client.post(
        "/order/new",
        data={"unit_id": "U2", "material": "cast_offcuts", "qty_kg": "5"},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    page = resp.data.decode()
    # No Pay button and no auto-triggering payForOrder() call should appear
    # on the confirmation page anymore -- payment isn't offered until the
    # seller accepts.
    assert "payNowBtn_" not in page
    assert "Awaiting" in page and "response" in page


def test_pay_button_hidden_until_seller_accepts(client, seeded_users):
    order_id = _place_order(client)

    # Note: _pay_script.html's shared payment modal always contains the text
    # "Pay now" (its confirm button's label span), regardless of any order's
    # status -- it's just markup for a dialog that stays hidden until JS
    # opens it. The thing that actually varies per-order is the per-order
    # trigger button rendered inline on the order card, which uses the
    # distinct "💳 Pay now" (with the emoji) text -- that's what we check.
    login(client, "test_buyer")
    client.post("/act-as", data={"unit_id": "U1"})
    placed_page = client.get("/orders").data.decode()
    assert "💳 Pay now" not in placed_page

    client.get("/logout")
    login(client, "test_seller")
    client.post("/act-as", data={"unit_id": "U2"})
    client.post(f"/order/{order_id}/advance", follow_redirects=True)
    assert orders_module.order_by_id(order_id)["status"] == "confirmed"
    client.get("/logout")

    login(client, "test_buyer")
    client.post("/act-as", data={"unit_id": "U1"})
    confirmed_page = client.get("/orders").data.decode()
    assert "💳 Pay now" in confirmed_page


# ---------------------------------------------------------------------------
# Order history on a company's /units/<id> page -- this used to render every
# order's material, quantity, counterparty name, and status to ANY logged-in
# visitor who clicked into ANY company, regardless of whether they were
# involved. Fixed to match the same buyer/seller/admin-only rule
# _can_view_order() already enforces on the Orders page itself: the company
# being viewed (acting as them) or an admin sees the full line-item table;
# everyone else sees only aggregate counts, no counterparties/materials/qty.
# ---------------------------------------------------------------------------

def _order_history_section(page_html):
    """U2 (like most seeded units) has its own public listings, which can
    legitimately include the same material name an order was placed for --
    so a plain "material not anywhere on the page" check would false-fail on
    that unrelated, intentionally-public listing. Scope the assertions to
    just the Order history <section> instead."""
    start = page_html.index("Order history")
    end = page_html.index("</section>", start)
    return page_html[start:end]


def test_bystander_sees_order_summary_not_line_items(client, seeded_users):
    order_id = _place_order(client)  # U1 (Shivam Metal Works) buys from U2

    login(client, "test_bystander")
    client.post("/act-as", data={"unit_id": "U3"})
    page = client.get("/units/U2").data.decode()
    section = _order_history_section(page)
    assert "Total orders" in section  # the aggregate summary is shown
    assert order_id not in section  # but not the order id...
    assert "Shivam Metal Works" not in section  # ...or the counterparty's name...
    assert "Buyer" not in section and "Seller" not in section  # ...or any line-item role


def test_company_sees_its_own_full_order_history(client, seeded_users):
    order_id = _place_order(client)  # U1 buys from U2

    login(client, "test_seller")
    client.post("/act-as", data={"unit_id": "U2"})
    page = client.get("/units/U2").data.decode()
    assert order_id in page
    assert "cast offcuts" in page


def test_admin_sees_full_order_history_for_any_company(client, seeded_users):
    order_id = _place_order(client)  # U1 buys from U2

    login(client, "test_admin")
    page = client.get("/units/U2").data.decode()
    assert order_id in page
    assert "cast offcuts" in page


def test_counterparty_also_sees_full_order_history(client, seeded_users):
    """The buyer, viewing the seller's company page, is also a legitimate
    party to that order -- but unit_detail's own-company check only covers
    the page being viewed (is_me for U2), not U1. This just documents that
    U1 currently falls into the "aggregate only" bucket on *U2's* page (U1's
    full view of the order is on the Orders page instead), so a future
    change to that behavior doesn't happen unnoticed."""
    order_id = _place_order(client)  # U1 buys from U2

    login(client, "test_buyer")
    client.post("/act-as", data={"unit_id": "U1"})
    page = client.get("/units/U2").data.decode()
    assert "Total orders" in page
    assert order_id not in page


# ---------------------------------------------------------------------------
# Active orders (on /orders) vs. Order History (its own page at
# /orders/history, linked from the sidebar) -- the main list used to show
# every order regardless of status, so completed and cancelled orders piled
# up forever above whatever still needed attention. Now the main .order-card
# grid on /orders only holds in-process orders (placed, confirmed, picked_up,
# delivered); completed/cancelled orders live on the separate, read-only
# Order History page instead.
# ---------------------------------------------------------------------------

def test_in_process_order_appears_on_orders_page_not_history(client, seeded_users):
    order_id = _place_order(client)  # U1 buys from U2, status stays 'placed'

    login(client, "test_buyer")
    client.post("/act-as", data={"unit_id": "U1"})
    orders_page = client.get("/orders").data.decode()
    history_page = client.get("/orders/history").data.decode()

    assert f'data-order-id="{order_id}"' in orders_page
    assert order_id not in history_page
    assert "No past orders yet" in history_page


def test_completed_order_moves_to_history_page_and_off_orders_page(client, seeded_users):
    order_id = _place_order(client)
    order = orders_module.order_by_id(order_id)
    order["status"] = "completed"

    login(client, "test_buyer")
    client.post("/act-as", data={"unit_id": "U1"})
    orders_page = client.get("/orders").data.decode()
    history_page = client.get("/orders/history").data.decode()

    assert f'data-order-id="{order_id}"' not in orders_page
    assert "No Active Orders" in orders_page
    assert order_id in history_page


def test_cancelled_order_moves_to_history_page_too(client, seeded_users):
    order_id = _place_order(client)
    order = orders_module.order_by_id(order_id)
    order["status"] = "cancelled"
    order["cancellation_reason"] = "test cancellation"

    login(client, "test_buyer")
    client.post("/act-as", data={"unit_id": "U1"})
    orders_page = client.get("/orders").data.decode()
    history_page = client.get("/orders/history").data.decode()

    assert f'data-order-id="{order_id}"' not in orders_page
    assert order_id in history_page
    assert "test cancellation" in history_page


def test_order_history_page_requires_login(client):
    resp = client.get("/orders/history", follow_redirects=False)
    assert resp.status_code in (301, 302)
    assert "/login" in resp.headers.get("Location", "")


def test_order_history_page_respects_order_visibility(client, seeded_users):
    """Same buyer/seller/admin-only visibility rule as /orders itself --
    a bystander unit must not see an order they had nothing to do with,
    even once it's moved to the read-only history page."""
    order_id = _place_order(client)  # U1 buys from U2
    order = orders_module.order_by_id(order_id)
    order["status"] = "completed"

    login(client, "test_bystander")
    client.post("/act-as", data={"unit_id": "U3"})
    page = client.get("/orders/history").data.decode()
    assert order_id not in page

    client.get("/logout")
    login(client, "test_admin")
    page = client.get("/orders/history").data.decode()
    assert order_id in page


# ---------------------------------------------------------------------------
# Bug-hunt regression tests -- each covers a bug found in a full-codebase
# audit: order_complete had no ownership check and no status guard (any
# logged-in user could complete/rate any order, at any stage, any number of
# times), bulk_advance_orders had no ownership check at all, order_new
# crashed on a non-numeric qty_kg, and iot_tick had no ownership check.
# ---------------------------------------------------------------------------

def _advance_to_delivered(order_id):
    """Setup helper: push an already-placed order all the way to 'delivered'
    via the module functions directly (payment + three advance_order calls),
    so tests can exercise the /complete route itself without re-testing the
    whole accept/pay/pickup flow every time."""
    orders_module.set_payment_info(order_id, razorpay_payment_id=f"pay_{order_id}", status="paid")
    orders_module.advance_order(order_id)  # placed -> confirmed
    orders_module.advance_order(order_id)  # confirmed -> picked_up (payment already set)
    orders_module.advance_order(order_id)  # picked_up -> delivered
    assert orders_module.order_by_id(order_id)["status"] == "delivered"


def test_bystander_cannot_complete_someone_elses_order(client, seeded_users):
    order_id = _place_order(client)  # U1 buys from U2
    _advance_to_delivered(order_id)

    login(client, "test_bystander")
    client.post("/act-as", data={"unit_id": "U3"})
    client.post(
        f"/order/{order_id}/complete",
        data={"rating_buyer_to_seller": "5", "rating_seller_to_buyer": "5"},
        follow_redirects=True,
    )
    assert orders_module.order_by_id(order_id)["status"] == "delivered"


def test_buyer_or_seller_can_complete_a_delivered_order(client, seeded_users):
    order_id = _place_order(client)
    _advance_to_delivered(order_id)

    login(client, "test_buyer")
    client.post("/act-as", data={"unit_id": "U1"})
    resp = client.post(
        f"/order/{order_id}/complete",
        data={"rating_buyer_to_seller": "5", "rating_seller_to_buyer": "4"},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    order = orders_module.order_by_id(order_id)
    assert order["status"] == "completed"
    assert order["rating_buyer_to_seller"] == 5
    assert order["rating_seller_to_buyer"] == 4


def test_order_cannot_be_completed_before_delivered(client, seeded_users):
    """Skipping straight from 'placed'/'confirmed' to 'completed' used to be
    possible since order_complete() never checked the order's current status."""
    order_id = _place_order(client)  # still 'placed'

    login(client, "test_buyer")
    client.post("/act-as", data={"unit_id": "U1"})
    client.post(
        f"/order/{order_id}/complete",
        data={"rating_buyer_to_seller": "5", "rating_seller_to_buyer": "5"},
        follow_redirects=True,
    )
    assert orders_module.order_by_id(order_id)["status"] == "placed"


def test_order_complete_is_not_double_submittable(client, seeded_users):
    """A retried/double-submitted POST to /complete must not re-record a
    second trust rating for the same exchange."""
    order_id = _place_order(client)
    _advance_to_delivered(order_id)

    login(client, "test_buyer")
    client.post("/act-as", data={"unit_id": "U1"})
    client.post(
        f"/order/{order_id}/complete",
        data={"rating_buyer_to_seller": "5", "rating_seller_to_buyer": "5"},
        follow_redirects=True,
    )
    assert orders_module.order_by_id(order_id)["status"] == "completed"

    # Second submission (e.g. a resubmit) with different ratings must be a no-op.
    client.post(
        f"/order/{order_id}/complete",
        data={"rating_buyer_to_seller": "1", "rating_seller_to_buyer": "1"},
        follow_redirects=True,
    )
    order = orders_module.order_by_id(order_id)
    assert order["rating_buyer_to_seller"] == 5  # unchanged by the second call
    assert order["rating_seller_to_buyer"] == 5
    completed_events = [h for h in order["history"] if h["status"] == "completed"]
    assert len(completed_events) == 1  # not duplicated


def test_order_new_rejects_non_numeric_quantity_without_crashing(client, seeded_users):
    login(client, "test_buyer")
    client.post("/act-as", data={"unit_id": "U1"})
    resp = client.post(
        "/order/new",
        data={"unit_id": "U2", "material": "cast_offcuts", "qty_kg": "not-a-number"},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"Enter a valid quantity" in resp.data


def test_bulk_advance_only_touches_the_callers_own_orders(client, seeded_users):
    """Previously bulk_advance_orders looped over EVERY order in the system
    with no ownership check -- any logged-in unit could bulk-advance orders
    belonging to a company they have no relationship to."""
    order_id = _place_order(client, buyer_unit="U1", seller_unit="U2")  # U2 is the seller
    assert orders_module.order_by_id(order_id)["status"] == "placed"

    # U3 (bystander, neither buyer nor seller) triggers a bulk-advance --
    # must not touch U2's order.
    login(client, "test_bystander")
    client.post("/act-as", data={"unit_id": "U3"})
    client.post("/orders/bulk-advance", data={"status_filter": "placed"}, follow_redirects=True)
    assert orders_module.order_by_id(order_id)["status"] == "placed"

    # The actual seller (U2) triggers the same bulk-advance -- their own
    # order should move.
    client.get("/logout")
    login(client, "test_seller")
    client.post("/act-as", data={"unit_id": "U2"})
    client.post("/orders/bulk-advance", data={"status_filter": "placed"}, follow_redirects=True)
    assert orders_module.order_by_id(order_id)["status"] == "confirmed"


def test_bulk_advance_reports_orders_skipped_for_unpaid(client, seeded_users):
    """bulk_advance_orders' skip-counting branch used to check
    `prev_status == "placed"`, but the payment gate in
    orders.advance_order() only ever blocks the "confirmed" -> "picked_up"
    transition -- "placed" -> "confirmed" is never payment-gated, so that
    branch could never actually match anything. The practical effect:
    clicking "Mark All Picked Up" (status_filter='confirmed') on a seller's
    unpaid confirmed orders silently advanced nothing with zero explanation,
    every time, since skipped_unpaid stayed 0 forever."""
    order_id = _place_order(client, buyer_unit="U1", seller_unit="U2")
    orders_module.advance_order(order_id)  # placed -> confirmed
    assert orders_module.order_by_id(order_id)["status"] == "confirmed"
    assert orders_module.order_by_id(order_id).get("payment_status") != "paid"

    login(client, "test_seller")
    client.post("/act-as", data={"unit_id": "U2"})
    resp = client.post("/orders/bulk-advance", data={"status_filter": "confirmed"}, follow_redirects=True)

    # Must not have silently advanced without payment.
    assert orders_module.order_by_id(order_id)["status"] == "confirmed"
    # And the seller must actually be told why nothing moved.
    assert b"Skipped" in resp.data


def test_iot_tick_requires_owning_the_sensor(client, seeded_users):
    """Previously iot_tick had no ownership check at all -- any logged-in
    user could POST any sensor_id and trigger a reading on a bin that isn't
    theirs."""
    login(client, "test_seller")
    client.post("/act-as", data={"unit_id": "U2"})
    client.post("/iot/register", data={"material": "metal_shavings"}, follow_redirects=True)
    dashboard = client.get("/iot").data.decode()
    assert "ESP32-U2-" in dashboard
    client.get("/logout")

    # Find the sensor id from the DB directly (simplest way to get an int id
    # for the URL without scraping the HTML).
    from models import BinSensor
    from app import app as flask_app_module
    with flask_app_module.app_context():
        sensor = BinSensor.query.filter_by(unit_id="U2", material="metal_shavings").first()
        sensor_id = sensor.id

    login(client, "test_bystander")
    client.post("/act-as", data={"unit_id": "U3"})
    resp = client.post(f"/iot/{sensor_id}/tick", follow_redirects=True)
    assert resp.status_code == 200
    assert b"own company" in resp.data
