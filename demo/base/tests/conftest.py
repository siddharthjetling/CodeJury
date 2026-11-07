import pytest
from fastapi.testclient import TestClient

from app.db import connect, get_db, init_db
from app.main import app


@pytest.fixture
def db(tmp_path):
    conn = connect(str(tmp_path / "test.db"))
    init_db(conn)
    conn.execute("INSERT INTO customers (id, name, api_key) VALUES (1, 'Alice', 'alice-key')")
    conn.execute("INSERT INTO orders (id, customer_id, created_at) VALUES (10, 1, '2026-01-05')")
    conn.execute(
        "INSERT INTO order_items (order_id, sku, quantity, unit_price_cents) "
        "VALUES (10, 'MUG', 2, 1200)"
    )
    conn.commit()
    yield conn
    conn.close()


@pytest.fixture
def client(db):
    app.dependency_overrides[get_db] = lambda: db
    yield TestClient(app)
    app.dependency_overrides.clear()
