# Customer Understanding V1

Schema identifier: `customer-understanding.v1`.

This internal contract carries extracted customer facts from a provider to the application layer.
Unknown fields are rejected.

```json
{
  "schema_version": "customer-understanding.v1",
  "detected_language": "tr",
  "jobs": [
    {
      "job_reference": null,
      "service_candidate": "formwork.standard.turnkey",
      "service_confidence": 0.98,
      "quantity": { "value": 4000, "unit": "m2" },
      "location": {
        "country": "TR",
        "city": "Istanbul",
        "district": null,
        "locality": null,
        "code": "TR/ISTANBUL",
        "display_name": "İstanbul"
      },
      "customer_additions": [],
      "customer_exclusions": [],
      "constraints": [],
      "evidence": [
        {
          "field_path": "jobs[0].quantity",
          "value": { "value": 4000, "unit": "m2" },
          "source_message_id": "uuid",
          "source_type": "USER_EXPLICIT",
          "confidence": 1.0
        }
      ]
    }
  ]
}
```

The provider must separate different locations or services into different jobs. It must not return
prices, totals, feasibility decisions, or legal conclusions. The application stores each accepted
contract as a new `analysis_snapshots` row.
