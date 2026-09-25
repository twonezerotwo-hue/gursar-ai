# GÜRSAR AI — Global Construction Intelligence Roadmap

Current phase base commit: `405438e`

Current phase: `PHASE 7C — Resource Loading, Actuals and 4D Linkage`

Current overall simple score: `5.48 / 10`
Weighted product-readiness score: `5.37 / 10`

Status values: `NOT_STARTED`, `FOUNDATION`, `FUNCTIONAL`, `VALIDATED`,
`BLOCKED_BY_REAL_DATA`, `PRODUCTION_READY`. No area is production-ready without measured,
auditable, security-tested, documented, realistic-data evidence.

## Category scorecard

| Category | Before | After | /10 target | Status | Evidence | Main remaining gap |
|---|---:|---:|---:|---|---|---|
| Backend / Domain Architecture | 7.8 | 7.9 | 10 | VALIDATED | Resource engine, immutable schedule control, reversible migrations and 274 tests | Distributed production validation |
| Data Governance | 8.1 | 8.2 | 10 | VALIDATED | Frozen baselines, append-only progress/delays, audit and lineage | Retention and tenant policy |
| Security | 7.5 | 7.5 | 10 | VALIDATED | Ownership, RBAC, upload/license gates | Full threat model and penetration test |
| RAG / Knowledge Infrastructure | 7.8 | 7.8 | 10 | VALIDATED | 17,379 chunks, bounded FTS/vector retrieval, hierarchy | pgvector production benchmark |
| Construction Knowledge | 6.8 | 6.8 | 10 | FUNCTIONAL | Rule applicability, 17 domain routes, conflict/UNVERIFIED | Expert-validated multi-domain rule coverage |
| BIM / IFC Intelligence | 7.5 | 7.7 | 10 | VALIDATED | Verified BuildingElement-to-Activity 4D links plus IFC evidence | Floor/location 4D packaging and broader real IFC suite |
| Vector PDF Intelligence | 7.0 | 7.0 | 10 | VALIDATED | Sheet/region/scale/vector evidence | Diverse real drawing benchmark |
| DXF / CAD Intelligence | 7.2 | 7.2 | 10 | VALIDATED | Units, mappings, geometry, evidence | Office-standard and large-file validation |
| Vision Infrastructure | 6.5 | 6.5 | 10 | FUNCTIONAL | Dataset, benchmark, shadow/review lifecycle | Diverse real labeled drawings |
| Vision Model Intelligence | 2.5 | 2.5 | 10 | BLOCKED_BY_REAL_DATA | Training readiness gate prevents fake model | Verified real dataset and trained model |
| Quantity Intelligence | 6.8 | 6.8 | 10 | FUNCTIONAL | Deterministic evidence/source priority | Full BOQ rules and real tender validation |
| Cost Intelligence | 5.5 | 5.5 | 10 | FUNCTIONAL | Versioned prices and deterministic totals | Supplier/date/scenario/actual-cost depth |
| Architecture Intelligence | 6.2 | 6.2 | 10 | FUNCTIONAL | Space/zone graph, adjacency, backend area, deterministic evidence-linked QA | Real drawing extraction, regulation packs and expert benchmark |
| Structural Engineering | 5.8 | 5.8 | 10 | FUNCTIONAL | Shared structural graph, deterministic load-path checks, immutable loads and evidence | Rebar parser, code/solver adapters and engineer-reviewed real benchmark |
| MEP Intelligence | 2.0 | 2.0 | 10 | FOUNDATION | Shared taxonomy and MEP knowledge route | Discipline graphs and verified takeoff |
| Scheduling / Planning | 7.2 | 8.0 | 10 | FUNCTIONAL | Deterministic resource loading/leveling, frozen baselines, actuals, delay impact and 4D links | Calendars, multi-project leveling and real-field calibration |
| Site Management | 1.5 | 2.2 | 10 | FOUNDATION | Append-only verified progress and categorized delay events | Daily report, RFI, inspections and production UI |
| Safety / HSE | 1.7 | 1.7 | 10 | FOUNDATION | Domain routing and sourced-rule path | HSE records, deterministic risk engine |
| Quality / QAQC | 1.7 | 1.7 | 10 | FOUNDATION | Domain routing and sourced-rule path | ITP, NCR, inspection and punch workflow |
| Contract / Commercial | 2.1 | 2.1 | 10 | FOUNDATION | Contract routing, quote ledger | Contract/change/claim/payment engine |
| Global Regulations | 3.4 | 3.4 | 10 | FOUNDATION | Jurisdiction/date/building applicability | Municipality hierarchy and global corpora |
| Customer UX / Conversation | 6.3 | 6.4 | 10 | FUNCTIONAL | Context reuse, quick replies, CPM V2 details and schedule snapshots | Scenario comparison and progress journeys |
| Continuous Learning | 6.0 | 6.0 | 10 | FUNCTIONAL | Review, active learning, versioned datasets | Actual site/cost/productivity feedback |
| Production / SaaS | 4.5 | 4.5 | 10 | FOUNDATION | PostgreSQL compatibility, jobs, backup | Tenancy, observability, deployment hardening |
| Global Product Readiness | 5.3 | 5.5 | 10 | FUNCTIONAL | Integrated CPM, resource control, actuals, delay and 4D scheduling subsystem | Global real-project end-to-end validation |

## Completed phases

1. Conversation State V2, persistent project context, Planning V1 and Architecture V1.
2. Phase 2A Knowledge Brain: configurable source hierarchy, expanded domain routing,
   jurisdiction/date/building-type applicability, conflict surfacing, `UNVERIFIED`, and
   traceable evidence/checksum API.
3. Phase 3A Architectural Spatial Graph: shared ProjectGraph spaces and relationships,
   zones, deterministic area calculation, adjacency/access/privacy/daylight/wet-core QA,
   immutable analysis snapshots, audit evidence and admin API.
4. Phase 4A Structural Graph and Load-Path Foundation: shared structural elements and support
   relationships, immutable declared loads, topology/elevation/property checks, evidence, audit and
   admin API without unsafe design claims.
5. Phase 7A WBS and CPM V2: validated WBS hierarchy, all four dependency types with lag,
   corrected forward/backward passes, total/free float, multiple critical paths, exact crew peak and
   backward-compatible conversation/API output.
6. Phase 7B Productivity and Crew Intelligence: immutable source-ranked productivity records,
   crew composition and field-sample metadata, deterministic crew-day/crew/worker sizing,
   workforce-cap risks, three scenarios, audit trail and explicit heuristic disclosure.
7. Phase 7C Resource Loading, Actuals and 4D Linkage: deterministic workforce/equipment overload
   detection and bounded leveling, frozen idempotent baselines, append-only progress and delay
   intelligence, audit trail and verified ProjectGraph element linkage.

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
- Structural validation is topological only; member capacity, code load combinations, joints,
  reinforcement and finite-element results are deliberately outside this slice.
- Structural graph changes require a dedicated immutable relationship-correction UX.
- Bundled productivity inputs are still heuristic; the verified actual-output path now exists but
  needs completed-project field records.
- Resource calendars, shifts/holidays, multi-project leveling, floor/location 4D work packaging and
  earned-value metrics remain incomplete.
- Synthetic vision data has high duplicate-image concentration.
- Starlette TestClient emits one upstream deprecation warning.

## Real-data dependencies

- Licensed, verified architectural drawing set
- Completed-project duration, crew, quantity and actual-cost records
- Engineer-reviewed rule extraction and jurisdiction applicability
- Diverse real IFC/DXF/PDF projects

## Last validation

- Tests: `274/274` passed for this phase checkpoint
- Knowledge chunks protected: `17,379`
- Latest migration: `0023`
- Backup: `RESTORE_VALIDATED`, SQLite integrity check: `ok`
- Benchmark: 500 parallel activities, shared capacity 10, leveled in 282.41 ms; 490 shifts,
  0 remaining overloads
- Real-data benchmark: pending completed-project baselines, actuals and verified delay records

## Next highest priority phase

`PHASE 5B — Architecture Regulation and Drawing Integration`

Connect verified architectural spaces and drawing regions to jurisdiction-aware deterministic rules,
reviewable findings and architect-approved golden cases without inventing compliance conclusions.

## Next actions

1. Build architecture rule applicability packs on the existing knowledge hierarchy.
2. Link verified ViewRegions/spaces to rule checks and immutable evidence.
3. Prepare architect-reviewed golden cases; keep unvalidated rules explicitly `UNVERIFIED`.
