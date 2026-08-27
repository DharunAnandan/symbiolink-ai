"""
Tests for the multi-hop matching engine (matching.py), including the new
semantic/fuzzy material-matching layer. Pure-Python, no Flask/DB needed --
these exercise data.py's real seeded units/listings directly.
"""

import matching


def test_exact_material_match_is_found():
    """Sanity check on the original behavior: Shivam Metal Works (U1) lists
    waste metal_shavings, Ganga Alloy Casting (U2) needs metal_shavings --
    an exact-material edge between them must exist within radius."""
    edges = matching.build_edges()
    exact_edges = [e for e in edges if e["exact_match"] and e["from"] == "U1" and e["to"] == "U2"]
    assert exact_edges, "expected an exact edge U1 -> U2 for metal_shavings"
    assert exact_edges[0]["material"] == "metal_shavings"
    assert exact_edges[0]["similarity"] == 1.0


def test_fuzzy_match_rescues_orphaned_listing():
    """metal_dust (RMD Precision Tools, U11) has no exact-material need
    anywhere in the seed data -- the pre-existing exact-only matcher would
    silently drop it. The similarity layer should surface it as a "similar"
    edge to a same-category need with a close enough name (metal_shavings),
    without ever calling it an exact match."""
    edges = matching.build_edges()
    fuzzy_edges = [e for e in edges if not e["exact_match"] and e["material"] == "metal_dust"]
    assert fuzzy_edges, "expected at least one similar-material edge for the orphaned metal_dust listing"
    for e in fuzzy_edges:
        assert e["similarity"] < 1.0
        assert e["similarity"] >= matching.FUZZY_MATCH_THRESHOLD
        assert e["matched_material"] != "metal_dust"


def test_fuzzy_match_respects_category_boundary():
    """Two materials can be textually similar but belong to units in
    unrelated categories -- those must never be linked. Cross-check that no
    fuzzy edge crosses a category boundary by construction (build_edges only
    considers same-category unit pairs for the fuzzy path)."""
    edges = matching.build_edges()
    for e in edges:
        if e["exact_match"]:
            continue
        from_unit = matching.data.unit_by_id(e["from"])
        to_unit = matching.data.unit_by_id(e["to"])
        assert from_unit["category"] == to_unit["category"]


def test_material_similarity_symmetry_and_bounds():
    assert matching.material_similarity("metal_shavings", "metal_shavings") == 1.0
    a = matching.material_similarity("metal_dust", "metal_shavings")
    b = matching.material_similarity("metal_shavings", "metal_dust")
    assert a == b
    assert 0.0 <= a <= 1.0
    # Genuinely unrelated materials should score low.
    assert matching.material_similarity("bagasse", "electronic_scrap") < matching.FUZZY_MATCH_THRESHOLD


def test_exact_chains_outrank_equal_similar_chains():
    """score_chain's fuzzy discount must never let a fuzzy chain outscore an
    exact chain that is otherwise identical (same saving/distance/trust)."""
    base_edge = {
        "from": "A", "to": "B", "material": "x", "matched_material": "x",
        "qty_kg": 100, "distance_km": 1.0, "distance_source": "estimated",
        "saving_rs": 500, "co2_saved_kg": 10,
    }
    exact_chain = [dict(base_edge, exact_match=True, similarity=1.0)]
    similar_chain = [dict(base_edge, exact_match=False, similarity=0.8)]

    exact_score, _, exact_flag, _, _ = matching.score_chain(exact_chain, trust_scores={})
    similar_score, _, similar_flag, _, _ = matching.score_chain(similar_chain, trust_scores={})

    assert exact_flag is True
    assert similar_flag is False
    assert exact_score > similar_score


def test_rank_matches_exposes_match_type_and_reason():
    ranked = matching.rank_matches(top_n=50)
    assert ranked, "expected at least one ranked match from the seeded dataset"
    types = {m["match_type"] for m in ranked}
    assert types <= {"exact", "similar"}
    for m in ranked:
        assert isinstance(m["reason"], str) and m["reason"]
        if m["match_type"] == "similar":
            assert "Similar-material match" in m["reason"]


def test_find_chains_respects_max_hops():
    edges = matching.build_edges()
    chains = matching.find_chains(edges, max_hops=3)
    assert chains
    assert all(len(c) <= 3 for c in chains)


def test_find_chains_never_revisits_a_unit_in_one_chain():
    edges = matching.build_edges()
    chains = matching.find_chains(edges, max_hops=3)
    for chain in chains:
        path = [chain[0]["from"]] + [e["to"] for e in chain]
        assert len(path) == len(set(path)), f"chain revisits a unit: {path}"
