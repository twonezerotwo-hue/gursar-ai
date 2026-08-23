# GÜRSAR AI

GÜRSAR AI is a quote-first construction intake foundation. A customer can scan a GÜRSAR QR
code, describe work naturally, and receive a unit price or approximate total sourced only from
the versioned internal price book.

## What Works

- Anonymous QR entry at `/q/CARD001` with source attribution
- Turkish construction intent, quantity, location, addition, and exclusion extraction
- Multiple independent jobs in one customer message
- Standard turnkey work packages and customer scope overrides
- Hierarchical location pricing: locality → district → city → country → global
- Deterministic totals using `Decimal`; the understanding provider never calculates prices
- Immutable messages and understanding snapshots with evidence
- Audit and transactional outbox records
- Versioned internal price-book API protected by `X-Admin-Key`
- Mobile-first customer chat served by FastAPI

## Local Setup

Python 3.12 or newer is required.

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -e ".[dev]"
python3 -m uvicorn app.main:app --app-dir backend --reload
```

Open [http://127.0.0.1:8000/q/CARD001](http://127.0.0.1:8000/q/CARD001).

The local defaults run without environment setup and use SQLite. Export values based on
`.env.example` before starting the process when configuration changes are needed. Set
`GURSAR_DATABASE_URL` to a PostgreSQL SQLAlchemy URL for a shared environment. Run migrations with:

```bash
alembic -c backend/alembic.ini upgrade head
```

## Quality Checks

```bash
ruff check .
pyright
pytest
```

## Price Book Administration

Set a strong `GURSAR_ADMIN_API_KEY`, then send it as `X-Admin-Key`.

```bash
curl -H "X-Admin-Key: $GURSAR_ADMIN_API_KEY" \
  "http://127.0.0.1:8000/api/v1/admin/prices?service_code=formwork.standard.turnkey"
```

Creating a new price or changing an existing one inserts a new immutable version. Deactivation
also creates a newer inactive version; previous quote references remain reproducible.

Seed values are internal V1 demonstration records, not external market data. The formwork seed
uses the approved `1,350 TRY/m2` scenario. Replace demonstration records through the admin API
before customer production use.

## Repository Shape

```text
backend/app/
├── contracts/v1/                 versioned cross-module schemas
├── core/                         database, security, audit, outbox
├── modules/
│   ├── conversation/             immutable messages and customer jobs
│   ├── customer_understanding/   provider port, structured extraction, snapshots
│   ├── service_catalog/          versionable service definitions
│   ├── work_package/             standard scope and supply responsibility
│   ├── pricing/                  internal price versions and location resolution
│   ├── quick_quote/              deterministic quote orchestration
│   └── qr/                       source attribution and anonymous entry
└── main.py                       composition and HTTP delivery
frontend/                         dependency-free mobile customer UI
docs/                             architecture, ADRs, and contracts
```

Start with [Architecture Overview](docs/architecture/OVERVIEW.md) and
[Architecture Rules](docs/architecture/ARCHITECTURE_RULES.md).
