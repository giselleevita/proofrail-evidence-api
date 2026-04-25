# Webhooks runbook (ProofRail Evidence API)

## Expected behavior

- Subscriptions are per customer; deliveries are queued in Postgres and processed by **`proofrail-worker`**.
- Each delivery is attempted with timeout **`PROOFRAIL_WEBHOOK_TIMEOUT_S`** and retried up to **`PROOFRAIL_WEBHOOK_MAX_ATTEMPTS`** with exponential backoff from **`PROOFRAIL_WEBHOOK_RETRY_BASE_S`**.
- Receiver must respond with a success HTTP status for the delivery to be marked **delivered**.

## Signature verification (receiver)

- Verify the HMAC signature your integration agreed on (subscription `secret`) over the raw request body.
- Reject replays if you track **`event_id`** (or equivalent) and enforce idempotency on your side.

## Common failure modes

| Symptom | Likely cause | Action |
|--------|----------------|--------|
| Spike in `retry` | Slow or flaky receiver | Scale receiver; increase timeout only if appropriate. |
| Spike in `failed` | 4xx from receiver, bad URL, TLS issues | Fix receiver; inspect DLQ payload and last error. |
| No deliveries at all | Worker down or DB unreachable | Restart worker; check `PROOFRAIL_DB_URL`. |
| Duplicate deliveries | At-least-once semantics | Enforce idempotent handling using `event_id`. |

## Replay semantics

- ProofRail may deliver the same logical event more than once across retries or operational duplicates. **Receivers must be idempotent.**
- Use **`GET /v1/admin/webhooks/deliveries/dlq`** (cursor from **`x-next-cursor`**) to inspect failed rows before manual replay from your side.

## Operational checks

1. `GET /v1/admin/metrics` — webhook counts and job lag.
2. Worker logs for `webhook_delivery` job errors.
3. Receiver logs for 401/403 (wrong secret) or 413 (body too large).
