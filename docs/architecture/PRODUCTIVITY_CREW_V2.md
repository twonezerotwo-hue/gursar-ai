# Productivity and Crew Intelligence V2

## Scope

Phase 7B turns planning productivity from an anonymous heuristic lookup into an auditable,
source-ranked input. It adds immutable productivity versions and deterministic crew-day, crew and
worker sizing for `FAST`, `BALANCED` and `ECONOMIC` scenarios. It does not claim that the bundled
residential baselines are real site actuals.

## Source hierarchy

The resolver applies this order before location, verification and version tie-breakers:

1. `VERIFIED_PROJECT_ACTUAL`
2. `COMPANY_STANDARD`
3. `VERIFIED_REFERENCE`
4. `HEURISTIC`

A record marked `verified=true` cannot use `HEURISTIC`. Every record can retain its work type,
quantity and duration sample, productive hours, crew composition, location, floor, confidence and
human-readable source. A correction creates a new active version and deactivates the previous row;
the historical row is not overwritten.

## Deterministic calculations

For a compatible unit, the engine calculates:

```text
crew-days = quantity / output per crew-day
required crews = ceil(crew-days / target duration)
required workers = required crews * crew size
planned duration = crew-days / available crews
```

An optional worker cap limits crews to whole teams. If that limit makes the target impossible, the
response carries an explicit risk instead of hiding the variance. Crew role composition is scaled
with the selected number of teams.

## Scenario policy

The balanced target is transformed deterministically into three alternatives:

- `FAST`: 75% of the balanced target, rounded up;
- `BALANCED`: the supplied target;
- `ECONOMIC`: 125% of the balanced target, rounded up.

Each scenario returns its source tier, confidence, verification state, assumptions and risks. The
planning response also explicitly states when all productivity inputs are heuristic.

## API and governance

The existing admin-key authorization protects:

- `POST /api/v1/intelligence/productivity-records`
- `GET /api/v1/intelligence/productivity-records`
- `POST /api/v1/intelligence/productivity/crew-plan`

Creating a version writes `PRODUCTIVITY_RECORD_VERSION_CREATED` to the append-only audit log.
Invalid units, non-positive inputs, inconsistent crew composition and false verified provenance are
rejected.

## Remaining boundary

Phase 7C should add resource loading and leveling, equipment constraints, schedule baselines,
progress actuals, delay events and Activity-to-BuildingElement 4D links. Productivity calibration
remains blocked until completed-project field records are imported and engineer-reviewed.
