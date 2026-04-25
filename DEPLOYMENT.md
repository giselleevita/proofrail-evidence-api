# Deployment (Pilot) — Fly/Render/Railway

This repo is designed to start with a **single-customer pilot** on managed hosting while keeping an upgrade path to banking-partner production.

## Services

- **API**: `proofrail-evidence-api` (FastAPI)
- **Worker**: `proofrail-worker` (delivers webhooks + runs background tasks)
- **Postgres**: managed Postgres (Railway/Render/Fly)
- **Object storage**: S3-compatible bucket (e.g., Cloudflare R2, Backblaze B2 S3, AWS S3)

## Required environment

### API + Worker

- `PROOFRAIL_ADMIN_KEY`
- `PROOFRAIL_SIGNING_KEYS="k1:<secret>,k2:<secret>"`
- `PROOFRAIL_SIGNING_KEY_CURRENT="k2"`
- `PROOFRAIL_DB_URL="postgresql://..."`
- `PROOFRAIL_S3_BUCKET="proofrail"`
- `PROOFRAIL_S3_ENDPOINT_URL="https://<s3-endpoint>"`
- `PROOFRAIL_S3_REGION="us-east-1"` (or provider region)
- `PROOFRAIL_S3_ACCESS_KEY_ID="..."`
- `PROOFRAIL_S3_SECRET_ACCESS_KEY="..."`

Optional:

- `PROOFRAIL_S3_PREFIX="pilot/"`
- `PROOFRAIL_RPM="120"` (per–API-key throughput after authentication)
- `PROOFRAIL_PREAUTH_RPM="60"` (per-IP cap **before** DB key resolution when `x-api-key` is present)
- `PROOFRAIL_RATELIMIT_MAX_BUCKETS="50000"` (in-memory rate limiter LRU cap; set `0` or `none` for unbounded — not recommended)
- `PROOFRAIL_DB_POOL_MIN="1"` / `PROOFRAIL_DB_POOL_MAX="20"` (Postgres connection pool sizes)
- `PROOFRAIL_PROMETHEUS_METRICS="1"` — exposes plaintext **`/metrics`** (protect with network policy or auth at the edge)
- `PROOFRAIL_JOBS_RETENTION_DAYS="7"` (worker deletes old `done`/`failed` jobs)

Operations and SLOs: see **`RUNBOOK_INCIDENT.md`**, **`RUNBOOK_WEBHOOKS.md`**, **`RUNBOOK_DR.md`**, and **`SLO.md`**. Compliance index: **`docs/compliance/README.md`**.

## Local parity stack (Postgres + MinIO)

Run the pilot-parity profile:

```bash
docker compose --profile pilot up -d --build
```

- API: `http://127.0.0.1:8001/docs`
- Postgres (host): `localhost:5433` (container is still `postgres:5432`)
- MinIO S3 (host): `http://127.0.0.1:9000`
- MinIO console: `http://127.0.0.1:9001` (default credentials in `docker-compose.yml`)
- Webhook sink (host): `http://127.0.0.1:8089` (simple local receiver for “delivered” demos)

### Pilot smoke test (copy/paste)

```bash
# Should return {"status":"ready"}
curl -sf http://127.0.0.1:8001/readyz && echo

# Create API key (includes write:cases so the case workflow works too)
API_KEY="$(
  curl -sS -X POST http://127.0.0.1:8001/v1/admin/keys \
    -H 'content-type: application/json' \
    -H 'x-admin-key: change-me' \
    -d '{"customer_id":"pilot-smoke","scopes":["write:screen","read:evidence","write:cases"]}' \
  | python3 -c 'import sys,json; print(json.load(sys.stdin)["api_key"])'
)"

# Subscribe to local webhook sink (will be delivered by the worker)
curl -sS -X POST http://127.0.0.1:8001/v2/webhooks/subscriptions \
  -H 'content-type: application/json' \
  -H "x-api-key: ${API_KEY}" \
  -d '{"url":"http://webhook_sink/webhooks","secret":"supersecret123","events":["screening.created"]}' >/dev/null

# Create screening (enqueues evidence pack + webhook delivery job)
curl -sS -X POST http://127.0.0.1:8001/v2/screenings \
  -H 'content-type: application/json' \
  -H "x-api-key: ${API_KEY}" \
  -d '{"screening_type":"onboarding","subject":{"name":"Alice Example"}}' \
  | python3 -c 'import sys,json; d=json.load(sys.stdin); print("decision=", d["decision"], "evidence_pack_id=", d["evidence_pack_id"][:12])'

# Confirm delivery via metrics (look for "delivered" increasing)
curl -sS http://127.0.0.1:8001/v1/admin/metrics -H 'x-admin-key: change-me'
```

## Notes

- The default `docker compose up` still runs the SQLite + filesystem demo stack on port `8000`.
- For pilots, use the Postgres + S3 stack (`--profile pilot`) so you don’t have to migrate data later.
- For safe retries of write requests, send an `Idempotency-Key` header on `POST /v2/screenings`, `POST /v2/cases/{case_id}/events`, and `POST /v2/webhooks/subscriptions`.
- For paging through list endpoints, use `limit` + `cursor` query params and the `x-next-cursor` response header on `GET /v2/cases` and `GET /v2/webhooks/subscriptions`.

