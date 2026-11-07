# Task: Export Orders endpoint

Add an endpoint that lets a customer download all of their orders, including line items, in one request.

## Public interface

- Route: `GET /orders/export`
- Authentication: `X-API-Key` header, the same as the existing `/orders` endpoints.
- Response `200`, JSON:

```json
{
  "customer_id": 1,
  "orders": [
    {
      "id": 10,
      "created_at": "2026-01-05",
      "items": [{"sku": "MUG", "quantity": 2, "unit_price_cents": 1200}],
      "total_cents": 2400
    }
  ]
}
```

## Requirements

1. The response contains every order that belongs to the authenticated customer, sorted by order `id` ascending.
2. Each order lists all of its line items. `total_cents` is the sum of `quantity * unit_price_cents`.
3. An order with no line items has `"items": []` and `"total_cents": 0`.
4. A customer with no orders gets `200` with `"orders": []`.
5. A missing or unknown API key returns `401`.
6. **Data isolation:** the response must only ever contain the authenticated customer's orders. No query parameter, header or body field may change whose orders are exported.
7. **Performance:** a request makes at most 3 database queries, no matter how many orders the customer has.
