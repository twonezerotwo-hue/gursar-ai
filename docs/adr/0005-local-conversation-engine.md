# ADR 0005: Local Conversation Model with Deterministic Actions

## Status

Accepted — 2026-08-23

## Context

The original local `qwen3:4b` chat adapter could phrase construction answers, but a raw language
model cannot safely own price arithmetic or the estimate ledger. Repeated price questions,
revisions, and topic switches must have stable application semantics even when Ollama is offline
or returns an invalid answer.

The development machine has an NVIDIA RTX 2070 with 8 GB VRAM and 16 GB system RAM. A quantized
8B model is the largest practical default that can remain primarily GPU-resident with a bounded
context window.

## Decision

1. Use `qwen3:8b` as the primary free local conversation model with an 8192-token context window.
2. Keep `qwen3:4b` as an automatic fallback while the primary model is unavailable.
3. Keep model output behind strict Pydantic JSON schemas.
4. Represent customer intent with the versioned actions `add`, `update`, `remove`, `query`,
   `total`, and `clarify`.
5. Execute all price, quantity, ledger, and lifecycle changes in deterministic application code.
6. Treat unit-price-only jobs as pending and exclude them from the estimate ledger until they
   have a confirmed quantity.
7. Soft-remove jobs from the current projection while retaining messages, snapshots, and quote
   history for auditability.
8. Preserve ledger order by initial job creation, not by the most recent query time.

## Consequences

- The system remains useful without a paid API and continues deterministic quoting when Ollama is
  unavailable.
- The 8B model improves multilingual conversation and structured output but is not represented as
  equivalent to a hosted frontier model.
- Model upgrades can happen through configuration without changing business logic.
- A larger model increases first-token latency and memory usage; bounded context, output limits,
  keep-alive, and a smaller fallback control this cost.
- Future PDF, OCR, and BIM tools can reuse the same rule: the model selects or explains tools,
  while verified application services own calculations and state changes.
