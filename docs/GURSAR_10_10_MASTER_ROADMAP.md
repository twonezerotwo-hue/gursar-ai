# GÜRSAR AI — Global Construction Intelligence Roadmap

Status values: `NOT_STARTED`, `FOUNDATION`, `FUNCTIONAL`, `VALIDATED`, `PRODUCTION_READY`.
No area is marked production-ready without functional, measured, versioned, auditable,
documented and production-safe evidence.

## Current scorecard

| Area | Status | Evidence / next gate |
|---|---|---|
| Backend architecture | VALIDATED | Modular monolith, migrations, automated test suite |
| Security | VALIDATED | Conversation ownership, review RBAC, upload controls, audit |
| Customer UX | FUNCTIONAL | Structured responses, persistent context, quick replies |
| LLM orchestration | FUNCTIONAL | Local-first fallback chain; specialist routing is next |
| RAG / knowledge | VALIDATED | Bounded hybrid retrieval and versioned knowledge |
| Architecture | FUNCTIONAL | Metrics and baseline QA; spatial adjacency comes next |
| Structural | FOUNDATION | IFC graph and quantities; drawing/rebar parser comes next |
| IFC | VALIDATED | Elements, Qto/geometry fallback and evidence |
| Vector PDF | VALIDATED | Metadata, regions, scale, geometry and evidence |
| DXF | VALIDATED | Units, semantics, geometry and evidence |
| Vision | FOUNDATION | Dataset, shadow inference, review and benchmark infrastructure |
| Quantity | VALIDATED | Source priority, evidence and review gates |
| Cost | FUNCTIONAL | Versioned price book and deterministic totals |
| Planning | FUNCTIONAL | WBS, CPM, baseline productivity and crew plan |
| Site / HSE / Quality | NOT_STARTED | Sprint 4 target |
| MEP | FOUNDATION | Common element taxonomy only |
| Contract / commercial | FOUNDATION | Quote and ledger; contract engine pending |
| Global regulations | FOUNDATION | Türkiye corpus; jurisdiction engine pending |
| Continuous learning | FUNCTIONAL | Review, active learning, dataset lifecycle |
| Production / SaaS | FOUNDATION | PostgreSQL compatibility and jobs; tenancy pending |

## Delivery sequence

1. Customer Conversation V2, persistent project context, Planning V1 and Architecture V1.
2. Construction Knowledge Brain collections and structured rule priority.
3. Architectural spatial graph and structural drawing/rebar intelligence.
4. Site, HSE and quality records connected to planned-vs-actual learning.
5. Cost scenarios, contracts, payment certificates and global jurisdiction engine.
6. Multi-tenant production hardening, observability, rate limits and disaster recovery.

## Sprint 1 engineering boundaries

- Planning quantities are drivers, not approved takeoff.
- CPM and crew arithmetic run in deterministic code, never in an LLM.
- Productivity baselines are explicitly unverified until calibrated with completed projects.
- Conversation context only changes from explicit or clearly derived customer facts.
- A customer answer asks at most one question and keeps quick replies optional.
