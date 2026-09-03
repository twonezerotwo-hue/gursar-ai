# GÜRSAR AI — Global Construction Intelligence Roadmap

Current phase base commit: `dc1a2f3`

Current phase: `PHASE 2A — Construction Knowledge Brain Applicability`

Current overall simple score: `5.15 / 10`
Weighted product-readiness score: `4.90 / 10`

Status values: `NOT_STARTED`, `FOUNDATION`, `FUNCTIONAL`, `VALIDATED`,
`BLOCKED_BY_REAL_DATA`, `PRODUCTION_READY`. No area is production-ready without measured,
auditable, security-tested, documented, realistic-data evidence.

## Category scorecard

| Category | Before | After | /10 target | Status | Evidence | Main remaining gap |
|---|---:|---:|---:|---|---|---|
| Backend / Domain Architecture | 7.5 | 7.5 | 10 | VALIDATED | Modular monolith, migrations, 231 tests | Distributed production validation |
| Data Governance | 7.5 | 7.7 | 10 | VALIDATED | Version, rights, lineage, applicability evidence | Retention and tenant policy |
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
| Architecture Intelligence | 4.8 | 4.8 | 10 | FUNCTIONAL | Baseline metrics and QA | Spatial graph, adjacency and real review |
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
| Global Product Readiness | 4.5 | 4.7 | 10 | FOUNDATION | Integrated deterministic subsystems | Global real-project end-to-end validation |

## Completed phases

1. Conversation State V2, persistent project context, Planning V1 and Architecture V1.
2. Phase 2A Knowledge Brain: configurable source hierarchy, expanded domain routing,
   jurisdiction/date/building-type applicability, conflict surfacing, `UNVERIFIED`, and
   traceable evidence/checksum API.

## Blocked areas

- `Vision Model Intelligence`: `BLOCKED_BY_REAL_DATA`
- Production compliance claims: `BLOCKED_BY_EXPERT_VALIDATION`
- Global regulation coverage: `REAL_DATA_REQUIRED`
- Cost/productivity calibration: `REAL_COMPLETED_PROJECT_DATA_REQUIRED`

## Known technical debt

- Knowledge applicability is not yet automatically invoked from every conversation path.
- Rule exception/dependency graphs and municipality inheritance are not modeled.
- Resource leveling and project actuals are incomplete.
- Synthetic vision data has high duplicate-image concentration.
- Starlette TestClient emits one upstream deprecation warning.

## Real-data dependencies

- Licensed, verified architectural drawing set
- Completed-project duration, crew, quantity and actual-cost records
- Engineer-reviewed rule extraction and jurisdiction applicability
- Diverse real IFC/DXF/PDF projects

## Last validation

- Tests: `231/231` expected for this phase checkpoint
- Knowledge chunks protected: `17,379`
- Latest migration before this phase: `0019`
- This phase adds no schema migration
- Benchmark: deterministic knowledge applicability tests; global expert benchmark pending

## Next highest priority phase

`PHASE 3A — Architectural Spatial Graph`

Deliver a reusable Building/Level/Space/Zone/Circulation/Core graph, adjacency and wet-zone
checks, deterministic architecture QA, evidence references, and residential golden tests.

## Next actions

1. Complete the full regression/security/integrity gate for Phase 2A.
2. Commit and push the clean Phase 2A checkpoint.
3. Start Phase 3A from the graph model only after the checkpoint is green.
