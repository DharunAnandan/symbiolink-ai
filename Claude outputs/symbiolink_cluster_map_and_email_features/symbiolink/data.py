"""
Synthetic dataset for the SymbioLink AI demo.

Modeled loosely on documented Indian industrial cluster patterns (e.g. Muzaffarnagar's
paper/sugar-mill belt, Mysore's mixed manufacturing cluster): a set of small units, each
generating a byproduct that another nearby unit could plausibly use as an input.

This is illustrative demo data, not real MSME records.
"""

import random
from datetime import datetime, timedelta

random.seed(42)

# Each unit: id, name, category, location (x, y) in a notional cluster grid (km),
# and a WhatsApp-style contact (mocked).
UNITS = [
    {"id": "U1", "name": "Shivam Metal Works",       "category": "metal",     "loc": (0.0, 0.0), "phone": "+91-90000-00001"},
    {"id": "U2", "name": "Ganga Alloy Casting",       "category": "metal",    "loc": (1.2, 0.4), "phone": "+91-90000-00002"},
    {"id": "U3", "name": "Everest Sheet Fabricators", "category": "metal",    "loc": (2.5, 1.0), "phone": "+91-90000-00003"},
    {"id": "U4", "name": "Om Sugar Mill",             "category": "sugar",    "loc": (3.0, 2.0), "phone": "+91-90000-00004"},
    {"id": "U5", "name": "Bharat Paper Mill",         "category": "paper",    "loc": (3.4, 2.3), "phone": "+91-90000-00005"},
    {"id": "U6", "name": "Vishwa Packaging Co.",      "category": "packaging","loc": (4.0, 1.6), "phone": "+91-90000-00006"},
    {"id": "U7", "name": "Sri Textiles",              "category": "textile",  "loc": (0.8, 3.0), "phone": "+91-90000-00007"},
    {"id": "U8", "name": "Nandini Dyeing Unit",       "category": "textile",  "loc": (1.4, 3.3), "phone": "+91-90000-00008"},
    {"id": "U9", "name": "Mysore Chemical Processors","category": "chemical","loc": (2.0, 4.0), "phone": "+91-90000-00009"},
    {"id": "U10","name": "Kaveri Agro Foods",         "category": "food",     "loc": (2.6, 4.4), "phone": "+91-90000-00010"},
    {"id": "U11","name": "RMD Precision Tools",       "category": "metal",    "loc": (0.5, 1.8), "phone": "+91-90000-00011"},
    {"id": "U12","name": "Cauvery Plastics",          "category": "plastic",  "loc": (3.8, 3.6), "phone": "+91-90000-00012"},

    # Second wave of units -- broadens the marketplace beyond the original 11-material,
    # 9-category set so the search/matching pages have more than a couple of listings
    # left once a demo run has consumed some of the originals via real orders.
    {"id": "U13","name": "Konkan Wood Crafts",        "category": "wood",        "loc": (2.2, 0.8), "phone": "+91-90000-00013"},
    {"id": "U14","name": "Deccan Furniture Works",    "category": "wood",        "loc": (1.9, 1.1), "phone": "+91-90000-00014"},
    {"id": "U15","name": "Ratna Rubber Industries",   "category": "rubber",      "loc": (0.6, 2.6), "phone": "+91-90000-00015"},
    {"id": "U16","name": "Nilgiri Tyre Retreaders",   "category": "rubber",      "loc": (1.0, 2.8), "phone": "+91-90000-00016"},
    {"id": "U17","name": "Sahyadri Leather Co.",      "category": "leather",     "loc": (3.3, 0.5), "phone": "+91-90000-00017"},
    {"id": "U18","name": "Kolhapur Footwear Works",   "category": "leather",     "loc": (3.6, 0.8), "phone": "+91-90000-00018"},
    {"id": "U19","name": "Bangalore Circuit Recyclers","category": "electronics","loc": (2.9, 3.0), "phone": "+91-90000-00019"},
    {"id": "U20","name": "TechRefurb Solutions",      "category": "electronics", "loc": (2.6, 2.8), "phone": "+91-90000-00020"},
    {"id": "U21","name": "Coastal Glass Works",       "category": "glass",       "loc": (0.3, 3.8), "phone": "+91-90000-00021"},
    {"id": "U22","name": "Clearwater Ceramics",       "category": "glass",       "loc": (0.7, 4.0), "phone": "+91-90000-00022"},
    {"id": "U23","name": "Deccan Biogas Energy",      "category": "agro",        "loc": (2.8, 4.1), "phone": "+91-90000-00023"},
    {"id": "U24","name": "Godavari Agro Fertilizers", "category": "agro",        "loc": (3.2, 2.2), "phone": "+91-90000-00024"},
]

# Listings: each unit posts WASTE (byproduct it wants to give away/sell cheap) and/or
# NEED (raw material it wants to buy cheap). Quantities in kg per listing cycle.
# `interval_days` is used by the predictive layer to simulate a listing history.
LISTINGS = [
    {"unit_id": "U1",  "type": "waste", "material": "metal_shavings",   "qty_kg": 220, "interval_days": 9},
    {"unit_id": "U2",  "type": "need",  "material": "metal_shavings",   "qty_kg": 150, "interval_days": 12},
    {"unit_id": "U2",  "type": "waste", "material": "cast_offcuts",     "qty_kg": 80,  "interval_days": 14},
    {"unit_id": "U3",  "type": "need",  "material": "cast_offcuts",     "qty_kg": 60,  "interval_days": 10},
    {"unit_id": "U3",  "type": "waste", "material": "sheet_trimmings",  "qty_kg": 40,  "interval_days": 11},
    {"unit_id": "U11", "type": "need",  "material": "sheet_trimmings",  "qty_kg": 35,  "interval_days": 13},
    {"unit_id": "U11", "type": "waste", "material": "metal_dust",       "qty_kg": 25,  "interval_days": 15},

    {"unit_id": "U4",  "type": "waste", "material": "bagasse",          "qty_kg": 500, "interval_days": 7},
    {"unit_id": "U5",  "type": "need",  "material": "bagasse",          "qty_kg": 450, "interval_days": 7},
    {"unit_id": "U5",  "type": "waste", "material": "paper_pulp_reject","qty_kg": 90,  "interval_days": 10},
    {"unit_id": "U6",  "type": "need",  "material": "paper_pulp_reject","qty_kg": 70,  "interval_days": 12},
    {"unit_id": "U6",  "type": "waste", "material": "cardboard_offcuts","qty_kg": 35,  "interval_days": 9},

    {"unit_id": "U7",  "type": "waste", "material": "fabric_offcuts",   "qty_kg": 60,  "interval_days": 8},
    {"unit_id": "U8",  "type": "need",  "material": "fabric_offcuts",   "qty_kg": 55,  "interval_days": 9},
    {"unit_id": "U8",  "type": "waste", "material": "dye_sludge",       "qty_kg": 30,  "interval_days": 16},
    {"unit_id": "U9",  "type": "need",  "material": "dye_sludge",       "qty_kg": 20,  "interval_days": 18},
    {"unit_id": "U9",  "type": "waste", "material": "process_effluent_solids", "qty_kg": 45, "interval_days": 14},
    {"unit_id": "U10", "type": "need",  "material": "process_effluent_solids", "qty_kg": 15, "interval_days": 20},

    {"unit_id": "U12", "type": "waste", "material": "plastic_regrind",  "qty_kg": 20,  "interval_days": 17},
    {"unit_id": "U6",  "type": "need",  "material": "plastic_regrind",  "qty_kg": 18,  "interval_days": 19},

    # Second wave -- new materials/categories, plus filling a gap where U10 (food)
    # previously only had a NEED listing and nothing to actually give the cluster.
    {"unit_id": "U13", "type": "waste", "material": "sawdust",             "qty_kg": 150, "interval_days": 8},
    {"unit_id": "U14", "type": "need",  "material": "sawdust",             "qty_kg": 120, "interval_days": 10},
    {"unit_id": "U15", "type": "waste", "material": "rubber_scrap",        "qty_kg": 90,  "interval_days": 12},
    {"unit_id": "U16", "type": "need",  "material": "rubber_scrap",        "qty_kg": 75,  "interval_days": 14},
    {"unit_id": "U17", "type": "waste", "material": "leather_scraps",      "qty_kg": 40,  "interval_days": 10},
    {"unit_id": "U18", "type": "need",  "material": "leather_scraps",      "qty_kg": 32,  "interval_days": 12},
    {"unit_id": "U19", "type": "waste", "material": "electronic_scrap",  "qty_kg": 15,  "interval_days": 21},
    {"unit_id": "U20", "type": "need",  "material": "electronic_scrap",  "qty_kg": 10,  "interval_days": 25},
    {"unit_id": "U21", "type": "waste", "material": "glass_cullet",        "qty_kg": 200, "interval_days": 9},
    {"unit_id": "U22", "type": "need",  "material": "glass_cullet",        "qty_kg": 160, "interval_days": 11},
    {"unit_id": "U10", "type": "waste", "material": "fruit_pulp_waste",    "qty_kg": 300, "interval_days": 6},
    {"unit_id": "U23", "type": "need",  "material": "fruit_pulp_waste",    "qty_kg": 250, "interval_days": 7},
    {"unit_id": "U4",  "type": "waste", "material": "press_mud",           "qty_kg": 180, "interval_days": 7},
    {"unit_id": "U24", "type": "need",  "material": "press_mud",           "qty_kg": 140, "interval_days": 9},
]

# Reference price table (Rs./kg): cost of buying the material brand new vs. the
# byproduct cost, used to estimate savings. Illustrative, not live market data
# (flagged as an honest limitation).
PRICE_TABLE_RS_PER_KG = {
    "metal_shavings": {"new_material": 62, "byproduct": 18},
    "cast_offcuts": {"new_material": 70, "byproduct": 22},
    "sheet_trimmings": {"new_material": 58, "byproduct": 16},
    "metal_dust": {"new_material": 40, "byproduct": 8},
    "bagasse": {"new_material": 9, "byproduct": 2},
    "paper_pulp_reject": {"new_material": 34, "byproduct": 10},
    "cardboard_offcuts": {"new_material": 26, "byproduct": 6},
    "fabric_offcuts": {"new_material": 45, "byproduct": 14},
    "dye_sludge": {"new_material": 20, "byproduct": 5},
    "process_effluent_solids": {"new_material": 18, "byproduct": 4},
    "plastic_regrind": {"new_material": 55, "byproduct": 17},
    "sawdust": {"new_material": 20, "byproduct": 5},
    "rubber_scrap": {"new_material": 48, "byproduct": 14},
    "leather_scraps": {"new_material": 90, "byproduct": 25},
    "electronic_scrap": {"new_material": 220, "byproduct": 60},
    "glass_cullet": {"new_material": 15, "byproduct": 4},
    "fruit_pulp_waste": {"new_material": 12, "byproduct": 3},
    "press_mud": {"new_material": 10, "byproduct": 2},
}

# Pooled transport cost model: flat rate per km per pickup, discounted when pooled.
TRANSPORT_RS_PER_KM_SOLO = 45
TRANSPORT_RS_PER_KM_POOLED = 18  # per unit, when 2+ pickups share a run

# Approximate CO2e avoided (kg CO2e per kg of material) when a byproduct is reused by
# another unit instead of (a) that byproduct being landfilled/incinerated and (b) the
# receiving unit buying freshly manufactured new material instead. These are typical
# published LCA-range averages per material family, not a certified emissions factor --
# illustrative, same honest-estimate framing as PRICE_TABLE_RS_PER_KG above.
CO2_FACTOR_KG_PER_KG = {
    "metal_shavings": 1.8,
    "cast_offcuts": 1.9,
    "sheet_trimmings": 1.7,
    "metal_dust": 1.5,
    "bagasse": 0.4,
    "paper_pulp_reject": 0.9,
    "cardboard_offcuts": 0.8,
    "fabric_offcuts": 2.5,
    "dye_sludge": 0.6,
    "process_effluent_solids": 0.3,
    "plastic_regrind": 1.6,
    "sawdust": 0.5,
    "rubber_scrap": 2.2,
    "leather_scraps": 3.0,
    "electronic_scrap": 4.5,
    "glass_cullet": 0.3,
    "fruit_pulp_waste": 0.35,
    "press_mud": 0.3,
}
DEFAULT_CO2_FACTOR_KG_PER_KG = 1.0  # fallback for a material with no specific factor yet


def co2_factor_for(material_key):
    return CO2_FACTOR_KG_PER_KG.get(material_key, DEFAULT_CO2_FACTOR_KG_PER_KG)

# Simulated past completed matches used to seed trust scores.
# U1<->U2 (Shivam Metal Works / Ganga Alloy Casting) and U4<->U5 (Om Sugar
# Mill / Bharat Paper Mill) are the cluster's two strongest recurring
# symbiosis pairs -- they anchor the top-ranked chains on the Matching Engine
# page (see matching.py). Giving each pair a few rounds of completed,
# mostly-but-not-uniformly-high-rated exchanges (rather than a single lucky
# 5/5) is what actually earns them the "Highly recommended" badge in
# search.html -- app.py requires both a high average score AND a minimum
# completed-exchange count, so a real multi-exchange track record, not a
# one-off. Deliberately not a flat perfect score in every round either, so it
# reads as real ratings rather than fabricated ones.
COMPLETED_MATCHES_SEED = [
    {"from": "U4", "to": "U5", "rating_from": 5, "rating_to": 5},
    {"from": "U4", "to": "U5", "rating_from": 5, "rating_to": 4},
    {"from": "U4", "to": "U5", "rating_from": 4, "rating_to": 5},
    {"from": "U7", "to": "U8", "rating_from": 4, "rating_to": 5},
    {"from": "U1", "to": "U2", "rating_from": 5, "rating_to": 4},
    {"from": "U1", "to": "U2", "rating_from": 5, "rating_to": 5},
    {"from": "U1", "to": "U2", "rating_from": 4, "rating_to": 5},
    {"from": "U13", "to": "U14", "rating_from": 5, "rating_to": 4},
    {"from": "U21", "to": "U22", "rating_from": 4, "rating_to": 5},
]


def unit_by_id(unit_id):
    for u in UNITS:
        if u["id"] == unit_id:
            return u
    return None


def unit_by_name(name):
    name_lower = name.strip().lower()
    for u in UNITS:
        if u["name"].strip().lower() == name_lower:
            return u
    return None


def _next_unit_id():
    nums = [int(u["id"][1:]) for u in UNITS if u["id"][1:].isdigit()]
    return f"U{max(nums) + 1 if nums else 1}"


def add_unit(name, category, loc, phone="", email="", latitude=None, longitude=None):
    """Register a new company/unit. Returns the existing unit if the name already exists.

    latitude/longitude are the real-world pin dropped on the OpenStreetMap
    picker at registration (see templates/register_unit.html). They are
    optional -- a unit without them still works everywhere, and geo.py
    back-projects `loc` to put it on the map."""
    existing = unit_by_name(name)
    if existing:
        return existing
    new_unit = {"id": _next_unit_id(), "name": name, "category": category, "loc": loc,
                "phone": phone, "email": email,
                "latitude": latitude, "longitude": longitude}
    UNITS.append(new_unit)
    return new_unit


def add_listing(unit_id, listing_type, material, qty_kg, interval_days=14):
    """Post a new waste/need listing for a unit. Adds a generic reference price for a
    brand-new material so the platform keeps working end to end; a real deployment
    would have a cluster association or admin confirm the real reference price."""
    material_key = material.strip().lower().replace(" ", "_").replace("-", "_")
    if material_key not in PRICE_TABLE_RS_PER_KG:
        PRICE_TABLE_RS_PER_KG[material_key] = {"new_material": 50, "byproduct": 15}
    listing = {
        "unit_id": unit_id,
        "type": listing_type,
        "material": material_key,
        "qty_kg": qty_kg,
        "interval_days": interval_days,
    }
    LISTINGS.append(listing)
    return listing


KNOWN_MATERIALS = sorted(PRICE_TABLE_RS_PER_KG.keys())
KNOWN_CATEGORIES = sorted({u["category"] for u in UNITS})


def find_listing(unit_id, material, listing_type=None):
    for listing in LISTINGS:
        if listing["unit_id"] == unit_id and listing["material"] == material:
            if listing_type is None or listing["type"] == listing_type:
                return listing
    return None


def update_listing_qty_absolute(unit_id, material, listing_type, new_qty_kg):
    """Set a listing's quantity to an exact new value (used by the edit form)."""
    listing = find_listing(unit_id, material, listing_type)
    if listing:
        listing["qty_kg"] = new_qty_kg
    return listing


def remove_listing(unit_id, material, listing_type):
    listing = find_listing(unit_id, material, listing_type)
    if listing:
        LISTINGS.remove(listing)
        return True
    return False


def reduce_listing_qty(unit_id, material, qty_kg):
    """Reduce a unit's WASTE listing for a material by qty_kg (an order was placed
    against it). Removes the listing entirely once it hits zero. Returns the amount
    actually deducted (may be less than requested if the listing had less left)."""
    for listing in LISTINGS:
        if listing["unit_id"] == unit_id and listing["material"] == material and listing["type"] == "waste":
            deducted = min(listing["qty_kg"], qty_kg)
            listing["qty_kg"] -= deducted
            if listing["qty_kg"] <= 0:
                LISTINGS.remove(listing)
            return deducted
    return 0


def seed_listing_history(listing, cycles=5):
    """Generate a synthetic timestamp history for a listing, used by the predictive layer."""
    now = datetime.now()
    history = []
    for i in range(cycles, 0, -1):
        jitter = random.randint(-1, 1)
        ts = now - timedelta(days=listing["interval_days"] * i + jitter)
        history.append(ts)
    return history
