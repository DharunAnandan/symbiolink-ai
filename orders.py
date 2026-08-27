"""
Order layer.

Turns "browse and search" into an actual transaction with a real lifecycle, not a
single pending -> completed jump: placed -> confirmed by seller -> picked up ->
delivered -> rated. Each transition is logged with a timestamp, and only the final
rating step feeds into the trust ledger (see trust.py), exactly like the seeded
historical exchanges do.
"""

import threading
from datetime import datetime

ORDERS = []  # in-memory order ledger for this demo session

# app.py runs the dev server with threaded=True (see the bottom of app.py --
# needed so several requests per page load, e.g. the HTML plus static
# assets, don't queue up behind each other), so this module's ORDERS list is
# genuinely shared across concurrent request-handling threads within one
# process. _next_order_id() read len(ORDERS) and place_order() appended to
# it as two separate, non-atomic steps -- two place_order() calls landing on
# different threads at close enough to the same time could both read the
# same len(ORDERS) before either appended, handing out the same order id
# (e.g. two different buyers both getting "ORD007"). order_by_id() only
# ever finds the first match, so every route addressing the second order by
# id (/order/<id>/advance, /complete, /cancel, Razorpay lookups...) would
# silently operate on the wrong order instead. This lock makes "compute the
# next id and append the new order" one atomic step, the same spirit as
# data_access.py's _generate_unit_id() collision guard for the DB-backed
# unit-id path.
_ORDERS_LOCK = threading.Lock()

STAGE_SEQUENCE = ["placed", "confirmed", "picked_up", "delivered", "completed", "cancelled"]
STAGE_LABELS = {
    "placed": "Placed",
    "confirmed": "Confirmed by seller",
    "picked_up": "Picked up",
    "delivered": "Delivered",
    "completed": "Completed & rated",
    "cancelled": "Cancelled",
}
# What action the *next* button should say, keyed by current stage
NEXT_ACTION_LABEL = {
    "placed": "Confirm order (seller)",
    "confirmed": "Mark picked up",
    "picked_up": "Mark delivered",
}


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def _next_order_id():
    return f"ORD{len(ORDERS) + 1:03d}"


def place_order(seller_unit_id, seller_name, material, buyer_unit_id, buyer_name, qty_kg, co2_saved_kg=0):
    with _ORDERS_LOCK:
        order = {
            "id": _next_order_id(),
            "seller_unit_id": seller_unit_id,
            "seller_name": seller_name,
            "material": material,
            "buyer_unit_id": buyer_unit_id,
            "buyer_name": buyer_name,
            "qty_kg": qty_kg,
            "co2_saved_kg": co2_saved_kg,
            "status": "placed",
            "payment_status": "unpaid",
            "dispute_status": None,  # None | "open" | "resolved_refunded" | "resolved_rejected"
            "created_at": _now(),
            "history": [{"status": "placed", "at": _now()}],
        }
        ORDERS.append(order)
    return order


def order_by_id(order_id):
    for o in ORDERS:
        if o["id"] == order_id:
            return o
    return None


def next_stage(current_status):
    """Returns the next stage name in the sequence, or None if there isn't one
    (either already completed, or the next step is the rating step, which is
    handled separately by complete_order since it needs extra input)."""
    try:
        idx = STAGE_SEQUENCE.index(current_status)
    except ValueError:
        return None
    if idx + 1 >= len(STAGE_SEQUENCE):
        return None
    return STAGE_SEQUENCE[idx + 1]


def advance_order(order_id):
    """Move an order to its next stage (placed -> confirmed -> picked_up -> delivered).
    Does not handle the final 'completed' transition -- that requires ratings and goes
    through complete_order() instead."""
    order = order_by_id(order_id)
    if not order:
        return None
    # Terminal states must be a no-op. Without this guard, next_stage("completed")
    # returns "cancelled" (it's the entry right after "completed" in STAGE_SEQUENCE),
    # which would silently flip a completed order to cancelled if advance_order is
    # ever called on it again (e.g. via bulk-advance).
    if order["status"] in ("completed", "cancelled"):
        return order
    # Accepting (or declining) an order is a decision the seller makes on the
    # order request itself, independent of payment -- a seller can say yes/no
    # to "will you take 5kg of X" before any money has moved, the same way a
    # marketplace seller confirms an order before it's necessarily been paid
    # for. So "placed" -> "confirmed" (accepting) is never payment-gated,
    # same as decline never has been (see app.py::order_cancel).
    #
    # But fulfillment -- actually handing the material over -- shouldn't
    # start until the buyer has paid for what they agreed to accept. So the
    # *next* transition, "confirmed" -> "picked_up", IS gated on payment: if
    # the seller tries to mark it picked up before payment_status == 'paid',
    # this is a no-op (order stays 'confirmed') rather than silently
    # skipping ahead. Once paid, the seller (or, once picked up, whoever
    # progresses it) can move it through picked_up -> delivered freely --
    # payment only needs to be checked once, at the gate right after it.
    if order["status"] == "confirmed" and order.get("payment_status") != "paid":
        return order
    nxt = next_stage(order["status"])
    if nxt and nxt != "completed":
        order["status"] = nxt
        order["history"].append({"status": nxt, "at": _now()})
    return order


def complete_order(order_id, rating_buyer_to_seller, rating_seller_to_buyer):
    order = order_by_id(order_id)
    if not order:
        return None
    # Only a 'delivered' order can be completed -- without this guard, a
    # still-'placed'/'confirmed' order could jump straight to 'completed'
    # (skipping pickup/delivery/payment entirely), and calling this a second
    # time on an already-completed order would silently overwrite its
    # ratings and append a duplicate "completed" history entry -- app.py's
    # order_complete() records a trust rating on every successful call, so
    # an unguarded double-call double-records ratings for the same exchange.
    # This also doubles as the idempotency guard: once status flips to
    # 'completed', a retried/double-submitted request finds this check
    # failing and returns None instead of doing anything.
    if order["status"] != "delivered":
        return None
    order["status"] = "completed"
    order["rating_buyer_to_seller"] = rating_buyer_to_seller
    order["rating_seller_to_buyer"] = rating_seller_to_buyer
    order["history"].append({"status": "completed", "at": _now()})
    return order


def cancel_order(order_id, reason, cancelled_by):
    """Cancel an order with reason tracking."""
    order = order_by_id(order_id)
    if not order:
        return None
    
    # Only allow cancellation for placed or confirmed orders
    if order["status"] not in ["placed", "confirmed"]:
        return None
    
    order["status"] = "cancelled"
    order["cancellation_reason"] = reason
    order["cancelled_by"] = cancelled_by
    order["cancelled_at"] = _now()
    order["history"].append({
        "status": "cancelled",
        "at": _now(),
        "reason": reason,
        "by": cancelled_by
    })
    
    # Restore listing quantity. If the seller still has an active listing for this
    # material, top it up rather than unconditionally creating a new one -- calling
    # add_listing() unconditionally here used to create a duplicate listing row
    # every time an order was cancelled while an active listing already existed,
    # inflating search results and total-waste stats for that material.
    try:
        import data
        existing = data.find_listing(order["seller_unit_id"], order["material"], "waste")
        if existing:
            data.update_listing_qty_absolute(
                order["seller_unit_id"], order["material"], "waste",
                existing["qty_kg"] + order["qty_kg"],
            )
        else:
            data.add_listing(order["seller_unit_id"], "waste", order["material"], order["qty_kg"])
    except Exception as e:
        print(f"Error restoring listing quantity: {e}")
    
    return order


# How long an order can sit, unattended, in a state where a specific party
# owes the next action before this platform proactively nudges them (see
# orders_needing_reminder below). 24h keeps a demo/short-lived cluster
# responsive without spamming a party who simply hasn't gotten to it yet.
REMINDER_STALE_HOURS = 24


def _hours_since(timestamp_str):
    """Hours elapsed since a _now()-format ("%Y-%m-%d %H:%M") history
    timestamp. Returns 0 (never "stale") for a missing/malformed timestamp
    rather than raising -- reminders are a best-effort nudge, not something
    that should ever break a page load."""
    try:
        then = datetime.strptime(timestamp_str, "%Y-%m-%d %H:%M")
    except (TypeError, ValueError):
        return 0
    return (datetime.now() - then).total_seconds() / 3600.0


def _last_status_at(order, status):
    """Timestamp of the most recent history entry matching `status` (searched
    newest-first, since a status can in principle be re-entered), or None if
    that status was never reached. Used to measure how long an order has
    been sitting in its CURRENT stage, not how long ago it was first
    created."""
    for entry in reversed(order["history"]):
        if entry["status"] == status:
            return entry["at"]
    return None


def orders_needing_reminder(stale_hours=REMINDER_STALE_HOURS):
    """Active orders that have been sitting, past `stale_hours`, in a state
    where a specific party owes the next action -- something today's
    lifecycle notifications (_notify_order_update in app.py) never cover,
    since those only fire the moment a status *changes*, not while it stays
    unchanged. Returns a list of {"order", "reason", "target_unit_id"}:

      - "payment_due": confirmed but still unpaid -- nudges the BUYER, who
        has to pay before advance_order()'s own payment gate lets the seller
        move it to picked_up.
      - "pickup_due": confirmed AND paid, but not yet marked picked up --
        nudges the SELLER, who drives every fulfillment step (see
        order_advance's ownership rule in app.py).
      - "delivery_due": picked up but not yet marked delivered -- also
        nudges the SELLER, same reason.

    Deliberately never flags "placed" (the seller hasn't even decided yet --
    nobody's overdue) or "delivered" (nothing left to nudge; completing/
    rating is the buyer's call whenever they get to it, not a stalled
    fulfillment step)."""
    due = []
    for order in ORDERS:
        if order["status"] == "confirmed":
            if order.get("payment_status") != "paid":
                confirmed_at = _last_status_at(order, "confirmed")
                if confirmed_at and _hours_since(confirmed_at) >= stale_hours:
                    due.append({"order": order, "reason": "payment_due", "target_unit_id": order["buyer_unit_id"]})
            else:
                # set_payment_info() logs a "payment_paid" history entry the
                # moment payment clears -- prefer that as the reference point
                # (measures time since payment, not since confirmation), but
                # fall back to the confirmed timestamp if it's somehow
                # missing (e.g. a payment_status set directly, bypassing
                # set_payment_info) rather than never firing at all.
                reference_at = _last_status_at(order, "payment_paid") or _last_status_at(order, "confirmed")
                if reference_at and _hours_since(reference_at) >= stale_hours:
                    due.append({"order": order, "reason": "pickup_due", "target_unit_id": order["seller_unit_id"]})
        elif order["status"] == "picked_up":
            picked_up_at = _last_status_at(order, "picked_up")
            if picked_up_at and _hours_since(picked_up_at) >= stale_hours:
                due.append({"order": order, "reason": "delivery_due", "target_unit_id": order["seller_unit_id"]})
    return due


def order_by_razorpay_order_id(razorpay_order_id):
    """Look up an order by the Razorpay order id we stashed on it in
    set_payment_info(). Used by /payment/verify, which only gets handed the
    razorpay_order_id back from the browser, not our own order id."""
    for o in ORDERS:
        if o.get("razorpay_order_id") == razorpay_order_id:
            return o
    return None


def order_by_razorpay_payment_id(razorpay_payment_id):
    """Look up an order by its Razorpay payment id -- used so a refund issued
    against a raw payment id (the direct /payment/refund/<payment_id> API) can
    still find and update the matching order's payment_status, instead of only
    the /order/<id>/refund path keeping the two in sync."""
    for o in ORDERS:
        if o.get("razorpay_payment_id") == razorpay_payment_id:
            return o
    return None


def set_payment_info(order_id, razorpay_order_id=None, razorpay_payment_id=None, amount_rs=None, status=None):
    """Record Razorpay payment details against an order. Called twice in the normal
    flow: once from /payment/create-order (razorpay_order_id + amount, status
    'awaiting_payment'), and once from /payment/verify after Razorpay confirms the
    payment (razorpay_payment_id, status 'paid')."""
    order = order_by_id(order_id)
    if not order:
        return None
    if razorpay_order_id:
        order["razorpay_order_id"] = razorpay_order_id
    if razorpay_payment_id:
        order["razorpay_payment_id"] = razorpay_payment_id
    if amount_rs is not None:
        order["payment_amount"] = amount_rs
    if status:
        order["payment_status"] = status
        order["history"].append({"status": f"payment_{status}", "at": _now()})
    return order


def raise_dispute(order_id, reason, raised_by):
    """Buyer flags a problem with a paid order (e.g. material never arrived, quality
    issue). Puts the order into 'open' dispute state for an admin to review --
    disputing doesn't change order.status itself, since the underlying exchange may
    still be in progress while the dispute is being looked at."""
    order = order_by_id(order_id)
    if not order:
        return None
    order["dispute_status"] = "open"
    order["dispute_reason"] = reason
    order["dispute_raised_by"] = raised_by
    order["dispute_raised_at"] = _now()
    order["history"].append({"status": "dispute_open", "at": _now(), "reason": reason, "by": raised_by})
    return order


def resolve_dispute(order_id, resolution, resolved_by, notes=None):
    """Admin closes out an open dispute. resolution is 'resolved_refunded' or
    'resolved_rejected' -- the actual refund call (Razorpay + payment_status update)
    happens separately in app.py via set_payment_info(); this just records the
    dispute's own outcome."""
    order = order_by_id(order_id)
    if not order:
        return None
    order["dispute_status"] = resolution
    order["dispute_resolved_by"] = resolved_by
    order["dispute_resolved_at"] = _now()
    if notes:
        order["dispute_resolution_notes"] = notes
    order["history"].append({"status": resolution, "at": _now(), "reason": notes, "by": resolved_by})
    return order


def open_disputes():
    return [o for o in all_orders() if o.get("dispute_status") == "open"]


def orders_for_unit(unit_id):
    return [o for o in ORDERS if o["seller_unit_id"] == unit_id or o["buyer_unit_id"] == unit_id]


def all_orders():
    return sorted(ORDERS, key=lambda o: o["id"], reverse=True)
