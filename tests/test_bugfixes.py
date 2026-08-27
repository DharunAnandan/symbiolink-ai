"""
Regression tests for a batch of bugs found in a full-codebase audit that
don't fit the HTTP-permission-test shape of test_app_permissions.py:
- optimization.py's chain saving/CO2e figures not matching what its own
  capacity constraints actually reserve for unequal-quantity hops
- whatsapp_service.py matching an empty/malformed phone number to an
  arbitrary unit via `LIKE '%%'`
- orders.py's order-id generation and carbon_credits.py's credit issuance
  both being non-atomic read-then-write sequences under the dev server's
  threaded=True concurrency
"""

import threading

import pytest

import optimization
import orders as orders_module


# ---------------------------------------------------------------------------
# optimization.py -- a chain's reported total_saving_rs/total_co2_saved_kg
# must be consistent with the bottleneck quantity _chain_listing_draws()
# actually reserves against each listing, not each hop's own (possibly
# larger) qty_kg.
# ---------------------------------------------------------------------------

def test_bottleneck_adjusted_totals_scales_each_edge_to_the_tightest_hop():
    chain = [
        {"qty_kg": 100.0, "saving_rs": 1000.0, "co2_saved_kg": 200.0},
        {"qty_kg": 25.0, "saving_rs": 300.0, "co2_saved_kg": 50.0},
    ]
    bottleneck = min(e["qty_kg"] for e in chain)  # 25.0, the tighter hop
    saving, co2 = optimization._bottleneck_adjusted_totals(chain, bottleneck)

    # First edge's saving/co2 must be scaled down by 25/100 = 0.25 (250 Rs,
    # 50kg), not counted at its own full 100kg figure (1000 Rs, 200kg) --
    # the naive `sum(e["saving_rs"] for e in chain)` matching.py uses would
    # give 1300, overstating what the plan can actually deliver.
    assert saving == pytest.approx(250.0 + 300.0)
    assert co2 == pytest.approx(50.0 + 50.0)
    naive_total = sum(e["saving_rs"] for e in chain)
    assert saving < naive_total


def test_chain_listing_draws_and_bottleneck_totals_agree_on_the_bottleneck():
    """The quantity _chain_listing_draws() reserves against every listing in
    the chain and the quantity _bottleneck_adjusted_totals() scales down to
    must be the same number -- that agreement is the whole point of the fix."""
    chain = [
        {"from": "U1", "to": "U2", "material": "metal_shavings", "matched_material": "metal_shavings",
         "qty_kg": 80.0, "saving_rs": 800.0, "co2_saved_kg": 160.0},
        {"from": "U2", "to": "U3", "material": "cast_offcuts", "matched_material": "cast_offcuts",
         "qty_kg": 20.0, "saving_rs": 200.0, "co2_saved_kg": 40.0},
    ]
    draws, bottleneck = optimization._chain_listing_draws(chain)
    assert bottleneck == 20.0
    assert all(qty == bottleneck for qty in draws.values())

    saving, co2 = optimization._bottleneck_adjusted_totals(chain, bottleneck)
    # At the 20kg bottleneck: edge 1 scaled by 20/80=0.25 -> 200; edge 2 at its own 20kg -> 200.
    assert saving == pytest.approx(200.0 + 200.0)
    assert co2 == pytest.approx(40.0 + 40.0)


# ---------------------------------------------------------------------------
# whatsapp_service.py -- an empty/malformed sender phone number must not
# silently match an arbitrary unit via `Unit.phone.like('%%')`.
# ---------------------------------------------------------------------------

def test_find_unit_by_phone_rejects_empty_and_malformed_numbers(flask_app):
    from whatsapp_service import whatsapp_service
    from models import db, Unit

    with flask_app.app_context():
        unit = Unit(id="U_PHONE_TEST", name="Phone Test Co", category="metal",
                    location_x=1.0, location_y=1.0, phone="+91-9876543210")
        db.session.add(unit)
        db.session.commit()
        try:
            assert whatsapp_service._find_unit_by_phone("") is None
            assert whatsapp_service._find_unit_by_phone("abc") is None
            assert whatsapp_service._find_unit_by_phone("12") is None  # too short to be a real fragment
            # A real, if partial, phone number should still match.
            found = whatsapp_service._find_unit_by_phone("9876543210")
            assert found is not None
            assert found["id"] == "U_PHONE_TEST"
        finally:
            db.session.delete(unit)
            db.session.commit()


# ---------------------------------------------------------------------------
# orders.py -- concurrent place_order() calls must never hand out the same
# order id (a read-len-then-append race, closed by _ORDERS_LOCK).
# ---------------------------------------------------------------------------

def test_concurrent_place_order_never_duplicates_an_id():
    n_threads = 12
    barrier = threading.Barrier(n_threads)
    results = []
    results_lock = threading.Lock()

    def worker():
        barrier.wait()  # maximize the chance of a real race
        order = orders_module.place_order("U2", "S", "cast_offcuts", "U1", "B", 5)
        with results_lock:
            results.append(order["id"])

    threads = [threading.Thread(target=worker) for _ in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(results) == n_threads
    assert len(set(results)) == n_threads, f"duplicate order ids handed out: {results}"


# ---------------------------------------------------------------------------
# carbon_credits.py -- concurrent issue_credits() calls for the same unit,
# sitting at exactly enough accrued CO2e for one tonne, must mint exactly
# one credit total, not one per concurrent caller.
# ---------------------------------------------------------------------------

def test_concurrent_issue_credits_does_not_double_mint(flask_app):
    import carbon_credits
    from models import db, CarbonCredit

    with flask_app.app_context():
        # Exactly 1000kg (1 tCO2e) of accrued, completed-order CO2e -- just
        # enough for a single whole tonne, so a race would show up as two
        # credits minted instead of one.
        order = orders_module.place_order("U1", "Shivam Metal Works", "metal_shavings",
                                           "U2", "Ganga Alloy Casting", 100, co2_saved_kg=1000)
        order["status"] = "completed"

        n_threads = 8
        barrier = threading.Barrier(n_threads)
        results = []
        results_lock = threading.Lock()

        def worker():
            barrier.wait()
            with flask_app.app_context():
                credit, message = carbon_credits.issue_credits("U1")
                with results_lock:
                    results.append(credit)

        threads = [threading.Thread(target=worker) for _ in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        try:
            minted = [c for c in results if c is not None]
            assert len(minted) == 1, f"expected exactly 1 credit minted, got {len(minted)}"
            total_tco2e = sum(c.tco2e for c in CarbonCredit.query.filter_by(unit_id="U1").all())
            assert total_tco2e == 1
        finally:
            CarbonCredit.query.filter_by(unit_id="U1").delete()
            db.session.commit()
