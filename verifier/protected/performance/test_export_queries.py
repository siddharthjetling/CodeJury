"""Protected performance tests for GET /orders/export (spec requirement 7).

Uses database query counts instead of timing so results are deterministic.
"""
import pytest

ALICE_ID = 1
MAX_QUERIES = 3


@pytest.mark.parametrize("order_count", [1, 100])
def test_export_query_count_is_bounded(alice, add_orders, query_counter, order_count):
    add_orders(ALICE_ID, order_count)
    with query_counter:
        r = alice.get("/orders/export")
    assert r.status_code == 200
    assert query_counter.count <= MAX_QUERIES, (
        f"{order_count} orders -> {query_counter.count} queries (threshold <= {MAX_QUERIES})"
    )
