# DXF Intelligence

## Pipeline

Persistent project uploads validate DXF content with `ezdxf`, calculate SHA-256, and create a
`DocumentVersion`. Analysis uses structured DXF entities directly; rasterization and LLM quantity
calculation are not part of this path.

`DocumentVersion → unit resolution → entity geometry → semantic mapping → BuildingElement →
Measurement → MeasurementEvidence → reviewed TakeoffItem`

Analysis is idempotent for `(document_version_id, DXF_INTELLIGENCE_V1)`.

## Unit handling

Internal physical coordinates use meters. `$INSUNITS` supports millimeter, centimeter, meter,
inch, and foot. Missing units produce `UNKNOWN`; geometry remains reviewable but physical
measurements are blocked. Expert overrides are append-only `DxfUnitResolution` records and emit
`SET_DXF_UNIT` audit events. A metadata/override disagreement is reported as `CONFLICT` and is
never silently resolved.

## Semantic mapping

Verified resolution order is project, office profile, then global. Equal-priority disagreement
produces `SEMANTIC_MAPPING_CONFLICT`. Deterministic layer/block heuristics produce only
`REVIEW_REQUIRED` candidates. Unknown layers remain `UNKNOWN` and enter the existing active
learning queue. Global mappings require an ADMIN reviewer.

## Geometry and evidence

Supported entities are LINE, LWPOLYLINE, POLYLINE, ARC, CIRCLE, ELLIPSE, HATCH, INSERT, TEXT,
MTEXT, and DIMENSION. Deterministic calculations cover length, area, circumference, and count.
Self-intersecting, zero-area, and incomplete polygons are marked `GEOMETRY_INVALID` and do not
produce final measurements.

Every measurement has evidence containing document version, entity handle, entity type, layer,
block, raw geometry, normalized geometry, raw/normalized units, resolution method, and algorithm
version.

## Review and training

Heuristic/unknown candidates use the common immutable review workflow. Correction preserves the
original element, creates a new revision, closes the related DXF active-learning item, and adds a
`HUMAN_CORRECTION` item. Eligible corrected DXF elements can create the existing governed
`TrainingSample` task types such as `DXF_ELEMENT_CLASSIFICATION`.

## Safety limits

Analysis has a bounded entity count and does not expand nested blocks. Persistent uploads reject
invalid content instead of trusting the extension. Verified DXF measurements can create takeoff
candidates with quantity source `VERIFIED_DXF_GEOMETRY`; heuristic quantities never enter a
verified takeoff automatically.
