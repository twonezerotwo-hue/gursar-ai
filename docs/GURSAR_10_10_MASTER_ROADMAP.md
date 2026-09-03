# GÜRSAR AI — Global Construction Intelligence Roadmap

Current phase base commit: `b03ecb5`

Current phase: `PHASE 3A — Architectural Spatial Graph`

Current overall simple score: `5.22 / 10`
Weighted product-readiness score: `5.00 / 10`

Status values: `NOT_STARTED`, `FOUNDATION`, `FUNCTIONAL`, `VALIDATED`,
`BLOCKED_BY_REAL_DATA`, `PRODUCTION_READY`. No area is production-ready without measured,
auditable, security-tested, documented, realistic-data evidence.

## Category scorecard

| Category | Before | After | /10 target | Status | Evidence | Main remaining gap |
|---|---:|---:|---:|---|---|---|
| Backend / Domain Architecture | 7.5 | 7.6 | 10 | VALIDATED | Shared ProjectGraph, idempotent spatial service, migration, 238 tests | Distributed production validation |
| Data Governance | 7.7 | 7.8 | 10 | VALIDATED | Immutable analysis snapshot, audit, evidence IDs, existing rights/lineage | Retention and tenant policy |
| Security | 7.5 | 7.5 | 10 | VALIDATED | Ownership, RBAC, upload/license gates | Full threat model and penetration test |
| RAG / Knowledge Infrastructure | 7.2 | 7.8 | 10 | VALIDATED | 17,379 chunks, bounded FTS/vector retrieval, hierarchy | pgvector production benchmark |
| Construction Knowledge | 5.5 | 6.8 | 10 | FUNCTIONAL | Rule applicability, 17 domain routes, conflict/UNVERIFIED | Expert-validated multi-domain rule coverage |
| BIM / IFC Intelligence | 7.5 | 7.5 | 10 | VALIDATED | Qto, geometry, evidence, relations | Broader real IFC suite and 4D/5D |
| Vector PDF Intelligence | 7.0 | 7.0 | 10 | VALIDATED | Sheet/region/scale/vector evidence | Diverse real drawing benchmark |
| DXF / CAD Intelligence | 7.2 | 7.2 | 10 | VALIDATED | Units, mappings, geometry, evidence | Office-standard and large-file validation |
| Vision Infrastructure | 6.5 | 6.5 | 10 | FUNCTIONAL | Dataset, benchmark, shadow/review lifecycle | Diverse real labeled drawings |
| Vision Model Intelligence | 2.5 | 2.5 | 10 | BLOCKED_BY_REAL_DATA | Training readiness gate prevents fake model | Verified real dataset and trained model |
| Quantity Intelligence | 6.8 | 6.8 | 10 | FUNCTIONAL | Deterministic evidence/source priority | Full BOQ rules and real tender validation |
| Cost Intelligence | 5.5 | 5.5 | 10 | FUNCTIONAL | Versioned prices and deterministic totals | Supplier/date/scenario/actual-cost depth |
| Architecture Intelligence | 4.8 | 6.2 | 10 | FUNCTIONAL | Space/zone graph, adjacency, backend area, deterministic evidence-linked QA | Real drawing extraction, regulation packs and expert benchmark |
| Structural Engineering | 4.0 | 4.0 | 10 | FOUNDATION | IFC structural elements and estimates | Load path, rebar parser, solver interfaces |
| MEP Intelligence | 2.0 | 2.0 | 10 | FOUNDATION | Shared taxonomy and MEP knowledge route | Discipline graphs and verified takeoff |
| Scheduling / Planning | 5.8 | 5.8 | 10 | FUNCTIONAL | WBS, CPM, productivity and crew plan | Resource leveling and actual calibration |
| Site Management | 1.5 | 1.5 | 10 | NOT_STARTED | Site knowledge route only | Daily report, progress, RFI and issues |
| Safety / HSE | 1.5 | 1.7 | 10 | FOUNDATION | Domain routing and sourced-rule path | HSE records, deterministic risk engine |
| Quality / QAQC | 1.5 | 1.7 | 10 | FOUNDATION | Domain routing and sourced-rule path | ITP, NCR, inspection and punch workflow |
| Contract / Commercial | 2.0 | 2.1 | 10 | FOUNDATION | Contract routing, quote ledger | Contract/change/claim/payment engine |
| Global Regulations | 3.0 | 3.4 | 10 | FOUNDATION | Jurisdiction/date/building applicability | Municipality hierarchy and global corpora |
| Customer UX / Conversation | 6.2 | 6.3 | 10 | FUNCTIONAL | Persistent context, quick replies, rule API | Automatic safe tool invocation and golden journeys |
| Continuous Learning | 6.0 | 6.0 | 10 | FUNCTIONAL | Review, active learning, versioned datasets | Actual site/cost/productivity feedback |
| Production / SaaS | 4.5 | 4.5 | 10 | FOUNDATION | PostgreSQL compatibility, jobs, backup | Tenancy, observability, deployment hardening |
| Global Product Readiness | 4.7 | 4.9 | 10 | FOUNDATION | Integrated deterministic subsystems plus architectural spatial graph | Global real-project end-to-end validation |

## Completed phases

1. Conversation State V2, persistent project context, Planning V1 and Architecture V1.
2. Phase 2A Knowledge Brain: configurable source hierarchy, expanded domain routing,
   jurisdiction/date/building-type applicability, conflict surfacing, `UNVERIFIED`, and
   traceable evidence/checksum API.
3. Phase 3A Architectural Spatial Graph: shared ProjectGraph spaces and relationships,
   zones, deterministic area calculation, adjacency/access/privacy/daylight/wet-core QA,
   immutable analysis snapshots, audit evidence and admin API.

## Blocked areas

- `Vision Model Intelligence`: `BLOCKED_BY_REAL_DATA`
- Production compliance claims: `BLOCKED_BY_EXPERT_VALIDATION`
- Global regulation coverage: `REAL_DATA_REQUIRED`
- Cost/productivity calibration: `REAL_COMPLETED_PROJECT_DATA_REQUIRED`

## Known technical debt

- Knowledge applicability is not yet automatically invoked from every conversation path.
- Rule exception/dependency graphs and municipality inheritance are not modeled.
- Architectural graph changes require the existing review/revision workflow to be exposed as a
  dedicated program-editing UX; the analysis service correctly refuses silent mutation.
- Architectural regulation checks and drawing-derived spaces still require licensed real data and
  architect-reviewed golden cases.
- Resource leveling and project actuals are incomplete.
- Synthetic vision data has high duplicate-image concentration.
- Starlette TestClient emits one upstream deprecation warning.

## Real-data dependencies

- Licensed, verified architectural drawing set
- Completed-project duration, crew, quantity and actual-cost records
- Engineer-reviewed rule extraction and jurisdiction applicability
- Diverse real IFC/DXF/PDF projects

## Last validation

- Tests: `238/238` passed for this phase checkpoint
- Knowledge chunks protected: `17,379`
- Latest migration: `0020`
- Migration backup: restore-validated SQLite backup before upgrade; post-upgrade integrity `ok`
- Benchmark: 250 spaces and 249 connections in 338.78 ms; idempotent reuse in 8.48 ms
- Real-data benchmark: pending licensed architectural programs and expert review

## Next highest priority phase

`PHASE 4A — Structural Graph and Load-Path Foundation`

Deliver reusable support/load relationships on the shared ProjectGraph, deterministic load-path
continuity checks, structural issue evidence and engineer-reviewed golden tests without performing
automatic structural design.

## Next actions

1. Commit and push the green Phase 3A checkpoint.
2. Define structural load/support relationships on existing BuildingElements.
3. Add deterministic continuity checks before any solver or reinforcement design integration.
