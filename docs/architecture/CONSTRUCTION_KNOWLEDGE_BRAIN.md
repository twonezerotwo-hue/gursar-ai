# Construction Knowledge Brain V1

## Purpose

The knowledge brain turns active, versioned knowledge rules into traceable applicability
results. It does not replace an engineer, architect, lawyer, municipality, or standards
subscription. Absence of a matching rule is reported as `UNVERIFIED`, never as permission.

## Retrieval and domain routing

The existing bounded lexical/vector retrieval pipeline remains the source of document
context. Physical collections organize storage by source type. Logical domain collections
route questions across architecture, structural, MEP, geotechnical, fire, energy,
materials, site, HSE, quality, planning, cost, contract, regulations, specifications,
company, and project knowledge.

Search ranking combines lexical relevance, hashed-vector similarity, source quality, and
the configured source hierarchy. All weights are stored in `pipeline.yaml`; source ranks
and rule thresholds are stored in `knowledge_brain.yaml`.

## Source hierarchy

The initial deterministic order is:

`LAW > REGULATION > MANDATORY_STANDARD > TECHNICAL_SPECIFICATION > OFFICIAL_GUIDANCE
> REFERENCE_STANDARD > VERIFIED_PROJECT > PROFESSIONAL/ACADEMIC/MANUFACTURER
> SITE_EXPERIENCE > HEURISTIC > AI_INFERENCE`

Hierarchy affects selection but never hides contradictory requirements. Conflicting active
rules produce `CONFLICT` and require expert review.

## Applicability contract

`POST /api/v1/knowledge/rules/evaluate` accepts a query, jurisdiction, building type,
optional topic, and effective date. The engine considers only active document/rule
versions valid on that date. It returns:

- `APPLICABLE`: an active, sufficiently authoritative rule matched the supplied context.
- `CONFLICT`: applicable active rules disagree.
- `NOT_APPLICABLE`: semantically relevant rules exist but not for the supplied context.
- `UNVERIFIED`: no verified rule supports a conclusion or source quality is insufficient.

Every response contains source URL, document/version IDs, article, effective dates,
authority, source class, rule text, engine version, and a deterministic evaluation checksum.

## Safety boundaries

- LLM output is not a rule and cannot override active sourced knowledge.
- Metadata-only licensed standards are never treated as ingested full text.
- International references trigger a local-applicability warning.
- A conflict is exposed rather than resolved silently.
- The engine does not perform structural design arithmetic or claim legal certainty.

## Current limitations

The initial corpus is Türkiye-heavy. Municipality-level applicability, explicit rule
dependencies/exceptions, expert review of all extracted rules, and end-to-end conversation
tool invocation remain future phases. Real professional validation is required before a
production compliance claim.
