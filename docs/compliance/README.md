# Compliance and assurance (ProofRail Evidence API)

This folder links **in-repo controls and evidence** to enterprise diligence. Legal, privacy, and independent testing workstreams are tracked here with **placeholders** for owners and dates; update them in your ticket system or spreadsheet and keep this file as the index.

## In-repo artifacts

| Topic | Document |
|--------|----------|
| SOC 2 Trust Services Criteria mapping | [SOC2_TSC_mapping.md](./SOC2_TSC_mapping.md) |
| Security threat model (architecture-grounded) | [../security/threat_model.md](../security/threat_model.md) |
| Supply chain / dependency audit policy | [SUPPLY_CHAIN.md](./SUPPLY_CHAIN.md) |
| Security audit backlog / reconciliation | [../../security_best_practices_report.md](../../security_best_practices_report.md) |
| Tabletop drill checklist (ops readiness) | [../ops/TABLETOP_DRILL.md](../ops/TABLETOP_DRILL.md) |

## External program (cannot be completed in code alone)

Track these outside the repo if needed, but **link the canonical doc or ticket** in the table below (replace `TBD` and empty `Link` cells when artifacts exist).

**Commit-friendly in-repo support:** Dependabot config lives at [`.github/dependabot.yml`](../../.github/dependabot.yml); supply-chain policy in [SUPPLY_CHAIN.md](./SUPPLY_CHAIN.md). Operational drills use [../ops/TABLETOP_DRILL.md](../ops/TABLETOP_DRILL.md).

| Workstream | Owner (role) | Milestone / next review | Link |
|------------|----------------|-------------------------|------|
| Sanctions data licensing and retention (OFAC/EU/UN terms, redistribution) | Legal / Compliance | TBD | |
| Privacy program (DPIA, ROPA, subprocessors, Denmark-first residency choices) | Privacy / DPO | TBD | |
| Customer contracts (DPA, SLA, incident notification, audit cooperation) | Legal / Sales | TBD | |
| Annual penetration test | Security | TBD | |
| Targeted pen test after major releases | Security | TBD | |

## Operational runbooks and SLOs

- [RUNBOOK_INCIDENT.md](../../RUNBOOK_INCIDENT.md)
- [RUNBOOK_WEBHOOKS.md](../../RUNBOOK_WEBHOOKS.md)
- [RUNBOOK_DR.md](../../RUNBOOK_DR.md)
- [SLO.md](../../SLO.md)
