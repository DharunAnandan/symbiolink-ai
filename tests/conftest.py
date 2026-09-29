"""
Shared pytest fixtures for the SymbioLink AI test suite.

Sets FLASK_ENV=testing *before* app.py is ever imported, so the module-level
`with app.app_context(): db.create_all()` block in app.py (which runs as a
side effect of import, not inside a function) creates tables against the
in-memory SQLite testing DB instead of the real dev/prod database. This has
to happen here, in conftest.py, because pytest imports conftest.py before it
imports any test module -- setting the env var inside a test file would
already be too late for tests in *other* files that import app first.

Because the testing DB starts empty, data_access.update_data_imports() (also
called from that same startup block) will find no units in the DB and leave
data.UNITS / data.LISTINGS as the plain in-memory lists from data.py -- which
is exactly what we want for tests: fast, deterministic, no seeding required
to test the matching engine, search, or order flow. Only Flask-Login's
User.query needs real DB rows, which the `seeded_users` fixture below
provides.
"""

import os

os.environ.setdefault("FLASK_ENV", "testing")

import pytest

import app as app_module
import orders as orders_module
from models import db, User, Order, OrderItem, OrderHistory, TrustRating


@pytest.fixture(autouse=True)
def no_real_email(monkeypatch):
    """Never send real mail from the test suite.

    config.py loads the developer's real .env (SMTP credentials included), so
    without this every order/login test would send actual emails through the
    project's Gmail account -- burning its ~500/day sending limit (which is
    exactly what happened) and mailing the admin inbox. Simulation mode logs
    the email instead, and tests that assert on sends patch the methods
    themselves."""
    from email_service import email_service
    monkeypatch.setattr(email_service, "available", False)


@pytest.fixture()
def flask_app():
    """The Flask app configured for testing (in-memory SQLite, TESTING=True)."""
    return app_module.app


@pytest.fixture()
def client(flask_app):
    return flask_app.test_client()


@pytest.fixture()
def seeded_users(flask_app):
    """Create a small set of login-able accounts against real data.py unit ids
    (U1/U2/U3 exist as plain Python data regardless of the DB), rolled back
    after the test.

    Addresses are on symbiolink-test.org rather than the usual example.com
    because otp_service.is_deliverable() classifies example.com as reserved
    and non-routable (RFC 2606), which would make every one of these accounts
    skip the second factor -- see tests/test_otp_login.py. Covers the
    buyer/seller/bystander/admin shape needed for order-visibility tests
    without replaying the full database_migration.py seed script."""
    with flask_app.app_context():
        admin = User(username="test_admin", email="test_admin@symbiolink-test.org", role="admin")
        admin.set_password("pw12345")
        buyer = User(username="test_buyer", email="test_buyer@symbiolink-test.org", role="unit", unit_id="U1")
        buyer.set_password("pw12345")
        seller = User(username="test_seller", email="test_seller@symbiolink-test.org", role="unit", unit_id="U2")
        seller.set_password("pw12345")
        bystander = User(username="test_bystander", email="test_bystander@symbiolink-test.org", role="unit", unit_id="U3")
        bystander.set_password("pw12345")

        db.session.add_all([admin, buyer, seller, bystander])
        db.session.commit()

        yield {"admin": admin, "buyer": buyer, "seller": seller, "bystander": bystander}

        # Clean up so re-running the suite (or later tests) doesn't hit
        # unique-constraint errors on username/email.
        for u in (admin, buyer, seller, bystander):
            db.session.delete(u)
        db.session.commit()


@pytest.fixture(autouse=True)
def clean_orders(flask_app):
    """The live order lifecycle (place/advance/complete/cancel, driven from
    app.py's routes) is actually backed by orders.py's in-memory ORDERS list,
    not the DB Order table below -- see orders.py's own module docstring
    ("Order layer... ORDERS = [] # in-memory order ledger"). The DB Order/
    OrderItem table exists but is write-orphaned (nothing in the live flow
    ever inserts into it -- see the note in app.py's _log_order_history).
    So the actual state that leaks between tests is orders_module.ORDERS,
    not the DB tables; several tests hard-assert the very first order placed
    in a test is "ORD001", which relies on that list being empty at the
    start of every test. OrderHistory/TrustRating *are* real DB tables that
    the live flow writes to (see app.py's _log_order_history and
    trust.TrustLedger.record_exchange), so those still need wiping too.

    Also pushes an app context for the whole test, not just per-request --
    test_orders.py calls orders_module functions directly, with no Flask
    request/test-client involved, so without this those calls would fail
    with "working outside of application context". Children are deleted
    before parents to satisfy FK constraints (order_id -> orders.id) under
    engines that enforce them."""
    def _wipe():
        orders_module.ORDERS.clear()
        OrderHistory.query.delete()
        TrustRating.query.delete()
        OrderItem.query.delete()
        Order.query.delete()
        db.session.commit()

    with flask_app.app_context():
        _wipe()
        yield
        _wipe()


def login(client, username, password="pw12345"):
    return client.post("/login", data={"username": username, "password": password}, follow_redirects=True)
