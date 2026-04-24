# MIGRATION: v1 → v2

## What changed

- **New primary endpoint**: `POST /v2/screenings` replaces `POST /v1/sanctions/screen`.
- **Exports**: `GET /v2/evidence-packs/{id}/export?format=pdf|json` replaces:
  - `GET /v1/evidence-packs/{id}/export.pdf`
  - `GET /v1/evidence-packs/{id}` (still exists; v2 keeps JSON export via `format=json`)

## Endpoint mapping

- **Create screening**
  - v1: `POST /v1/sanctions/screen`
  - v2: `POST /v2/screenings`

- **Get evidence pack**
  - v1: `GET /v1/evidence-packs/{evidence_pack_id}`
  - v2: `GET /v2/evidence-packs/{evidence_pack_id}`

- **Export evidence**
  - v1: `GET /v1/evidence-packs/{evidence_pack_id}/export.pdf`
  - v2: `GET /v2/evidence-packs/{evidence_pack_id}/export?format=pdf`
  - v2 JSON: `GET /v2/evidence-packs/{evidence_pack_id}/export?format=json`

## Auth / scopes

- Same as v1:
  - `write:screen` for screenings
  - `read:evidence` for evidence packs + exports

## Notes

- v1 remains supported during migration and serves as a compatibility layer.
- OpenAPI snapshots are guarded separately:
  - `tests/openapi.v1.snapshot.json`
  - `tests/openapi.v2.snapshot.json`

