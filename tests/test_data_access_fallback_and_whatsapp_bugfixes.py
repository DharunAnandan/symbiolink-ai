"""
Regression tests for two real bugs found during a full-project functional
audit (HTTP-level smoke testing beyond what the existing suite exercised,
since nothing previously called edit_listing/delete_listing or the WhatsApp
webhook's unregistered-number path).

1. data_access.py's find_listing_in_db / update_listing_qty_in_db /
   remove_listing_in_db returned None/False the moment a DB query came up
   empty, instead of falling through to their _mem_* in-memory fallback the
   way every sibling accessor in the file does (get_unit_by_id, etc). Because
   update_data_imports()'s `if units and listings:` guard is always true
   (get_units()/get_listings() themselves already fall back to non-empty
   in-memory data), data.find_listing gets switched to the DB-backed version
   even against a genuinely empty Listing table -- which is exactly this
   app's own ':memory:' SQLite TestingConfig. The result: editing or
   deleting a listing that only exists in data.LISTINGS (e.g. any seed
   listing, or one created before database_migration.py has run) 404'd as
   "Listing not found" even though it's visible everywhere else in the app.

2. whatsapp_service.py's receive_message() called
   MessagingResponse().body(...) for the unregistered-phone-number reply --
   .body() isn't part of Twilio's TwiML API -- crashing the public,
   unauthenticated /whatsapp/webhook route with an unhandled AttributeError
   (500) for any message from a number that isn't on file.
"""

import app as app_module
import data
from conftest import login


# ---------------------------------------------------------------------------
# data_access.py DB-empty fallback (find_listing / update / remove)
# ---------------------------------------------------------------------------

def test_edit_listing_get_works_for_a_seed_listing_with_no_db_row(client, seeded_users):
    """U1's metal_shavings waste listing exists only in data.LISTINGS (the
    testing DB's Listing table is empty) -- edit_listing must still find it
    via the in-memory fallback instead of 404ing."""
    login(client, "test_buyer")
    client.post("/act-as", data={"unit_id": "U1"})

    resp = client.get("/listings/U1/metal_shavings/edit?type=waste")
    assert resp.status_code == 200


def test_edit_listing_post_updates_qty_for_a_seed_listing(client, seeded_users):
    login(client, "test_buyer")
    client.post("/act-as", data={"unit_id": "U1"})

    original_qty = next(
        l["qty_kg"] for l in data.LISTINGS
        if l["unit_id"] == "U1" and l["material"] == "metal_shavings" and l["type"] == "waste"
    )
    try:
        resp = client.post(
            "/listings/U1/metal_shavings/edit?type=waste",
            data={"qty_kg": "150"},
            follow_redirects=True,
        )
        assert resp.status_code == 200
        listing = data.find_listing("U1", "metal_shavings", "waste")
        assert listing["qty_kg"] == 150
    finally:
        data.update_listing_qty_absolute("U1", "metal_shavings", "waste", original_qty)


def test_delete_listing_removes_and_restores_a_seed_listing(client, seeded_users):
    """Exercises remove_listing_in_db's same empty-DB fallback bug, then
    re-adds the listing so this test doesn't leak state into others."""
    login(client, "test_buyer")
    client.post("/act-as", data={"unit_id": "U1"})

    resp = client.post(
        "/listings/U1/metal_shavings/delete",
        data={"type": "waste"},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert data.find_listing("U1", "metal_shavings", "waste") is None

    # Restore, so other tests relying on this seed listing aren't affected.
    data.LISTINGS.append(
        {"unit_id": "U1", "type": "waste", "material": "metal_shavings", "qty_kg": 220, "interval_days": 9}
    )


# ---------------------------------------------------------------------------
# whatsapp_service.py's unregistered-number webhook reply
# ---------------------------------------------------------------------------

def test_whatsapp_webhook_from_unregistered_number_does_not_crash(client):
    """Previously raised AttributeError: 'MessagingResponse' object has no
    attribute 'body', turning any message from a number not on file into an
    unhandled 500 on this public webhook."""
    resp = client.post(
        "/whatsapp/webhook",
        data={"From": "whatsapp:+10000000000", "Body": "hello", "MessageSid": "SM_test_unregistered"},
    )
    assert resp.status_code == 200
    assert b"not registered" in resp.data
