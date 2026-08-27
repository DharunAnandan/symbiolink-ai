"""
SymbioLink AI -- demo application entry point.

Run with:  python3 app.py
Then open: http://localhost:5000

Ties together all six layers for the dashboard view:
  1. Access & listing (data.py + whatsapp_stub.py)
  2. Predictive layer (predictive.py)
  3. Multi-hop matching engine (matching.py)
  4. Logistics pooling (pooling.py)
  5. Verification & trust (trust.py)
  6. Output / dashboard (this file)
"""

import random
import re
import os
from datetime import datetime

from flask import Flask, render_template, request, redirect, url_for, session, jsonify, flash, abort
from flask_login import LoginManager, login_required, login_user, logout_user, current_user

# UNITS, LISTINGS, KNOWN_MATERIALS, KNOWN_CATEGORIES, add_unit, add_listing, unit_by_id,
# reduce_listing_qty, find_listing, update_listing_qty_absolute, and remove_listing must
# stay data.X attribute lookups everywhere below, not bare "from data import ..." names.
# data_access.update_data_imports() reassigns these data.* attributes at startup (to
# database-backed values) and add_unit/add_listing keep data.UNITS/data.LISTINGS synced
# on every registration -- a bare import freezes a reference to whatever object existed
# at import time, so newly registered units/listings would silently never show up
# anywhere (search, matches, dashboard stats, etc). Always read through `data.`.
import data
import ml_models
from matching import rank_matches, build_edges, estimate_co2_saved_kg, notify_new_matches_for_unit, distance_km, MAX_RADIUS_KM, network_gap_report
from pooling import evaluate_pooling, LOW_VOLUME_THRESHOLD_KG
from predictive import forecast_all, benchmark_vs_baseline as forecast_benchmark_vs_baseline
from trust import TrustLedger, MIN_DISTINCT_COUNTERPARTIES_FOR_TOP_TIER
from whatsapp_stub import SIMULATED_INBOX, parse_message
import orders as orders_module
import notifications as notifications_module
import brsr_report
import carbon_credits
import iot_sensors
import optimization
import geofencing
import photo_classifier
import market_insights

from config import config
from models import db, User, Unit, Listing, MaterialPrice, Order, WhatsAppMessage, OrderHistory, TrustRating
import data_access
from whatsapp_service import whatsapp_service
from auth_decorators import admin_required, auditor_required, check_permission, role_required
from payment_service import payment_service
from email_service import email_service
import i18n

# Create Flask app
app = Flask(__name__)

# Configuration
env = os.environ.get('FLASK_ENV', 'development')
app.config.from_object(config[env])

# Initialize extensions
db.init_app(app)
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'
login_manager.login_message = 'Please log in to access this page.'


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


# Create database tables and update data imports
with app.app_context():
    try:
        db.create_all()

        # db.create_all() only creates tables that don't exist yet -- it never
        # alters an already-existing table, so adding `email` to the Unit
        # model (models.py) does nothing for a dev database file that was
        # first created before this column existed. Rather than pull in a
        # full migration framework for one column, self-heal it the same
        # lightweight way: try to add it, and silently ignore the failure
        # when it's already there (a brand-new DB) or the table doesn't
        # exist yet (falls through to the except below anyway).
        from sqlalchemy import text
        try:
            db.session.execute(text("ALTER TABLE units ADD COLUMN email VARCHAR(120)"))
            db.session.commit()
        except Exception:
            db.session.rollback()

        # Import and update data access after database is initialized
        import data_access
        data_access.update_data_imports()
    except Exception as e:
        print(f"Warning: Database initialization issue: {e}")
        # Continue with in-memory data as fallback


# Authentication routes
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        remember = request.form.get('remember', False)
        
        user = User.query.filter_by(username=username).first()
        
        if user and user.check_password(password):
            login_user(user, remember=remember)
            # A unit-role account already tied to a company (unit_id set, either
            # from the seed data or from an earlier /register-unit) should never
            # need the old manual "act as your company" step -- every session.
            # get("acting_as") check across the app treats that value as "which
            # company is this action for", so binding it straight to the user's
            # own unit_id at login makes every order/listing they place from here
            # on automatically theirs, with no extra click and no chance of them
            # ever landing on (or needing) someone else's company context.
            if user.role == 'unit' and user.unit_id:
                session['acting_as'] = user.unit_id
            next_page = request.args.get('next')
            return redirect(next_page or url_for('dashboard'))
        else:
            return render_template('login.html', error='Invalid username or password')
    
    return render_template('login.html')


@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))


@app.route('/register', methods=['GET', 'POST'])
def register():
    """Account signup -- creates BOTH the login (User) and the company it
    belongs to (Unit) in one step, instead of the old flow where a fresh
    account had no company at all until it happened to submit the separate
    "Register a Listing" form. That older flow meant a brand-new user could
    wander the whole app (dashboard, orders, marketplace) with no company
    identity, and only backed into having one as a side effect of posting a
    listing. Asking for the company name/category right here means every
    account is a company account from the moment it's created, matching how
    the rest of the app already treats current_user.unit_id as "who is this
    login" (see /login's and /register-unit's auto-binding of session
    ["acting_as"])."""
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        company_name = request.form.get('company_name', '').strip()
        category = request.form.get('category', '')
        email = request.form.get('email', '').strip()
        password = request.form.get('password')
        confirm_password = request.form.get('confirm_password')
        form_values = {'username': username, 'company_name': company_name, 'category': category, 'email': email}

        def _error(message):
            return render_template('register_user.html', error=message, categories=data.KNOWN_CATEGORIES, form_values=form_values)

        if not company_name:
            return _error('Enter your company / unit name.')
        if not category:
            return _error('Select a category for your company.')
        if password != confirm_password:
            return _error('Passwords do not match')
        if User.query.filter_by(username=username).first():
            return _error('Username already exists')
        if User.query.filter_by(email=email).first():
            return _error('Email already exists')

        user = User(username=username, email=email, role='unit')
        user.set_password(password)
        db.session.add(user)
        db.session.commit()

        # simple demo placement: spread new units around the existing cluster so
        # distance-based matching/pooling still behaves sensibly (same approach
        # /register-unit uses for a first-time company).
        loc = (round(random.uniform(0, 4.2), 2), round(random.uniform(0, 4.4), 2))
        unit = data.add_unit(company_name, category, loc, phone="", email=email)
        user.unit_id = unit["id"]
        db.session.commit()

        login_user(user)
        session['acting_as'] = unit["id"]
        return redirect(url_for('dashboard'))

    return render_template('register_user.html', categories=data.KNOWN_CATEGORIES)


def whatsapp_link(phone, message):
    digits = re.sub(r"\D", "", phone or "")
    if not digits:
        return None
    from urllib.parse import quote
    return f"https://wa.me/{digits}?text={quote(message)}"


@app.context_processor
def inject_acting_as():
    acting_as_id = session.get("acting_as")
    acting_as_unit = data.unit_by_id(acting_as_id) if acting_as_id else None
    return {"acting_as_unit": acting_as_unit}


def _notification_context():
    """(unit_id, include_admin): which notifications the current request
    should see -- whichever unit the user is acting as, plus admin-broadcast
    notifications if they're an admin. Same split every other permission
    check in this app already uses (see notifications.py)."""
    unit_id = session.get("acting_as")
    include_admin = current_user.is_authenticated and current_user.role == "admin"
    return unit_id, include_admin


@app.context_processor
def inject_notification_badge():
    """Unread count for the bell icon, available on every authenticated page
    without every route having to compute it. The dropdown's actual contents
    are fetched separately by motion.js via /api/notifications so the count
    here stays a cheap single query."""
    if not current_user.is_authenticated:
        return {"unread_notif_count": 0}
    unit_id, include_admin = _notification_context()
    return {"unread_notif_count": notifications_module.unread_count_for_context(unit_id, include_admin)}


@app.context_processor
def inject_i18n():
    """Makes t() (translate a string) and current_lang() available in every
    template without every route passing them explicitly -- same pattern as
    the two context processors above. See i18n.py for scope/design notes."""
    return {"t": i18n.t, "current_lang": i18n.current_lang(), "supported_langs": i18n.SUPPORTED_LANGS, "lang_labels": i18n.LANG_LABELS}


@app.route("/set-language/<lang>")
def set_language(lang):
    """Public (no login required) so the toggle works on the landing and
    login pages too, not just once signed in. Redirects back to wherever the
    visitor was -- a language switch should never itself navigate anywhere."""
    if lang in i18n.SUPPORTED_LANGS:
        session["lang"] = lang
    return redirect(request.referrer or url_for("index"))


# One shared ledger for the whole running server, NOT re-created per request --
# otherwise every order completion's rating would be forgotten on the next page load.
ledger = TrustLedger()

# A unit is flagged "Highly recommended" (search.html) only once it has BOTH a
# high score AND a real track record behind it -- gating on score alone would
# also flag units with zero completed exchanges, since TrustLedger.score()
# returns a neutral 3.5 default for those (see trust.py's DEFAULT_SCORE), which
# would make an unproven unit look endorsed. Both thresholds must be met.
HIGHLY_RECOMMENDED_MIN_SCORE = 4.5
HIGHLY_RECOMMENDED_MIN_EXCHANGES = 3


def _trust_badge_info(unit_id):
    """Shared by both tables in search() so the badge logic can't drift
    between the waste-listing and need-listing sides of the marketplace.

    BUG FIX / ring-trading mitigation: "Highly recommended" now also requires
    a minimum number of DISTINCT trading counterparties (see
    trust.MIN_DISTINCT_COUNTERPARTIES_FOR_TOP_TIER), not just enough total
    completed exchanges. Without this, two units trading only with each
    other could hit both the score and exchange-count thresholds purely by
    repetition, with zero evidence they've ever worked with anyone else in
    the cluster. ledger.score() itself is now also capped per counterparty
    (see trust.MAX_COUNTED_RATINGS_PER_COUNTERPARTY), so a repeat partner
    can't keep pushing the score up indefinitely either."""
    score = ledger.score(unit_id)
    completed = len(ledger.ratings_received.get(unit_id, []))
    distinct_partners = ledger.distinct_counterparties(unit_id)
    highly_recommended = (
        score >= HIGHLY_RECOMMENDED_MIN_SCORE
        and completed >= HIGHLY_RECOMMENDED_MIN_EXCHANGES
        and distinct_partners >= MIN_DISTINCT_COUNTERPARTIES_FOR_TOP_TIER
    )
    return {"trust_score": score, "completed_exchanges": completed, "highly_recommended": highly_recommended}


def _log_order_history(order_id_str, status, changed_by=None, notes=None):
    """Persist an order-lifecycle transition to the OrderHistory table so
    admin_dashboard.html / audit_logs.html show real activity instead of staying
    permanently empty. orders.py's live order flow (place/advance/complete/cancel)
    is in-memory and uses string ids like "ORD001" -- OrderHistory.order_id is an
    Integer column (FK'd to the separate, write-orphaned DB Order table, which the
    live flow never populates). Rather than standing up a whole parallel DB-backed
    order model just for an audit trail, we store the numeric suffix of the in-memory
    id (e.g. "ORD001" -> 1) and have audit_logs.html re-format it back to "ORD001"
    for display -- a lightweight bridge, not a second source of truth. Best-effort:
    a DB hiccup here must never break the actual order action for the user.
    """
    try:
        order_num = int(order_id_str[3:]) if order_id_str.startswith("ORD") else None
        if order_num is None:
            return
        db.session.add(OrderHistory(order_id=order_num, status=status, changed_by=changed_by, notes=notes))
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        print(f"Error logging order history: {e}")


def _notify_order_update(order, status_override=None):
    """Best-effort WhatsApp notification to both sides of an order whenever its
    status changes. whatsapp_service already falls back to a console-log
    "simulation mode" when Twilio credentials aren't configured (see
    whatsapp_service.py), so this is safe to call unconditionally at every
    lifecycle step instead of only once real credentials exist -- exactly like
    payment_service's simulation mode, nothing here breaks the actual request if
    the notification itself fails.
    """
    status_text = status_override or order["status"]
    try:
        whatsapp_service.send_order_update(order["buyer_unit_id"], {
            "order_number": order["id"],
            "status": status_text,
            "material": order["material"],
            "qty_kg": order["qty_kg"],
            "partner_name": order["seller_name"],
        })
        whatsapp_service.send_order_update(order["seller_unit_id"], {
            "order_number": order["id"],
            "status": status_text,
            "material": order["material"],
            "qty_kg": order["qty_kg"],
            "partner_name": order["buyer_name"],
        })
    except Exception as e:
        print(f"Error sending order update notification: {e}")

    # Computed once, up front, so a failure in either channel below can never
    # rob the other one of its content -- WhatsApp/in-app/email are three
    # fully independent best-effort channels, not a chain.
    material_readable = order["material"].replace("_", " ").title()
    title = f"Order {order['id']} {status_text}"
    buyer_message = f"{material_readable} · {order['qty_kg']:.0f}kg with {order['seller_name']}"
    seller_message = f"{material_readable} · {order['qty_kg']:.0f}kg with {order['buyer_name']}"

    # In-app bell notification for both sides -- every order lifecycle step
    # (advance/complete/cancel/payment/refund) already funnels through this one
    # function, so hooking in here covers every case without touching each of
    # those route handlers individually.
    try:
        notifications_module.notify_unit(
            order["buyer_unit_id"], "order", title, message=buyer_message, link=url_for("orders_page"),
        )
        notifications_module.notify_unit(
            order["seller_unit_id"], "order", title, message=seller_message, link=url_for("orders_page"),
        )
    except Exception as e:
        print(f"Error creating order notification: {e}")

    # A third, independent channel alongside WhatsApp + the in-app bell --
    # email_service is just as best-effort/non-raising as the two above, so
    # this can't be the thing that breaks an order update.
    email_service.notify_unit(order["buyer_unit_id"], title, buyer_message)
    email_service.notify_unit(order["seller_unit_id"], title, seller_message)


_REMINDER_IN_APP_TEXT = {
    "payment_due": "Payment is still pending on this order.",
    "pickup_due": "This order is paid and waiting to be marked picked up.",
    "delivery_due": "This order has been picked up and is waiting to be marked delivered.",
}


def _send_pending_order_reminders():
    """Best-effort WhatsApp + in-app nudge for every order
    orders_module.orders_needing_reminder() flags as stalled past the
    reminder threshold -- same best-effort, never-breaks-the-request
    philosophy as _notify_order_update above, just triggered on demand (via
    the admin "Send pending reminders" action) rather than on every status
    change, since there's no background scheduler in this app to run it on a
    timer. Returns a dict of counts by reason so the caller can report what
    actually went out."""
    counts = {"payment_due": 0, "pickup_due": 0, "delivery_due": 0}
    for item in orders_module.orders_needing_reminder():
        order = item["order"]
        reason = item["reason"]
        target_unit_id = item["target_unit_id"]
        partner_name = order["seller_name"] if target_unit_id == order["buyer_unit_id"] else order["buyer_name"]

        try:
            whatsapp_service.send_order_reminder(target_unit_id, {
                "order_number": order["id"],
                "material": order["material"],
                "qty_kg": order["qty_kg"],
                "partner_name": partner_name,
            }, reason)
        except Exception as e:
            print(f"Error sending order reminder: {e}")

        try:
            notifications_module.notify_unit(
                target_unit_id, "order", f"Reminder: order {order['id']}",
                message=_REMINDER_IN_APP_TEXT.get(reason, "This order needs attention."),
                link=url_for("orders_page"),
            )
        except Exception as e:
            print(f"Error creating reminder notification: {e}")

        counts[reason] = counts.get(reason, 0) + 1
    return counts


def _refund_order(order, amount_rs=None):
    """Shared refund logic used by both the admin's direct "Issue refund" action
    and dispute resolution: calls payment_service (Razorpay, or simulated when no
    keys are configured) and marks the order's payment_status as refunded. Returns
    the refund dict, or None if the order has no payment on record to refund."""
    payment_id = order.get("razorpay_payment_id")
    if not payment_id:
        return None
    refund_details = payment_service.refund_payment(payment_id, amount_rs)
    orders_module.set_payment_info(order["id"], status="refunded")
    return refund_details


def _can_view_order(order):
    """An order is only visible to the buyer, the seller, or an admin --
    never to every logged-in user regardless of company. Centralized here so
    the order list, the JSON order endpoints, and the invoice page (which
    already had its own copy of this exact check) can't drift out of sync."""
    if current_user.role == "admin":
        return True
    acting_as_id = session.get("acting_as")
    return bool(acting_as_id) and acting_as_id in (order["buyer_unit_id"], order["seller_unit_id"])


# Rough "large order" scales used only to normalize order value/quantity into
# the same 0..1 factor range the order-risk model was trained on (see
# ml_training_data.generate_order_outcomes) -- not a hard cap on order size,
# just the point past which _order_risk_score() treats an order as maximally
# "large" for risk-scoring purposes.
#
# ORDER_VALUE_NORM_RS was originally set to 50000, which turned out to be
# roughly 12x too high for this app's actual data: the single largest real
# order value in data.py's seed listings (metal_shavings, 220kg x Rs18/kg
# byproduct price) is ~Rs 3,960, with most orders in the Rs 60-1,760 range.
# At 50000, order_value_factor topped out around 0.08 for every real order in
# the app, which made the value dimension nearly dead weight -- the risk
# model would score every seed-data order "low risk" regardless of trust or
# distance, silently defeating the point of the feature. 4000 (just above the
# real observed max) puts real orders across a genuine 0..1 spread, so a
# large, low-trust, far-apart order can actually reach "medium"/"high".
ORDER_VALUE_NORM_RS = 4000
ORDER_QTY_NORM_KG = 500


def _order_risk_factors(order):
    """Computes the order-risk model's 5 input factors live from the order
    plus the current trust ledger and unit locations. Returns a dict, or
    None if either unit can't be resolved. Split out from _order_risk_score()
    below so _order_risk_explanation() can reuse the exact same factors
    instead of recomputing (and risking drifting out of sync with) them."""
    seller = data.unit_by_id(order["seller_unit_id"])
    buyer = data.unit_by_id(order["buyer_unit_id"])
    if not seller or not buyer:
        return None

    prices = data.PRICE_TABLE_RS_PER_KG.get(order["material"], {"byproduct": 15})
    order_value_rs = prices.get("byproduct", 15) * order["qty_kg"]
    # Clamped to [0, 1] on both ends, not just capped at 1 -- qty_kg isn't
    # re-validated here as strictly positive (order_new.html's min="1" is a
    # client-side hint, not a server-side guarantee), so a zero/negative
    # quantity must not silently hand the model a negative feature outside
    # its trained [0, 1] support.
    order_value_factor = max(0.0, min(1.0, order_value_rs / ORDER_VALUE_NORM_RS))
    qty_factor = max(0.0, min(1.0, order["qty_kg"] / ORDER_QTY_NORM_KG))
    dist = distance_km(seller, buyer)
    distance_factor = max(0.0, 1 - (dist / MAX_RADIUS_KM))  # closer is better, same convention as matching.py
    seller_trust_factor = ledger.score(seller["id"]) / 5.0
    buyer_trust_factor = ledger.score(buyer["id"]) / 5.0

    return {
        "order_value_factor": order_value_factor,
        "qty_factor": qty_factor,
        "distance_factor": distance_factor,
        "seller_trust_factor": seller_trust_factor,
        "buyer_trust_factor": buyer_trust_factor,
    }


def _order_risk_score(order):
    """Scores a single order's probability of completing successfully (as
    opposed to being cancelled or disputed) using the trained order-risk
    model. Returns (success_probability, risk_label) where risk_label is
    "low"/"medium"/"high", or (None, None) if either unit can't be resolved
    or the model hasn't been trained yet (fresh clone before
    `python train_ml_models.py` has been run) -- callers treat that as "don't
    show a risk badge" rather than guessing, the same graceful-fallback
    philosophy as every other ml_models.py prediction in this app."""
    factors = _order_risk_factors(order)
    if factors is None:
        return None, None

    prob = ml_models.predict_order_success_probability(**factors)
    if prob is None:
        return None, None
    if prob >= 0.75:
        label = "low"
    elif prob >= 0.5:
        label = "medium"
    else:
        label = "high"
    return round(prob, 2), label


# Thresholds used only by _order_risk_explanation() below to decide which of
# the 5 factors are worth naming as a "concern" -- not the model's own
# decision boundary (that's whatever the trained LogisticRegression/whatever
# ml_models.py loads actually learned). Picked as the midpoint of each
# factor's [0, 1] range, in the direction ml_training_data.generate_order_outcomes()
# trained the model to treat as risk-increasing (low trust/distance-factor,
# high value/qty) -- a plain-language approximation of "which inputs look
# unusually bad here", not a real feature-attribution method (no SHAP/
# coefficient introspection). Good enough to turn a bare "medium risk" badge
# into a reason a seller can actually act on.
_RISK_CONCERN_THRESHOLD = 0.5


def _order_risk_explanation(order, label):
    """A short, rule-based reason for a medium/high order-risk label, so the
    badge isn't just a bare number -- see _RISK_CONCERN_THRESHOLD's docstring
    for what "rule-based" means here. Returns None for "low" risk (nothing to
    explain) or if factors can't be computed."""
    if label not in ("medium", "high"):
        return None
    factors = _order_risk_factors(order)
    if factors is None:
        return None

    # (human-readable concern, how far past the threshold -- used only to
    # rank which 1-2 factors are worth naming)
    concerns = []
    if factors["distance_factor"] < _RISK_CONCERN_THRESHOLD:
        concerns.append(("distance between the two units", _RISK_CONCERN_THRESHOLD - factors["distance_factor"]))
    if factors["seller_trust_factor"] < _RISK_CONCERN_THRESHOLD:
        concerns.append(("the seller's trust history", _RISK_CONCERN_THRESHOLD - factors["seller_trust_factor"]))
    if factors["buyer_trust_factor"] < _RISK_CONCERN_THRESHOLD:
        concerns.append(("the buyer's trust history", _RISK_CONCERN_THRESHOLD - factors["buyer_trust_factor"]))
    if factors["order_value_factor"] > _RISK_CONCERN_THRESHOLD:
        concerns.append(("the order's value", factors["order_value_factor"] - _RISK_CONCERN_THRESHOLD))
    if factors["qty_factor"] > _RISK_CONCERN_THRESHOLD:
        concerns.append(("the order's quantity", factors["qty_factor"] - _RISK_CONCERN_THRESHOLD))

    if not concerns:
        # The model landed on medium/high without any single factor crossing
        # its threshold -- a combination of smaller effects rather than one
        # dominant cause. Still worth saying something rather than nothing.
        return "No single factor stands out -- a combination of smaller effects."

    concerns.sort(key=lambda c: c[1], reverse=True)
    top = [c[0] for c in concerns[:2]]
    return "Mainly due to " + " and ".join(top) + "."


def _at_risk_orders(limit=8):
    """Active (not completed/cancelled) orders the risk model scores as
    medium/high risk, riskiest first -- feeds the admin dashboard's "At-risk
    orders" panel. Returns [] if the model hasn't been trained yet."""
    scored = []
    for o in orders_module.all_orders():
        if o["status"] in ("completed", "cancelled"):
            continue
        prob, label = _order_risk_score(o)
        if prob is not None and label in ("medium", "high"):
            scored.append({
                "order": o,
                "success_probability": prob,
                "risk_label": label,
                "explanation": _order_risk_explanation(o, label),
            })
    scored.sort(key=lambda r: r["success_probability"])
    return scored[:limit]


def cluster_snapshot(match_top_n=10):
    """One place that computes everything every layer page needs, so each route stays
    a thin wrapper around this shared snapshot. match_top_n defaults to 10 (enough for
    the dashboard's highlight card); the dedicated Matching Engine page asks for more
    so that lower-scoring but still real opportunities -- like similar-material chains,
    which get a scoring discount vs. an equally-good exact match -- aren't invisible
    just because a handful of exact matches outrank them."""
    trust_scores = ledger.all_scores()

    matches = rank_matches(trust_scores=trust_scores, top_n=match_top_n)
    pooling_results, pool_groups = evaluate_pooling()
    forecasts = forecast_all()[:6]
    trust_summary = ledger.summary()
    whatsapp_results = [parse_message(uid, msg) for uid, msg in SIMULATED_INBOX]

    total_waste_listed_kg = sum(l["qty_kg"] for l in data.LISTINGS if l["type"] == "waste")
    total_matched_saving = sum(m["total_saving_rs"] for m in matches)
    total_matched_co2_saved_kg = sum(m.get("total_co2_saved_kg", 0) for m in matches)
    stats = {
        "num_units": len(data.UNITS),
        "num_listings": len(data.LISTINGS),
        "num_matches": len(matches),
        "num_pool_groups": len(pool_groups),
        "total_waste_listed_kg": total_waste_listed_kg,
        "total_matched_saving_rs": round(total_matched_saving, 2),
        "total_matched_co2_saved_kg": round(total_matched_co2_saved_kg, 1),
    }
    return {
        "matches": matches,
        "pooling_results": pooling_results,
        "forecasts": forecasts,
        "trust_summary": trust_summary,
        "whatsapp_results": whatsapp_results,
        "stats": stats,
    }


@app.route("/")
def index():
    """The site's true entry point. Signed-in visitors go straight to their
    dashboard, same as before; a first-time or signed-out visitor now sees a
    public pitch page instead of being bounced straight to a bare login
    form -- there was previously no page anywhere that explained what this
    platform even does before asking someone to sign in to it."""
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))
    return render_template("landing.html")


@app.route("/admin/seed-database")
def seed_database_once():
    """One-time DB seeding trigger for hosts (e.g. Render's free tier) that
    don't provide shell/SSH access to run `python database_migration.py`
    by hand. Gated by a token so a random visitor can't trigger it -- though
    seed_all() itself is idempotent (checks for existing rows before
    inserting each table), so even an accidental repeat call is harmless."""
    expected = os.environ.get("SEED_TOKEN", "symbio-seed-2026-x7k9")
    if request.args.get("token", "") != expected:
        abort(404)
    from database_migration import seed_all
    seed_all()
    return (
        "Database seeded. Log in with admin / admin123, or any of u1..u24 / "
        "demo123 -- or just register a brand-new company from the site's "
        "own sign-up page.",
        200,
    )


@app.route("/dashboard")
@login_required
def dashboard():
    snap = cluster_snapshot()
    top_matches = snap["matches"][:5]
    chart_labels = [" → ".join(m["path_names"][:2]) + (" ..." if m["hops"] > 1 else "") for m in top_matches]
    chart_values = [m["total_saving_rs"] for m in top_matches]
    return render_template(
        "dashboard.html",
        stats=snap["stats"],
        chart_labels=chart_labels,
        chart_values=chart_values,
    )


@app.route("/report/impact")
@login_required
def impact_report():
    """Printable sustainability/impact report -- same 'browser print to PDF'
    pattern as invoice.html (no PDF library dependency needed). Cluster-wide
    numbers are always shown; if the viewer is currently acting as a
    company, a second section breaks out that company's own order history
    (orders placed/fulfilled, CO2 avoided, money saved) so a unit can hand a
    judge/auditor/partner a one-page summary of their own contribution."""
    snap = cluster_snapshot()

    acting_as_id = session.get("acting_as")
    acting_as_unit = data.unit_by_id(acting_as_id) if acting_as_id else None
    my_orders = my_stats = None
    if acting_as_unit:
        my_orders = orders_module.orders_for_unit(acting_as_id)
        counted = [o for o in my_orders if o["status"] != "cancelled"]
        my_stats = {
            "total_orders": len(my_orders),
            "as_seller": len([o for o in my_orders if o["seller_unit_id"] == acting_as_id]),
            "as_buyer": len([o for o in my_orders if o["buyer_unit_id"] == acting_as_id]),
            "co2_saved_kg": round(sum(o.get("co2_saved_kg", 0) for o in counted), 1),
            "completed": len([o for o in my_orders if o["status"] == "completed"]),
        }

    return render_template(
        "impact_report.html",
        generated_at=datetime.now().strftime("%d %b %Y, %H:%M"),
        stats=snap["stats"],
        top_matches=snap["matches"][:8],
        acting_as_unit=acting_as_unit,
        my_orders=my_orders,
        my_stats=my_stats,
    )


@app.route("/report/brsr")
@login_required
def brsr_report_page():
    """BRSR-ready ESG compliance annexure for one unit -- see brsr_report.py's
    module docstring for what this is and why it's just a presentation layer
    over data the platform already logs. Defaults to whichever company the
    viewer is acting as; an admin may instead pass ?unit_id=U4 to pull up any
    unit's annexure (e.g. while helping a unit prepare one for a buyer)."""
    requested_unit_id = request.args.get("unit_id")
    if requested_unit_id and current_user.role == "admin":
        unit_id = requested_unit_id
    else:
        unit_id = session.get("acting_as")

    if not unit_id:
        flash("Act as your company first to generate its BRSR compliance report.", "error")
        return redirect(url_for("act_as"))

    report = brsr_report.generate_brsr_data(unit_id)
    if not report:
        flash("Unit not found.", "error")
        return redirect(url_for("act_as"))

    return render_template("brsr_report.html", report=report)


@app.route("/carbon-credits")
@login_required
def carbon_credits_page():
    """Turns a unit's accumulated, verified CO2e savings into tradeable
    carbon credits -- see carbon_credits.py's module docstring. Shows the
    acting-as unit's own credit position (available/listed/sold + revenue)
    plus the cross-cluster marketplace of every currently-listed credit,
    which any unit (or admin, browsing on their behalf) can buy."""
    acting_as_id = session.get("acting_as")
    my_summary = my_credits = None
    if acting_as_id:
        my_summary = carbon_credits.unit_credit_summary(acting_as_id)
        my_credits = carbon_credits.unit_credits(acting_as_id)
    listings = [
        l for l in carbon_credits.marketplace_listings()
        if not acting_as_id or l["unit_id"] != acting_as_id
    ]
    return render_template(
        "carbon_credits.html",
        my_summary=my_summary,
        my_credits=my_credits,
        listings=listings,
        reference_price=carbon_credits.REFERENCE_PRICE_RS_PER_TONNE,
        methodology_note=carbon_credits.METHODOLOGY_NOTE,
    )


@app.route("/carbon-credits/issue", methods=["POST"])
@login_required
def carbon_credits_issue():
    acting_as_id = session.get("acting_as")
    if not acting_as_id:
        flash("Act as your company first.", "error")
        return redirect(url_for("carbon_credits_page"))
    credit, message = carbon_credits.issue_credits(acting_as_id)
    flash(message, "success" if credit else "error")
    return redirect(url_for("carbon_credits_page"))


@app.route("/carbon-credits/<int:credit_id>/list", methods=["POST"])
@login_required
def carbon_credits_list_route(credit_id):
    acting_as_id = session.get("acting_as")
    if not acting_as_id:
        flash("Act as your company first.", "error")
        return redirect(url_for("carbon_credits_page"))
    price = request.form.get("price_rs_per_tonne", carbon_credits.REFERENCE_PRICE_RS_PER_TONNE)
    try:
        price = float(price)
    except (TypeError, ValueError):
        price = carbon_credits.REFERENCE_PRICE_RS_PER_TONNE
    credit, message = carbon_credits.list_credit(credit_id, acting_as_id, price)
    flash(message, "success" if credit else "error")
    return redirect(url_for("carbon_credits_page"))


@app.route("/carbon-credits/<int:credit_id>/unlist", methods=["POST"])
@login_required
def carbon_credits_unlist_route(credit_id):
    acting_as_id = session.get("acting_as")
    if not acting_as_id:
        flash("Act as your company first.", "error")
        return redirect(url_for("carbon_credits_page"))
    credit, message = carbon_credits.unlist_credit(credit_id, acting_as_id)
    flash(message, "success" if credit else "error")
    return redirect(url_for("carbon_credits_page"))


@app.route("/carbon-credits/<int:credit_id>/buy", methods=["POST"])
@login_required
def carbon_credits_buy_route(credit_id):
    acting_as_id = session.get("acting_as")
    buyer_unit = data.unit_by_id(acting_as_id) if acting_as_id else None
    buyer_name = request.form.get("buyer_name", "").strip() or (buyer_unit["name"] if buyer_unit else "")
    credit, message = carbon_credits.buy_credit(credit_id, buyer_name)
    if credit:
        notifications_module.notify_unit(
            credit.unit_id, "info", f"Carbon credit sold: {credit.tco2e:.0f} tCO2e",
            message=f"Bought by {credit.buyer_name} for Rs {(credit.price_rs_per_tonne or 0) * credit.tco2e:,.0f}",
            link=url_for("carbon_credits_page"),
        )
    flash(message, "success" if credit else "error")
    return redirect(url_for("carbon_credits_page"))


@app.route("/iot")
@login_required
def iot_dashboard():
    """IoT bin-sensor dashboard -- see iot_sensors.py's module docstring for
    what's real (the sensor/reading model, the auto-listing trigger) vs.
    simulated (an actual ESP32 push, which this stands in for with a manual
    'Simulate reading' tick, same demo pattern as whatsapp_stub.py's
    SIMULATED_INBOX). Scoped to the acting-as unit's own bins; admins see
    every sensor across the cluster."""
    acting_as_id = session.get("acting_as")
    if current_user.role == "admin" and not acting_as_id:
        sensors = iot_sensors.all_sensors()
    elif acting_as_id:
        sensors = iot_sensors.sensors_for_unit(acting_as_id)
    else:
        sensors = []

    sensor_rows = []
    for s in sensors:
        unit = data.unit_by_id(s.unit_id)
        sensor_rows.append({
            "sensor": s,
            "unit_name": unit["name"] if unit else s.unit_id,
            "readings": iot_sensors.recent_readings(s.id, limit=8),
        })

    return render_template(
        "iot_dashboard.html",
        sensor_rows=sensor_rows,
        acting_as_id=acting_as_id,
        known_materials=data.KNOWN_MATERIALS,
    )


@app.route("/iot/register", methods=["POST"])
@login_required
def iot_register():
    acting_as_id = session.get("acting_as")
    if not acting_as_id:
        flash("Act as your company first to register a bin sensor.", "error")
        return redirect(url_for("iot_dashboard"))

    material = request.form.get("material", "").strip().lower().replace(" ", "_").replace("-", "_")
    if not material:
        flash("Choose a material for this bin sensor.", "error")
        return redirect(url_for("iot_dashboard"))
    try:
        capacity_kg = float(request.form.get("capacity_kg") or iot_sensors.DEFAULT_CAPACITY_KG)
        fill_threshold_pct = float(request.form.get("fill_threshold_pct") or iot_sensors.DEFAULT_FILL_THRESHOLD_PCT)
    except ValueError:
        capacity_kg, fill_threshold_pct = iot_sensors.DEFAULT_CAPACITY_KG, iot_sensors.DEFAULT_FILL_THRESHOLD_PCT

    sensor, message = iot_sensors.register_sensor(acting_as_id, material, capacity_kg, fill_threshold_pct)
    flash(message, "success" if sensor else "error")
    return redirect(url_for("iot_dashboard"))


@app.route("/iot/<int:sensor_id>/tick", methods=["POST"])
@login_required
def iot_tick(sensor_id):
    # Previously had no ownership check at all -- unlike iot_register (which
    # requires acting_as before it'll provision a sensor), any logged-in
    # user could POST here with any other company's sensor_id and trigger a
    # simulated reading (and possible auto-listing) on a bin that isn't
    # theirs. The dashboard only ever shows/links "tick" buttons for the
    # acting-as unit's own sensors, so this just enforces server-side what
    # the UI already implies.
    sensor_record = iot_sensors.BinSensor.query.get(sensor_id)
    if not sensor_record:
        flash("Sensor not found.", "error")
        return redirect(url_for("iot_dashboard"))
    acting_as_id = session.get("acting_as")
    if current_user.role != "admin" and acting_as_id != sensor_record.unit_id:
        flash("You can only simulate readings for your own company's bin sensors.", "error")
        return redirect(url_for("iot_dashboard"))

    sensor, listing, message = iot_sensors.simulate_reading(sensor_id)
    if listing:
        notifications_module.notify_unit(
            sensor.unit_id, "info", f"Bin sensor auto-listed {listing['qty_kg']:.0f}kg",
            message=f"{sensor.device_id} crossed its fill threshold and listed itself automatically.",
            link=url_for("listings_page"),
        )
        # An IoT auto-listing is a brand-new WASTE listing just like a manual
        # or WhatsApp one -- any unit with a matching open NEED should get
        # alerted the same way a /register-unit signup already does.
        notify_new_matches_for_unit(sensor.unit_id)
        flash(message, "success")
    else:
        flash(message, "success" if sensor else "error")
    return redirect(url_for("iot_dashboard"))


@app.route("/listings", methods=["GET", "POST"])
@login_required
def listings_page():
    snap = cluster_snapshot()
    tried = None
    tried_unit_id = None
    tried_message = ""
    photo_tried = None
    photo_tried_unit_id = None
    if request.method == "POST":
        tried_unit_id = request.form.get("unit_id", "")
        tried_message = request.form.get("message", "").strip()
        if tried_message:
            tried = parse_message(tried_unit_id, tried_message)

        photo_file = request.files.get("photo")
        if photo_file and photo_file.filename:
            photo_tried_unit_id = request.form.get("photo_unit_id", "")
            photo_tried = photo_classifier.classify_photo(photo_file.read())
    return render_template(
        "listings.html",
        whatsapp_results=snap["whatsapp_results"],
        units=sorted(data.UNITS, key=lambda u: u["name"].lower()),
        tried=tried,
        tried_unit_id=tried_unit_id,
        tried_unit=data.unit_by_id(tried_unit_id) if tried_unit_id else None,
        tried_message=tried_message,
        photo_tried=photo_tried,
        photo_tried_unit_id=photo_tried_unit_id,
        photo_tried_unit=data.unit_by_id(photo_tried_unit_id) if photo_tried_unit_id else None,
    )


@app.route("/listings/quick-post", methods=["POST"])
@login_required
def listings_quick_post():
    """One-click "post it as a real listing" for the text/photo intake preview
    above on /listings, when the message/photo was simulated as an EXISTING
    real unit -- the "Simulating as" dropdown only ever offers real units
    (see listings_page()'s `units` list), so there is never a "new unit" case
    to handle here.

    BUG FIX / improvement: this used to not exist at all -- both preview
    paths' "Post it as a real listing" link sent the user to register_unit(),
    the NEW-unit registration form, even though the simulated sender was
    already a real registered unit with company/phone/email already on file.
    That forced a pointless full re-entry of details the app already had, and
    doesn't match what a genuine inbound WhatsApp message actually does (see
    whatsapp_service.receive_message()): it adds the listing straight to the
    sender's existing unit, no form at all. This route mirrors that real
    behavior for the web-preview path instead: one click, no retyping.

    Goes through data.add_listing() -- not data_access.add_listing_to_db()
    directly -- for the same reason register_unit() does: data.add_listing is
    swapped to the DB-backed version at startup only when the database
    actually has data (see data_access.update_data_imports()), so calling it
    through data.* keeps this working correctly against the in-memory
    fallback too (e.g. under the test suite's empty testing DB)."""
    unit = data.unit_by_id(request.form.get("unit_id", ""))
    if not unit:
        flash("Could not find that unit -- try simulating the message again.", "error")
        return redirect(url_for("listings_page"))

    listing_type = request.form.get("listing_type", "")
    material = request.form.get("material", "").strip()
    try:
        qty_kg = float(request.form.get("qty_kg", ""))
    except ValueError:
        qty_kg = 0

    if listing_type not in ("waste", "need") or not material or qty_kg <= 0:
        flash("That preview didn't have everything needed to post a real listing -- try simulating it again.", "error")
        return redirect(url_for("listings_page"))

    data.add_listing(unit["id"], listing_type, material, qty_kg)
    # Same "tell the requester inline, alert the counterpart" split as
    # register_unit()'s identical call -- see notify_new_matches_for_unit's
    # docstring for why this is shared across every listing-creation path.
    new_matches = notify_new_matches_for_unit(unit["id"], unit["name"])

    match_note = (
        f" {len(new_matches)} new match(es) found." if new_matches
        else " No matches yet -- it'll show up as soon as a fit appears."
    )
    flash(
        f"Posted: {listing_type.upper()} {qty_kg:.0f}kg {material.replace('_', ' ')} for {unit['name']}.{match_note}",
        "success",
    )
    return redirect(url_for("unit_detail", unit_id=unit["id"]))


@app.route("/predictions")
@login_required
def predictions_page():
    snap = cluster_snapshot()
    return render_template(
        "predictions.html",
        forecasts=snap["forecasts"],
        forecast_benchmark=forecast_benchmark_vs_baseline(),
    )


# Material-true categorical palette, tuned for the light surface -- these are
# the same darkened tones as the .cat-<category>-tx CSS custom properties in
# static/style.css, so a unit's node color on the network graph matches its
# category badge color everywhere else in the app.
GROUP_COLORS = {
    "metal": "#1d4ed8", "sugar": "#c2410c", "paper": "#047857", "packaging": "#a16207",
    "textile": "#be185d", "chemical": "#6d28d9", "food": "#b91c1c", "plastic": "#475569",
    "wood": "#7c4a1e", "rubber": "#3f3f46", "leather": "#6b4423", "electronics": "#0e7490",
    "glass": "#0369a1", "agro": "#4d7c0f",
    "other": "#4b5563",
}


@app.route("/matches")
@login_required
def matches_page():
    snap = cluster_snapshot(match_top_n=25)

    involved_ids = set()
    for m in snap["matches"]:
        involved_ids.update(m["path"])

    network_nodes = [
        {
            "id": u["id"],
            "label": u["name"].replace(" ", "\n", 1),
            "group": u["category"],
            "color": GROUP_COLORS.get(u["category"], "#4b5563"),
            "font": {"color": "#164e63", "size": 12, "strokeWidth": 3, "strokeColor": "#ffffff"},
            "opacity": 1.0 if u["id"] in involved_ids else 0.35,
        }
        for u in data.UNITS
    ]

    edges = build_edges()
    network_edges = [
        {
            "from": e["from"],
            "to": e["to"],
            # Exact matches show a single material label; a similar-material
            # (fuzzy) match shows both names so it's clear at a glance this
            # edge is an AI-inferred pairing, not a listing-to-listing exact
            # match.
            "label": (
                e["material"].replace("_", " ") if e.get("exact_match", True)
                else e["material"].replace("_", " ") + " ~ " + e["matched_material"].replace("_", " ")
            ),
            "arrows": "to",
            "width": max(1.5, min(6, e["saving_rs"] / 1800)),
            # Exact matches: solid brand-green edge. Similar-material matches:
            # dashed cyan edge, so the graph visually distinguishes "confirmed
            # listing match" from "AI-suggested near match" at a glance.
            "dashes": not e.get("exact_match", True),
            "color": {"color": "#16a34a" if e.get("exact_match", True) else "#0891b2", "opacity": 0.6},
            "font": {"size": 9, "color": "#3b6577", "strokeWidth": 3, "strokeColor": "#ffffff"},
        }
        for e in edges
    ]

    trust_scores = ledger.all_scores()
    network_plan = optimization.optimize_network(trust_scores=trust_scores)
    accepted_ids = {"->".join(m["path"]) for m in network_plan["accepted"]}

    return render_template(
        "matches.html",
        matches=snap["matches"],
        network_nodes=network_nodes,
        network_edges=network_edges,
        network_plan=network_plan,
        accepted_ids=accepted_ids,
    )


@app.route("/pooling")
@login_required
def pooling_page():
    snap = cluster_snapshot()
    return render_template(
        "pooling.html",
        pooling_results=snap["pooling_results"],
        low_volume_threshold=LOW_VOLUME_THRESHOLD_KG,
    )


@app.route("/trust")
@login_required
def trust_page():
    snap = cluster_snapshot()
    return render_template("trust.html", trust_summary=snap["trust_summary"])


@app.route("/units")
@login_required
def units():
    trust_map = ledger.all_scores()
    listing_counts = {}
    for l in data.LISTINGS:
        listing_counts[l["unit_id"]] = listing_counts.get(l["unit_id"], 0) + 1

    return render_template(
        "units.html",
        units=sorted(data.UNITS, key=lambda u: u["name"].lower()),
        trust_map=trust_map,
        listing_counts=listing_counts,
    )


@app.route("/units/<unit_id>")
@login_required
def unit_detail(unit_id):
    unit = data.unit_by_id(unit_id)
    if not unit:
        return "Unit not found", 404

    trust_scores = ledger.all_scores()
    trust_score = trust_scores.get(unit_id, 3.5)

    listings = [l for l in data.LISTINGS if l["unit_id"] == unit_id]

    matches = rank_matches(trust_scores=trust_scores, top_n=50)
    involved_matches = [m for m in matches if unit_id in m["path"]]
    is_me = session.get("acting_as") == unit_id

    # Order history here used to be shown in full -- material, quantity,
    # counterparty name, status -- to ANY logged-in visitor who clicked into
    # ANY company's page, with no relation to that company at all. That's
    # the same kind of order detail _can_view_order() (see order_detail()/
    # api_order_detail() above) deliberately restricts to the order's own
    # buyer, seller, or an admin -- it just wasn't applied here. So: only the
    # company itself (acting as them) or an admin gets the full line-item
    # table; everyone else gets a privacy-safe aggregate (counts only, no
    # counterparties/materials/quantities) -- enough to gauge how active/
    # reliable a company is without exposing who they've been trading with.
    can_view_order_history = is_me or current_user.role == "admin"
    all_unit_orders = orders_module.orders_for_unit(unit_id)
    unit_orders = all_unit_orders if can_view_order_history else None
    order_summary = None
    if not can_view_order_history:
        order_summary = {
            "total": len(all_unit_orders),
            "completed": sum(1 for o in all_unit_orders if o["status"] == "completed"),
            "cancelled": sum(1 for o in all_unit_orders if o["status"] == "cancelled"),
            "in_progress": sum(
                1 for o in all_unit_orders if o["status"] not in ("completed", "cancelled")
            ),
        }

    contact_link = whatsapp_link(unit.get("phone"), f"Hi {unit['name']}, I saw your listing on SymbioLink AI.")

    return render_template(
        "unit_detail.html",
        unit=unit,
        trust_score=trust_score,
        listings=listings,
        involved_matches=involved_matches,
        unit_orders=unit_orders,
        can_view_order_history=can_view_order_history,
        order_summary=order_summary,
        is_me=is_me,
        contact_link=contact_link,
    )


@app.route("/act-as", methods=["GET", "POST"])
@login_required
def act_as():
    if request.method == "POST":
        unit_id = request.form.get("unit_id") or None
        if unit_id:
            session["acting_as"] = unit_id
        else:
            session.pop("acting_as", None)
        return redirect(request.form.get("next") or url_for("dashboard"))
    # Alphabetical (case-insensitive) so "nattar lingam" and friends sort by
    # letter rather than however they happen to appear in data.UNITS (insertion
    # order -- originally seeded units first, then whatever got registered
    # after, in registration order).
    sorted_units = sorted(data.UNITS, key=lambda u: u["name"].lower())
    current_unit_id = session.get("acting_as")
    current_unit = data.unit_by_id(current_unit_id) if current_unit_id else None
    return render_template(
        "act_as.html",
        units=sorted_units,
        current_unit_id=current_unit_id,
        current_unit_name=current_unit["name"] if current_unit else None,
    )


@app.route("/listings/<unit_id>/<material>/edit", methods=["GET", "POST"])
@login_required
def edit_listing(unit_id, material):
    unit = data.unit_by_id(unit_id)
    if not unit:
        return "Unit not found", 404
    if session.get("acting_as") != unit_id:
        return redirect(url_for("act_as", next=url_for("edit_listing", unit_id=unit_id, material=material)))

    listing_type = request.args.get("type", "waste")
    listing = data.find_listing(unit_id, material, listing_type)
    if not listing:
        return "Listing not found", 404

    if request.method == "POST":
        new_qty = float(request.form["qty_kg"])
        data.update_listing_qty_absolute(unit_id, material, listing_type, new_qty)
        return redirect(url_for("unit_detail", unit_id=unit_id))

    return render_template("edit_listing.html", unit=unit, listing=listing)


@app.route("/listings/<unit_id>/<material>/delete", methods=["POST"])
@login_required
def delete_listing(unit_id, material):
    if session.get("acting_as") != unit_id:
        return "Not authorized -- switch to acting as this unit first", 403
    listing_type = request.form.get("type", "waste")
    data.remove_listing(unit_id, material, listing_type)
    return redirect(url_for("unit_detail", unit_id=unit_id))


@app.route("/register-unit", methods=["GET", "POST"])
@login_required
def register_unit():
    confirmation = None
    if request.method == "POST":
        listing_type = request.form["listing_type"]
        material = request.form["material"].strip()
        qty_kg = float(request.form["qty_kg"])

        # A user who already has a company (unit_id set -- from login-time
        # auto-binding, or from an earlier visit to this same form) is just
        # posting another listing for THEIR company, not founding a brand new
        # one -- so this attaches the listing to their existing unit instead
        # of calling data.add_unit() again, which used to spin up a fresh,
        # unrelated company record every single time this form was submitted.
        # Only a user with no unit_id yet (first company setup) goes through
        # the create-a-new-unit path, and that new unit is then permanently
        # linked back to their account so every future listing/order is
        # attributed the same way with no manual step.
        if current_user.unit_id:
            unit = data.unit_by_id(current_user.unit_id)
        else:
            company_name = request.form["company_name"].strip()
            category = request.form["category"]
            phone = request.form.get("phone", "").strip()
            email = request.form.get("email", "").strip()
            # simple demo placement: spread new units around the existing
            # cluster so distance-based matching/pooling still behaves sensibly
            loc = (round(random.uniform(0, 4.2), 2), round(random.uniform(0, 4.4), 2))
            unit = data.add_unit(company_name, category, loc, phone, email)
            current_user.unit_id = unit["id"]
            db.session.commit()

        session["acting_as"] = unit["id"]
        listing = data.add_listing(unit["id"], listing_type, material, qty_kg)

        # Show the requester any matches this new listing immediately created,
        # and notify the *other* unit on each new edge -- the requester already
        # sees these matches inline on the confirmation page below, so only the
        # counterpart needs the bell alert telling them a new match just appeared.
        # Shared with the WhatsApp-intake and IoT-auto-listing paths -- see
        # matching.notify_new_matches_for_unit()'s docstring for why this isn't
        # just a register_unit()-only concern anymore.
        new_matches = notify_new_matches_for_unit(unit["id"], unit["name"])

        confirmation = {
            "unit": unit,
            "listing": listing,
            "new_matches": new_matches,
        }

    return render_template(
        "register_unit.html",
        materials=data.KNOWN_MATERIALS,
        categories=data.KNOWN_CATEGORIES,
        confirmation=confirmation,
        # Prefill support for handoff from the text/photo intake previews on
        # /listings ("Looks right? Post it as a real listing") -- ignored on a
        # plain visit since request.args is just empty then.
        prefill_material=request.args.get("material", ""),
        prefill_qty_kg=request.args.get("qty_kg", ""),
        prefill_listing_type=request.args.get("listing_type", "waste"),
    )


@app.route("/market-prices")
@login_required
def market_prices_page():
    """Reference price guide + real trade activity per material -- see
    market_insights.py's module docstring for why this deliberately isn't a
    fake 'price trend' chart (this app has no per-order negotiated price to
    build one from)."""
    return render_template("market_prices.html", price_guide=market_insights.material_price_guide())


@app.route("/search")
@login_required
def search():
    query = request.args.get("q", "").strip().lower()
    category_filter = request.args.get("category", "")
    # Whichever company the viewer is currently acting as -- used below to flag
    # each listing as "yours" vs. another company's, so the marketplace doesn't
    # read as one undifferentiated pile (and so a unit isn't tempted to try
    # ordering/chatting with itself).
    acting_as_id = session.get("acting_as")

    waste_listings = [l for l in data.LISTINGS if l["type"] == "waste"]
    results = []
    for l in waste_listings:
        unit = data.unit_by_id(l["unit_id"])
        if category_filter and unit["category"] != category_filter:
            continue
        material_readable = l["material"].replace("_", " ")
        if query and query not in material_readable and query not in unit["category"]:
            continue
        prices = data.PRICE_TABLE_RS_PER_KG.get(l["material"], {"new_material": 0, "byproduct": 0})
        results.append({
            "unit_id": unit["id"],
            "unit_name": unit["name"],
            "phone": unit.get("phone", ""),
            "category": unit["category"],
            "material": material_readable,
            "material_key": l["material"],
            "qty_kg": l["qty_kg"],
            "price_per_kg": prices["byproduct"],
            "new_material_price_per_kg": prices["new_material"],
            "is_own": unit["id"] == acting_as_id,
            "contact_link": whatsapp_link(unit.get("phone"), f"Hi {unit['name']}, I saw your {material_readable} listing on SymbioLink AI."),
            **_trust_badge_info(unit["id"]),
        })

    # "Need" listings were captured in data.py/registration all along (matching.py's
    # multi-hop engine already reads them to build match edges) but the marketplace
    # search page only ever rendered the "waste" half -- a unit sitting on a byproduct
    # had no way to see who in the cluster actually wants to buy it without going
    # through the Matching Engine page first. Surfacing needs here directly closes
    # that loop for a plain "I have X, who wants it" search.
    need_listings = [l for l in data.LISTINGS if l["type"] == "need"]
    need_results = []
    for l in need_listings:
        unit = data.unit_by_id(l["unit_id"])
        if category_filter and unit["category"] != category_filter:
            continue
        material_readable = l["material"].replace("_", " ")
        if query and query not in material_readable and query not in unit["category"]:
            continue
        prices = data.PRICE_TABLE_RS_PER_KG.get(l["material"], {"new_material": 0, "byproduct": 0})
        need_results.append({
            "unit_id": unit["id"],
            "unit_name": unit["name"],
            "phone": unit.get("phone", ""),
            "category": unit["category"],
            "material": material_readable,
            "material_key": l["material"],
            "qty_kg": l["qty_kg"],
            "price_per_kg": prices["byproduct"],
            "is_own": unit["id"] == acting_as_id,
            "contact_link": whatsapp_link(unit.get("phone"), f"Hi {unit['name']}, I saw you're looking for {material_readable} on SymbioLink AI -- I may have some to offer."),
            **_trust_badge_info(unit["id"]),
        })

    return render_template(
        "search.html",
        query=request.args.get("q", ""),
        category_filter=category_filter,
        categories=data.KNOWN_CATEGORIES,
        results=results,
        need_results=need_results,
    )


@app.route("/order/new", methods=["GET", "POST"])
@login_required
def order_new():
    seller_id = request.values.get("unit_id", "")
    material = request.values.get("material", "")
    available_qty = float(request.values.get("qty", 0) or 0)
    seller = data.unit_by_id(seller_id)
    if not seller:
        return "Listing not found", 404

    # The buyer is always the company the user is currently "acting as" -- never a
    # value picked from a form field. Previously buyer_unit_id was read straight off
    # the POST body with no ownership check, so any logged-in user could place an
    # order as ANY unit just by changing that form value, not just the one they're
    # acting as. This matches the same acting_as-only pattern edit_listing/delete_listing
    # already enforce.
    acting_as_id = session.get("acting_as")
    buyer = data.unit_by_id(acting_as_id) if acting_as_id else None

    error = None
    order = None
    if request.method == "POST":
        try:
            qty_wanted = float(request.form["qty_kg"])
        except (KeyError, ValueError):
            qty_wanted = None
            error = "Enter a valid quantity in kg."

        # re-check current availability at submit time (another order may have used some up)
        current_listing = next(
            (l for l in data.LISTINGS if l["unit_id"] == seller_id and l["material"] == material and l["type"] == "waste"),
            None,
        )
        current_available = current_listing["qty_kg"] if current_listing else 0

        if error:
            pass  # qty_wanted failed to parse -- error already set above
        elif not buyer:
            error = "Act as your company before placing an order."
        elif buyer["id"] == seller_id:
            error = "A unit can't order its own listing."
        elif qty_wanted <= 0 or qty_wanted > current_available:
            error = f"Enter a quantity between 1 and {current_available:.0f}kg (current availability)."
        else:
            data.reduce_listing_qty(seller_id, material, qty_wanted)
            order = orders_module.place_order(
                seller_unit_id=seller_id,
                seller_name=seller["name"],
                material=material,
                buyer_unit_id=buyer["id"],
                buyer_name=buyer["name"],
                qty_kg=qty_wanted,
                co2_saved_kg=estimate_co2_saved_kg(material, qty_wanted),
            )
            _log_order_history(
                order["id"], "placed", changed_by=current_user.username,
                notes=f"{buyer['name']} ordered {qty_wanted:.0f}kg {material.replace('_', ' ')} from {seller['name']}",
            )
            # Placement itself never routed through _notify_order_update -- every
            # *later* stage (confirmed, picked up, delivered, paid, refunded...)
            # notified both sides, but a seller had no way to know a brand-new
            # order had come in at all until they happened to check /orders.
            _notify_order_update(order, status_override="placed")
            available_qty = current_available - qty_wanted

    return render_template(
        "order_new.html",
        seller=seller,
        material=material,
        available_qty=available_qty,
        buyer=buyer,
        error=error,
        order=order,
    )


@app.route("/orders")
@login_required
def orders_page():
    # Previously rendered orders_module.all_orders() unfiltered -- any logged-in
    # user could see every order in the system (buyer/seller names, pricing,
    # dispute reasons) regardless of which company, if any, they were acting
    # as. Now scoped to orders the current viewer is actually a party to,
    # admins excepted.
    visible_orders = [o for o in orders_module.all_orders() if _can_view_order(o)]
    # The main list only shows orders still in progress -- completed/cancelled
    # ones move to the Order History section below instead of piling up
    # forever at the top of an active-orders view.
    active_orders = [o for o in visible_orders if o["status"] not in ("completed", "cancelled")]
    history_orders = [o for o in visible_orders if o["status"] in ("completed", "cancelled")]
    # Risk badge only makes sense on still-active orders (nothing left to flag
    # once an order's already completed or cancelled), and only computed for
    # the orders this viewer can already see -- the template additionally
    # only shows it to the seller or an admin, never the buyer, so a low
    # score never reads as "the platform doubts you'll pay."
    risk_by_order = {}
    for o in active_orders:
        prob, label = _order_risk_score(o)
        if prob is not None:
            risk_by_order[o["id"]] = {
                "probability": prob,
                "label": label,
                "explanation": _order_risk_explanation(o, label),
            }
    return render_template(
        "orders.html",
        orders=visible_orders,
        active_orders=active_orders,
        history_orders=history_orders,
        risk_by_order=risk_by_order,
    )


@app.route("/orders/history")
@login_required
def order_history_page():
    # Its own page (linked from the sidebar) rather than a section glued to
    # the bottom of /orders -- same visibility rule as orders_page() above,
    # just scoped to the completed/cancelled orders instead of the active
    # ones.
    visible_orders = [o for o in orders_module.all_orders() if _can_view_order(o)]
    history_orders = [o for o in visible_orders if o["status"] in ("completed", "cancelled")]
    return render_template("order_history.html", history_orders=history_orders)


@app.route("/order/<order_id>/advance", methods=["POST"])
@login_required
def order_advance(order_id):
    existing = orders_module.order_by_id(order_id)
    if not existing:
        flash(f"Order {order_id} not found.", "error")
        return redirect(url_for("orders_page"))

    # Every stage this route drives -- accepting a placed order, marking it
    # picked up, marking it delivered -- is a seller-side fulfillment action.
    # Previously this route only checked @login_required, so ANY logged-in
    # user (the buyer on the order, or even a unit with no relationship to it
    # at all) could hit this endpoint directly and accept/advance an order
    # that wasn't theirs to accept. Same acting_as-ownership pattern already
    # used by order_raise_dispute() below.
    acting_as_id = session.get("acting_as")
    if current_user.role != "admin" and acting_as_id != existing["seller_unit_id"]:
        flash("Only the seller on this order can accept it or move it to the next stage.", "error")
        return redirect(url_for("orders_page"))

    prev_status = existing["status"]
    order = orders_module.advance_order(order_id)

    if order and order["status"] == prev_status:
        # advance_order() no-ops for two different reasons: an already-
        # terminal order (completed/cancelled, nothing to say), or a
        # 'confirmed' order whose buyer hasn't paid yet (fulfillment -- i.e.
        # marking it picked up -- can't start until payment clears). Only
        # the second case is actionable/worth telling the seller about.
        if prev_status == "confirmed" and order.get("payment_status") != "paid":
            flash("This order can't be marked picked up until the buyer has paid.", "error")
        return redirect(url_for("orders_page"))

    if order:
        _log_order_history(order_id, order["status"], changed_by=current_user.username)
        _notify_order_update(order)
    return redirect(url_for("orders_page"))


@app.route("/order/<order_id>/gps-tick", methods=["POST"])
@login_required
def order_gps_tick(order_id):
    """Advance one order's simulated pickup/delivery vehicle a step closer to
    its target geofence -- see geofencing.py's module docstring. Same
    seller-or-admin ownership rule as order_advance() (the manual button this
    supplements), since starting/progressing the delivery run is a seller-side
    fulfillment action either way."""
    existing = orders_module.order_by_id(order_id)
    if not existing:
        flash(f"Order {order_id} not found.", "error")
        return redirect(url_for("orders_page"))

    acting_as_id = session.get("acting_as")
    if current_user.role != "admin" and acting_as_id != existing["seller_unit_id"]:
        flash("Only the seller on this order can simulate its GPS-tracked vehicle.", "error")
        return redirect(url_for("orders_page"))

    order, message = geofencing.simulate_gps_tick(
        order_id, log_history_fn=_log_order_history, notify_fn=_notify_order_update,
    )
    flash(message, "success" if order else "error")
    return redirect(url_for("orders_page"))


@app.route("/order/<order_id>/complete", methods=["POST"])
@login_required
def order_complete(order_id):
    """Confirm delivery and record both sides' star ratings.

    Previously this route had NO ownership check at all -- unlike every
    other order-mutating route here (order_advance/order_cancel both check
    acting_as against the order), any logged-in user could complete and
    rate an order that wasn't theirs. The UI shows one shared rating form
    to whichever party is viewing a 'delivered' order (see orders.html),
    so either the buyer or the seller may submit it -- just not someone
    with no relationship to the order at all. orders.complete_order() also
    now refuses to run unless the order is actually 'delivered', which
    doubles as the fix for a double-submit (browser back-button resubmit,
    a retried request, or two quick clicks) re-recording a second, possibly
    different, trust rating for the same exchange -- once complete_order()
    flips the status to 'completed', a second call finds the status guard
    failing and simply returns None instead of recording anything again.
    """
    existing = orders_module.order_by_id(order_id)
    if not existing:
        flash(f"Order {order_id} not found.", "error")
        return redirect(url_for("orders_page"))

    acting_as_id = session.get("acting_as")
    is_party = acting_as_id in (existing["buyer_unit_id"], existing["seller_unit_id"])
    if current_user.role != "admin" and not is_party:
        flash("Only the buyer or seller on this order can confirm delivery and rate it.", "error")
        return redirect(url_for("orders_page"))

    try:
        rating_buyer_to_seller = int(request.form["rating_buyer_to_seller"])
        rating_seller_to_buyer = int(request.form["rating_seller_to_buyer"])
        if not (1 <= rating_buyer_to_seller <= 5 and 1 <= rating_seller_to_buyer <= 5):
            raise ValueError("rating out of range")
    except (KeyError, ValueError):
        flash("Ratings must be a whole number from 1 to 5.", "error")
        return redirect(url_for("orders_page"))

    order = orders_module.complete_order(order_id, rating_buyer_to_seller, rating_seller_to_buyer)
    if order:
        # buyer rates the seller -> seller receives that rating, and vice versa
        #
        # BUG FIX: this used to ALSO call the now-removed _log_trust_rating()
        # helper for both directions right after record_exchange() above --
        # but record_exchange() already writes both TrustRating rows straight
        # to the same table _log_trust_rating wrote to (see trust.py's
        # TrustLedger.record_exchange and _RatingsReceivedView, which reads
        # live from TrustRating, not an in-memory structure, despite the
        # removed helper's docstring calling the ledger "in-memory"). Every
        # completed order was silently writing 4 TrustRating rows instead of
        # 2 -- doubling completed_exchanges and letting a unit reach the
        # "Highly recommended" exchange-count threshold in half the real
        # number of actual exchanges. That's exactly the kind of gap a
        # ring-trading pair could have exploited, so on top of removing the
        # duplicate write, _trust_badge_info() above now also requires a
        # minimum number of distinct counterparties for that badge.
        ledger.record_exchange(
            from_id=order["buyer_unit_id"],
            to_id=order["seller_unit_id"],
            rating_from_gives_to_to=rating_buyer_to_seller,
            rating_to_gives_to_from=rating_seller_to_buyer,
            order_id_str=order_id,
        )
        _log_order_history(order_id, "completed", changed_by=current_user.username)
        _notify_order_update(order)
    else:
        flash(f"Could not complete order {order_id} -- it isn't in 'delivered' status (already completed, or not delivered yet).", "error")
    return redirect(url_for("orders_page"))


@app.route("/order/<order_id>/cancel", methods=["POST"])
@login_required
def order_cancel(order_id):
    """Cancel (or, for a still-'placed' order, decline) an order with reason
    tracking.

    Ownership rules -- previously this route only checked @login_required, so
    any logged-in user could cancel any order in the system, buyer/seller/
    unrelated third party alike:
      - The seller may decline/cancel at any still-cancellable stage (placed
        or confirmed) -- this is the seller's side of "accept or decline an
        incoming order".
      - The buyer may only cancel while the order is still 'placed' -- i.e.
        withdrawing their own request before the seller has acted on it.
        Once the seller has confirmed, the buyer can no longer unilaterally
        cancel; a paid, confirmed order they need to back out of goes through
        the existing dispute flow (/order/<id>/dispute) instead, so an admin
        is in the loop.
      - Admins retain their existing unrestricted ability to intervene.
    """
    existing = orders_module.order_by_id(order_id)
    if not existing:
        flash(f"Order {order_id} not found.", "error")
        return redirect(url_for("orders_page"))

    acting_as_id = session.get("acting_as")
    is_seller = acting_as_id == existing["seller_unit_id"]
    is_buyer = acting_as_id == existing["buyer_unit_id"]
    if current_user.role != "admin" and not is_seller and not (is_buyer and existing["status"] == "placed"):
        flash("Only the seller can decline this order, or the buyer can withdraw it before the seller confirms.", "error")
        return redirect(url_for("orders_page"))

    prev_status = existing["status"]
    reason = request.form.get("reason", "No reason provided")
    cancelled_by = current_user.username if current_user.is_authenticated else "unknown"

    order = orders_module.cancel_order(order_id, reason, cancelled_by)
    if order:
        # A seller declining an order that was still awaiting their decision
        # reads better as "declined" than "cancelled" -- same underlying
        # status/history entry either way, just clearer wording back to the
        # people involved.
        verb = "declined" if (is_seller and prev_status == "placed") else "cancelled"
        _log_order_history(order_id, "cancelled", changed_by=cancelled_by, notes=reason)

        # If the buyer had already paid, cancelling/declining must not just
        # silently keep their money -- refund it the same way an admin's
        # direct refund or a resolved dispute does (_refund_order is shared
        # with both of those paths).
        if order.get("payment_status") == "paid":
            refund_details = _refund_order(order)
            if refund_details:
                _log_order_history(order_id, "cancelled", changed_by="system", notes="Auto-refunded on cancellation")

        _notify_order_update(order, status_override=verb)
        flash(f"Order {order_id} has been {verb}.", "success")
    else:
        flash(f"Could not cancel order {order_id}. It may already be processed.", "error")

    return redirect(url_for("orders_page"))


@app.route("/orders/bulk-advance", methods=["POST"])
@login_required
def bulk_advance_orders():
    """Bulk advance multiple orders to next stage -- the Quick Actions panel's
    "Confirm All Placed" / "Mark All Picked Up" buttons on /orders.

    Previously this looped over orders_module.all_orders() (every order in
    the whole system) with no ownership check, so any logged-in user could
    bulk-advance orders belonging to companies they have no relationship to,
    just by having ANY unit acting-as set (or even none). advance_order()
    is a seller-side fulfillment action everywhere else it's exposed (see
    order_advance()'s own docstring), so this bulk version is scoped the
    same way: only orders where the current acting_as unit is the seller,
    unless the caller is an admin (who can already act on any single order
    via order_advance/order_cancel)."""
    status_filter = request.form.get("status_filter", "placed")
    acting_as_id = session.get("acting_as")

    if current_user.role == "admin":
        orders = orders_module.all_orders()
    elif acting_as_id:
        orders = [o for o in orders_module.all_orders() if o["seller_unit_id"] == acting_as_id]
    else:
        orders = []

    advanced_count = 0
    skipped_unpaid = 0

    for order in orders:
        if order["status"] == status_filter:
            prev_status = order["status"]
            result = orders_module.advance_order(order["id"])
            if result and result["status"] != prev_status:
                advanced_count += 1
                _log_order_history(order["id"], result["status"], changed_by=current_user.username, notes="Bulk advance")
            elif result and prev_status == "confirmed" and result.get("payment_status") != "paid":
                # BUG FIX: this used to check prev_status == "placed", but the
                # payment gate in orders.advance_order() only ever blocks the
                # "confirmed" -> "picked_up" transition (see its own comment) --
                # "placed" -> "confirmed" is never payment-gated, so it always
                # advances and hits the `if` branch above instead. With the old
                # check, this `elif` could never actually match anything:
                # skipped_unpaid stayed 0 forever, and clicking "Mark All Picked
                # Up" on a seller's unpaid confirmed orders silently did nothing
                # with no explanation at all (not even the flash message's
                # "Advanced 0 orders" hinted at why).
                skipped_unpaid += 1

    message = f"Advanced {advanced_count} orders from {status_filter} to next stage."
    if skipped_unpaid:
        message += f" Skipped {skipped_unpaid} awaiting payment."
    flash(message, "success")

    return redirect(url_for("orders_page"))


# WhatsApp webhook endpoint
@app.route("/whatsapp/webhook", methods=["POST"])
def whatsapp_webhook():
    """Twilio WhatsApp webhook endpoint."""
    return whatsapp_service.receive_message()


@app.route("/whatsapp/test-notification/<unit_id>", methods=["POST"])
@login_required
def test_whatsapp_notification(unit_id):
    """Test WhatsApp notification (for development)."""
    match_details = {
        'partner_name': 'Test Partner Unit',
        'material': 'metal_shavings',
        'savings': '5000',
        'distance': '2.5',
        'partner_phone': '+919000000000'
    }
    success = whatsapp_service.send_match_notification(unit_id, match_details)
    if success:
        return "Test notification sent successfully"
    else:
        return "Failed to send test notification", 500


# Admin routes
@app.route("/admin")
@admin_required
def admin_dashboard():
    """Admin dashboard with system overview and management tools."""
    total_users = User.query.count()
    total_units = Unit.query.count()
    total_listings = Listing.query.filter_by(is_active=True).count()
    # The live order flow (order_new/advance/complete/cancel) reads and writes
    # orders.py's in-memory ORDERS list, never the DB-backed Order model -- so
    # Order.query.count() always reported 0 here regardless of actual order
    # activity. orders_module.all_orders() is the real source of truth.
    total_orders = len(orders_module.all_orders())
    open_disputes_count = len(orders_module.open_disputes())
    recent_messages = WhatsAppMessage.query.order_by(WhatsAppMessage.created_at.desc()).limit(10).all()
    at_risk_orders = _at_risk_orders()
    pending_reminders_count = len(orders_module.orders_needing_reminder())
    unmet_supply, unmet_demand = network_gap_report()
    concentrated_pairs = ledger.concentrated_pairs_report()

    return render_template(
        "admin_dashboard.html",
        total_users=total_users,
        total_units=total_units,
        total_listings=total_listings,
        total_orders=total_orders,
        open_disputes_count=open_disputes_count,
        recent_messages=recent_messages,
        at_risk_orders=at_risk_orders,
        pending_reminders_count=pending_reminders_count,
        unmet_supply=unmet_supply,
        unmet_demand=unmet_demand,
        concentrated_pairs=concentrated_pairs,
    )


@app.route("/admin/send-reminders", methods=["POST"])
@admin_required
def admin_send_reminders():
    """Admin-triggered nudge for every order sitting stalled past the
    reminder threshold (see orders.orders_needing_reminder) -- this app has
    no background scheduler, so "on demand from the admin dashboard" is the
    simplest way to actually fire these without adding one."""
    counts = _send_pending_order_reminders()
    total = sum(counts.values())
    if total == 0:
        flash("No orders are currently stalled past the reminder threshold.", "info")
    else:
        flash(
            f"Sent {total} reminder(s): {counts['payment_due']} payment, "
            f"{counts['pickup_due']} pickup, {counts['delivery_due']} delivery.",
            "success",
        )
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/users")
@admin_required
def admin_users():
    """User management for admins."""
    users = User.query.all()
    return render_template("admin_users.html", users=users)


@app.route("/admin/audit")
@role_required('admin', 'auditor')
def audit_logs():
    """Audit logs for compliance and monitoring. Open to both admin (full
    system access, per auth_decorators.py's module docstring and the
    'audit_logs' entry already granted to 'admin' in check_permission()'s
    permission matrix) and auditor -- previously restricted to @auditor_required
    only, which silently 403'd admins despite the app's own permission model
    saying they should have access."""
    order_history = OrderHistory.query.order_by(OrderHistory.created_at.desc()).limit(50).all()
    whatsapp_logs = WhatsAppMessage.query.order_by(WhatsAppMessage.created_at.desc()).limit(50).all()
    trust_ratings = TrustRating.query.order_by(TrustRating.created_at.desc()).limit(50).all()

    return render_template(
        "audit_logs.html",
        order_history=order_history,
        whatsapp_logs=whatsapp_logs,
        trust_ratings=trust_ratings
    )


# Payment routes
@app.route("/payment/create-order", methods=["POST"])
@login_required
def create_payment_order():
    """Create a Razorpay order for an existing SymbioLink order and remember the
    mapping between the two, so /payment/verify can find the order again once
    Razorpay hands the browser back a payment id."""
    payload = request.get_json(silent=True) or {}
    order_id = payload.get('order_id')

    order = orders_module.order_by_id(order_id) if order_id else None
    if not order:
        return jsonify({"success": False, "error": "Order not found"}), 404

    # Only the buyer on this order (acting as that company) can pay for it --
    # otherwise anyone logged in could trigger a Razorpay order for someone else's
    # purchase just by knowing/guessing the order id.
    acting_as_id = session.get("acting_as")
    if not acting_as_id or order["buyer_unit_id"] != acting_as_id:
        return jsonify({"success": False, "error": "You can only pay for your own orders"}), 403

    if order.get("payment_status") == "paid":
        return jsonify({"success": False, "error": "This order has already been paid"}), 400

    # Calculate amount from the order's own material/qty rather than trusting
    # client-supplied values.
    amount_details = payment_service.calculate_order_amount(order["material"], order["qty_kg"])

    receipt = f"order_{order['id']}"
    razorpay_order = payment_service.create_order(
        amount_rs=amount_details['total_amount'],
        receipt=receipt,
        notes={
            "order_id": order["id"],
            "material": order["material"],
            "qty_kg": order["qty_kg"],
            "user_id": current_user.id
        }
    )

    orders_module.set_payment_info(
        order["id"],
        razorpay_order_id=razorpay_order.get("id"),
        amount_rs=amount_details['total_amount'],
        status="awaiting_payment",
    )

    return jsonify({
        "success": True,
        "order": razorpay_order,
        "amount_details": amount_details,
        "key_id": payment_service.key_id,
        "order_id": order["id"],
        # Enough context for the payment UI to render a clear "what am I paying
        # for" summary without a second round-trip.
        "order_summary": {
            "material": order["material"],
            "qty_kg": order["qty_kg"],
            "buyer_name": order["buyer_name"],
            "seller_name": order["seller_name"],
            "co2_saved_kg": order.get("co2_saved_kg"),
        },
    })


@app.route("/payment/verify", methods=["POST"])
@login_required
def verify_payment():
    """Verify Razorpay payment signature and mark the matching order as paid."""
    if request.is_json:
        payload = request.get_json()
    else:
        payload = request.form

    razorpay_order_id = payload.get('razorpay_order_id')
    razorpay_payment_id = payload.get('razorpay_payment_id')
    razorpay_signature = payload.get('razorpay_signature')

    is_valid = payment_service.verify_payment(
        razorpay_order_id,
        razorpay_payment_id,
        razorpay_signature
    )

    if is_valid:
        payment_details = payment_service.get_payment_details(razorpay_payment_id)

        order = orders_module.order_by_razorpay_order_id(razorpay_order_id)
        if order:
            orders_module.set_payment_info(
                order["id"],
                razorpay_payment_id=razorpay_payment_id,
                status="paid",
            )
            _log_order_history(
                order["id"], order["status"], changed_by=current_user.username,
                notes=f"Paid Rs.{order.get('payment_amount', 0):.0f} via Razorpay ({razorpay_payment_id})",
            )
            _notify_order_update(order, status_override="payment received")

        return jsonify({
            "success": True,
            "payment_details": payment_details,
            "order_id": order["id"] if order else None,
        })
    else:
        return jsonify({
            "success": False,
            "error": "Invalid payment signature"
        }), 400


@app.route("/payment/refund/<payment_id>", methods=["POST"])
@admin_required
def process_refund(payment_id):
    """Process a refund for a raw Razorpay payment id (JSON API, e.g. for the
    mobile app or external tools). Admin-only. If the payment id happens to match
    a SymbioLink order, that order's payment_status is kept in sync too -- the
    HTML admin UI normally goes through /order/<id>/refund instead (see below),
    but a caller that only has the payment id shouldn't leave the order stuck
    showing "Paid" after the money's actually been returned."""
    amount_rs = request.form.get('amount', type=float) or (request.get_json(silent=True) or {}).get('amount')

    refund_details = payment_service.refund_payment(payment_id, amount_rs)

    order = orders_module.order_by_razorpay_payment_id(payment_id)
    if order:
        orders_module.set_payment_info(order["id"], status="refunded")
        _log_order_history(order["id"], order["status"], changed_by=current_user.username, notes="Refunded via payment API")
        _notify_order_update(order, status_override="refunded")

    return jsonify({
        "success": True,
        "refund": refund_details,
        "order_id": order["id"] if order else None,
    })


@app.route("/order/<order_id>/refund", methods=["POST"])
@admin_required
def order_refund(order_id):
    """Admin-initiated refund on a specific order, independent of any dispute --
    e.g. a courtesy refund the admin decides to issue directly. Dispute-driven
    refunds go through /admin/disputes/<id>/resolve instead, which calls the same
    _refund_order() helper underneath."""
    order = orders_module.order_by_id(order_id)
    if not order:
        flash(f"Order {order_id} not found.", "error")
        return redirect(request.referrer or url_for("orders_page"))

    if order.get("payment_status") != "paid":
        flash(f"Order {order_id} isn't in a paid state, so there's nothing to refund.", "error")
        return redirect(request.referrer or url_for("orders_page"))

    reason = (request.get_json(silent=True) or request.form).get("reason", "Admin-initiated refund")
    refund_details = _refund_order(order)

    if refund_details:
        _log_order_history(order_id, order["status"], changed_by=current_user.username, notes=f"Refunded: {reason}")
        _notify_order_update(order, status_override="refunded")
        flash(f"Order {order_id} has been refunded.", "success")
    else:
        flash(f"Couldn't refund order {order_id} -- no payment record on file.", "error")

    return redirect(request.referrer or url_for("orders_page"))


@app.route("/order/<order_id>/dispute", methods=["POST"])
@login_required
def order_raise_dispute(order_id):
    """Buyer flags a problem with a paid order for admin review. Accepts either a
    JSON body (mobile app) or a regular form post (web) -- same pattern as
    /payment/verify above."""
    order = orders_module.order_by_id(order_id)
    if not order:
        flash(f"Order {order_id} not found.", "error")
        return redirect(url_for("orders_page"))

    acting_as_id = session.get("acting_as")
    if not acting_as_id or order["buyer_unit_id"] != acting_as_id:
        flash("Only the buyer on this order can raise a dispute.", "error")
        return redirect(url_for("orders_page"))

    if order.get("payment_status") != "paid":
        flash("A dispute can only be raised on a paid order.", "error")
        return redirect(url_for("orders_page"))

    if order.get("dispute_status") == "open":
        flash(f"Order {order_id} already has an open dispute awaiting review.", "error")
        return redirect(url_for("orders_page"))

    payload = request.get_json(silent=True) or request.form
    reason = (payload.get("reason") or "").strip()
    if not reason:
        flash("Please describe the issue before raising a dispute.", "error")
        return redirect(url_for("orders_page"))

    orders_module.raise_dispute(order_id, reason, raised_by=current_user.username)
    _log_order_history(order_id, order["status"], changed_by=current_user.username, notes=f"Dispute raised: {reason}")
    dispute_title = f"Dispute raised on order {order_id}"
    notifications_module.notify_admins(
        "dispute", dispute_title, message=reason, link=url_for("admin_disputes"),
    )
    email_service.notify_admins(dispute_title, reason)
    flash(f"Dispute raised on order {order_id}. An admin will review it.", "success")
    return redirect(url_for("orders_page"))


@app.route("/admin/disputes")
@admin_required
def admin_disputes():
    """Admin queue for reviewing buyer-raised disputes -- resolve each as a
    refund (money back via Razorpay/simulated, order marked refunded) or a
    rejection (dispute closed, no money moves)."""
    all_orders = orders_module.all_orders()
    open_disputes = [o for o in all_orders if o.get("dispute_status") == "open"]
    resolved_disputes = [o for o in all_orders if o.get("dispute_status") in ("resolved_refunded", "resolved_rejected")]
    return render_template("admin_disputes.html", open_disputes=open_disputes, resolved_disputes=resolved_disputes)


@app.route("/admin/disputes/<order_id>/resolve", methods=["POST"])
@admin_required
def admin_resolve_dispute(order_id):
    order = orders_module.order_by_id(order_id)
    if not order or order.get("dispute_status") != "open":
        flash(f"Order {order_id} has no open dispute to resolve.", "error")
        return redirect(url_for("admin_disputes"))

    resolution = request.form.get("resolution")  # "refund" or "reject"
    notes = request.form.get("notes", "")

    if resolution == "refund":
        refund_details = _refund_order(order)
        orders_module.resolve_dispute(order_id, "resolved_refunded", resolved_by=current_user.username, notes=notes)
        _log_order_history(order_id, order["status"], changed_by=current_user.username, notes=f"Dispute resolved (refunded): {notes}")
        if refund_details:
            _notify_order_update(order, status_override="refunded after dispute review")
            refund_title = f"Dispute on order {order_id} resolved: refunded"
            notifications_module.notify_unit(
                order["buyer_unit_id"], "dispute", refund_title, message=notes or None, link=url_for("orders_page"),
            )
            email_service.notify_unit(order["buyer_unit_id"], refund_title, notes or "No further notes provided.")
            flash(f"Order {order_id} refunded and dispute closed.", "success")
        else:
            flash(f"Dispute on order {order_id} marked refunded, but no payment record was found to actually refund.", "error")
    elif resolution == "reject":
        orders_module.resolve_dispute(order_id, "resolved_rejected", resolved_by=current_user.username, notes=notes)
        _log_order_history(order_id, order["status"], changed_by=current_user.username, notes=f"Dispute rejected: {notes}")
        reject_title = f"Dispute on order {order_id} resolved: rejected"
        notifications_module.notify_unit(
            order["buyer_unit_id"], "dispute", reject_title, message=notes or None, link=url_for("orders_page"),
        )
        email_service.notify_unit(order["buyer_unit_id"], reject_title, notes or "No further notes provided.")
        flash(f"Dispute on order {order_id} rejected.", "success")
    else:
        flash("Choose either 'Refund' or 'Reject' to resolve this dispute.", "error")

    return redirect(url_for("admin_disputes"))


@app.route("/order/<order_id>/invoice")
@login_required
def order_invoice(order_id):
    """Printable/linkable invoice for one order. Viewable by either party on the
    order or by an admin -- not by every logged-in user, since it includes cost
    and payment-reference details."""
    order = orders_module.order_by_id(order_id)
    if not order:
        return "Order not found", 404

    if not _can_view_order(order):
        return "You don't have access to this order's invoice.", 403

    amount_details = payment_service.calculate_order_amount(order["material"], order["qty_kg"])
    return render_template("invoice.html", order=order, amount_details=amount_details)


@app.route("/notifications")
@login_required
def notifications_page():
    """Full notification history -- the bell dropdown only shows the most
    recent handful; this is the "View all" destination."""
    unit_id, include_admin = _notification_context()
    items = notifications_module.notifications_for_context(unit_id, include_admin, limit=200)
    return render_template("notifications.html", notifications=items)


@app.route("/api/notifications")
@login_required
def api_notifications():
    """JSON feed the bell dropdown polls for its unread count and populates
    its list from when opened."""
    unit_id, include_admin = _notification_context()
    items = notifications_module.notifications_for_context(unit_id, include_admin)
    return jsonify({
        "success": True,
        "unread_count": notifications_module.unread_count_for_context(unit_id, include_admin),
        "notifications": [n.to_dict() for n in items],
    })


@app.route("/notifications/<int:notification_id>/read", methods=["POST"])
@login_required
def notification_mark_read(notification_id):
    unit_id, include_admin = _notification_context()
    notifications_module.mark_read(notification_id, unit_id, include_admin)
    if request.accept_mimetypes.best == "application/json" or request.is_json:
        return jsonify({"success": True})
    return redirect(request.referrer or url_for("dashboard"))


@app.route("/notifications/read-all", methods=["POST"])
@login_required
def notifications_mark_all_read():
    unit_id, include_admin = _notification_context()
    notifications_module.mark_all_read(unit_id, include_admin)
    if request.accept_mimetypes.best == "application/json" or request.is_json:
        return jsonify({"success": True})
    return redirect(request.referrer or url_for("dashboard"))


@app.route("/api/orders")
@login_required
def api_orders_list():
    """JSON order list, used by the mobile app's Orders screen. /orders (no id)
    renders the full HTML page instead -- kept separate so the web page doesn't
    have to change shape for the mobile client. Scoped to the caller's own
    orders (buyer/seller/admin), same rule as the HTML page and the detail
    endpoint below -- the mobile client shouldn't be able to pull every
    order in the system either."""
    visible_orders = [o for o in orders_module.all_orders() if _can_view_order(o)]
    return jsonify({"success": True, "orders": visible_orders})


@app.route("/api/order/<order_id>")
@login_required
def api_order_detail(order_id):
    """JSON order detail, used by the mobile app (web templates read straight
    from orders_module instead)."""
    order = orders_module.order_by_id(order_id)
    if not order:
        return jsonify({"success": False, "error": "Order not found"}), 404
    if not _can_view_order(order):
        return jsonify({"success": False, "error": "You don't have access to this order"}), 403
    return jsonify({"success": True, "order": order})


if __name__ == "__main__":
    # threaded=True matters here, not just for perf polish: Werkzeug's dev
    # server defaults to handling ONE request at a time when this is off.
    # Every authenticated page fires several requests (the HTML itself, plus
    # style.css, motion.js, and any per-page DB queries via the notification
    # badge context processor) -- without threading those all queue up
    # behind each other instead of running concurrently, which reads as
    # "every page is slow" even though no single request is doing much work.
    # Respects $PORT if a host sets one (Render, Railway, etc. all assign a
    # dynamic port and expect the app to bind to it) -- falls back to 5000
    # for local dev, unchanged from before. This only matters if something
    # runs `python app.py` directly; the real production entry point is
    # gunicorn (see Procfile), which binds its own port independently of
    # this block entirely.
    app.run(debug=True, host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), threaded=True)
