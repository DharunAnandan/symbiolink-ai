"""
Market price transparency.

Every order in this app draws on a single shared, fixed reference price
(data.PRICE_TABLE_RS_PER_KG) -- there's no per-order negotiated price field
anywhere in orders.py, so a "price history" or "price trend" built from
completed orders would just re-report the same fixed number every time,
dressed up as if it varied. That would be dishonest, so this module doesn't
pretend otherwise: it's a reference price GUIDE (byproduct vs. brand-new
price and the saving that implies), paired with the one thing that *does*
genuinely vary and is worth surfacing -- how much of that material has
actually traded hands on the platform. A seller deciding what to ask, or a
buyer wondering whether Rs X/kg is reasonable, gets both in one place instead
of reverse-engineering it from an individual match/order card.
"""

import data
import orders as orders_module


def material_price_guide():
    """One row per known material: the reference byproduct/new-material price
    and the saving that implies, plus real completed-order activity for that
    material (how many exchanges, how much material, how much total saving)
    pulled from orders_module.all_orders() -- not fabricated. Sorted by
    completed-order count (most actively traded first), since that's the
    signal most likely to matter to someone deciding whether to trust the
    listed price."""
    completed = [o for o in orders_module.all_orders() if o["status"] == "completed"]

    activity_by_material = {}
    for o in completed:
        stats = activity_by_material.setdefault(
            o["material"], {"completed_orders": 0, "total_kg_traded": 0.0, "total_saving_rs": 0.0},
        )
        prices = data.PRICE_TABLE_RS_PER_KG.get(o["material"])
        stats["completed_orders"] += 1
        stats["total_kg_traded"] += o["qty_kg"]
        if prices:
            stats["total_saving_rs"] += (prices.get("new_material", 0) - prices.get("byproduct", 0)) * o["qty_kg"]

    rows = []
    for material, prices in sorted(data.PRICE_TABLE_RS_PER_KG.items()):
        new_price = prices.get("new_material", 0)
        byproduct_price = prices.get("byproduct", 0)
        saving_pct = round((1 - byproduct_price / new_price) * 100) if new_price else 0
        activity = activity_by_material.get(
            material, {"completed_orders": 0, "total_kg_traded": 0.0, "total_saving_rs": 0.0},
        )
        rows.append({
            "material": material,
            "new_material_price_rs": new_price,
            "byproduct_price_rs": byproduct_price,
            "saving_pct": saving_pct,
            "completed_orders": activity["completed_orders"],
            "total_kg_traded": round(activity["total_kg_traded"], 1),
            "total_saving_rs": round(activity["total_saving_rs"], 2),
        })

    rows.sort(key=lambda r: r["completed_orders"], reverse=True)
    return rows
