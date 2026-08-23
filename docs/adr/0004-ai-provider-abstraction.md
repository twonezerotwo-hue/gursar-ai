# ADR 0004: AI Provider Abstraction

## Status

Accepted.

## Context

Natural-language understanding benefits from AI, but business state must not depend on a specific
vendor or unvalidated prose.

## Decision

Use the `CustomerUnderstandingProvider` port and the strict
`CustomerUnderstandingResultV1` Pydantic contract. Provide rule-based, fake, and OpenAI adapters.
The default rule-based adapter runs offline. Invalid provider output is rejected before any job or
quote mutation and every accepted result is appended as an immutable snapshot.

## Consequences

Providers can change without domain rewrites. Tests are deterministic and offline. AI remains
responsible for fact extraction only; prices, totals, feasibility, and compliance remain in
deterministic engines.
