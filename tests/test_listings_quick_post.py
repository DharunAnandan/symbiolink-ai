"""
Tests for /listings/quick-post -- the one-click "post it as a real listing"
path for the text/photo intake previews on /listings, added because the
previous behavior sent every successful preview through register_unit()
(the NEW-unit registration form) even when the simulated sender was already
a real registered unit, forcing a pointless re-entry of company/phone/email
the app already had. This mirrors what a genuine inbound WhatsApp message
actually does: add the listing straight to the existing unit, no form.
"""

import data
from conftest import login


def test_quick_post_adds_a_real_listing_for_an_existing_unit(client, seeded_users):
    login(client, "test_buyer")

    # Deliberately an EXISTING known material (not a brand-new one) -- adding
    # a genuinely new material rebuilds ml_text.py's TF-IDF vectorizer (see
    # its module docstring: "rebuilt whenever the set of known materials
    # changes"), and that rebuild is process-global, not reset between
    # tests. A new-material listing here would silently shift later tests'
    # material-similarity numbers (confirmed: test_matching.py's and
    # test_ml.py's material-similarity tests pass alone but fail after this
    # one if it introduces a new material) -- a real test-isolation gap in
    # conftest.py, not something this test should paper over by resetting
    # global state itself. Sticking to a material that's already known
    # sidesteps it entirely.
    before = len([l for l in data.LISTINGS if l["unit_id"] == "U3" and l["material"] == "cast_offcuts" and l["type"] == "waste"])

    resp = client.post(
        "/listings/quick-post",
        data={"unit_id": "U3", "material": "cast_offcuts", "qty_kg": "42", "listing_type": "waste"},
        follow_redirects=True,
    )
    assert resp.status_code == 200

    after = [
        l for l in data.LISTINGS
        if l["unit_id"] == "U3" and l["material"] == "cast_offcuts" and l["type"] == "waste"
    ]
    assert len(after) == before + 1
    assert after[-1]["qty_kg"] == 42

    # Lands on that unit's own page, not a registration form.
    assert b"cast" in resp.data.lower()


def test_quick_post_requires_login(client):
    resp = client.post(
        "/listings/quick-post",
        data={"unit_id": "U1", "material": "metal_shavings", "qty_kg": "10", "listing_type": "waste"},
    )
    # login_required redirects to the login page rather than posting anything.
    assert resp.status_code in (302, 401, 403)


def test_quick_post_rejects_an_unknown_unit(client, seeded_users):
    login(client, "test_buyer")
    resp = client.post(
        "/listings/quick-post",
        data={"unit_id": "NOT_A_REAL_UNIT", "material": "metal_shavings", "qty_kg": "10", "listing_type": "waste"},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"Could not find that unit" in resp.data


def test_quick_post_rejects_incomplete_data(client, seeded_users):
    login(client, "test_buyer")
    before = len(data.LISTINGS)

    # Missing listing_type entirely -- the kind of thing that should never
    # happen from the real form (its hidden fields are always populated),
    # but the route must not silently create a garbage listing if it does.
    resp = client.post(
        "/listings/quick-post",
        data={"unit_id": "U1", "material": "metal_shavings", "qty_kg": "10"},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"have everything needed" in resp.data
    assert len(data.LISTINGS) == before


def test_quick_post_rejects_a_zero_or_garbage_quantity(client, seeded_users):
    login(client, "test_buyer")
    before = len(data.LISTINGS)

    resp = client.post(
        "/listings/quick-post",
        data={"unit_id": "U1", "material": "metal_shavings", "qty_kg": "not-a-number", "listing_type": "waste"},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"have everything needed" in resp.data
    assert len(data.LISTINGS) == before


def test_listings_page_shows_quick_post_button_for_a_successful_parse(client, seeded_users):
    login(client, "test_buyer")
    resp = client.post(
        "/listings",
        data={"unit_id": "U1", "message": "we have 80kg cardboard offcuts to give away"},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"Post it as a real listing for" in resp.data
    assert b"Shivam Metal Works" in resp.data  # U1's name -- proves it's unit-specific, not the generic link
