# Service level objectives and alerting (ProofRail Evidence API)

SLOs are **targets** for internal reliability; customer-facing SLAs may be stricter and live in contracts.

## Availability (API)

| Tier | Target | Measurement window |
|------|--------|----------------------|
| Production | 99.9% successful `GET /readyz` and `GET /healthz` from synthetic probe | 30 rolling days |

**Prometheus (when `PROOFRAIL_PROMETHEUS_METRICS=1`)**: use your probe success rate; correlate with ingress 5xx. If **`PROOFRAIL_METRICS_BEARER_TOKEN`** is set, configure the scraper with the same bearer secret.

## Latency (API, authenticated)

| Percentile | Target | Route group |
|------------|--------|-------------|
| p95 | \< 800 ms | `POST /v2/screenings` (excluding upstream list fetch variance) |
| p99 | \< 2 s | same |

Use `x-proofrail-latency-ms` response header in logs or APM; avoid logging request bodies.

## Worker / queue health

| Signal | Warning | Critical |
|--------|---------|----------|
| `jobs.lag_seconds` (admin JSON) | \> 300 | \> 1800 |
| `jobs.stale_leases` | \> 0 for \> 15 min while worker “healthy” | \> 10 sustained |
| `usage.dropped` | \> 0 in any 5 min window | sustained |

**Prometheus gauges** (see `/metrics`): `proofrail_jobs_stale_leases`, `proofrail_jobs_locked`, `proofrail_usage_queue_depth`, `proofrail_db_ping_ms` (negative value means ping failed).

## Webhooks

| Signal | Warning | Critical |
|--------|---------|----------|
| `failed` deliveries share of last 24h | \> 1% | \> 5% |

Inspect DLQ endpoint and receiver availability.

## Dashboard wiring

Map panels to:

- `GET /v1/admin/metrics` (JSON) for ad-hoc / human triage.
- `/metrics` exposition for Prometheus + Grafana: job gauges above, plus standard process metrics from your scrape config if attached.

## Alert routing

- PagerDuty / Opsgenie on-call: critical thresholds only.
- Slack channel for warnings and daily digest of DLQ depth.
