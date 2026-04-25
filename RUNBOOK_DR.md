# Disaster recovery runbook (ProofRail Evidence API)

This document states **assumptions** and a **checklist** for restore drills. Tune RPO/RTO with your compliance owner.

## Components

| Asset | Role | Default pilot assumption |
|-------|------|---------------------------|
| Postgres | Screenings, cases, jobs, webhooks, idempotency keys | RPO: point-in-time recovery (PITR) if enabled by provider; else last backup |
| S3-compatible bucket | Evidence packs and blobs | RPO: replication/versioning policy dependent |
| SQLite + filesystem | Local demo only | RPO: last file copy |

## RPO / RTO targets (fill per environment)

| Environment | RPO (data loss max) | RTO (service restore max) | Owner |
|-------------|---------------------|---------------------------|--------|
| Production | | | |
| Staging | | | |

## Restore drill checklist (Postgres + object storage)

1. **Verify backups**: automated backup job success, retention meets policy, restore test in non-prod quarterly.
2. **Restore Postgres** to an isolated instance or database; validate schema and row counts vs snapshot.
3. **Restore or verify bucket**: list critical prefixes; confirm object lock/versioning if used for immutability.
4. **Reconcile application config**: `PROOFRAIL_DB_URL`, S3 credentials, signing keys — keys must match data signed at rest.
5. **Start API + worker** against restored backends; run smoke: `GET /readyz`, create key, `POST /v2/screenings`, confirm webhook delivery in sink.
6. **Document** actual restore duration, issues, and follow-up tickets.

## Evidence integrity

- Evidence pack IDs are content hashes; after restore, spot-check `GET` of known pack IDs and integrity paths (`evidence_pack_integrity_failed` should not appear for uncorrupted objects).

## Secrets rotation after compromise

- Rotate `PROOFRAIL_ADMIN_KEY`, API keys, S3 keys, DB password, and signing keys; invalidate outstanding JWT/session integrations if any.
