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
- Versioned, source-linked Turkish construction legislation and technical-specification search
- Mobile-first customer chat served by FastAPI

## Local Setup

Python 3.12 or newer is required.

Install [Ollama](https://ollama.com/), then download the local conversational model once:

```bash
ollama pull qwen3:4b
```

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -e ".[dev]"
python3 -m uvicorn app.main:app --app-dir backend --reload
```

Open [http://127.0.0.1:8000/q/CARD001](http://127.0.0.1:8000/q/CARD001).

The local defaults run without environment setup and use SQLite. Export values based on
`.env.example` before starting the process when configuration changes are needed. The default
chat provider is the local `qwen3:4b` model served by Ollama. If Ollama is unavailable, quote
generation remains deterministic and the customer receives the safe fallback response. Set
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

The chat model is restricted to construction, architecture, renovation, and site-work topics.
It turns verified price-book results into natural responses but is not allowed to invent or
calculate a monetary value that is absent from the internal price book.

## Official Legal Corpus

Import or refresh the official Turkish construction corpus with:

```powershell
$env:PYTHONPATH = "backend"
.\.venv\Scripts\python.exe backend\scripts\ingest_legal_corpus.py --missing-only
```

The importer accepts HTTPS documents only from Turkish government domains (`*.gov.tr`). Each
document is checksum-versioned, split into searchable passages, and retained with its authority,
source URL, publication metadata, and retrieval time. Legal answers receive only the best matching
passages and expose their official sources; the model must say when the retrieved material is
insufficient.

The corpus is an informational aid, not legal or permit approval. Scanned PDFs require OCR before
they become searchable. TSE standards are referenced but their licensed full texts must not be
copied into the system without an appropriate licence. See
[Mevzuat Coverage Report](docs/MEVZUAT_KAPSAM_RAPORU.md) for the current snapshot and known gaps.

## Price Book Administration

Set a strong `GURSAR_ADMIN_API_KEY`, then send it as `X-Admin-Key`.

```bash
curl -H "X-Admin-Key: $GURSAR_ADMIN_API_KEY" \
  "http://127.0.0.1:8000/api/v1/admin/prices?service_code=formwork.labor"
```

Creating a new price or changing an existing one inserts a new immutable version. Deactivation
also creates a newer inactive version; previous quote references remain reproducible.

The active labor seeds are user-provided temporary company prices: reinforcement labor
`500 TRY/m2`, formwork labor `700 TRY/m2`, masonry labor `200 TRY/m2`, and formwork labor plus
material `925 TRY/m2` (`700 + 225`). Replace or version these records through the admin API as the
remaining company price list becomes available.

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
