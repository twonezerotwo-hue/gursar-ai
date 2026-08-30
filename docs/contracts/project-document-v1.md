# Project Document V1

## Project

`POST /api/v1/projects` creates an active project. `GET /api/v1/projects/{project_id}` returns the
same server-owned project record.

```json
{
  "name": "Göktürk Konut Projesi",
  "locale": "tr"
}
```

## PDF upload

`POST /api/v1/projects/{project_id}/documents` accepts one multipart field named `file`. The client
MIME value and extension are informational only; the server verifies the content signature and PDF
structure.

Successful uploads return the logical document, latest immutable version, and page/sheet index:

```json
{
  "id": "document-uuid",
  "project_id": "project-uuid",
  "original_name": "statik-proje.pdf",
  "latest_version": {
    "id": "version-uuid",
    "version": 1,
    "sha256": "64-character digest",
    "size_bytes": 120000,
    "media_type": "application/pdf",
    "page_count": 2,
    "status": "READY"
  },
  "sheets": [
    {
      "id": "sheet-uuid",
      "document_version_id": "version-uuid",
      "page_number": 1,
      "title": null,
      "discipline": "UNKNOWN",
      "width_points": 595,
      "height_points": 842
    }
  ]
}
```

Errors:

- `404`: project or document does not exist.
- `409`: the same PDF hash already exists in that project.
- `413`: configured byte limit exceeded.
- `415`: content does not have a PDF signature.
- `422`: empty, corrupt, encrypted, or excessive-page PDF.

`GET /api/v1/documents/{document_id}` returns the latest version. `GET
/api/v1/documents/{document_id}/sheets` returns the latest version's ordered sheets.
