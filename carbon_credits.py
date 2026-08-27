"""
Voluntary carbon credit marketplace.

Every completed exchange on SymbioLink AI already produces a CO2e-avoided figure
(see data.co2_factor_for / matching.estimate_co2_saved_kg) -- today that number
just sits inside a report. Saved emissions can, in voluntary carbon markets, be
packaged into a standardized tradeable unit ("1 credit = 1 tonne CO2e") that
other companies buy to offset their own emissions. This module does that
packaging: it turns a unit's own accumulated, verified CO2e savings into
CarbonCredit records the unit can list for sale, and lets any buyer (another
unit on the platform, or an outside company entered by name) purchase one.

Issuance never re-uses the same avoided-CO2e kilogram twice: rather than
flagging individual orders as "already converted" (fragile once orders can be
edited/refunded/etc.), the running total of tCO2e already issued to a unit is
read back from this table itself (sum of every CarbonCredit ever minted for
that unit_id) and subtracted from the unit's current lifetime total before
deciding how many *new* whole tonnes are available to mint. Credits are only
ever minted in whole-tonne units (1000kg increments) -- the standard unit
voluntary registries actually issue -- so a few hundred kg of freshly avoided
CO2e simply carries forward, unissued, until enough accrues to cross the next
whole tonne.

Honest scope note (same framing as brsr_report.py / README.md): this is a
simulated, in-platform marketplace, not a listing on a real registry (Verra,
Gold Standard, etc.) or an on-chain/regulated instrument -- a real deployment
would still need to go through a recognized voluntary carbon methodology and
registry-side verification before these credits could be sold to an actual
offset buyer. REFERENCE_PRICE_RS_PER_TONNE is an illustrative market price
(the voluntary carbon market has traded very roughly Rs 400-1500/tCO2e in
recent years depending on project type), not a live quote -- same "illustrative
estimate" framing already used for PRICE_TABLE_RS_PER_KG and CO2_FACTOR_KG_PER_KG
in data.py.
"""

import threading
from datetime import datetime

import data
from models import db, CarbonCredit
import orders as orders_module

KG_PER_TONNE = 1000.0

# issue_credits() below is a read-then-write: it computes issuable_tco2e()
# (which itself re-reads every previously minted CarbonCredit row) and then
# inserts a new row for whatever it found, with no locking or transaction
# isolation between the two. app.py's dev server runs threaded=True, so two
# concurrent POST /carbon-credits/issue calls for the same unit (a
# double-click, two open tabs) can both read the same "1 tonne issuable"
# state before either commits, and both mint a 1-tonne credit for the same
# underlying CO2e -- directly contradicting this module's own documented
# invariant that issuance never re-uses the same avoided-CO2e kilogram
# twice. Serializing the whole read-then-mint section per-process closes
# that window (same spirit as orders.py's _ORDERS_LOCK around place_order).
_ISSUE_LOCK = threading.Lock()

# buy_credit() below has the same read-then-write shape as issue_credits():
# it checks credit.status != "listed" and then, separately, writes
# status="sold". Under app.py's threaded=True dev server, two concurrent
# POST /carbon-credits/buy calls for the same credit_id (double-click, two
# open tabs/buyers) can both pass the "still listed" check before either
# commits, and both would overwrite each other's sale -- double-selling one
# credit. Serializing the check-and-write closes that window.
_BUY_LOCK = threading.Lock()
REFERENCE_PRICE_RS_PER_TONNE = 800  # illustrative voluntary-market reference price
METHODOLOGY_NOTE = (
    "Illustrative industrial-symbiosis avoidance methodology: CO2e avoided by reusing "
    "byproduct material instead of (a) landfilling/incinerating it and (b) the receiving "
    "unit manufacturing/buying virgin material, per data.py's published reference emission "
    "factors. Not a registry-certified methodology (e.g. Verra VM0038-style project "
    "accounting) -- would need third-party validation before sale on a real registry."
)


def _lifetime_co2_saved_kg(unit_id):
    """Every completed order this unit was a party to, either side -- both
    supplying the diverted waste and sourcing the recycled input avoid
    emissions, so both count toward this unit's own avoided-CO2e total,
    same as brsr_report.py's ghg_avoided figure."""
    completed = [o for o in orders_module.orders_for_unit(unit_id) if o["status"] == "completed"]
    return sum(o.get("co2_saved_kg", 0) or 0 for o in completed), len(completed)


def _already_issued_tco2e(unit_id):
    rows = CarbonCredit.query.filter_by(unit_id=unit_id).all()
    return sum(c.tco2e for c in rows)


def issuable_tco2e(unit_id):
    """How many *new* whole tonnes this unit could mint right now, and the kg
    remaining that hasn't yet crossed a whole tonne."""
    lifetime_kg, _count = _lifetime_co2_saved_kg(unit_id)
    already_issued_kg = _already_issued_tco2e(unit_id) * KG_PER_TONNE
    available_kg = max(0.0, lifetime_kg - already_issued_kg)
    whole_tonnes = int(available_kg // KG_PER_TONNE)
    remainder_kg = round(available_kg - whole_tonnes * KG_PER_TONNE, 1)
    return whole_tonnes, remainder_kg


def issue_credits(unit_id):
    """Mint a new CarbonCredit for every whole tonne of not-yet-issued CO2e
    this unit has accrued. Returns (credit_or_None, message)."""
    unit = data.unit_by_id(unit_id)
    if not unit:
        return None, "Unit not found."

    # See _ISSUE_LOCK's module-level comment -- this whole "how much is
    # issuable" read plus the mint below has to happen as one atomic step,
    # or two concurrent calls can both see the same not-yet-issued CO2e and
    # both mint a credit for it.
    with _ISSUE_LOCK:
        whole_tonnes, remainder_kg = issuable_tco2e(unit_id)
        if whole_tonnes < 1:
            _lifetime_kg, count = _lifetime_co2_saved_kg(unit_id)
            return None, (
                f"Not enough newly-verified CO2e yet to mint a credit -- {remainder_kg:.0f}kg accrued "
                f"toward the next 1,000kg (1 tCO2e) threshold, from {count} completed exchange(s)."
            )

        credit = CarbonCredit(
            unit_id=unit_id,
            tco2e=whole_tonnes,
            source_order_count=len([o for o in orders_module.orders_for_unit(unit_id) if o["status"] == "completed"]),
            source_note=f"Minted from {unit['name']}'s verified exchange history on SymbioLink AI.",
            status="available",
        )
        db.session.add(credit)
        db.session.commit()
    return credit, f"Minted {whole_tonnes:.0f} tCO2e of new carbon credit for {unit['name']}."


def list_credit(credit_id, unit_id, price_rs_per_tonne):
    credit = CarbonCredit.query.get(credit_id)
    if not credit or credit.unit_id != unit_id:
        return None, "Credit not found or not owned by this unit."
    if credit.status != "available":
        return None, f"This credit is already {credit.status}."
    credit.price_rs_per_tonne = max(1, float(price_rs_per_tonne))
    credit.status = "listed"
    credit.listed_at = datetime.utcnow()
    db.session.commit()
    return credit, f"Listed {credit.tco2e:.0f} tCO2e at Rs {credit.price_rs_per_tonne:,.0f}/tonne."


def unlist_credit(credit_id, unit_id):
    credit = CarbonCredit.query.get(credit_id)
    if not credit or credit.unit_id != unit_id or credit.status != "listed":
        return None, "Credit not found, not owned by this unit, or not currently listed."
    credit.status = "available"
    credit.listed_at = None
    db.session.commit()
    return credit, "Listing removed."


def buy_credit(credit_id, buyer_name):
    if not buyer_name or not buyer_name.strip():
        return None, "Enter a buyer name."
    # See _BUY_LOCK's module-level comment -- the "still listed?" check and
    # the "mark sold" write have to happen as one atomic step, or two
    # concurrent buyers can both pass the check before either commits.
    with _BUY_LOCK:
        credit = CarbonCredit.query.get(credit_id)
        if not credit or credit.status != "listed":
            return None, "This credit is no longer listed for sale."
        credit.status = "sold"
        credit.buyer_name = buyer_name.strip()
        credit.sold_at = datetime.utcnow()
        db.session.commit()
    return credit, f"Sold {credit.tco2e:.0f} tCO2e to {credit.buyer_name} for Rs {(credit.price_rs_per_tonne or 0) * credit.tco2e:,.0f}."


def marketplace_listings():
    """Every currently-listed credit, across every unit, newest first."""
    rows = CarbonCredit.query.filter_by(status="listed").order_by(CarbonCredit.listed_at.desc()).all()
    out = []
    for c in rows:
        unit = data.unit_by_id(c.unit_id)
        out.append({
            "id": c.id,
            "unit_id": c.unit_id,
            "unit_name": unit["name"] if unit else c.unit_id,
            "tco2e": c.tco2e,
            "price_rs_per_tonne": c.price_rs_per_tonne,
            "total_price_rs": round((c.price_rs_per_tonne or 0) * c.tco2e, 2),
            "listed_at": c.listed_at,
        })
    return out


def unit_credits(unit_id):
    """All credits this unit owns (any status), newest first."""
    rows = CarbonCredit.query.filter_by(unit_id=unit_id).order_by(CarbonCredit.created_at.desc()).all()
    return rows


def unit_credit_summary(unit_id):
    rows = unit_credits(unit_id)
    available = [c for c in rows if c.status == "available"]
    listed = [c for c in rows if c.status == "listed"]
    sold = [c for c in rows if c.status == "sold"]
    revenue_rs = sum((c.price_rs_per_tonne or 0) * c.tco2e for c in sold)
    whole_tonnes, remainder_kg = issuable_tco2e(unit_id)
    return {
        "available_tco2e": sum(c.tco2e for c in available),
        "listed_tco2e": sum(c.tco2e for c in listed),
        "sold_tco2e": sum(c.tco2e for c in sold),
        "revenue_rs": round(revenue_rs, 2),
        "issuable_tco2e": whole_tonnes,
        "pending_kg_toward_next_tonne": remainder_kg,
    }


if __name__ == "__main__":
    from app import app
    with app.app_context():
        for uid in ("U1", "U4", "U7"):
            credit, msg = issue_credits(uid)
            print(f"{uid}: {msg}")
        print("\nMarketplace listings:", marketplace_listings())
