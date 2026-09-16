"""
SQLAlchemy ORM models for SymbioLink AI.

Replaces in-memory data structures with proper database models for persistence,
relationships, and data integrity.
"""

from datetime import datetime
from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash

db = SQLAlchemy()


class User(UserMixin, db.Model):
    """User accounts for authentication and authorization."""
    __tablename__ = 'users'
    
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False, default='unit')  # admin, unit, auditor
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Relationship to Unit (if user represents a unit)
    unit_id = db.Column(db.String(20), db.ForeignKey('units.id'), nullable=True)
    unit = db.relationship('Unit', backref='user', uselist=False)
    
    def set_password(self, password):
        self.password_hash = generate_password_hash(password)
    
    def check_password(self, password):
        return check_password_hash(self.password_hash, password)
    
    def __repr__(self):
        return f'<User {self.username}>'


class Unit(db.Model):
    """Industrial units (MSMEs) in the cluster."""
    __tablename__ = 'units'
    
    id = db.Column(db.String(20), primary_key=True)  # U1, U2, etc.
    name = db.Column(db.String(200), nullable=False)
    category = db.Column(db.String(50), nullable=False)
    location_x = db.Column(db.Float, nullable=False)  # X coordinate in km
    location_y = db.Column(db.Float, nullable=False)  # Y coordinate in km
    # Real-world position, pinned on the OpenStreetMap picker at registration
    # (see templates/register_unit.html). Nullable because units seeded
    # before this existed predate it -- geo.coords_for_unit() back-projects
    # location_x/y for those so they still appear on the map. When these ARE
    # set they are the authoritative location and location_x/y is projected
    # from them, so the matching engine keeps working on kilometres exactly
    # as before.
    latitude = db.Column(db.Float, nullable=True)
    longitude = db.Column(db.Float, nullable=True)
    phone = db.Column(db.String(20), nullable=True)
    # Contact email for this company -- captured on "Register a Listing"
    # (see app.py::register_unit()), used by email_service.py so a unit that
    # has no separate login account can still receive order/match/dispute
    # notification emails. See app.py's startup block for the ALTER TABLE
    # step that adds this column to a pre-existing dev database file.
    email = db.Column(db.String(120), nullable=True)
    address = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Relationships
    listings = db.relationship('Listing', backref='unit', lazy=True, cascade='all, delete-orphan')
    trust_scores_received = db.relationship('TrustRating', foreign_keys='TrustRating.to_unit_id', backref='rated_unit', lazy=True)
    trust_scores_given = db.relationship('TrustRating', foreign_keys='TrustRating.from_unit_id', backref='rating_unit', lazy=True)
    
    @property
    def loc(self):
        """Return location as tuple for compatibility with existing code."""
        return (self.location_x, self.location_y)

    @property
    def latlng(self):
        """Real-world (lat, lng) for mapping, or None if unmappable.
        Falls back to back-projecting location_x/y -- see geo.py."""
        import geo
        return geo.coords_for_unit(self)

    @property
    def trust_score(self):
        """Calculate current trust score from ratings."""
        ratings = [r.rating for r in self.trust_scores_received]
        if not ratings:
            return 3.5  # Default neutral score
        return round(sum(ratings) / len(ratings), 2)
    
    def __repr__(self):
        return f'<Unit {self.id}: {self.name}>'


class MaterialPrice(db.Model):
    """Reference prices for materials (new/freshly-manufactured vs byproduct)."""
    __tablename__ = 'material_prices'

    id = db.Column(db.Integer, primary_key=True)
    material_key = db.Column(db.String(100), unique=True, nullable=False)
    material_name = db.Column(db.String(200), nullable=False)
    # Column name kept as "virgin_price_per_kg" in the DB itself so this maps onto
    # the existing table on already-seeded databases without a schema migration --
    # only the Python-facing attribute name changed.
    new_material_price_per_kg = db.Column("virgin_price_per_kg", db.Float, nullable=False)
    byproduct_price_per_kg = db.Column(db.Float, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    def __repr__(self):
        return f'<MaterialPrice {self.material_key}>'


class Listing(db.Model):
    """Waste or need listings posted by units."""
    __tablename__ = 'listings'
    
    id = db.Column(db.Integer, primary_key=True)
    unit_id = db.Column(db.String(20), db.ForeignKey('units.id'), nullable=False)
    type = db.Column(db.String(10), nullable=False)  # 'waste' or 'need'
    material = db.Column(db.String(100), nullable=False)
    qty_kg = db.Column(db.Float, nullable=False)
    interval_days = db.Column(db.Integer, default=14)  # For predictive layer
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Time window fields for improved matching
    available_from = db.Column(db.DateTime, nullable=True)  # When waste becomes available
    available_until = db.Column(db.DateTime, nullable=True)  # When waste is no longer available
    need_deadline = db.Column(db.DateTime, nullable=True)  # When material is needed by
    
    # Relationships
    order_items = db.relationship('OrderItem', backref='listing', lazy=True)
    
    def __repr__(self):
        return f'<Listing {self.type} {self.material} {self.qty_kg}kg>'


class TrustRating(db.Model):
    """Trust ratings exchanged between units after completed transactions."""
    __tablename__ = 'trust_ratings'
    
    id = db.Column(db.Integer, primary_key=True)
    from_unit_id = db.Column(db.String(20), db.ForeignKey('units.id'), nullable=False)
    to_unit_id = db.Column(db.String(20), db.ForeignKey('units.id'), nullable=False)
    rating = db.Column(db.Integer, nullable=False)  # 1-5 stars
    order_id = db.Column(db.Integer, db.ForeignKey('orders.id'), nullable=True)
    comments = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    def __repr__(self):
        return f'<TrustRating {self.from_unit_id}->{self.to_unit_id}: {self.rating}>'


class Order(db.Model):
    """Purchase orders with full lifecycle tracking."""
    __tablename__ = 'orders'
    
    id = db.Column(db.Integer, primary_key=True)
    order_number = db.Column(db.String(20), unique=True, nullable=False)
    seller_unit_id = db.Column(db.String(20), db.ForeignKey('units.id'), nullable=False)
    buyer_unit_id = db.Column(db.String(20), db.ForeignKey('units.id'), nullable=False)
    # Denormalized display names, captured at order-placement time -- mirrors
    # what orders.py's old in-memory dict stored, so a unit renaming itself
    # later doesn't rewrite the label on someone's past order history.
    seller_name = db.Column(db.String(200), nullable=True)
    buyer_name = db.Column(db.String(200), nullable=True)
    material = db.Column(db.String(100), nullable=False)
    total_qty_kg = db.Column(db.Float, nullable=False)
    total_price_rs = db.Column(db.Float, nullable=True)
    co2_saved_kg = db.Column(db.Float, nullable=True, default=0)
    status = db.Column(db.String(20), default='placed')  # placed, confirmed, picked_up, delivered, completed, cancelled
    payment_status = db.Column(db.String(20), default='pending')  # pending, paid, refunded
    payment_id = db.Column(db.String(100), nullable=True)  # Razorpay payment ID
    razorpay_order_id = db.Column(db.String(100), nullable=True)
    payment_amount = db.Column(db.Float, nullable=True)
    rating_buyer_to_seller = db.Column(db.Integer, nullable=True)
    rating_seller_to_buyer = db.Column(db.Integer, nullable=True)
    # Dispute lifecycle
    dispute_status = db.Column(db.String(30), nullable=True)  # None | open | resolved_refunded | resolved_rejected
    dispute_reason = db.Column(db.Text, nullable=True)
    dispute_raised_by = db.Column(db.String(100), nullable=True)
    dispute_raised_at = db.Column(db.DateTime, nullable=True)
    dispute_resolved_by = db.Column(db.String(100), nullable=True)
    dispute_resolved_at = db.Column(db.DateTime, nullable=True)
    dispute_resolution_notes = db.Column(db.Text, nullable=True)
    # Cancellation
    cancellation_reason = db.Column(db.Text, nullable=True)
    cancelled_by = db.Column(db.String(100), nullable=True)
    cancelled_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    items = db.relationship('OrderItem', backref='order', lazy=True, cascade='all, delete-orphan')
    history = db.relationship('OrderHistory', backref='order', lazy=True, cascade='all, delete-orphan')
    ratings = db.relationship('TrustRating', backref='order', lazy=True)
    
    @property
    def seller(self):
        return Unit.query.get(self.seller_unit_id)
    
    @property
    def buyer(self):
        return Unit.query.get(self.buyer_unit_id)
    
    def __repr__(self):
        return f'<Order {self.order_number}: {self.status}>'


class OrderItem(db.Model):
    """Individual items within an order."""
    __tablename__ = 'order_items'
    
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey('orders.id'), nullable=False)
    listing_id = db.Column(db.Integer, db.ForeignKey('listings.id'), nullable=True)
    material = db.Column(db.String(100), nullable=False)
    qty_kg = db.Column(db.Float, nullable=False)
    price_per_kg = db.Column(db.Float, nullable=True)
    total_price = db.Column(db.Float, nullable=True)
    
    def __repr__(self):
        return f'<OrderItem {self.material} {self.qty_kg}kg>'


class OrderHistory(db.Model):
    """Order status change history for audit trail."""
    __tablename__ = 'order_history'
    
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey('orders.id'), nullable=False)
    status = db.Column(db.String(20), nullable=False)
    changed_by = db.Column(db.String(100), nullable=True)  # User who made the change
    notes = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    def __repr__(self):
        return f'<OrderHistory {self.order_id}: {self.status}>'


class WhatsAppMessage(db.Model):
    """Log of WhatsApp messages for audit and processing."""
    __tablename__ = 'whatsapp_messages'
    
    id = db.Column(db.Integer, primary_key=True)
    phone_number = db.Column(db.String(20), nullable=False)
    message_body = db.Column(db.Text, nullable=False)
    direction = db.Column(db.String(10), nullable=False)  # inbound or outbound
    processed = db.Column(db.Boolean, default=False)
    listing_created = db.Column(db.Boolean, default=False)
    error_message = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    def __repr__(self):
        return f'<WhatsAppMessage {self.phone_number}: {self.direction}>'


class Notification(db.Model):
    """In-app notifications for the bell/inbox UI -- 'new match found', order
    status changes, disputes opened/resolved, etc. Targeted either at a single
    unit (unit_id set, audience='unit') or broadcast to every admin
    (unit_id=None, audience='admin'), mirroring how acting_as/admin already
    split every other permission check in this app."""
    __tablename__ = 'notifications'

    id = db.Column(db.Integer, primary_key=True)
    unit_id = db.Column(db.String(20), db.ForeignKey('units.id'), nullable=True)
    audience = db.Column(db.String(10), nullable=False, default='unit')  # 'unit' or 'admin'
    type = db.Column(db.String(20), nullable=False, default='info')  # match, order, dispute, payment
    title = db.Column(db.String(200), nullable=False)
    message = db.Column(db.Text, nullable=True)
    link = db.Column(db.String(300), nullable=True)
    is_read = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        seconds = (datetime.utcnow() - self.created_at).total_seconds()
        if seconds < 60:
            relative = "just now"
        elif seconds < 3600:
            relative = f"{int(seconds // 60)}m ago"
        elif seconds < 86400:
            relative = f"{int(seconds // 3600)}h ago"
        else:
            relative = f"{int(seconds // 86400)}d ago"
        return {
            "id": self.id,
            "type": self.type,
            "title": self.title,
            "message": self.message,
            "link": self.link,
            "is_read": self.is_read,
            "relative_time": relative,
            "created_at": self.created_at.strftime("%b %d, %H:%M"),
        }

    def __repr__(self):
        return f'<Notification {self.id} [{self.audience}] {self.title}>'


class BinSensor(db.Model):
    """A simulated IoT bin-monitoring device: a load cell (weight) + an
    ultrasonic sensor (fill level) wired to an ESP32, sitting under/inside one
    unit's waste collection bin for one material. See iot_sensors.py for the
    simulation and auto-listing logic that reads/writes this table."""
    __tablename__ = 'bin_sensors'

    id = db.Column(db.Integer, primary_key=True)
    device_id = db.Column(db.String(40), unique=True, nullable=False)  # e.g. "ESP32-U1-MET-01"
    unit_id = db.Column(db.String(20), db.ForeignKey('units.id'), nullable=False)
    material = db.Column(db.String(100), nullable=False)
    capacity_kg = db.Column(db.Float, nullable=False, default=250.0)
    fill_threshold_pct = db.Column(db.Float, nullable=False, default=80.0)
    last_weight_kg = db.Column(db.Float, nullable=False, default=0.0)
    last_fill_pct = db.Column(db.Float, nullable=False, default=0.0)
    last_reading_at = db.Column(db.DateTime, nullable=True)
    listings_auto_created = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    readings = db.relationship('SensorReading', backref='sensor', lazy=True, cascade='all, delete-orphan',
                                order_by='SensorReading.created_at.desc()')

    def __repr__(self):
        return f'<BinSensor {self.device_id}: {self.last_fill_pct:.0f}% full>'


class SensorReading(db.Model):
    """One weight/fill-level sample from a BinSensor -- the raw log an ESP32
    would stream over WiFi. Kept even after a bin auto-lists/resets, so this
    table doubles as the real, measured posting-history data
    predictive.py's forecaster would eventually train on instead of
    data.seed_listing_history()'s synthetic timestamps."""
    __tablename__ = 'sensor_readings'

    id = db.Column(db.Integer, primary_key=True)
    sensor_id = db.Column(db.Integer, db.ForeignKey('bin_sensors.id'), nullable=False)
    weight_kg = db.Column(db.Float, nullable=False)
    fill_pct = db.Column(db.Float, nullable=False)
    triggered_listing = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f'<SensorReading sensor={self.sensor_id} {self.weight_kg}kg {self.fill_pct:.0f}%>'


class CarbonCredit(db.Model):
    """A tradeable voluntary-carbon-credit-style record, minted from a unit's
    own verified (completed, two-way-rated) exchange history -- see
    carbon_credits.py for how the CO2e-avoided figure a credit represents is
    computed and why cumulative already-issued tCO2e is tracked here (in this
    table) rather than by flagging individual orders."""
    __tablename__ = 'carbon_credits'

    id = db.Column(db.Integer, primary_key=True)
    unit_id = db.Column(db.String(20), db.ForeignKey('units.id'), nullable=False)
    tco2e = db.Column(db.Float, nullable=False)  # whole tonnes CO2e represented by this credit
    source_order_count = db.Column(db.Integer, default=0)
    source_note = db.Column(db.Text, nullable=True)
    price_rs_per_tonne = db.Column(db.Float, nullable=True)
    status = db.Column(db.String(20), default='available')  # available, listed, sold
    buyer_name = db.Column(db.String(200), nullable=True)
    listed_at = db.Column(db.DateTime, nullable=True)
    sold_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f'<CarbonCredit {self.id}: {self.tco2e}tCO2e [{self.status}]>'


class SystemConfig(db.Model):
    """System-wide configuration parameters."""
    __tablename__ = 'system_config'
    
    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(100), unique=True, nullable=False)
    value = db.Column(db.Text, nullable=False)
    description = db.Column(db.Text, nullable=True)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    def __repr__(self):
        return f'<SystemConfig {self.key}={self.value}>'


class LoginOtp(db.Model):
    """A one-time code issued during two-factor login.

    Only the HASH of the code is stored, never the code itself -- the same
    reasoning as User.password_hash. Anyone with read access to this table
    (a leaked backup, a SQL-injection hole elsewhere) would otherwise be able
    to complete a login for any account that happens to have a live code, and
    a 6-digit code is trivially reversible from plaintext storage.

    Rows are deliberately kept after use rather than deleted: consumed_at and
    attempts turn this table into an audit trail of second-factor activity,
    which is exactly what you want to look at after a suspected account
    compromise. otp_service.purge_expired() trims the old ones.
    """
    __tablename__ = 'login_otps'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    code_hash = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    expires_at = db.Column(db.DateTime, nullable=False)
    # Set the moment a code is successfully used, so the same code can never
    # be replayed -- checked explicitly in otp_service.verify().
    consumed_at = db.Column(db.DateTime, nullable=True)
    # Wrong guesses against THIS code. A 6-digit code is only a million
    # possibilities, which is nothing to a script, so the code is burned after
    # MAX_ATTEMPTS rather than left open until it expires.
    attempts = db.Column(db.Integer, default=0, nullable=False)
    # Where the code was requested from -- recorded for the audit trail, not
    # used as a security check (an attacker controls their own source IP, and
    # legitimate users move between networks mid-login).
    ip_address = db.Column(db.String(64), nullable=True)

    # delete-orphan: a deleted account's codes go with it. Without the
    # cascade SQLAlchemy tries to NULL out user_id instead, which trips the
    # NOT NULL constraint and makes users undeletable once they have ever
    # logged in -- and a live code outliving its account would be a dangling
    # credential for an account that no longer exists.
    user = db.relationship(
        'User',
        backref=db.backref('login_otps', cascade='all, delete-orphan'),
    )

    @property
    def is_live(self):
        """Usable right now: not already used, not expired, not burned.

        MAX_ATTEMPTS is imported lazily from otp_service rather than repeated
        here -- otp_service is the authority on the policy and does the
        authoritative check itself, and a second hardcoded copy of the limit
        would silently drift the day someone tunes it. The import is inside
        the property because otp_service imports this module."""
        from otp_service import MAX_ATTEMPTS
        return (self.consumed_at is None
                and self.attempts < MAX_ATTEMPTS
                and datetime.utcnow() < self.expires_at)

    def __repr__(self):
        state = 'consumed' if self.consumed_at else ('expired' if datetime.utcnow() >= self.expires_at else 'live')
        return f'<LoginOtp user={self.user_id} [{state}]>'