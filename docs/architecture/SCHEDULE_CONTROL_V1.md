# Schedule Control V1

## Scope

Phase 7C extends deterministic CPM with bounded resource loading, immutable baselines, append-only
progress and delay records, and initial Activity-to-BuildingElement 4D links. It does not claim that
the bundled productivity values are verified site actuals, and it does not ask an LLM to calculate
dates, float, resource demand or delay impact.

## Resource loading and leveling

Activities declare a workforce resource through `crew_code` and `crew_size`, plus optional
equipment requirements. Capacities are explicit inputs with either `WORKFORCE` or `EQUIPMENT`
kind. The engine first reports overload intervals from the unlevelled CPM plan, then applies a
deterministic serial schedule-generation scheme:

1. CPM produces a validated topological activity order.
2. Every activity is placed at its earliest dependency-safe time.
3. If the placement exceeds a declared workforce or equipment capacity, the activity is shifted in
   bounded one-day increments.
4. FS, SS, FF and SF dependency constraints and lag remain active against shifted predecessor
   times.
5. The result is checked again; remaining overloads are never hidden.

An activity whose individual demand exceeds capacity is rejected as infeasible instead of being
silently split into fractional crews.

## Immutable baseline

The baseline stores normalized activity timing, critical/float state, declared capacities,
pre-leveling overloads, assumptions and a SHA-256 checksum under `SCHEDULE_CONTROL_V1`. An
identical request reuses the existing frozen baseline. Changed input creates a new project-level
version; old baselines are not overwritten.

## Planned versus actual

Progress updates are append-only observations. The backend calculates linear planned progress at
the observation day and reports:

- planned and actual progress;
- progress variance;
- baseline and forecast finish;
- finish variance in days.

The record retains source and verification state. A later update creates another observation rather
than mutating the earlier report.

## Delay intelligence

Delay causes use a controlled taxonomy: material, labor, equipment, design, weather, client,
subcontractor, approval, quality, safety or unknown. For a critical activity, declared impact is
project impact. For a non-critical activity, available total float is consumed first. This is a
transparent deterministic schedule calculation, not a legal entitlement or claim determination.

## 4D ProjectGraph linkage

Activities carrying element types are linked only to `VERIFIED` or `CORRECTED` BuildingElements in
the same project. Predicted or review-required elements do not enter the 4D baseline. The first
slice links by verified element type; floor/location work packaging remains the next refinement.

## Authorization and audit

Admin authorization protects baseline, progress and delay endpoints. Baseline creation, progress
observations and delay events produce append-only audit events. Cross-project baseline and element
access is rejected.

## Remaining boundary

The next scheduling slice should add resource calendars, holidays/shifts, multi-project capacity,
floor/location work packages, equipment maintenance windows, earned-value metrics and a dedicated
timeline UI. Real forecasting accuracy remains blocked until completed-project baseline and actual
records are imported and independently reviewed.
