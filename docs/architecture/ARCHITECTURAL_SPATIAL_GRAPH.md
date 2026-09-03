# Architectural Spatial Graph

## Purpose

Phase 3A adds a deterministic architectural program and spatial-quality layer to the shared
ProjectGraph. It does not generate a design and does not replace an architect's review. It stores
reviewed space geometry, explicit connections and zones, then produces traceable QA findings.

## Shared graph model

- A space is a `BuildingElement` with `element_type=ROOM` and a subtype of `ROOM`,
  `CIRCULATION`, `CORE`, `SERVICE` or `ENTRANCE`.
- A connection is an `ElementRelationship` with an explicit connection type and accessibility
  metadata.
- Zones and memberships add program grouping without creating a second element graph.
- Geometry is accepted in project metres. Polygon area is always recalculated by the backend.

The service rejects invalid, zero-area and self-intersecting boundaries. Existing graph records are
never silently overwritten. A changed geometry, semantic property, connection or zone membership
returns `SPATIAL_GRAPH_REVISION_REQUIRED` so that a future immutable correction flow can own the
change.

## Deterministic checks

Thresholds and enabled checks live in `architecture_qa.yaml`. Version 1 evaluates:

- net/gross, circulation and core ratios;
- reachability from an entrance over accessible connections;
- direct public/private openings;
- required daylight evidence;
- declared accessibility conflicts;
- wet-space adjacency to a core or service space;
- dead-end circulation candidates.

Each issue references the exact element and, where relevant, relationship identifiers used to
produce it. Missing input is recorded as an assumption instead of being invented.

## Immutability and idempotency

Canonical input plus analysis version is SHA-256 hashed. Repeating the same request returns the
same analysis run and does not duplicate elements, relationships, zones or memberships.
`ArchitectureAnalysisRun` is an immutable snapshot of metrics, findings and assumptions. Creation
also writes an append-only audit event.

## API

The endpoints are protected by the existing admin-key policy:

- `POST /api/v1/intelligence/projects/{project_id}/architecture/spatial-graph`
- `GET /api/v1/intelligence/projects/{project_id}/architecture/spatial-graph`

The POST response exposes calculated metrics, evidence-linked findings, graph identifiers,
analysis version and input checksum. HTTP 409 means an immutable revision is required; invalid
geometry or graph references return HTTP 422.

## Current boundary

This phase validates manually supplied program geometry. It does not yet infer rooms from drawings,
check municipality-specific design regulations, simulate egress distances, or provide code-compliant
design approval. Those require verified jurisdictional rules and real-project benchmarks.
