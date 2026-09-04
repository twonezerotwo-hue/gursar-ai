# Planning CPM V2

## Scope

Phase 7A strengthens the deterministic scheduling core used by both the internal intelligence API
and customer conversation flow. It does not perform resource leveling, forecast actual completion,
or claim that heuristic productivity is verified field data.

## WBS validation

`WorkBreakdownStructure` is an explicit hierarchy of unique code, name and optional parent code.
Before scheduling, the engine rejects:

- missing or duplicate codes;
- unknown parents;
- parent cycles;
- activities assigned to an unknown WBS code.

The residential schedule generator now runs against its declared WBS instead of treating WBS codes
as display-only strings.

## CPM relationships

The forward and backward passes support:

- finish-to-start (`FS`);
- start-to-start (`SS`);
- finish-to-finish (`FF`);
- start-to-finish (`SF`);
- positive, zero or negative lag.

The engine calculates early start/finish, late start/finish, total float and free float using
`Decimal`. Backward constraints retain the project-finish bound for non-FS relationships. Duplicate,
self-referencing, unknown and cyclic dependencies are rejected.

## Critical paths

All zero-total-float paths connected by binding precedence constraints are returned as
`critical_paths`. The legacy `critical_path` field remains as the first path for backward
compatibility. Enumeration is iterative and protected by a configurable maximum-path guard.

## Resource peak

Peak crew is calculated using exact schedule boundary events rather than integer-day sampling.
Activities finishing at a timestamp do not overlap activities starting at the same timestamp.
Resource capacity and leveling are intentionally deferred to Phase 7B/7C.

## Persistence and API

Conversation plan snapshots now preserve ES, EF, LS, LF, total float and free float for every
activity. Existing response fields remain unchanged, while internal schedule and conversation
responses add `critical_paths` and detailed float fields.

## Validation boundary

The deterministic algorithm is covered by synthetic golden networks and a 2,000-activity scale
benchmark. Durations still rely on configurable but currently heuristic residential productivity
records. Real project actuals are required before duration accuracy can be claimed.
