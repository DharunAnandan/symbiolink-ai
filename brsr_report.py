"""
BRSR-ready ESG compliance report generator.

SEBI's Business Responsibility and Sustainability Report (BRSR) framework requires
large listed companies to report on the environmental performance of their supply
chain -- including small suppliers -- under Principle 6 ("Businesses should respect
and make efforts to protect and restore the environment"), covering resource use,
waste management, and GHG emissions. Most MSMEs have no clean, dated, auditable
record of this: their "proof" is scattered WhatsApp screenshots and memory, so a
large buyer often can't onboard them as a compliant supplier even when the
underlying practice (reusing byproduct instead of landfilling it / buying virgin
material) is exactly what BRSR wants to see.

This module builds nothing new on the data side -- it's a *presentation* layer over
data this project already has and already logs with a timestamp on every completed
order (see orders.py / app.py's _log_order_history): what material moved, how much,
between which two units, and the CO2e avoided. That's precisely the shape of
evidence a BRSR-style waste/emissions annexure needs. generate_brsr_data() below
maps a single unit's own completed-order history onto the specific line items a
Principle 6 environmental disclosure asks for:

  - Total waste diverted from landfill (this unit as seller: byproduct someone
    else took instead of it being dumped/incinerated)
  - Total waste recovered through reuse (this unit as buyer: byproduct material
    it sourced instead of buying newly manufactured/virgin material)
  - Total GHG (CO2e) avoided, in both kg and tCO2e (the unit BRSR disclosures use)
  - A dated, line-item evidence table of every completed, two-way-rated exchange
    behind those numbers -- rated and completed, not merely "placed", so this
    isn't self-reported inventory but a transaction both counterparties confirmed

Honest scope note (same framing as README.md's "Honest scope notes" section):
this is a self-generated annexure built from this platform's own audit trail, not
a third-party-assured BRSR filing -- a real deployment would still go through
whatever verification/assurance process the buyer's compliance team requires. The
CO2e-avoided figures inherit the same illustrative-estimate caveat already
disclosed for data.CO2_FACTOR_KG_PER_KG (see data.py / README.md), not a certified
life-cycle assessment.
"""

from datetime import datetime

import data
import orders as orders_module

KG_PER_TONNE = 1000.0


def _parse_order_date(order):
    """orders.py stores created_at as a "%Y-%m-%d %H:%M" string, not a
    datetime -- parse defensively so a malformed/legacy record can't crash
    report generation, it just sorts last."""
    try:
        return datetime.strptime(order["created_at"], "%Y-%m-%d %H:%M")
    except (KeyError, ValueError, TypeError):
        return datetime.min


def generate_brsr_data(unit_id, reporting_label=None):
    """Build the BRSR Principle 6 (environment) annexure data for one unit.

    Only COMPLETED orders count as evidence -- a placed or in-transit order is
    an intent, not yet a verified exchange; completion requires both sides to
    have rated each other (see orders.complete_order), which is the closest
    thing this platform has to independent confirmation that the material
    actually changed hands.
    """
    unit = data.unit_by_id(unit_id)
    if not unit:
        return None

    all_orders = orders_module.orders_for_unit(unit_id)
    completed = [o for o in all_orders if o["status"] == "completed"]
    completed.sort(key=_parse_order_date, reverse=True)

    as_seller = [o for o in completed if o["seller_unit_id"] == unit_id]
    as_buyer = [o for o in completed if o["buyer_unit_id"] == unit_id]

    waste_diverted_kg = round(sum(o["qty_kg"] for o in as_seller), 1)
    waste_recovered_kg = round(sum(o["qty_kg"] for o in as_buyer), 1)
    total_waste_managed_kg = round(waste_diverted_kg + waste_recovered_kg, 1)
    ghg_avoided_kg = round(sum(o.get("co2_saved_kg", 0) or 0 for o in completed), 1)

    materials_by_key = {}
    for o in completed:
        m = o["material"]
        row = materials_by_key.setdefault(m, {"material": m, "qty_kg": 0.0, "co2_saved_kg": 0.0, "count": 0})
        row["qty_kg"] += o["qty_kg"]
        row["co2_saved_kg"] += o.get("co2_saved_kg", 0) or 0
        row["count"] += 1
    material_breakdown = sorted(materials_by_key.values(), key=lambda r: r["qty_kg"], reverse=True)
    for row in material_breakdown:
        row["qty_kg"] = round(row["qty_kg"], 1)
        row["co2_saved_kg"] = round(row["co2_saved_kg"], 1)

    evidence = []
    for o in completed:
        role = "seller (waste diverted)" if o["seller_unit_id"] == unit_id else "buyer (recycled input used)"
        counterparty = o["buyer_name"] if o["seller_unit_id"] == unit_id else o["seller_name"]
        evidence.append({
            "order_id": o["id"],
            "date": o.get("created_at", ""),
            "role": role,
            "counterparty": counterparty,
            "material": o["material"],
            "qty_kg": o["qty_kg"],
            "co2_saved_kg": round(o.get("co2_saved_kg", 0) or 0, 1),
        })

    return {
        "unit": unit,
        "reporting_label": reporting_label or "Since onboarding (all recorded activity to date)",
        "generated_at": datetime.now().strftime("%d %b %Y, %H:%M"),
        "indicators": {
            "waste_diverted_from_landfill_kg": waste_diverted_kg,
            "waste_recovered_reused_kg": waste_recovered_kg,
            "total_waste_managed_kg": total_waste_managed_kg,
            "total_waste_managed_tonnes": round(total_waste_managed_kg / KG_PER_TONNE, 3),
            "ghg_avoided_kg_co2e": ghg_avoided_kg,
            "ghg_avoided_tco2e": round(ghg_avoided_kg / KG_PER_TONNE, 3),
            "verified_transaction_count": len(completed),
            "as_seller_count": len(as_seller),
            "as_buyer_count": len(as_buyer),
        },
        "material_breakdown": material_breakdown,
        "evidence": evidence,
    }


if __name__ == "__main__":
    from app import app
    with app.app_context():
        for uid in ("U1", "U4"):
            report = generate_brsr_data(uid)
            if not report:
                continue
            print(f"\n=== BRSR annexure -- {report['unit']['name']} ({uid}) ===")
            for k, v in report["indicators"].items():
                print(f"  {k}: {v}")
            print(f"  evidence rows: {len(report['evidence'])}")
