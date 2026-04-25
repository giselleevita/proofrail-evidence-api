# SOC 2 Trust Services Criteria — control mapping (pilot)

This is a **starter mapping** for SOC 2–style discussions (CC6 logical access, CC7 system operations, CC8 change management, A1 availability). Replace “pilot” assumptions with your production control owners and evidence locations.

| TSC | Control theme | Implemented control | Evidence in repo | Known gaps |
|-----|---------------|---------------------|-------------------|------------|
| CC6.1 | Logical access | API keys with scopes; admin routes require `x-admin-key` (constant-time compare) | `ProofRail/service/middleware.py`, `ProofRail/service/app.py` | Per-tenant signing optional (see threat model) |
| CC6.2 | Registration / revocation | Admin create/revoke API keys; DB records | `ProofRail/service/db.py`, admin routes | Key rotation runbook in ops process |
| CC6.3 | Authorization | Scope checks on routes (`read:evidence`, `write:screen`, etc.) | `ProofRail/service/app.py` | Fine-grained RBAC beyond scopes if required |
| CC6.6 | Session / credential abuse | Rate limits: global, per-key, per-IP anonymous, **pre-auth** before DB on invalid keys | `ProofRail/service/ratelimit.py`, `middleware.py` | Edge/WAF limits recommended |
| CC6.7 | Transmission confidentiality | TLS at load balancer (deployment) | `DEPLOYMENT.md` | mTLS partner-specific |
| CC7.2 | System monitoring | Admin metrics JSON; optional Prometheus `/metrics` | `app.py`, `SLO.md` | Central SIEM integration external |
| CC7.3 | Evaluation of anomalies | DLQ endpoints for webhooks and jobs; job stale lease gauge | `app.py`, `RUNBOOK_INCIDENT.md` | Anomaly ML external |
| CC7.4 | Incident response | Incident + webhook runbooks | `RUNBOOK_*.md` | Tabletop schedule external |
| CC8.1 | Change management | PR review, CI tests, ruff | `.github/workflows/ci.yml` | Formal approval workflow external |
| A1.2 | Availability commitments | Health/readiness, worker queue, DR assumptions | `SLO.md`, `RUNBOOK_DR.md` | Contractual SLA external |

## Notes

- **CC7 / CC8** evidence is stronger when linked to your ticketing (Jira/Linear) and deployment system (GitHub Actions environments, protected branches).
- Pair this matrix with **`docs/security/threat_model.md`** for abuse-path coverage.
