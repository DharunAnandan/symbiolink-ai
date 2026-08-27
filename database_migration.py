"""
Database migration and seeding script.

Transitions from in-memory data (data.py) to database models.
Run this after setting up the database to populate it with existing synthetic data.
"""

# `data` MUST be imported before `app`. Importing `app` runs its module-level
# `with app.app_context(): data_access.update_data_imports()` block, which --
# if the database already has units/listings in it -- overwrites data.UNITS /
# data.LISTINGS / data.PRICE_TABLE_RS_PER_KG *in place* with whatever's
# currently in the DB (see data_access.update_data_imports()). If `from data
# import UNITS, LISTINGS, ...` ran *after* that, these names would silently
# bind to the DB's already-seeded (and possibly older/smaller) snapshot
# instead of the actual source lists below -- so re-running this script on an
# existing database would seed nothing new no matter how much was added to
# data.py. Importing `data` first captures the real source lists before
# `app`'s import has a chance to mutate them.
from data import UNITS, LISTINGS, PRICE_TABLE_RS_PER_KG, COMPLETED_MATCHES_SEED
from app import app
from models import db, User, Unit, MaterialPrice, Listing, TrustRating, Order, OrderItem, WhatsAppMessage
from werkzeug.security import generate_password_hash


def seed_material_prices():
    """Migrate material price table from data.py."""
    print("Seeding material prices...")
    for material_key, prices in PRICE_TABLE_RS_PER_KG.items():
        existing = MaterialPrice.query.filter_by(material_key=material_key).first()
        if not existing:
            material_price = MaterialPrice(
                material_key=material_key,
                material_name=material_key.replace('_', ' ').title(),
                new_material_price_per_kg=prices['new_material'],
                byproduct_price_per_kg=prices['byproduct']
            )
            db.session.add(material_price)
    db.session.commit()
    print(f"[OK] Material prices seeded: {len(PRICE_TABLE_RS_PER_KG)} materials")


def seed_units():
    """Migrate units from data.py."""
    print("Seeding units...")
    for unit_data in UNITS:
        existing = Unit.query.filter_by(id=unit_data['id']).first()
        if not existing:
            unit = Unit(
                id=unit_data['id'],
                name=unit_data['name'],
                category=unit_data['category'],
                location_x=unit_data['loc'][0],
                location_y=unit_data['loc'][1],
                phone=unit_data.get('phone', '')
            )
            db.session.add(unit)
            
            # Create a corresponding user account for each unit
            username = unit_data['id'].lower()
            email = f"{username}@symbiolink.demo"
            if not User.query.filter_by(username=username).first():
                user = User(
                    username=username,
                    email=email,
                    password_hash=generate_password_hash('demo123'),  # Default password
                    role='unit',
                    unit_id=unit_data['id']
                )
                db.session.add(user)
    
    db.session.commit()
    print(f"[OK] Units seeded: {len(UNITS)} units with user accounts")


def seed_listings():
    """Migrate listings from data.py."""
    print("Seeding listings...")
    for listing_data in LISTINGS:
        unit = Unit.query.filter_by(id=listing_data['unit_id']).first()
        if unit:
            existing = Listing.query.filter_by(
                unit_id=listing_data['unit_id'],
                type=listing_data['type'],
                material=listing_data['material']
            ).first()
            
            if not existing:
                listing = Listing(
                    unit_id=listing_data['unit_id'],
                    type=listing_data['type'],
                    material=listing_data['material'],
                    qty_kg=listing_data['qty_kg'],
                    interval_days=listing_data.get('interval_days', 14)
                )
                db.session.add(listing)
    
    db.session.commit()
    print(f"[OK] Listings seeded: {len(LISTINGS)} listings")


def seed_trust_ratings():
    """Migrate trust ratings from data.py. Guarded so re-running seed_all()
    against an already-seeded database (e.g. after a restart) doesn't double
    up every seeded rating and skew every unit's trust score -- unlike the
    other seed_* functions, this one used to have no guard at all.

    Can't dedupe row-by-row the way the other seed_* functions do (checking
    "does a TrustRating for this (from, to) pair already exist"):
    COMPLETED_MATCHES_SEED intentionally repeats the same (from, to) pair
    several times to represent multiple separate historical exchanges between
    the same two units (e.g. U4/U5 traded three times, each with its own
    rating) -- a per-pair check would treat the 2nd and 3rd exchange as
    "already seeded" and silently drop them. Instead, guard on whether *any*
    seed-origin rating already exists: order_id IS NULL identifies one (a
    real rating from a completed order always sets order_id via
    TrustLedger.record_exchange), so if any turn up, this function has
    already run once against this database and there's nothing left to do.
    """
    print("Seeding trust ratings...")
    already_seeded = TrustRating.query.filter_by(order_id=None).first() is not None
    if already_seeded:
        print("[OK] Trust ratings already seeded, skipping")
        return

    for rating_data in COMPLETED_MATCHES_SEED:
        # From unit rates To unit
        rating1 = TrustRating(
            from_unit_id=rating_data['from'],
            to_unit_id=rating_data['to'],
            rating=rating_data['rating_from']
        )
        db.session.add(rating1)

        # To unit rates From unit
        rating2 = TrustRating(
            from_unit_id=rating_data['to'],
            to_unit_id=rating_data['from'],
            rating=rating_data['rating_to']
        )
        db.session.add(rating2)

    db.session.commit()
    print(f"[OK] Trust ratings seeded: {len(COMPLETED_MATCHES_SEED) * 2} ratings")


def create_admin_user():
    """Create a default admin user."""
    print("Creating admin user...")
    if not User.query.filter_by(username='admin').first():
        admin = User(
            username='admin',
            email='admin@symbiolink.demo',
            password_hash=generate_password_hash('admin123'),
            role='admin'
        )
        db.session.add(admin)
        db.session.commit()
        print("[OK] Admin user created (username: admin, password: admin123)")
    else:
        print("[OK] Admin user already exists")


def seed_all():
    """Run all seeding operations."""
    with app.app_context():
        db.create_all()
        
        try:
            seed_material_prices()
            seed_units()
            seed_listings()
            seed_trust_ratings()
            create_admin_user()
            
            print("\n[SUCCESS] Database migration completed successfully!")
            print("\nDefault login credentials:")
            print("  Admin: admin / admin123")
            print("  Units: u1, u2, u3... / demo123")

        except Exception as e:
            db.session.rollback()
            print(f"\n[ERROR] Migration failed: {e}")
            raise


if __name__ == '__main__':
    seed_all()