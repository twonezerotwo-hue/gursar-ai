# Quick Quote V1

Schema identifier: `quick-quote.v1`.

`QuickQuoteV1` is returned for each independent job after service, package, location, and price
resolution.

```json
{
  "schema_version": "quick-quote.v1",
  "quote_id": "uuid",
  "job_id": "uuid",
  "service_code": "formwork.standard.turnkey",
  "service_label": "Standart Kalıp",
  "location_code": "TR/ISTANBUL",
  "location_label": "İstanbul",
  "quantity": 4000,
  "unit": "m2",
  "unit_price": 1350,
  "total_price": 5400000,
  "currency": "TRY",
  "vat_policy": "EXCLUDED",
  "price_version": 1,
  "status": "QUOTED",
  "scope_summary": "Standart kapsama ... dahildir.",
  "scope_items": [],
  "customer_text": ""
}
```

Statuses:

- `QUOTED`: internal effective price found; unit price and optional total are present.
- `PRICE_NOT_CONFIGURED`: service is known but no effective internal price exists.
- `SERVICE_CLARIFICATION_REQUIRED`: service category cannot yet be selected safely.

`total_price` is calculated as `quantity × unit_price` with `Decimal` and persisted with the exact
price-book row/version. `customer_text` is rendered separately so presentation can change without
changing the authoritative quote contract.
