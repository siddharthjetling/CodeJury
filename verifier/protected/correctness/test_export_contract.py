"""Protected correctness tests for GET /orders/export (spec requirements 1-5)."""
ALICE_ID = 1


def test_export_returns_all_own_orders_sorted(alice, add_orders):
    ids = add_orders(ALICE_ID, 3)
    r = alice.get("/orders/export")
    assert r.status_code == 200
    body = r.json()
    assert body["customer_id"] == ALICE_ID
    assert [o["id"] for o in body["orders"]] == sorted(ids)


def test_export_includes_items_and_totals(alice, add_orders):
    add_orders(ALICE_ID, 1, items_per_order=2)
    order = alice.get("/orders/export").json()["orders"][0]
    assert order["items"] == [
        {"sku": "SKU-0", "quantity": 1, "unit_price_cents": 500},
        {"sku": "SKU-1", "quantity": 2, "unit_price_cents": 500},
    ]
    assert order["total_cents"] == 1500


def test_export_order_without_items(alice, add_orders):
    add_orders(ALICE_ID, 1, items_per_order=0)
    order = alice.get("/orders/export").json()["orders"][0]
    assert order["items"] == []
    assert order["total_cents"] == 0


def test_export_customer_with_no_orders(alice):
    r = alice.get("/orders/export")
    assert r.status_code == 200
    assert r.json()["orders"] == []


def test_export_requires_valid_api_key(anon, client_for):
    assert anon.get("/orders/export").status_code == 401
    assert client_for("not-a-key").get("/orders/export").status_code == 401
