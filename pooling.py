"""
Logistics pooling layer.

Low-quantity matches are often skipped in practice because a solo pickup truck isn't
economical for a small load. This module groups nearby low-volume pickups into a single
shared transport run and recomputes the net saving with the pooled (cheaper, per-unit)
transport rate instead of the solo rate -- showing which matches only become viable once
pooled.
"""

import data
from data import TRANSPORT_RS_PER_KM_SOLO, TRANSPORT_RS_PER_KM_POOLED
from matching import build_edges, distance_km

# unit_by_id must stay a data.unit_by_id(...) attribute lookup, not a bare "from data
# import unit_by_id" -- see the note in matching.py for why.

LOW_VOLUME_THRESHOLD_KG = 100   # below this, solo transport usually eats the saving
POOL_RADIUS_KM = 1.8            # pickups within this radius of each other are pooled


class UnionFind:
    def __init__(self, items):
        self.parent = {i: i for i in items}

    def find(self, x):
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[ra] = rb


def build_pools():
    """Group low-volume edges whose pickup (waste-source) units are within POOL_RADIUS_KM
    of each other, using simple distance-threshold clustering (union-find), so pickups
    right on a grid boundary still get pooled correctly."""
    edges = build_edges()
    low_volume_edges = [e for e in edges if e["qty_kg"] < LOW_VOLUME_THRESHOLD_KG]

    source_ids = list({e["from"] for e in low_volume_edges})
    uf = UnionFind(source_ids)
    for i in range(len(source_ids)):
        for j in range(i + 1, len(source_ids)):
            u1, u2 = data.unit_by_id(source_ids[i]), data.unit_by_id(source_ids[j])
            if distance_km(u1, u2) <= POOL_RADIUS_KM:
                uf.union(source_ids[i], source_ids[j])

    clusters = {}
    for sid in source_ids:
        root = uf.find(sid)
        clusters.setdefault(root, []).append(sid)

    pool_groups = []
    for member_ids in clusters.values():
        if len(member_ids) < 2:
            continue  # pooling requires at least 2 nearby pickups
        group_edges = [e for e in low_volume_edges if e["from"] in member_ids]
        pool_groups.append(group_edges)

    return pool_groups, low_volume_edges


def transport_cost(distance_km_, pooled):
    rate = TRANSPORT_RS_PER_KM_POOLED if pooled else TRANSPORT_RS_PER_KM_SOLO
    return round(distance_km_ * rate, 2)


def evaluate_pooling():
    """For every low-volume edge, compare net saving solo vs. pooled, and report which
    pool group (if any) it belongs to."""
    pool_groups, low_volume_edges = build_pools()

    # map edge identity -> pool index
    edge_pool_index = {}
    for i, group in enumerate(pool_groups):
        for e in group:
            key = (e["from"], e["to"], e["material"])
            edge_pool_index[key] = i

    results = []
    for e in low_volume_edges:
        key = (e["from"], e["to"], e["material"])
        pool_idx = edge_pool_index.get(key)
        cost_solo = transport_cost(e["distance_km"], pooled=False)
        cost_pooled = transport_cost(e["distance_km"], pooled=True) if pool_idx is not None else cost_solo

        net_solo = round(e["saving_rs"] - cost_solo, 2)
        net_pooled = round(e["saving_rs"] - cost_pooled, 2)

        results.append({
            "from": data.unit_by_id(e["from"])["name"],
            "to": data.unit_by_id(e["to"])["name"],
            "material": e["material"],
            "qty_kg": e["qty_kg"],
            "distance_km": e["distance_km"],
            "distance_source": e.get("distance_source", "estimated"),
            "raw_saving_rs": e["saving_rs"],
            "cost_solo_rs": cost_solo,
            "cost_pooled_rs": cost_pooled,
            "net_saving_solo_rs": net_solo,
            "net_saving_pooled_rs": net_pooled,
            "pooled": pool_idx is not None,
            "pool_id": pool_idx,
            "made_viable_by_pooling": net_solo <= 0 and net_pooled > 0,
        })

    results.sort(key=lambda r: r["net_saving_pooled_rs"], reverse=True)
    return results, pool_groups


if __name__ == "__main__":
    results, pool_groups = evaluate_pooling()
    print(f"Formed {len(pool_groups)} pooled transport group(s) from low-volume (<{LOW_VOLUME_THRESHOLD_KG}kg) matches:\n")
    for r in results:
        flag = "  <-- made viable by pooling" if r["made_viable_by_pooling"] else ""
        print(f"{r['from']} -> {r['to']} ({r['material']}, {r['qty_kg']}kg): "
              f"solo net=Rs.{r['net_saving_solo_rs']}  pooled net=Rs.{r['net_saving_pooled_rs']}{flag}")
