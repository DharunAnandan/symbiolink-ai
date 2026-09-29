"""
Data access layer for SymbioLink AI.

Bridges between the old in-memory data structures and the new database models.
Gradually replaces functions in data.py with database-backed versions.
"""

from models import db, Unit, Listing, MaterialPrice, TrustRating, User, Order, WhatsAppMessage, OrderHistory
from data import PRICE_TABLE_RS_PER_KG, TRANSPORT_RS_PER_KM_SOLO, TRANSPORT_RS_PER_KM_POOLED
from config import Config

# Frozen references to the ORIGINAL in-memory implementations in data.py, captured at
# import time (before update_data_imports() ever runs) under different names.
#
# This module's own DB-backed functions below (get_unit_by_id, add_unit_to_db, etc.)
# get assigned onto data.unit_by_id / data.add_unit / etc. by update_data_imports() --
# so once that's happened, data.unit_by_id (for example) no longer points at data.py's
# original function, it points back at get_unit_by_id itself. Each DB-backed function's
# except-block fallback used to do a call-time `from data import unit_by_id`, which
# resolves the CURRENT value of that attribute -- after update_data_imports() has run,
# that's get_unit_by_id again, so hitting the fallback path called itself and recursed
# until RecursionError (reproduced: any request that renders acting_as_unit for a
# session unit not found via Unit.query.get crashed the page with a 500). Using these
# _mem_* aliases instead guarantees the fallback always reaches the real in-memory
# implementation, never a reassigned reference to itself.
from data import (
    unit_by_id as _mem_unit_by_id,
    add_unit as _mem_add_unit,
    add_listing as _mem_add_listing,
    reduce_listing_qty as _mem_reduce_listing_qty,
    find_listing as _mem_find_listing,
    update_listing_qty_absolute as _mem_update_listing_qty_absolute,
    remove_listing as _mem_remove_listing,
)

def delete_user_account(user):
    """Permanently delete a user account and, if nobody else uses it, the
    company (unit) it belongs to, together with everything that company
    created: listings, orders, ratings, notifications, sensors and credits.

    Deletes children before parents in foreign-key order so it also works on
    Postgres, which (unlike SQLite by default) enforces the constraints.
    Also purges the in-memory copies (data.UNITS / data.LISTINGS /
    orders.ORDERS) so the running app stops showing the company at once.

    Returns a dict of what was removed.
    """
    import data
    import orders as orders_module
    from models import (Notification, BinSensor, CarbonCredit, OrderItem,
                        LoginOtp)

    removed = {"user": user.username, "unit": None}
    unit_id = user.unit_id
    other_users = 0
    if unit_id:
        other_users = User.query.filter(User.unit_id == unit_id, User.id != user.id).count()

    LoginOtp.query.filter_by(user_id=user.id).delete(synchronize_session=False)
    user.unit_id = None
    db.session.delete(user)
    db.session.flush()

    if unit_id and other_users == 0:
        either_side = db.or_(Order.seller_unit_id == unit_id, Order.buyer_unit_id == unit_id)
        order_ids = [o.id for o in Order.query.filter(either_side).all()]
        TrustRating.query.filter(db.or_(
            TrustRating.from_unit_id == unit_id,
            TrustRating.to_unit_id == unit_id,
            TrustRating.order_id.in_(order_ids) if order_ids else db.false(),
        )).delete(synchronize_session=False)
        for o in Order.query.filter(either_side).all():
            db.session.delete(o)  # items + history cascade

        listing_ids = [l.id for l in Listing.query.filter_by(unit_id=unit_id).all()]
        if listing_ids:
            OrderItem.query.filter(OrderItem.listing_id.in_(listing_ids)).update(
                {OrderItem.listing_id: None}, synchronize_session=False)
        Listing.query.filter_by(unit_id=unit_id).delete(synchronize_session=False)
        Notification.query.filter_by(unit_id=unit_id).delete(synchronize_session=False)
        for sensor in BinSensor.query.filter_by(unit_id=unit_id).all():
            db.session.delete(sensor)  # readings cascade
        CarbonCredit.query.filter_by(unit_id=unit_id).delete(synchronize_session=False)
        unit = db.session.get(Unit, unit_id)
        if unit:
            db.session.delete(unit)
        removed["unit"] = unit_id

        data.UNITS[:] = [u for u in data.UNITS if u.get("id") != unit_id]
        data.LISTINGS[:] = [l for l in data.LISTINGS if l.get("unit_id") != unit_id]
        orders_module.ORDERS[:] = [
            o for o in orders_module.ORDERS
            if unit_id not in (o.get("seller_unit_id"), o.get("buyer_unit_id"))
        ]

    db.session.commit()
    return removed


# Compatibility layer - provide functions that work with both old and new data
def get_units():
    """Get all units - tries database first, falls back to in-memory."""
    try:
        units = Unit.query.all()
        if units:
            return [
                {
                    "id": u.id,
                    "name": u.name,
                    "category": u.category,
                    "loc": (u.location_x, u.location_y),
                    "phone": u.phone or "",
                    "email": u.email or "",
                    "latitude": u.latitude,
                    "longitude": u.longitude
                }
                for u in units
            ]
    except:
        pass
    # Fallback to in-memory data
    from data import UNITS
    return UNITS

def get_listings():
    """Get all listings - tries database first, falls back to in-memory."""
    try:
        listings = Listing.query.filter_by(is_active=True).all()
        if listings:
            return [
                {
                    "unit_id": l.unit_id,
                    "type": l.type,
                    "material": l.material,
                    "qty_kg": l.qty_kg,
                    "interval_days": l.interval_days
                }
                for l in listings
            ]
    except:
        pass
    # Fallback to in-memory data
    from data import LISTINGS
    return LISTINGS

def get_unit_by_id(unit_id):
    """Get unit by ID - tries database first, falls back to in-memory."""
    try:
        unit = Unit.query.get(unit_id)
        if unit:
            return {
                "id": unit.id,
                "name": unit.name,
                "category": unit.category,
                "loc": (unit.location_x, unit.location_y),
                "phone": unit.phone or "",
                "email": unit.email or "",
                "latitude": unit.latitude,
                "longitude": unit.longitude
            }
    except:
        pass
    # Fallback to in-memory data
    return _mem_unit_by_id(unit_id)

def get_price_table():
    """Get price table - tries database first, falls back to in-memory."""
    try:
        prices = MaterialPrice.query.all()
        if prices:
            return {
                p.material_key: {
                    "new_material": p.new_material_price_per_kg,
                    "byproduct": p.byproduct_price_per_kg
                }
                for p in prices
            }
    except:
        pass
    # Fallback to in-memory data
    return PRICE_TABLE_RS_PER_KG

def get_transport_rates():
    """Get transport rates from config."""
    return {
        "solo": Config.TRANSPORT_RS_PER_KM_SOLO,
        "pooled": Config.TRANSPORT_RS_PER_KM_POOLED
    }

def get_known_materials():
    """Get list of known materials."""
    try:
        materials = MaterialPrice.query.all()
        if materials:
            return sorted([m.material_key for m in materials])
    except:
        pass
    from data import KNOWN_MATERIALS
    return KNOWN_MATERIALS

def get_known_categories():
    """Get list of known categories."""
    try:
        categories = db.session.query(Unit.category).distinct().all()
        if categories:
            return sorted([c[0] for c in categories])
    except:
        pass
    from data import KNOWN_CATEGORIES
    return KNOWN_CATEGORIES

def add_unit_to_db(name, category, loc, phone="", email="", latitude=None, longitude=None):
    """Add a new unit to the database, keeping the in-memory data.UNITS cache in sync
    so it shows up immediately in search/matches/stats -- data.UNITS is a startup
    snapshot of the database, not a live query, so writes here must mirror into it.

    Two units registered close together used to be able to land on the SAME
    generated id (reproduced live: "Test Verify Unit" and "Delete Test Unit" both
    got "U14"). That's a classic read-max-then-insert race: _generate_unit_id()
    reads the current max id, but nothing stops a second call from reading that
    same max before the first insert has committed. A duplicate unit id doesn't
    fail loudly -- vis.DataSet() on the Matching Engine page throws
    "Cannot add item: item with id U14 already exists" while building the node
    list, which aborts that whole inline <script> before it ever calls
    new vis.Network(...), so the graph silently renders as an empty box with no
    visible error. Retrying with a freshly regenerated id on a collision (instead
    of only generating it once) closes that race regardless of what caused it.
    Also returns the existing unit for a repeat submission of the same company
    name, matching data.add_unit()'s dedup behavior, which this DB path had been
    missing -- so double-submitting the registration form no longer creates two
    full duplicate company records with two different ids."""
    from models import Unit as UnitModel
    existing = UnitModel.query.filter(db.func.lower(UnitModel.name) == name.strip().lower()).first()
    if existing:
        return {
            "id": existing.id, "name": existing.name, "category": existing.category,
            "loc": (existing.location_x, existing.location_y), "phone": existing.phone or "",
            "email": existing.email or "",
            "latitude": existing.latitude, "longitude": existing.longitude,
        }

    last_error = None
    for _attempt in range(5):
        try:
            unit = Unit(
                id=_generate_unit_id(),
                name=name,
                category=category,
                location_x=loc[0],
                location_y=loc[1],
                phone=phone,
                email=email,
                latitude=latitude,
                longitude=longitude,
            )
            db.session.add(unit)
            db.session.commit()
            unit_dict = {
                "id": unit.id,
                "name": unit.name,
                "category": unit.category,
                "loc": (unit.location_x, unit.location_y),
                "phone": unit.phone or "",
                "email": unit.email or "",
                "latitude": unit.latitude,
                "longitude": unit.longitude
            }
            import data
            data.UNITS.append(unit_dict)
            return unit_dict
        except Exception as e:
            db.session.rollback()
            last_error = e
            # Only a ready-to-retry id collision should loop again; any other failure
            # falls straight through to the in-memory fallback below.
            if "UNIQUE" not in str(e).upper() and "already exists" not in str(e):
                break
    print(f"Error adding unit to database: {last_error}")
    # Fallback to in-memory
    return _mem_add_unit(name, category, loc, phone, email, latitude, longitude)

def add_listing_to_db(unit_id, listing_type, material, qty_kg, interval_days=14):
    """Add a new listing to the database, keeping the in-memory data.LISTINGS cache and
    reference price table in sync. A brand-new material gets a starter reference price
    (both a MaterialPrice row and the data.PRICE_TABLE_RS_PER_KG cache entry), matching
    the in-memory data.add_listing's behavior described to the user on the listing form."""
    material_key = material.strip().lower().replace(" ", "_").replace("-", "_")
    try:
        listing = Listing(
            unit_id=unit_id,
            type=listing_type,
            material=material_key,
            qty_kg=qty_kg,
            interval_days=interval_days
        )
        db.session.add(listing)

        if not MaterialPrice.query.filter_by(material_key=material_key).first():
            db.session.add(MaterialPrice(
                material_key=material_key,
                material_name=material_key.replace("_", " ").title(),
                new_material_price_per_kg=50,
                byproduct_price_per_kg=15,
            ))

        db.session.commit()
        listing_dict = {
            "unit_id": listing.unit_id,
            "type": listing.type,
            "material": listing.material,
            "qty_kg": listing.qty_kg,
            "interval_days": listing.interval_days
        }
        import data
        data.LISTINGS.append(listing_dict)
        data.PRICE_TABLE_RS_PER_KG.setdefault(material_key, {"new_material": 50, "byproduct": 15})
        return listing_dict
    except Exception as e:
        db.session.rollback()
        print(f"Error adding listing to database: {e}")
        # Fallback to in-memory
        return _mem_add_listing(unit_id, listing_type, material, qty_kg, interval_days)

def reduce_listing_qty_in_db(unit_id, material, qty_kg):
    """Reduce a unit's WASTE listing quantity in the database, keeping the in-memory
    data.LISTINGS cache in sync."""
    try:
        listing = Listing.query.filter_by(
            unit_id=unit_id,
            material=material,
            type='waste',
            is_active=True
        ).first()

        if listing:
            deducted = min(listing.qty_kg, qty_kg)
            listing.qty_kg -= deducted
            now_inactive = listing.qty_kg <= 0
            if now_inactive:
                listing.is_active = False
            db.session.commit()

            import data
            cached = next((l for l in data.LISTINGS if l["unit_id"] == unit_id
                           and l["material"] == material and l["type"] == "waste"), None)
            if cached:
                if now_inactive:
                    data.LISTINGS.remove(cached)
                else:
                    cached["qty_kg"] = listing.qty_kg

            return deducted
        return 0
    except Exception as e:
        db.session.rollback()
        print(f"Error reducing listing quantity: {e}")
        # Fallback to in-memory
        return _mem_reduce_listing_qty(unit_id, material, qty_kg)

def find_listing_in_db(unit_id, material, listing_type=None):
    """Find a listing in the database.

    BUG FIX: every sibling function in this file (get_unit_by_id,
    get_price_table, get_known_materials, ...) falls through to its _mem_*
    in-memory counterpart whenever the DB query comes back empty, not just on
    an exception -- exactly because get_units()/get_listings() themselves
    fall back to the plain in-memory data.UNITS/data.LISTINGS whenever the DB
    is empty, which is enough for update_data_imports()'s `if units and
    listings:` guard to switch every data.* accessor over to its DB-backed
    version even though the actual Listing table has zero rows (a fresh
    clone before database_migration.py has been run, or the in-memory
    ':memory:' SQLite this app's own TestingConfig uses). This function used
    to `return None` the moment the DB query found nothing, instead of
    falling through the way every other accessor here does -- so a listing
    that genuinely exists in data.LISTINGS (visible on search/unit-detail/
    everywhere that reads data.LISTINGS directly) would 404 as "Listing not
    found" the moment a user tried to edit or delete it, purely because its
    row was never persisted to the DB table specifically. Falling through to
    _mem_find_listing on a "not found" result, not just on an exception,
    fixes that."""
    try:
        query = Listing.query.filter_by(unit_id=unit_id, material=material)
        if listing_type:
            query = query.filter_by(type=listing_type)
        listing = query.filter_by(is_active=True).first()

        if listing:
            return {
                "unit_id": listing.unit_id,
                "type": listing.type,
                "material": listing.material,
                "qty_kg": listing.qty_kg,
                "interval_days": listing.interval_days
            }
    except Exception as e:
        print(f"Error finding listing: {e}")
    # Fallback to in-memory -- reached both on a DB error above AND on a
    # clean "no matching row" result, per this function's docstring.
    return _mem_find_listing(unit_id, material, listing_type)

def update_listing_qty_in_db(unit_id, material, listing_type, new_qty_kg):
    """Update a listing's quantity in the database, keeping the in-memory
    data.LISTINGS cache in sync.

    BUG FIX: same "not found" vs. "error" gap as find_listing_in_db above --
    used to `return None` the instant the DB had no matching row, instead of
    falling through to _mem_update_listing_qty_absolute() the way every
    other accessor in this file falls back on a query that comes up empty.
    A listing that only exists in data.LISTINGS (not yet persisted to the
    DB) used to silently fail to update via this path."""
    try:
        listing = Listing.query.filter_by(
            unit_id=unit_id,
            material=material,
            type=listing_type,
            is_active=True
        ).first()

        if listing:
            listing.qty_kg = new_qty_kg
            db.session.commit()

            import data
            cached = next((l for l in data.LISTINGS if l["unit_id"] == unit_id
                           and l["material"] == material and l["type"] == listing_type), None)
            if cached:
                cached["qty_kg"] = new_qty_kg

            return {
                "unit_id": listing.unit_id,
                "type": listing.type,
                "material": listing.material,
                "qty_kg": listing.qty_kg,
                "interval_days": listing.interval_days
            }
    except Exception as e:
        db.session.rollback()
        print(f"Error updating listing quantity: {e}")
    # Fallback to in-memory -- reached both on a DB error above AND on a
    # clean "no matching row" result, per this function's docstring.
    return _mem_update_listing_qty_absolute(unit_id, material, listing_type, new_qty_kg)

def remove_listing_in_db(unit_id, material, listing_type):
    """Remove a listing from the database, keeping the in-memory data.LISTINGS cache
    in sync.

    BUG FIX: same "not found" vs. "error" gap as find_listing_in_db above --
    used to `return False` the instant the DB had no matching row, instead of
    falling through to _mem_remove_listing() the way every other accessor in
    this file falls back on a query that comes up empty. A listing that only
    exists in data.LISTINGS used to silently fail to delete via this path."""
    try:
        listing = Listing.query.filter_by(
            unit_id=unit_id,
            material=material,
            type=listing_type,
            is_active=True
        ).first()

        if listing:
            listing.is_active = False
            db.session.commit()

            import data
            cached = next((l for l in data.LISTINGS if l["unit_id"] == unit_id
                           and l["material"] == material and l["type"] == listing_type), None)
            if cached:
                data.LISTINGS.remove(cached)

            return True
    except Exception as e:
        db.session.rollback()
        print(f"Error removing listing: {e}")
    # Fallback to in-memory -- reached both on a DB error above AND on a
    # clean "no matching row" result, per this function's docstring.
    return _mem_remove_listing(unit_id, material, listing_type)

def _generate_unit_id():
    """Generate the next unit ID. Scans every existing numeric suffix rather than
    ordering by Unit.id DESC -- that's a string sort where 'U9' sorts after 'U10'/'U11'/
    'U12' (since '9' > '1' as the first differing character), so it previously returned
    'U9' as the "last" unit past U9 and generated a duplicate 'U10' id.

    Checks BOTH the DB and the in-memory data.UNITS cache and skips past every id
    already taken in either -- not just max()+1 from one source. A unit that only
    ever made it into data.UNITS (e.g. a prior call that hit add_unit_to_db's
    in-memory fallback) wouldn't show up in a DB-only query, so max()+1 from the DB
    alone could still hand out an id that's actually already in use in memory."""
    import data
    try:
        ids = set(row[0] for row in db.session.query(Unit.id).all())
    except Exception:
        ids = set()
    ids.update(u["id"] for u in data.UNITS)

    nums = [int(i[1:]) for i in ids if i.startswith('U') and i[1:].isdigit()]
    next_num = (max(nums) + 1) if nums else 1
    candidate = f"U{next_num}"
    while candidate in ids:
        next_num += 1
        candidate = f"U{next_num}"
    return candidate

# Update the data module imports to use these functions
def update_data_imports():
    """Update the data module to use database-backed functions."""
    import data
    try:
        # Only update if database has data
        units = get_units()
        listings = get_listings()
        
        if units and listings:
            data.UNITS = units
            data.LISTINGS = listings
            data.PRICE_TABLE_RS_PER_KG = get_price_table()
            data.KNOWN_MATERIALS = get_known_materials()
            data.KNOWN_CATEGORIES = get_known_categories()
            data.unit_by_id = get_unit_by_id
            data.add_unit = add_unit_to_db
            data.add_listing = add_listing_to_db
            data.reduce_listing_qty = reduce_listing_qty_in_db
            data.find_listing = find_listing_in_db
            data.update_listing_qty_absolute = update_listing_qty_in_db
            data.remove_listing = remove_listing_in_db
            print("[OK] Data imports updated to use database")
        else:
            print("[INFO] Database is empty, keeping in-memory data")
    except Exception as e:
        print(f"Warning: Could not update data imports: {e}")
        # Keep original in-memory data as fallback