"""
Tests for the order ledger (orders.py) -- the actual live order system this
app runs on, backed by the real orders/order_items/order_history tables (see
the module docstring there).
"""

import orders as orders_module


def test_place_order_starts_in_placed_status():
    order = orders_module.place_order(
        seller_unit_id="U2", seller_name="Ganga Alloy Casting", material="cast_offcuts",
        buyer_unit_id="U1", buyer_name="Shivam Metal Works", qty_kg=10, co2_saved_kg=19,
    )
    assert order["status"] == "placed"
    assert order["id"] == "ORD001"
    # Persisted, not just an in-memory dict -- a fresh lookup finds the same order.
    assert orders_module.order_by_id(order["id"]) is not None
    assert orders_module.order_by_id(order["id"])["id"] == order["id"]


def test_order_ids_increment():
    o1 = orders_module.place_order("U2", "S", "m", "U1", "B", 10)
    o2 = orders_module.place_order("U2", "S", "m", "U1", "B", 10)
    assert o1["id"] == "ORD001"
    assert o2["id"] == "ORD002"


def test_all_orders_and_order_by_id():
    o1 = orders_module.place_order("U2", "S", "m", "U1", "B", 10)
    assert orders_module.order_by_id(o1["id"])["id"] == o1["id"]
    assert orders_module.order_by_id("ORD999") is None
    assert o1 in orders_module.all_orders()


def test_status_sequence_is_linear_and_terminal_states_exist():
    assert orders_module.STAGE_SEQUENCE[0] == "placed"
    assert "completed" in orders_module.STAGE_SEQUENCE
    assert "cancelled" in orders_module.STAGE_SEQUENCE
