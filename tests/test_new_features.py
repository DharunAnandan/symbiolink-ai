"""
Tests for the new feature layers: network-optimized matching (optimization.py),
IoT bin sensors (iot_sensors.py), GPS-verified pickup/delivery (geofencing.py),
carbon credits (carbon_credits.py), the BRSR report (brsr_report.py), and the
photo classifier (photo_classifier.py).

conftest.py's autouse clean_orders fixture already wipes orders.ORDERS and the
Order/OrderItem/OrderHistory/TrustRating tables around every test and pushes
an app context for the whole test -- but NOT the BinSensor/SensorReading/
CarbonCredit tables these new features use (they didn't exist when that
fixture was written), so this file adds its own autouse wipe for those, same
spirit, so tests here can't leak state into each other or into other files.
"""

import io

import pytest

import brsr_report
import carbon_credits
import geofencing
import iot_sensors
import optimization
import orders as orders_module
import photo_classifier
from models import db, BinSensor, SensorReading, CarbonCredit


@pytest.fixture(autouse=True)
def clean_new_feature_tables(flask_app):
    def _wipe():
        SensorReading.query.delete()
        BinSensor.query.delete()
        CarbonCredit.query.delete()
        db.session.commit()
    _wipe()
    yield
    _wipe()


# ---------------------------------------------------------------------------
# optimization.py
# ---------------------------------------------------------------------------

def test_optimize_network_returns_a_plan():
    result = optimization.optimize_network()
    assert result["solver"] in ("ortools", "heuristic_fallback", "ortools_infeasible_fallback", "none")
    assert result["total_saving_rs"] >= 0
    assert isinstance(result["accepted"], list)


def test_optimized_plan_never_overdraws_a_listings_quantity():
    """The whole point of the optimizer: no listing gets promised to multiple
    accepted chains for more material than it actually has. Check every
    waste listing touched by the accepted plan against its real qty_kg."""
    import data
    result = optimization.optimize_network()
    drawn = {}
    for m in result["accepted"]:
        bottleneck = min(e["qty_kg"] for e in m["edges"])
        for e in m["edges"]:
            key = (e["from"], e["material"], "waste")
            drawn[key] = drawn.get(key, 0) + bottleneck
    for (unit_id, material, _type), qty in drawn.items():
        listing = next((l for l in data.LISTINGS if l["unit_id"] == unit_id and l["material"] == material and l["type"] == "waste"), None)
        assert listing is not None
        assert qty <= listing["qty_kg"] + 1e-6, f"{unit_id}/{material} overdrawn: {qty} > {listing['qty_kg']}"


# ---------------------------------------------------------------------------
# iot_sensors.py
# ---------------------------------------------------------------------------

def test_register_sensor_is_idempotent_per_unit_material():
    s1, msg1 = iot_sensors.register_sensor("U1", "metal_shavings")
    s2, msg2 = iot_sensors.register_sensor("U1", "metal_shavings")
    assert s1.id == s2.id
    assert "already registered" in msg2


def test_simulate_reading_crosses_threshold_and_creates_verified_listing():
    import data
    sensor, _msg = iot_sensors.register_sensor("U1", "metal_shavings", capacity_kg=100, fill_threshold_pct=10)
    before = len([l for l in data.LISTINGS if l["unit_id"] == "U1" and l["material"] == "metal_shavings"])

    triggered = False
    listing = None
    for _ in range(15):
        sensor, listing, _message = iot_sensors.simulate_reading(sensor.id)
        if listing:
            triggered = True
            break
    assert triggered, "expected at least one auto-listing within 15 simulated ticks at a 10% threshold"
    assert listing["verified_by"] == "iot_sensor"
    assert listing["material"] == "metal_shavings"
    assert listing["qty_kg"] > 0

    after = len([l for l in data.LISTINGS if l["unit_id"] == "U1" and l["material"] == "metal_shavings"])
    assert after == before + 1


# ---------------------------------------------------------------------------
# geofencing.py
# ---------------------------------------------------------------------------

def test_gps_tracking_drives_order_from_confirmed_to_delivered():
    order = orders_module.place_order("U1", "Shivam Metal Works", "metal_shavings", "U2", "Ganga Alloy Casting", 20, co2_saved_kg=36)
    order["payment_status"] = "paid"
    orders_module.advance_order(order["id"])  # placed -> confirmed
    assert order["status"] == "confirmed"

    seen_pickup = seen_delivered = False
    for _ in range(20):
        order, _message = geofencing.simulate_gps_tick(order["id"])
        if order["status"] == "picked_up":
            seen_pickup = True
        if order["status"] == "delivered":
            seen_delivered = True
            break
    assert seen_pickup, "GPS simulation should have advanced the order through picked_up"
    assert seen_delivered, "GPS simulation should have advanced the order to delivered"
    assert order["gps"]["leg"] == "done"
    assert any("geofence_entered_pickup" in e["event"] for e in order["gps_log"])
    assert any("geofence_entered_delivery" in e["event"] for e in order["gps_log"])


def test_gps_tracking_does_not_start_before_payment():
    order = orders_module.place_order("U1", "Shivam Metal Works", "metal_shavings", "U2", "Ganga Alloy Casting", 20, co2_saved_kg=36)
    orders_module.advance_order(order["id"])  # placed -> confirmed, but unpaid
    assert order.get("payment_status") != "paid"
    gps = geofencing.ensure_gps_tracking(order)
    assert gps is None
    assert order["status"] == "confirmed"


# ---------------------------------------------------------------------------
# carbon_credits.py
# ---------------------------------------------------------------------------

def test_issue_credits_requires_a_whole_tonne():
    order = orders_module.place_order("U1", "Shivam Metal Works", "metal_shavings", "U2", "Ganga Alloy Casting", 20, co2_saved_kg=200)
    order["status"] = "completed"
    credit, message = carbon_credits.issue_credits("U1")
    assert credit is None
    assert "Not enough" in message


def test_issue_credits_mints_whole_tonnes_and_tracks_cumulative_issuance():
    order = orders_module.place_order("U1", "Shivam Metal Works", "metal_shavings", "U2", "Ganga Alloy Casting", 500, co2_saved_kg=2500)
    order["status"] = "completed"

    credit, message = carbon_credits.issue_credits("U1")
    assert credit is not None
    assert credit.tco2e == 2
    assert "Minted 2" in message

    # Re-issuing immediately after must not double-mint the same CO2e.
    credit2, message2 = carbon_credits.issue_credits("U1")
    assert credit2 is None
    assert "Not enough" in message2


def test_list_and_buy_credit_flow():
    order = orders_module.place_order("U1", "Shivam Metal Works", "metal_shavings", "U2", "Ganga Alloy Casting", 500, co2_saved_kg=2500)
    order["status"] = "completed"
    credit, _msg = carbon_credits.issue_credits("U1")

    listed, msg = carbon_credits.list_credit(credit.id, "U1", 900)
    assert listed.status == "listed"
    assert "Listed" in msg

    listings = carbon_credits.marketplace_listings()
    assert any(l["id"] == credit.id for l in listings)

    sold, msg2 = carbon_credits.buy_credit(credit.id, "Acme Corp")
    assert sold.status == "sold"
    assert sold.buyer_name == "Acme Corp"

    summary = carbon_credits.unit_credit_summary("U1")
    assert summary["sold_tco2e"] == 2
    assert summary["revenue_rs"] == pytest.approx(1800.0)


# ---------------------------------------------------------------------------
# brsr_report.py
# ---------------------------------------------------------------------------

def test_brsr_report_aggregates_completed_orders_only():
    placed_only = orders_module.place_order("U1", "Shivam Metal Works", "metal_shavings", "U2", "Ganga Alloy Casting", 30, co2_saved_kg=54)
    completed = orders_module.place_order("U1", "Shivam Metal Works", "metal_shavings", "U3", "Everest Sheet Fabricators", 50, co2_saved_kg=90)
    completed["status"] = "completed"

    report = brsr_report.generate_brsr_data("U1")
    assert report["indicators"]["verified_transaction_count"] == 1
    assert report["indicators"]["waste_diverted_from_landfill_kg"] == 50
    assert report["indicators"]["ghg_avoided_kg_co2e"] == 90
    assert len(report["evidence"]) == 1
    assert report["evidence"][0]["order_id"] == completed["id"]
    # The still-placed order must not leak into the evidence table.
    assert placed_only["id"] not in [e["order_id"] for e in report["evidence"]]


def test_brsr_report_unknown_unit_returns_none():
    assert brsr_report.generate_brsr_data("U_NOT_REAL") is None


# ---------------------------------------------------------------------------
# photo_classifier.py
# ---------------------------------------------------------------------------

def test_classify_photo_recognizes_a_synthetic_family_image():
    img = photo_classifier.generate_synthetic_image("metal_scrap", seed=123)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    result = photo_classifier.classify_photo(buf.getvalue())
    assert result["ok"] is True
    assert result["family"] == "metal_scrap"
    assert result["material"] == "metal_shavings"
    assert 0.0 <= result["confidence"] <= 1.0
    assert result["qty_kg_low"] < result["qty_kg_high"]


def test_classify_photo_rejects_non_image_bytes():
    result = photo_classifier.classify_photo(b"not an image")
    assert result["ok"] is False


def _jpeg_bytes(img):
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


def test_tan_cardboard_like_photo_is_not_classified_as_metal():
    """Regression: a brown/tan cardboard photo used to come back as metal scrap."""
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (200, 150), (190, 190, 186))  # grey floor around it
    draw = ImageDraw.Draw(img)
    draw.rectangle((30, 20, 170, 130), fill=(186, 146, 98))  # tan board
    for y in range(24, 130, 6):
        draw.line((30, y, 170, y), fill=(160, 120, 76))  # corrugation lines
    result = photo_classifier.classify_photo(_jpeg_bytes(img))
    assert result["ok"] and result["method"] == "heuristic"
    assert result["family"] == "cardboard_paper"
    assert result["material"] == "cardboard_offcuts"
    assert "metal_shavings" in result["material_choices"]  # owner can still correct it


def test_photo_uses_gemini_vision_when_key_is_set(monkeypatch):
    import json
    import assistant
    from config import Config
    from PIL import Image

    monkeypatch.setattr(Config, "GEMINI_API_KEY", "test-key")
    sent = {}

    def _fake_generate(body):
        sent["body"] = body
        return json.dumps({"family": "cardboard_paper", "material": "cardboard_offcuts",
                           "confidence": 0.93, "qty_kg_low": 40, "qty_kg_high": 80,
                           "description": "Stack of flattened corrugated boxes"})

    monkeypatch.setattr(assistant, "gemini_generate", _fake_generate)
    result = photo_classifier.classify_photo(_jpeg_bytes(Image.new("RGB", (64, 64), (120, 120, 120))))
    assert result["method"] == "ai-vision"
    assert result["family"] == "cardboard_paper" and result["material"] == "cardboard_offcuts"
    assert (result["qty_kg_low"], result["qty_kg_high"]) == (40, 80)
    parts = sent["body"]["contents"][0]["parts"]
    assert parts[1]["inline_data"]["mime_type"] == "image/jpeg"


def test_photo_falls_back_when_gemini_fails(monkeypatch):
    import assistant
    from config import Config
    from PIL import Image

    monkeypatch.setattr(Config, "GEMINI_API_KEY", "test-key")

    def _boom(body):
        raise RuntimeError("All Gemini models failed")

    monkeypatch.setattr(assistant, "gemini_generate", _boom)
    result = photo_classifier.classify_photo(_jpeg_bytes(Image.new("RGB", (64, 64), (150, 152, 158))))
    assert result["ok"] and result["method"] == "heuristic"
