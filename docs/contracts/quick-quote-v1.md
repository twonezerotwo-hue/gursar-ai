# Quick Quote V1

Schema identifier: `quick-quote.v1`.

`QuickQuoteV1` is returned for each independent job after service, package, location, and price
resolution.

```json
{
  "schema_version": "quick-quote.v1",
  "quote_id": "uuid",
  "job_id": "uuid",
  "service_code": "formwork.labor",
  "service_label": "Kalıp İşçiliği",
  "location_code": "TR/ISTANBUL",
  "location_label": "İstanbul",
  "quantity": 4000,
  "unit": "m2",
  "unit_price": 700,
  "total_price": 2800000,
  "currency": "TRY",
  "vat_policy": "EXCLUDED",
  "price_version": 1,
  "status": "QUOTED",
  "scope_summary": "Kalıp malzemesi hariç, yalnız kalıp işçiliğidir.",
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
