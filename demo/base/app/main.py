import sqlite3

from fastapi import Depends, FastAPI, Header, HTTPException

from app.db import get_db

app = FastAPI(title="Orders Service")


def current_customer(
    x_api_key: str | None = Header(default=None),
    db: sqlite3.Connection = Depends(get_db),
) -> dict:
    if not x_api_key:
        raise HTTPException(status_code=401, detail="Missing API key")
    row = db.execute(
        "SELECT id, name FROM customers WHERE api_key = ?", (x_api_key,)
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=401, detail="Invalid API key")
    return {"id": row["id"], "name": row["name"]}


def order_payload(order: sqlite3.Row, items: list[sqlite3.Row]) -> dict:
    item_list = [
        {
            "sku": i["sku"],
            "quantity": i["quantity"],
            "unit_price_cents": i["unit_price_cents"],
        }
        for i in items
    ]
    return {
        "id": order["id"],
        "created_at": order["created_at"],
        "items": item_list,
        "total_cents": sum(i["quantity"] * i["unit_price_cents"] for i in item_list),
    }


@app.get("/orders")
def list_orders(
    customer: dict = Depends(current_customer),
    db: sqlite3.Connection = Depends(get_db),
) -> dict:
    rows = db.execute(
        "SELECT id, created_at FROM orders WHERE customer_id = ? ORDER BY id",
        (customer["id"],),
    ).fetchall()
    return {"orders": [{"id": r["id"], "created_at": r["created_at"]} for r in rows]}


@app.get("/orders/{order_id}")
def get_order(
    order_id: int,
    customer: dict = Depends(current_customer),
    db: sqlite3.Connection = Depends(get_db),
) -> dict:
    order = db.execute(
        "SELECT id, created_at FROM orders WHERE id = ? AND customer_id = ?",
        (order_id, customer["id"]),
    ).fetchone()
    if order is None:
        raise HTTPException(status_code=404, detail="Order not found")
    items = db.execute(
        "SELECT sku, quantity, unit_price_cents FROM order_items "
        "WHERE order_id = ? ORDER BY id",
        (order_id,),
    ).fetchall()
    return order_payload(order, items)
