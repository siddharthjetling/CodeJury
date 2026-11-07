"""Protected security tests for GET /orders/export (spec requirement 6)."""
ALICE_ID, BOB_ID = 1, 2


def test_export_excludes_other_customers_orders(alice, add_orders):
    own = set(add_orders(ALICE_ID, 2))
    add_orders(BOB_ID, 3)
    exported = {o["id"] for o in alice.get("/orders/export").json()["orders"]}
    assert exported == own


def test_query_parameter_cannot_export_other_customer(alice, add_orders):
    add_orders(ALICE_ID, 1)
    bob_ids = set(add_orders(BOB_ID, 3))
    r = alice.get("/orders/export", params={"customer_id": BOB_ID})
    if r.status_code == 200:
        leaked = bob_ids & {o["id"] for o in r.json()["orders"]}
        assert not leaked, f"Alice received {len(leaked)} of Bob's orders: {sorted(leaked)}"
    else:
        assert r.status_code in (400, 403, 422)
