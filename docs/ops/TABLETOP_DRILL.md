# Tabletop drill checklist (ProofRail Evidence API)

Use this once per quarter (or after major architecture changes) to satisfy operational readiness in the enterprise completion program. It complements the runbooks; it does not replace a live incident.

## Participants

- Facilitator (Engineering lead or SRE)
- API/on-call engineer
- Optional: Compliance / Security observer

## Materials (before the session)

- Access to staging or production read-only: admin metrics, DLQ endpoints, DB dashboard, object storage health
- Copies of [RUNBOOK_INCIDENT.md](../../RUNBOOK_INCIDENT.md), [RUNBOOK_WEBHOOKS.md](../../RUNBOOK_WEBHOOKS.md), [RUNBOOK_DR.md](../../RUNBOOK_DR.md)
- [SLO.md](../../SLO.md) alert thresholds

## Scenario A — API degradation (45 min)

1. Facilitator describes: elevated 5xx on `POST /v2/screenings`, normal `GET /readyz`.
2. Team walks through RUNBOOK_INCIDENT triage order: metrics, DLQ, DB, S3.
3. Record: which dashboards or commands were used, gaps in documentation, time-to-next-step.

**Exit criteria:** Agreed single “next owner” and any doc updates ticketed.

## Scenario B — Webhook backlog (30 min)

1. Facilitator describes: `failed` webhook deliveries rising; customers report missing events.
2. Team walks RUNBOOK_WEBHOOKS: signature verification, receiver errors, worker health.
3. Practice paging one on-call using SLO thresholds from SLO.md.

**Exit criteria:** Replay/idempotency expectations restated for receivers; any runbook gaps ticketed.

## Scenario C — Postgres restore (60 min, optional)

1. Facilitator describes: primary DB lost; restore from backup required.
2. Team walks RUNBOOK_DR checklist at a desk (no live restore required unless in maintenance window).
3. Confirm who holds RPO/RTO numbers and where secrets are rotated post-restore.

**Exit criteria:** RPO/RTO table in RUNBOOK_DR reviewed and owners assigned if blank.

## After the drill

- File a short summary (internal wiki or ticket): date, scenarios, findings, follow-ups.
- Update [docs/compliance/README.md](../compliance/README.md) if the drill surfaced compliance-relevant gaps (e.g. evidence retention, notification timelines).
