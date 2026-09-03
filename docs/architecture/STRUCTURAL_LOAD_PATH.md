# Structural Graph and Load-Path Foundation

## Scope

Phase 4A adds a deterministic topology check for declared structural support paths. It deliberately
does not size members, design reinforcement, solve a finite-element model, combine code loads, or
approve structural safety. Those activities require a licensed engineer and validated solver/code
adapters.

## Shared ProjectGraph

Columns, shear walls, walls, beams, slabs, foundations, footings and piles remain shared
`BuildingElement` records. A declared load-bearing edge is an `ElementRelationship` with:

- `relationship_type=STRUCTURAL_SUPPORTS`;
- source = supporting element;
- target = supported element;
- connection and load-path flags in relationship metadata.

The analysis therefore does not create a competing structural graph. It projects the existing
ProjectGraph into a directed supported-to-supporting graph for validation.

## Deterministic validation

The versioned `structural_qa.yaml` configuration defines allowed support types, terminal foundation
types, required member properties and elevation tolerance. The service reports:

- unsupported structural elements;
- paths that do not reach a foundation, footing or pile;
- cycles in the support graph;
- incompatible supported/supporting element types;
- a supporting element declared above the supported element;
- missing member properties;
- elements that still require expert review.

Every graph finding contains the exact element IDs and, where applicable, relationship IDs used as
evidence. Unknown geometry or capacity is never invented.

## Loads

Declared loads are immutable source-linked inputs with type, magnitude in kN, factor, direction and
an optional engineering reference. The reported factored-load metric is only a transparent sum of
declared inputs. It is not a design combination, demand, capacity or compliance conclusion.

## Immutability and idempotency

Canonical graph input plus analysis version is SHA-256 hashed. An identical request reuses its
`StructuralAnalysisRun` and never duplicates relationships or loads. A changed connection on an
existing edge returns `STRUCTURAL_GRAPH_REVISION_REQUIRED`; no relationship is silently overwritten.
Each new run writes an append-only audit event.

## API and authorization

The existing admin-key policy protects:

- `POST /api/v1/intelligence/projects/{project_id}/structural/load-path`
- `GET /api/v1/intelligence/projects/{project_id}/structural/load-path`

Cross-project elements, non-structural elements, inactive predictions, invalid loads, duplicate
edges and malformed graph references are rejected before graph persistence.

## Next boundary

The next structural slices should add reviewed grid/joint semantics, deterministic drawing/rebar
parsing, code-rule adapters and a solver-neutral analysis contract. Production safety claims remain
blocked until engineer-reviewed real projects and independent calculation benchmarks exist.
