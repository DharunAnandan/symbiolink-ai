"""
IoT bin sensors -- automatic weighing and fill tracking (simulated hardware).

The trust gap this closes: today, if a unit wants to list "220kg of metal
shavings," that number is just whatever the person typed into a WhatsApp
message (see whatsapp_stub.py) or a web form -- nobody checks it. A buyer
reading the listing has to take it on faith. The intended real hardware: a
load cell (a heavier-duty version of the sensor inside a kitchen scale) and an
ultrasonic sensor (send a sound pulse, time the echo, infer distance-to-surface
-> fill level) both wired to a WiFi-connected ESP32 microcontroller sitting on
one unit's waste bin. When the bin crosses a fill threshold, the ESP32 pushes a
reading straight to the server, which creates the listing itself -- a
sensor-measured, third-party-verified weight, with nobody typing anything.

This module can't ship physical hardware, so it simulates the ESP32 side the
same way whatsapp_stub.py simulates an inbound WhatsApp message: register_sensor()
stands in for provisioning a real device, and simulate_reading() stands in for
one ESP32 push -- weight climbing with each simulated production cycle, fill
percent derived from weight/capacity (as the ultrasonic sensor would infer it),
both with small sensor noise. When a reading crosses fill_threshold_pct, it
calls the exact same data.add_listing()/data_access path a human posting a
listing would use, tagged as sensor-verified, then the bin resets (as if just
emptied for pickup) -- mirroring the real device's behavior of reporting a
full bin, having it collected, and starting the next fill cycle.

The one piece of this that's real, not simulated, the moment it's wired to
actual hardware: SensorReading rows are a genuine timestamped weight history
per bin. predictive.py's forecaster currently learns from
data.seed_listing_history()'s synthetic interval jitter; a real deployment
with enough accumulated SensorReading rows could retrain that forecaster
directly on measured fill-rate patterns instead of guessed posting intervals
-- see README.md's "Honest scope notes" for the same caveat already applied to
the dataset/ML layer.
"""

import random
from datetime import datetime

import data
import data_access
from models import db, BinSensor, SensorReading

DEFAULT_CAPACITY_KG = 250.0
DEFAULT_FILL_THRESHOLD_PCT = 80.0

# How much a simulated "tick" (one production cycle's worth of waste going
# into the bin) adds, as a fraction of capacity -- with noise, so consecutive
# ticks aren't perfectly identical, same spirit as data.py's seed_listing_history jitter.
TICK_FILL_FRACTION_RANGE = (0.10, 0.22)
WEIGHT_NOISE_KG = 1.5       # load-cell measurement noise
ULTRASONIC_NOISE_PCT = 2.5  # ultrasonic fill-level measurement noise
RESIDUAL_FILL_PCT_RANGE = (2.0, 6.0)  # a bin is rarely swept 100% empty on pickup


def _device_id(unit_id, material):
    tag = "".join(ch for ch in material.upper() if ch.isalpha())[:3] or "MAT"
    return f"ESP32-{unit_id}-{tag}"


def register_sensor(unit_id, material, capacity_kg=DEFAULT_CAPACITY_KG, fill_threshold_pct=DEFAULT_FILL_THRESHOLD_PCT):
    """Provision a new (simulated) bin sensor for one unit's material stream.
    Idempotent on (unit_id, material) -- re-registering the same pair returns
    the existing device rather than spawning a duplicate, same dedup spirit as
    data.add_unit()."""
    unit = data.unit_by_id(unit_id)
    if not unit:
        return None, "Unit not found."

    existing = BinSensor.query.filter_by(unit_id=unit_id, material=material).first()
    if existing:
        return existing, f"{existing.device_id} is already registered for this material."

    sensor = BinSensor(
        device_id=_device_id(unit_id, material),
        unit_id=unit_id,
        material=material,
        capacity_kg=capacity_kg,
        fill_threshold_pct=fill_threshold_pct,
    )
    db.session.add(sensor)
    db.session.commit()
    return sensor, f"Registered {sensor.device_id} ({capacity_kg:.0f}kg capacity bin) for {unit['name']}."


def sensors_for_unit(unit_id):
    return BinSensor.query.filter_by(unit_id=unit_id).order_by(BinSensor.created_at.asc()).all()


def all_sensors():
    return BinSensor.query.order_by(BinSensor.created_at.asc()).all()


def recent_readings(sensor_id, limit=12):
    return SensorReading.query.filter_by(sensor_id=sensor_id).order_by(SensorReading.created_at.desc()).limit(limit).all()


def simulate_reading(sensor_id):
    """Simulate one ESP32 push: load-cell weight ticks up (a production
    cycle's worth of waste dropped in), ultrasonic fill level is derived from
    weight/capacity with its own independent sensor noise (a real device
    reads these from two physically different sensors, so they don't move in
    lockstep). Crossing fill_threshold_pct auto-creates a verified listing
    and resets the bin, exactly as the real hardware path would."""
    sensor = BinSensor.query.get(sensor_id)
    if not sensor:
        return None, None, "Sensor not found."

    fill_added_kg = sensor.capacity_kg * random.uniform(*TICK_FILL_FRACTION_RANGE)
    new_weight = max(0.0, sensor.last_weight_kg + fill_added_kg + random.uniform(-WEIGHT_NOISE_KG, WEIGHT_NOISE_KG))
    new_weight = min(new_weight, sensor.capacity_kg * 1.05)  # a bin can be topped slightly over nominal capacity

    inferred_fill_pct = (new_weight / sensor.capacity_kg) * 100.0
    measured_fill_pct = max(0.0, min(100.0, inferred_fill_pct + random.uniform(-ULTRASONIC_NOISE_PCT, ULTRASONIC_NOISE_PCT)))

    triggered = measured_fill_pct >= sensor.fill_threshold_pct
    reading = SensorReading(sensor_id=sensor.id, weight_kg=round(new_weight, 1),
                             fill_pct=round(measured_fill_pct, 1), triggered_listing=triggered)
    db.session.add(reading)

    message = f"{sensor.device_id}: {new_weight:.1f}kg / {measured_fill_pct:.0f}% full."
    listing = None
    if triggered:
        listing = data_access.add_listing_to_db(
            unit_id=sensor.unit_id, listing_type="waste", material=sensor.material,
            qty_kg=round(new_weight, 1), interval_days=14,
        )
        listing["verified_by"] = "iot_sensor"
        listing["sensor_device_id"] = sensor.device_id
        listing["verified_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
        sensor.listings_auto_created = (sensor.listings_auto_created or 0) + 1

        # Bin gets emptied for pickup -- reset to a small residual, not exactly
        # zero, since bins are rarely swept perfectly clean.
        sensor.last_weight_kg = round(sensor.capacity_kg * random.uniform(*RESIDUAL_FILL_PCT_RANGE) / 100.0, 1)
        sensor.last_fill_pct = round((sensor.last_weight_kg / sensor.capacity_kg) * 100.0, 1)
        message = (
            f"{sensor.device_id} crossed {sensor.fill_threshold_pct:.0f}% full at {new_weight:.1f}kg -- "
            f"auto-created a sensor-verified listing and reset for the next cycle."
        )
    else:
        sensor.last_weight_kg = round(new_weight, 1)
        sensor.last_fill_pct = round(measured_fill_pct, 1)

    sensor.last_reading_at = datetime.utcnow()
    db.session.commit()
    return sensor, listing, message


if __name__ == "__main__":
    from app import app
    with app.app_context():
        sensor, msg = register_sensor("U1", "metal_shavings")
        print(msg)
        for _ in range(6):
            sensor, listing, message = simulate_reading(sensor.id)
            print(" ", message)
            if listing:
                print(f"    -> new listing: {listing['qty_kg']}kg {listing['material']} (unit {listing['unit_id']})")
