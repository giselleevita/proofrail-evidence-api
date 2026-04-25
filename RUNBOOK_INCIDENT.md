# Incident response runbook (ProofRail Evidence API)

Use this order during an outage or severe degradation. Adjust for your hosting provider (Fly, Render, Railway, etc.).

## 1. Confirm scope and blast radius

- Which region(s) and which components fail: API only, worker only, Postgres, object storage, or edge/proxy?
- Is impact isolated to one customer or global?

## 2. Metrics first (`/v1/admin/metrics`)

With the admin key, fetch JSON metrics and record a timestamped snapshot.

- **Usage queue**: `usage.queue_depth`, `usage.dropped`. Sustained `dropped > 0` means the API is shedding usage telemetry; scale API replicas or slow traffic.
- **Jobs**: `jobs.by_status`, `jobs.lag_seconds`, `jobs.locked`, `jobs.stale_leases`, `jobs.oldest_pending`.
  - Rising **`stale_leases`** with stuck processing: confirm the worker process is running; worker clears expired leases each tick before claiming jobs.
  - High **`locked`** for long periods: possible worker crash during lease window; leases expire after the configured lease duration; verify worker logs.
- **Webhooks**: `webhooks.deliveries_by_status` for growth in `failed` / `retry`.

If **`PROOFRAIL_PROMETHEUS_METRICS=1`**, scrape **`/metrics`** (no admin key; protect at the edge). Metric names align with **`SLO.md`**.

## 3. DLQ and failed work

- Webhook failures: `GET /v1/admin/webhooks/deliveries/dlq?limit=...` — use `x-next-cursor` for paging.
- Job failures: `GET /v1/admin/jobs/dlq?limit=...` — same cursor pattern.
- `GET /v1/admin/jobs/stats` for compact job health including `stale_leases`.

## 4. Database

- Check connection errors in API/worker logs.
- Verify migrations/schema: advisory lock during Postgres init should not block steady state; look for long transactions or locks in your DB dashboard.
- For Postgres, confirm pool sizing: `PROOFRAIL_DB_POOL_MIN`, `PROOFRAIL_DB_POOL_MAX` vs instance limits.

## 5. Object storage (S3-compatible)

- `503` / timeouts on pack reads: verify bucket policy, credentials, endpoint URL, and regional outage notices.
- Integrity errors (`evidence_pack_integrity_failed`): treat as data or transport corruption; compare object ETag/hash if versioning is enabled.

## 6. Communication

- Page on-call; open a war room channel; log timeline and hypotheses.
- After mitigation, schedule a short post-incident review and update this runbook if gaps appeared.
