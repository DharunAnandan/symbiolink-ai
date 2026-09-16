"""
Real-world coordinates for the cluster, and the bridge to the km grid the
matching engine already runs on.

The problem this solves
-----------------------
Every distance calculation in this project (matching.distance_km,
pooling.py's clustering, geofencing.py) works on a unit's `loc` -- a plain
(x, y) pair measured in kilometres on a flat local grid, compared with
math.hypot(). That is fast, dependency-free, and accurate enough across an
industrial estate a few km wide.

What it is not is a *place*. You cannot put (1.2, 0.4) on a map, and a unit
registering through the web form had no way to say where it actually is.

Rather than rip out the km grid and convert the whole engine to haversine
(which would touch matching, pooling, geofencing and their tests), this
module makes the two representations two views of the same thing:

    latitude/longitude  <--  authoritative, what the user pins on the map
            |
            v  project()
    (x_km, y_km)        <--  derived, what the matching engine consumes

A unit registered via the map picker stores real lat/lng, and its x/y is
projected from that, so distance_km() keeps returning kilometres and every
existing test keeps passing. A unit seeded with only x/y (the 24 demo
companies in data.py) gets lat/lng back-projected from its grid position, so
it still appears in a sensible spot on the map.

Why equirectangular and not haversine
-------------------------------------
Over a cluster a few kilometres wide, the flat-earth approximation below is
off by centimetres -- far below the precision of a pin dropped by hand on a
map. It is also invertible in closed form, which is what lets xy_to_latlng()
exist at all. haversine_km() is still provided for anything that needs a
true great-circle distance between two arbitrary points.
"""

import math
import os

# Metres per degree of latitude, and per degree of longitude at the equator.
# Standard WGS-84 mean values; the longitude figure gets scaled by cos(lat)
# below because meridians converge as you move away from the equator.
_KM_PER_DEG_LAT = 110.574
_KM_PER_DEG_LNG_EQUATOR = 111.320

# Where the (0, 0) corner of the km grid sits in the real world.
#
# The 24 seeded demo companies in data.py carry grid coordinates only, spread
# across roughly 4.2 x 4.4 km, so they need an anchor before they can be
# drawn on a map. The default is Coimbatore, Tamil Nadu -- one of India's
# largest MSME engineering clusters, which matches the seed data's rupee
# pricing and company names. Override for a real deployment:
#
#     CLUSTER_ORIGIN_LAT=23.0225
#     CLUSTER_ORIGIN_LNG=72.5714
#
# Changing it moves every *seeded* unit (their grid coords are fixed and
# their lat/lng is derived). Units registered through the map picker store
# their own real lat/lng and do not move.
CLUSTER_ORIGIN_LAT = float(os.environ.get("CLUSTER_ORIGIN_LAT", "11.0168"))
CLUSTER_ORIGIN_LNG = float(os.environ.get("CLUSTER_ORIGIN_LNG", "76.9558"))

# Default zoom for a single-unit map. 15 shows the surrounding few streets --
# close enough to recognize the site, wide enough to not look lost.
DEFAULT_ZOOM = 15
# Zoom for the whole-cluster view, used only when there is nothing to fit to
# (an empty cluster); otherwise the map fits the markers' bounds instead.
CLUSTER_ZOOM = 12


def _lng_km_per_deg(lat):
    """Kilometres per degree of longitude at this latitude."""
    return _KM_PER_DEG_LNG_EQUATOR * math.cos(math.radians(lat))


def latlng_to_xy(lat, lng):
    """Project real coordinates onto the cluster's local km grid.

    Returns (x_km, y_km) relative to the cluster origin: x east, y north.
    These are the numbers matching.distance_km() compares with hypot(), so
    the result is directly consumable by the existing engine.
    """
    x = (float(lng) - CLUSTER_ORIGIN_LNG) * _lng_km_per_deg(CLUSTER_ORIGIN_LAT)
    y = (float(lat) - CLUSTER_ORIGIN_LAT) * _KM_PER_DEG_LAT
    return (round(x, 4), round(y, 4))


def xy_to_latlng(x, y):
    """Inverse of latlng_to_xy() -- put a grid position back on the map.

    Used for the seeded demo units, which have grid coordinates but no real
    location on file, so they can still be drawn as pins.
    """
    lat = CLUSTER_ORIGIN_LAT + (float(y) / _KM_PER_DEG_LAT)
    lng = CLUSTER_ORIGIN_LNG + (float(x) / _lng_km_per_deg(CLUSTER_ORIGIN_LAT))
    return (round(lat, 6), round(lng, 6))


def haversine_km(lat1, lng1, lat2, lng2):
    """True great-circle distance in km between two points.

    Not used by the matching engine (see the module docstring on why the km
    grid stays), but correct over any distance -- so it is the right tool for
    anything comparing a unit against a point outside the cluster.
    """
    r = 6371.0088  # mean Earth radius, km
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def coords_for_unit(unit):
    """The (lat, lng) to draw this unit at, whatever it has on file.

    Prefers real stored coordinates; falls back to back-projecting the km
    grid. Returns None only if the unit has neither, which would make it
    unmappable -- callers skip those rather than dropping a pin at (0, 0)
    in the Gulf of Guinea.

    Accepts either a plain dict (data.UNITS) or a Unit model instance, since
    both shapes are in circulation -- see data_access.py's mirroring.
    """
    if unit is None:
        return None

    def _get(key):
        if isinstance(unit, dict):
            return unit.get(key)
        return getattr(unit, key, None)

    lat, lng = _get("latitude"), _get("longitude")
    if lat is not None and lng is not None:
        try:
            return (float(lat), float(lng))
        except (TypeError, ValueError):
            pass  # fall through to the grid

    loc = _get("loc")
    if loc is None:
        x, y = _get("location_x"), _get("location_y")
        loc = (x, y) if x is not None and y is not None else None
    if loc is None:
        return None
    try:
        return xy_to_latlng(loc[0], loc[1])
    except (TypeError, ValueError, IndexError):
        return None


def is_valid_latlng(lat, lng):
    """Whether a submitted pin is a usable coordinate.

    Guards the register form: a missing or malformed pin must fall back to
    the old random placement rather than write NULL/garbage into a column
    the map later reads.
    """
    try:
        lat, lng = float(lat), float(lng)
    except (TypeError, ValueError):
        return False
    return -90.0 <= lat <= 90.0 and -180.0 <= lng <= 180.0
