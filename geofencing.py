"""
GPS-verified pickup and delivery (simulated vehicle GPS + geofencing).

The order lifecycle (see orders.py) already has "picked up" and "delivered"
stages, but today they're just a button tap -- nothing stops someone from
marking an order "Delivered" whether or not the material actually arrived
anywhere. This module makes those two transitions something that happens
automatically when a vehicle's GPS position actually enters the pickup or
delivery location's geofence, instead of only when a human says so.

Real hardware/production path: a phone or a small GPS tracker module on the
pickup vehicle streams coordinates; a geofence is just "is this point within
radius R of the pickup/delivery unit's registered location." This module
simulates the vehicle side of that (the same way whatsapp_stub.py simulates
an inbound WhatsApp message and iot_sensors.py simulates an ESP32 push):
simulate_gps_tick() advances a simulated vehicle a step closer to its current
target along a straight line with a little GPS-style position noise, and
the moment it's within GEOFENCE_RADIUS_KM of that target, it calls the exact
same orders.advance_order() the manual "Mark picked up"/"Mark delivered"
buttons call -- so a GPS-triggered transition is not a second, parallel
status field, it's the one real order status, just reached a different way.
Every transition this module causes is tagged order["gps_log"] so the UI can
show "GPS-verified" instead of "manual," which is the actual point: a
compliance-relying buyer (see brsr_report.py / README's BRSR framing) gets an
audit trail that records what physically happened, not just what someone
clicked.

Unit locations (data.UNITS[*]["loc"]) are already plain (x, y) coordinates in
km on a notional cluster grid -- see data.py's module docstring and
matching.distance_km()'s use of math.hypot on the same tuples -- so geofence
distance checks reuse that exact coordinate system, no lat/lng conversion
needed for this demo.
"""

import math
import random
from datetime import datetime

import data
import orders as orders_module

GEOFENCE_RADIUS_KM = 0.15  # ~150m -- "at" a location, not just nearby
TICK_PROGRESS_RANGE = (0.30, 0.60)  # fraction of the remaining leg covered per simulated tick
POSITION_NOISE_KM = 0.03  # small GPS-style jitter on the reported position


def _dist(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _dispatch_start_point(pickup_loc):
    """A vehicle doesn't start already AT the pickup point -- it's dispatched
    from somewhere nearby (a depot / the previous drop). Picks a random point
    0.8-1.6km away so the very first tick can't trivially "arrive"."""
    angle = random.uniform(0, 2 * math.pi)
    dist = random.uniform(0.8, 1.6)
    return (pickup_loc[0] + dist * math.cos(angle), pickup_loc[1] + dist * math.sin(angle))


def gps_enabled(order):
    return bool(order.get("gps"))


def ensure_gps_tracking(order):
    """Lazily start GPS tracking for a paid, confirmed-or-later order that
    doesn't have it yet -- called from wherever an order might first become
    eligible (order detail/list views), not tied to one specific route, so a
    payment that clears between page loads still gets picked up."""
    if order.get("gps") or order.get("status") not in ("confirmed", "picked_up"):
        return order.get("gps")
    if order.get("payment_status") != "paid":
        return None

    seller = data.unit_by_id(order["seller_unit_id"])
    buyer = data.unit_by_id(order["buyer_unit_id"])
    if not seller or not buyer:
        return None

    if order["status"] == "confirmed":
        leg, target_name = "to_pickup", "pickup"
    else:  # already picked_up when GPS tracking is (re)attached -- head straight to delivery
        leg, target_name = "to_delivery", "delivery"

    start = _dispatch_start_point(seller["loc"]) if leg == "to_pickup" else seller["loc"]
    order["gps"] = {
        "leg": leg,
        "target": target_name,
        "start": start,
        "current": start,
        "pickup_loc": seller["loc"],
        "delivery_loc": buyer["loc"],
    }
    order.setdefault("gps_log", []).append({
        "event": "tracking_started", "leg": leg, "at": datetime.now().strftime("%Y-%m-%d %H:%M"),
    })
    return order["gps"]


def simulate_gps_tick(order_id, log_history_fn=None, notify_fn=None):
    """Advance one order's simulated vehicle one step. Returns (order, message).
    log_history_fn(order_id, status, changed_by, notes) and notify_fn(order) are
    optional callbacks so app.py can hook this into its existing
    _log_order_history / _notify_order_update helpers without this module
    importing app.py (which would be a circular import -- app.py imports this
    module, not the other way round)."""
    order = orders_module.order_by_id(order_id)
    if not order:
        return None, "Order not found."

    gps = ensure_gps_tracking(order)
    if not gps:
        if order.get("status") not in ("confirmed", "picked_up"):
            return order, "This order isn't in a pickup/delivery stage right now."
        return order, "GPS tracking starts once the order is paid and confirmed."

    if gps["leg"] == "done":
        return order, "This order has already arrived at its destination."

    target_loc = gps["pickup_loc"] if gps["leg"] == "to_pickup" else gps["delivery_loc"]
    cx, cy = gps["current"]
    tx, ty = target_loc
    remaining = _dist((cx, cy), (tx, ty))

    step_frac = random.uniform(*TICK_PROGRESS_RANGE)
    new_x = cx + (tx - cx) * step_frac + random.uniform(-POSITION_NOISE_KM, POSITION_NOISE_KM)
    new_y = cy + (ty - cy) * step_frac + random.uniform(-POSITION_NOISE_KM, POSITION_NOISE_KM)
    gps["current"] = (round(new_x, 4), round(new_y, 4))
    new_remaining = _dist(gps["current"], (tx, ty))

    if new_remaining <= GEOFENCE_RADIUS_KM:
        # Arrived -- snap to the target point (a real device would still read
        # small jitter right at the fence, but showing it as exactly-arrived
        # is clearer for a demo) and drive the real order status transition
        # through the same path the manual "Mark picked up"/"Mark delivered"
        # button uses.
        gps["current"] = target_loc
        prev_status = order["status"]
        updated = orders_module.advance_order(order_id)
        arrived_leg = gps["leg"]

        if updated and updated["status"] != prev_status:
            order.setdefault("gps_log", []).append({
                "event": f"geofence_entered_{gps['target']}", "leg": arrived_leg,
                "at": datetime.now().strftime("%Y-%m-%d %H:%M"),
            })
            order["gps_verified_last_transition"] = True
            if log_history_fn:
                log_history_fn(order_id, updated["status"], changed_by="GPS geofence (simulated vehicle)",
                                notes=f"Auto-verified: vehicle entered the {gps['target']} geofence.")
            if notify_fn:
                notify_fn(updated)
            message = f"Vehicle entered the {gps['target']} geofence -- order auto-advanced to '{updated['status']}' (GPS-verified)."
            if arrived_leg == "to_pickup":
                gps["leg"] = "to_delivery"
                gps["target"] = "delivery"
                gps["start"] = gps["pickup_loc"]
                gps["current"] = gps["pickup_loc"]
            else:
                gps["leg"] = "done"
                gps["target"] = "arrived"
            return order, message
        else:
            # Reached the geofence but the underlying transition couldn't
            # happen yet (e.g. payment gate) -- report that plainly rather
            # than silently looping.
            return order, f"Vehicle is within the {gps['target']} geofence, but the order can't advance yet."

    order["gps_verified_last_transition"] = False
    pct = max(0.0, min(100.0, (1 - new_remaining / max(_dist(gps["start"], target_loc), 0.001)) * 100))
    return order, f"Vehicle en route to {gps['target']} -- {new_remaining:.2f}km out ({pct:.0f}% of the way there)."


if __name__ == "__main__":
    from app import app
    with app.app_context():
        import data_access  # noqa: ensures data.UNITS/LISTINGS are populated
        order = orders_module.place_order("U1", "Shivam Metal Works", "metal_shavings", "U2", "Ganga Alloy Casting", 50, co2_saved_kg=90)
        order["payment_status"] = "paid"
        orders_module.advance_order(order["id"])  # placed -> confirmed
        for _ in range(10):
            order, msg = simulate_gps_tick(order["id"])
            print(msg)
            if order.get("gps", {}).get("leg") == "done":
                break
