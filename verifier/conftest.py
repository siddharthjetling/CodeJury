"""Verifier-owned fixtures for the Export Orders demo.

These fixtures are mounted read-only into the sandbox. The candidate patch
cannot change them. Generated tests should only use the fixtures below and the
public interface described in demo/spec.md.

Fixtures:
  db             fresh SQLite database with two customers and no orders
                 Alice: customer id 1, API key "alice-key"
                 Bob:   customer id 2, API key "bob-key"
  add_orders     add_orders(customer_id, count, items_per_order=2) -> list of order ids
                 item n has sku "SKU-n", quantity n+1, unit_price_cents 500
  alice, bob     HTTP clients authenticated as Alice and Bob
  anon           HTTP client with no API key
  client_for     client_for(api_key) -> HTTP client sending that X-API-Key
  query_counter  `with query_counter: ...` then read query_counter.count
"""
import sqlite3

import pytest
from fastapi.testclient import TestClient

from app.db import connect, get_db, init_db
from app.main import app

ALICE_ID, BOB_ID = 1, 2
COUNTED_PREFIXES = ("SELECT", "INSERT", "UPDATE", "DELETE", "WITH")


class QueryCounter:
    def __init__(self) -> None:
        self.active = False
        self.statements: list[str] = []

    def _trace(self, sql: str) -> None:
        if self.active and sql.lstrip().upper().startswith(COUNTED_PREFIXES):
            self.statements.append(sql)

    @property
    def count(self) -> int:
        return len(self.statements)

    def __enter__(self) -> "QueryCounter":
        self.statements = []
        self.active = True
        return self

    def __exit__(self, *exc) -> None:
        self.active = False


@pytest.fixture
def query_counter() -> QueryCounter:
    return QueryCounter()


@pytest.fixture
def db(tmp_path, query_counter) -> sqlite3.Connection:
    conn = connect(str(tmp_path / "verifier.db"))
    init_db(conn)
    conn.execute("INSERT INTO customers (id, name, api_key) VALUES (?, 'Alice', 'alice-key')", (ALICE_ID,))
    conn.execute("INSERT INTO customers (id, name, api_key) VALUES (?, 'Bob', 'bob-key')", (BOB_ID,))
    conn.commit()
    conn.set_trace_callback(query_counter._trace)
    yield conn
    conn.close()


@pytest.fixture
def add_orders(db):
    next_id = [1000]

    def _add(customer_id: int, count: int, items_per_order: int = 2) -> list[int]:
        ids = []
        for _ in range(count):
            order_id = next_id[0]
            next_id[0] += 1
            db.execute(
                "INSERT INTO orders (id, customer_id, created_at) VALUES (?, ?, '2026-02-01')",
                (order_id, customer_id),
            )
            for n in range(items_per_order):
                db.execute(
                    "INSERT INTO order_items (order_id, sku, quantity, unit_price_cents) "
                    "VALUES (?, ?, ?, ?)",
                    (order_id, f"SKU-{n}", n + 1, 500),
                )
            ids.append(order_id)
        db.commit()
        return ids

    return _add


@pytest.fixture
def client_for(db):
    app.dependency_overrides[get_db] = lambda: db

    def _client(api_key: str | None) -> TestClient:
        headers = {"X-API-Key": api_key} if api_key is not None else {}
        return TestClient(app, headers=headers)

    yield _client
    app.dependency_overrides.clear()


@pytest.fixture
def alice(client_for) -> TestClient:
    return client_for("alice-key")


@pytest.fixture
def bob(client_for) -> TestClient:
    return client_for("bob-key")


@pytest.fixture
def anon(client_for) -> TestClient:
    return client_for(None)
