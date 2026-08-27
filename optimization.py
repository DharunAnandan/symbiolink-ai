"""
Network-optimized matching.

matching.rank_matches() finds every direct match and 2-3 hop chain, then
scores and ranks them one at a time -- which is honest about what it is (a
weighted ranking), but it's still "solving the puzzle by trying pieces one at
a time." Nothing stops two of its top-ranked chains from separately promising
the *same* underlying waste listing to two different buyers; they're
independently scored candidates, not a jointly-feasible plan. A judge asking
"how does the matching algorithm actually work" gets an honest but modest
answer: a set of weighted rules.

This module answers that question differently: it treats the whole cluster --
every unit, every candidate direct/chain match matching.py already found -- as
one graph, and solves for the best possible NON-OVERLAPPING set of accepted
exchanges across the entire network at once, using Google OR-Tools' CP-SAT
solver (the same class of combinatorial optimization the logistics/shipping
industry has used for decades for load assignment problems). Formulated as a
weighted set-packing / knapsack-with-shared-capacities problem:

  - one boolean decision variable per candidate chain (from matching.py's own
    ranked output -- this module doesn't rediscover chains, it decides which
    of the ones matching.py already found should jointly be accepted)
  - objective: maximize total accepted cost saving (Rs) across the whole plan
  - constraints: every listing's available quantity can only be drawn down
    once across every accepted chain that touches it -- a listing can't be
    promised to three different chains for more material than it actually
    has -- and every candidate chain already respects the "no more than
    max_hops hops" limit (matching.MAX_HOPS by default; pass a different value
    to explore tighter/looser plans, e.g. compare a 2-hop-only plan against
    the full 3-hop one)

Falls back to matching.rank_matches()'s own top-N ordering (flagged
"heuristic_fallback", not jointly feasibility-checked) if the `ortools`
package isn't installed, or if the solver can't find a feasible solution in
time -- same "degrade to the pre-existing approach, never crash the page"
pattern already used by ml_models.py for the trained-ranker layer.
"""

import data
import matching

DEFAULT_CANDIDATE_POOL_SIZE = 60
SOLVE_TIME_LIMIT_SECONDS = 5.0
QTY_SCALE = 10  # CP-SAT needs integer coefficients; keep 1 decimal of kg precision


def _listing_key(unit_id, material, listing_type):
    return (unit_id, material, listing_type)


def _chain_listing_draws(chain):
    """For one chain (a list of edges, matching.py's shape), the
    (listing_key -> qty_kg) draw it makes on the waste listing supplying each
    hop and the need listing each hop fulfils, all at the chain's own
    bottleneck quantity -- a chain can only move as much material as its
    tightest hop allows, same logic pooling.py already applies per-match."""
    if not chain:
        return {}, 0
    bottleneck = min(e["qty_kg"] for e in chain)
    draws = {}
    for e in chain:
        draws[_listing_key(e["from"], e["material"], "waste")] = bottleneck
        draws[_listing_key(e["to"], e["matched_material"], "need")] = bottleneck
    return draws, bottleneck


def _listing_capacity(unit_id, material, listing_type):
    listing = next(
        (l for l in data.LISTINGS if l["unit_id"] == unit_id and l["material"] == material and l["type"] == listing_type),
        None,
    )
    return listing["qty_kg"] if listing else 0


def _bottleneck_adjusted_totals(chain, bottleneck):
    """A chain's total_saving_rs/total_co2_saved_kg (as matching.py computes
    them) sum each hop's OWN saving/co2 at that hop's own qty_kg -- but
    _chain_listing_draws() above only ever reserves the chain-wide
    `bottleneck` (the smallest hop's qty) against every listing the chain
    touches, including hops whose own qty_kg is larger. Left uncorrected,
    the CP-SAT objective (and the plan's reported totals) would credit a
    chain with saving/CO2e figured at each hop's full, larger quantity while
    the capacity constraints only ever let it actually draw the smaller
    bottleneck amount -- overstating the accepted plan's numbers for any
    multi-hop chain whose hops carry unequal quantities. Both
    estimate_saving_rs() and estimate_co2_saved_kg() (matching.py) are
    linear in qty_kg, so rescaling each edge's own saving/co2 by
    bottleneck/edge_qty gives the same answer as recomputing them from
    scratch at the bottleneck quantity, without needing the price/material
    lookups again here."""
    total_saving = 0.0
    total_co2 = 0.0
    for e in chain:
        if e["qty_kg"] <= 0:
            continue
        ratio = bottleneck / e["qty_kg"]
        total_saving += e["saving_rs"] * ratio
        total_co2 += e.get("co2_saved_kg", 0) * ratio
    return round(total_saving, 2), round(total_co2, 2)


def _fallback_result(ranked, solver_label):
    accepted = ranked[: min(15, len(ranked))]
    return {
        "solved": False,
        "solver": solver_label,
        "accepted": accepted,
        "total_saving_rs": round(sum(m["total_saving_rs"] for m in accepted), 2),
        "total_co2_saved_kg": round(sum(m["total_co2_saved_kg"] for m in accepted), 2),
        "candidates_considered": len(ranked),
    }


def optimize_network(trust_scores=None, candidate_pool_size=DEFAULT_CANDIDATE_POOL_SIZE, max_hops=None):
    """Solve for the best mutually-feasible SET of chains across the whole
    cluster. Returns:
      {"solved": bool, "solver": "ortools" | "heuristic_fallback" | "ortools_infeasible_fallback" | "none",
       "status": "optimal" | "feasible" (only when solved),
       "accepted": [chain dicts -- same shape as matching.rank_matches()'s output],
       "total_saving_rs": float, "total_co2_saved_kg": float,
       "candidates_considered": int}
    """
    trust_scores = trust_scores or {}
    max_hops = max_hops or matching.MAX_HOPS

    ranked = matching.rank_matches(trust_scores=trust_scores, top_n=candidate_pool_size)
    ranked = [m for m in ranked if m["hops"] <= max_hops]

    if not ranked:
        return {"solved": True, "solver": "none", "accepted": [], "total_saving_rs": 0,
                "total_co2_saved_kg": 0, "candidates_considered": 0}

    try:
        from ortools.sat.python import cp_model
    except ImportError:
        return _fallback_result(ranked, "heuristic_fallback")

    draws_per_chain = []
    bottleneck_saving_rs = []
    bottleneck_co2_kg = []
    for m in ranked:
        draws, bottleneck = _chain_listing_draws(m["edges"])
        draws_per_chain.append(draws)
        saving, co2 = _bottleneck_adjusted_totals(m["edges"], bottleneck)
        bottleneck_saving_rs.append(saving)
        bottleneck_co2_kg.append(co2)

    capacities = {}
    for draws in draws_per_chain:
        for key in draws:
            if key not in capacities:
                capacities[key] = _listing_capacity(*key)

    model = cp_model.CpModel()
    x = [model.NewBoolVar(f"chain_{i}") for i in range(len(ranked))]

    for key, cap in capacities.items():
        involved = [(i, draws_per_chain[i][key]) for i in range(len(ranked)) if key in draws_per_chain[i]]
        if involved:
            model.Add(
                sum(int(round(qty * QTY_SCALE)) * x[i] for i, qty in involved)
                <= int(round(cap * QTY_SCALE))
            )

    # Maximize the saving each chain can actually deliver at its bottleneck
    # quantity (see _bottleneck_adjusted_totals), not each hop's own,
    # possibly larger, saving_rs -- otherwise the solver could "accept" a
    # chain for more saving than the capacity constraints above actually
    # let it draw.
    model.Maximize(sum(int(round(bottleneck_saving_rs[i])) * x[i] for i in range(len(ranked))))

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = SOLVE_TIME_LIMIT_SECONDS
    status = solver.Solve(model)

    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return _fallback_result(ranked, "ortools_infeasible_fallback")

    # Shallow-copy each accepted chain so its reported total_saving_rs/
    # total_co2_saved_kg matches what the plan actually reserved (the
    # bottleneck-adjusted figures), without mutating the shared `ranked`
    # dicts -- other keys (path, path_names, edges, score...) are untouched.
    accepted = []
    for i in range(len(ranked)):
        if solver.Value(x[i]):
            chain_copy = dict(ranked[i])
            chain_copy["total_saving_rs"] = bottleneck_saving_rs[i]
            chain_copy["total_co2_saved_kg"] = bottleneck_co2_kg[i]
            accepted.append(chain_copy)
    accepted.sort(key=lambda m: m["score"], reverse=True)
    return {
        "solved": True,
        "solver": "ortools",
        "status": "optimal" if status == cp_model.OPTIMAL else "feasible",
        "accepted": accepted,
        "total_saving_rs": round(sum(m["total_saving_rs"] for m in accepted), 2),
        "total_co2_saved_kg": round(sum(m["total_co2_saved_kg"] for m in accepted), 2),
        "candidates_considered": len(ranked),
    }


if __name__ == "__main__":
    from app import app
    from trust import TrustLedger
    with app.app_context():
        ledger = TrustLedger()
        result = optimize_network(trust_scores=ledger.all_scores())
        print(f"solver={result['solver']} status={result.get('status')} "
              f"considered={result['candidates_considered']} accepted={len(result['accepted'])}")
        print(f"total saving: Rs.{result['total_saving_rs']:,.0f}  total CO2e avoided: {result['total_co2_saved_kg']:,.1f}kg")
        for m in result["accepted"]:
            print(f"  [{m['hops']}-hop] {' -> '.join(m['path_names'])}  saving=Rs.{m['total_saving_rs']:,.0f}")
