AUTH = {"X-API-Key": "alice-key"}


def test_export_requires_api_key(client):
    assert client.get("/orders/export").status_code == 401


def test_export_returns_orders_with_items(client):
    r = client.get("/orders/export", headers=AUTH)
    assert r.status_code == 200
    body = r.json()
    assert body["customer_id"] == 1
    assert len(body["orders"]) == 1
    assert body["orders"][0]["total_cents"] == 2400
