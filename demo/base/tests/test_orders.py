AUTH = {"X-API-Key": "alice-key"}


def test_list_orders_requires_api_key(client):
    assert client.get("/orders").status_code == 401


def test_list_orders_rejects_unknown_key(client):
    assert client.get("/orders", headers={"X-API-Key": "nope"}).status_code == 401


def test_list_orders_returns_own_orders(client):
    r = client.get("/orders", headers=AUTH)
    assert r.status_code == 200
    assert r.json() == {"orders": [{"id": 10, "created_at": "2026-01-05"}]}


def test_get_order_includes_items_and_total(client):
    r = client.get("/orders/10", headers=AUTH)
    assert r.status_code == 200
    body = r.json()
    assert body["items"] == [{"sku": "MUG", "quantity": 2, "unit_price_cents": 1200}]
    assert body["total_cents"] == 2400


def test_get_unknown_order_returns_404(client):
    assert client.get("/orders/999", headers=AUTH).status_code == 404
