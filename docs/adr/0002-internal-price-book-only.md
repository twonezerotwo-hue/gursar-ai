# ADR 0002: Internal Price Book Only

## Status

Accepted.

## Context

Customer quotations are financially sensitive. Web prices, model knowledge, and inferred market
averages are neither authoritative nor reproducible.

## Decision

Every customer-facing number comes from a versioned GÜRSAR internal price row selected by service,
location hierarchy, validity interval, and active state. Quote rows store the exact price row and
version. Arithmetic uses `Decimal` application code.

## Consequences

Missing data produces `PRICE_NOT_CONFIGURED`, a professional customer response, an audit record,
and `price.missing.v1`. It never produces an estimated number. Price changes insert new versions;
history used by old quotes remains intact.
