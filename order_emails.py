"""
Branded order emails for buyer, seller and admin.

Called from app.py's _notify_order_update() at every order lifecycle step
(placed, accepted, picked up, delivered, completed, cancelled/declined,
payment received, refunded). Each party gets wording written for their side
of the deal -- the seller of a new request is told to accept it, the buyer is
told their request went out -- plus a progress tracker, the key numbers and
a button back to the orders page.

Recipient lookup and HTML rendering happen on the request thread (they need
the DB session and url_for); only the SMTP round-trip goes to a background
thread via email_service.send_async(), so an order action never waits on
Gmail.
"""

import logging

import data
import email_templates
from email_service import email_service

logger = logging.getLogger("symbiolink.email")

# The happy path shown in the progress tracker.
PROGRESS = [
    ("placed", "Placed"),
    ("confirmed", "Accepted"),
    ("picked_up", "Picked up"),
    ("delivered", "Delivered"),
    ("completed", "Completed"),
]
_ORDER_OF = {key: i for i, (key, _label) in enumerate(PROGRESS)}

# Events that get the green "good news" accent instead of the amber order one.
_SUCCESS_EVENTS = {"confirmed", "delivered", "completed", "payment received"}
_STOP_EVENTS = {"cancelled", "declined"}


def _material(order):
    return order["material"].replace("_", " ").title()


def _estimated_value(order):
    """Byproduct value of the order, from the reference price table."""
    prices = data.PRICE_TABLE_RS_PER_KG.get(order["material"]) or {}
    per_kg = prices.get("byproduct")
    if not per_kg:
        return None
    return per_kg * float(order.get("qty_kg") or 0)


def _progress(order, event):
    status = order.get("status")
    if event in _STOP_EVENTS or status == "cancelled":
        # Show how far it got, then a red stop on the next step.
        reached = max((_ORDER_OF.get(h.get("status"), 0) for h in order.get("history") or []), default=0)
        steps = []
        for i, (_key, label) in enumerate(PROGRESS):
            if i <= reached:
                steps.append((label, "done"))
            elif i == reached + 1:
                steps.append(("Declined" if event == "declined" else "Cancelled", "stopped"))
                break
        return steps
    current = _ORDER_OF.get(status, 0)
    return [
        (label, "done" if i < current or status == "completed" else "current" if i == current else "todo")
        for i, (_key, label) in enumerate(PROGRESS)
    ]


def _copy(order, event, role):
    """(subject, heading, intro, banner) for one recipient's side of the event."""
    oid, mat = order["id"], _material(order)
    qty = f"{float(order['qty_kg']):,.0f} kg"
    seller, buyer = order["seller_name"], order["buyer_name"]

    if event == "placed":
        return {
            "buyer": (f"Order {oid}: request sent to {seller}",
                      "Your request is on its way",
                      f"You requested {qty} of {mat} from {seller}. We'll email you as soon as they accept it.",
                      "Next: wait for the seller to accept, then pay to schedule pickup."),
            "seller": (f"New order request {oid}: {qty} of {mat}",
                       "You have a new order request",
                       f"{buyer} wants {qty} of your {mat}. Review the request and accept or decline it.",
                       "Action needed: accept or decline this request on your Orders page."),
            "admin": (f"[Admin] New order {oid}: {buyer} to {seller}",
                      "New order in the cluster",
                      f"{buyer} requested {qty} of {mat} from {seller}.",
                      None),
        }[role]
    if event == "confirmed":
        return {
            "buyer": (f"Order {oid} accepted by {seller}",
                      f"{seller} accepted your order",
                      f"Good news: your request for {qty} of {mat} was accepted.",
                      "Next: complete the payment so pickup can be scheduled."),
            "seller": (f"You accepted order {oid}",
                       "Order accepted",
                       f"You accepted {buyer}'s request for {qty} of {mat}. We've let them know.",
                       "Next: once the buyer pays, mark the order as picked up."),
            "admin": (f"[Admin] Order {oid} accepted",
                      "Order accepted",
                      f"{seller} accepted {buyer}'s request for {qty} of {mat}.",
                      None),
        }[role]
    if event in _STOP_EVENTS:
        reason = order.get("cancellation_reason")
        by = order.get("cancelled_by")
        why = f" Reason: {reason}." if reason else ""
        heading = "Order declined" if event == "declined" else "Order cancelled"
        return (f"Order {oid} {event}" if role != "admin" else f"[Admin] Order {oid} {event}",
                heading,
                f"The order for {qty} of {mat} between {buyer} and {seller} was {event}"
                f"{' by ' + by if by else ''}.{why}",
                "Any payment already made is refunded automatically." if order.get("payment_status") in ("paid", "refunded") else None)

    headings = {
        "picked_up": ("picked up", "Your material is on the move",
                      f"{qty} of {mat} has been picked up from {seller} and is heading to {buyer}."),
        "delivered": ("delivered", "Delivered!",
                      f"{qty} of {mat} from {seller} has been delivered to {buyer}.",),
        "completed": ("completed", "Order completed",
                      f"The exchange of {qty} of {mat} between {seller} and {buyer} is complete. "
                      "Thank you for keeping material out of landfill."),
        "payment received": ("paid", "Payment received",
                             f"Payment for {qty} of {mat} ({buyer} to {seller}) has been received."),
    }
    word, heading, intro = headings.get(event, (event, f"Order {event}",
                                                 f"Order {oid} for {qty} of {mat} is now {event}."))
    banner = None
    if event == "delivered":
        banner = "Next: confirm completion and rate your partner on the Orders page."
    elif event == "payment received" and role == "seller":
        banner = "Next: arrange pickup and mark the order as picked up."
    subject = f"Order {oid} {word}"
    return (f"[Admin] {subject}" if role == "admin" else subject), heading, intro, banner


def send_order_emails(order, event, orders_url):
    """Email buyer, seller and admins about `event` on `order`.

    `event` is the order's new status or a status_override from
    _notify_order_update ("placed", "declined", "payment received", ...).
    Never raises: a notification failure must not break the order action."""
    try:
        buyer_addrs = email_service._emails_for_unit(order["buyer_unit_id"])
        seller_addrs = email_service._emails_for_unit(order["seller_unit_id"])
        admin_addrs = [a for a in email_service._emails_for_admins()
                       if a not in buyer_addrs and a not in seller_addrs]

        value = _estimated_value(order)
        stats = [
            ("Material", _material(order)),
            ("Quantity", f"{float(order['qty_kg']):,.0f} kg"),
        ]
        if value:
            stats.append(("Est. value", f"Rs {value:,.0f}"))
        if order.get("co2_saved_kg"):
            stats.append(("CO2 saved", f"{float(order['co2_saved_kg']):,.0f} kg"))
        details = [
            ("Order", order["id"]),
            ("Seller", order["seller_name"]),
            ("Buyer", order["buyer_name"]),
            ("Payment", str(order.get("payment_status") or "unpaid").replace("_", " ").title()),
            ("Placed", order.get("created_at") or ""),
        ]
        kind = "alert" if event in _STOP_EVENTS else "success" if event in _SUCCESS_EVENTS else "order"
        progress = _progress(order, event)

        for role, addrs in (("buyer", buyer_addrs), ("seller", seller_addrs), ("admin", admin_addrs)):
            if not addrs:
                continue
            subject, heading, intro, banner = _copy(order, event, role)
            button = ("Review request" if (event == "placed" and role == "seller")
                      else "Open orders")
            kwargs = dict(stats=stats, details=details, progress=progress, banner=banner,
                          button_label=button, button_url=orders_url,
                          footnote=("You're receiving this because your company is part of this order "
                                    "on SymbioLink AI." if role != "admin" else
                                    "Admin copy: sent to the SymbioLink AI operations inbox."))
            html = email_templates.render(kind, heading, intro,
                                          preheader=f"{heading}: {_material(order)}, order {order['id']}",
                                          **kwargs)
            text = email_templates.plain_text(heading, intro, **kwargs)
            email_service.send_async(addrs, subject, text, html)
    except Exception:
        logger.exception("Error sending order emails for %s", order.get("id"))
