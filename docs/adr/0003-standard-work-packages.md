# ADR 0003: Standard Work Packages

## Status

Accepted.

## Context

Customers ask for recognizable construction jobs and expect standard scope to be understood
without a long questionnaire.

## Decision

Represent standard scope as versioned work packages and items. Each item records default inclusion,
supply responsibility, and pricing role. Turnkey is the default when the selected package says so.
Explicit customer additions and exclusions override defaults and are retained on the job projection.

## Consequences

The assistant can quote early and ask only category-defining questions. Future add-ons, credits,
exclusions, and custom pricing can be introduced without replacing the package schema.
