# ADR 0001: Modular Monolith

## Status

Accepted.

## Context

The product needs clean boundaries for intake, understanding, packages, pricing, quotes, and QR
sources, but V1 does not need operationally independent services.

## Decision

Build one FastAPI deployment with module-owned domain, application, infrastructure, and API
layers. Use a database outbox for future subscribers. PostgreSQL is the shared-environment target;
SQLite is the local and test default.

## Consequences

Transactions remain simple and deployment stays small. Module boundaries are enforced in code
review rather than by network calls. A module may be extracted later only when its load or release
cadence requires it.
