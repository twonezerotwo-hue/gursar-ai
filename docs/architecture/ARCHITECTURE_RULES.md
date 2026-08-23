# Architecture Rules

1. Domain code does not import FastAPI, SQLAlchemy, or an AI provider SDK.
2. API handlers validate and delegate; they do not calculate price or totals.
3. ORM rows, domain dataclasses, API schemas, and AI schemas remain separate types.
4. Modules use application contracts and repositories, not another module's tables.
5. All customer prices originate in the versioned GÜRSAR internal price book.
6. Price arithmetic is deterministic `Decimal` application logic.
7. Service codes, work packages, AI contracts, public APIs, prices, and events are versionable.
8. Customer messages and analysis snapshots are immutable.
9. Extracted facts include source message, source type, and confidence evidence.
10. Weaker AI inference cannot overwrite confirmed or human-verified facts.
11. Turnkey defaults come from work-package data, never from prompt-only assumptions.
12. Supply responsibility is configured per work-package item.
13. Missing prices emit `price.missing.v1` and never produce a number.
14. New integrations enter behind a port; tests must run without network access.
15. Secrets and internal administration never appear on customer endpoints.
16. New modules integrate through versioned contracts or outbox events.
17. No generic utility dumping ground or global mutable business state is permitted.
18. Microservices, queues, and workflow systems require a measured scaling need and a new ADR.
