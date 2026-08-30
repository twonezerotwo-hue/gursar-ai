# ADR 0006: Secure and Versioned PDF Ingestion

## Status

Accepted - 2026-08-24

## Context

Project drawings are untrusted customer uploads. File extensions and client MIME values cannot prove
content type, original names cannot be used as storage paths, and replacing a file in place would make
measurements and quotations impossible to reproduce.

## Decision

1. The first document format is PDF only.
2. Uploads are streamed into a bounded spooled file while SHA-256 and byte size are calculated.
3. A PDF must start with the PDF signature and pass strict structural inspection with `pypdf`.
4. Empty, corrupt, encrypted, oversized, and excessive-page documents are rejected before storage.
5. Duplicate content in one project is rejected by `(project_id, sha256)` uniqueness.
6. Uploading changed content under the same display name creates a new immutable document version.
7. Storage keys contain server-generated IDs only and are resolved beneath a non-public root.
8. Sheet rows are created from verified PDF pages with page number and point dimensions.
9. Stored files are not exposed directly by the static web mount.

## Consequences

Every later measurement can reference an immutable document version and page. Uploads remain useful
without OCR or an AI provider. Page rendering, text extraction, and OCR will be added behind separate
ports; they cannot change the original version or bypass these validation rules.
