# Architecture Overview

GÜRSAR AI V1 is a modular monolith. Each module owns its domain, application behavior,
persistence adapter, and API surface. Cross-module work happens through application contracts,
repository ports, and outbox events rather than table access.

## QR to Quote

```mermaid
flowchart LR
    QR["QR /q/CARD001"] --> Web["Mobile Web"]
    Web --> Conversation["Conversation"]
    Conversation --> Understanding["Customer Understanding V1"]
    Understanding --> Package["Standard Work Package"]
    Package --> PriceBook["Internal Price Book"]
    PriceBook --> Quote["Deterministic Quick Quote"]
    Quote --> Customer["Customer Response"]
```

The understanding provider returns facts only. `QuickQuoteService` selects the active service and
work package, resolves the price through the location hierarchy, and performs arithmetic with
`Decimal`. Every selected price version is stored with the quote.

## Module Architecture

```mermaid
flowchart TB
    API["FastAPI delivery"] --> Conversation["Conversation module"]
    API --> QR["QR module"]
    API --> PricingAdmin["Pricing admin API"]
    Conversation --> Understanding["Customer Understanding port"]
    Understanding --> RuleBased["Rule-based adapter"]
    Understanding --> Fake["Fake adapter"]
    Understanding --> OpenAI["OpenAI adapter"]
    Conversation --> Quote["Quick Quote application service"]
    Quote --> Catalog["Service Catalog repository"]
    Quote --> WorkPackage["Work Package repository"]
    Quote --> Pricing["Pricing repository"]
    Quote --> Outbox["Database outbox"]
    Quote --> Audit["Audit log"]
```

The domain layer imports neither FastAPI, SQLAlchemy, nor any AI SDK. ORM rows and Pydantic API
schemas are separate from domain dataclasses.

## Data Ownership

| Module | Owned records |
| --- | --- |
| Conversation | `conversations`, `messages`, `customer_jobs` |
| Customer understanding | `analysis_snapshots` |
| Service catalog | `service_catalog` |
| Work package | `work_packages`, `work_package_items` |
| Pricing | `price_book` |
| Quick quote | `quick_quotes` |
| QR | `qr_sources` |
| Core infrastructure | `audit_events`, `outbox_events` |

Messages and snapshots are append-only. Price changes create new rows. Customer jobs are current
working projections whose changes are reconstructable from immutable messages and snapshots.

## Future Extension

```mermaid
flowchart LR
    Documents["Documents / DWG / BIM"] --> Quantity["Quantity Engine"]
    Quantity --> Resource["Resource Engine"]
    Resource --> Productivity["Productivity / Schedule"]
    Schedule["Schedule"] --> Feasibility["Feasibility"]
    Specification["Specification"] --> Feasibility
    Cost["Cost"] --> Feasibility
    Productivity --> Schedule
    QuickQuote["Current Quick Quote Core"] --> Outbox["Versioned Outbox Events"]
    Outbox -. subscribe .-> Quantity
    Outbox -. subscribe .-> Cost
```

Future engines subscribe to versioned events. They do not change customer intake, work-package,
or price-book contracts.
