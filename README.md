# ProofRail Evidence API

[![CI](https://github.com/giselleevita/proofrail-evidence-api/actions/workflows/ci.yml/badge.svg)](https://github.com/giselleevita/proofrail-evidence-api/actions/workflows/ci.yml)
[![Deploy](https://github.com/giselleevita/proofrail-evidence-api/actions/workflows/deploy.yml/badge.svg)](https://github.com/giselleevita/proofrail-evidence-api/actions/workflows/deploy.yml)
![Version](https://img.shields.io/badge/version-1.0.0-green)
![Python](https://img.shields.io/badge/python-3.12-blue)
![License](https://img.shields.io/badge/license-proprietary-lightgrey)

> FastAPI service for audit-grade sanctions screening evidence packs.

ProofRail is an **evidence-first sanctions screening API** for fintech and crypto onboarding workflows.

Most screening APIs return only a decision. ProofRail also returns **portable proof** of how that decision was reached — shareable with banking partners, auditors, and internal compliance review.

---

## What It Does

You send a subject (name + optional metadata) and get back:

- **A decision**: `allow | block | review`
- **An evidence pack** (content-addressed, exportable as JSON or auditor-ready PDF)
- **A case workflow** (v2): analyst records a decision with an append-only event timeline
- **Verifiable bundles**: signed bundle containing evidence pack + case timeline + tamper-evident hash-chain

---

## Architecture

```
Client
  ↓
FastAPI (ProofRail/)
  ↓
Screening Engine → Sanctions Data Sources
  ↓
Evidence Pack Builder (content-addressed)
  ↓
Case Workflow (append-only timeline)
  ↓
Bundle Signer (key rotation support)
  ↓
Export: JSON | PDF
```

---

## Core Artifacts

| Artifact | Description |
|---|---|
| Evidence Pack | Deterministic JSON payload stored as a content hash; exportable as PDF |
| Case Timeline | Append-only events: screening created, comments, assignment, review decision |
| Case Bundle | `{bundle, signature}` — evidence pack + case + chained events + `key_id` for rotation |

---

## Quick Start

```bash
git clone https://github.com/giselleevita/proofrail-evidence-api
cd proofrail-evidence-api
python3 -m venv .venv
source .venv/bin/activate
pip install -U pip && pip install -e ".[dev]"

export PROOFRAIL_ADMIN_KEY="dev-admin"
export PROOFRAIL_SIGNING_SECRET="dev-signing-secret"
export PROOFRAIL_SIGNING_KEYS="k1:dev-signing-secret"
export PROOFRAIL_SIGNING_KEY_CURRENT="k1"
export PROOFRAIL_DB_PATH="./proofrail.db"
export PROOFRAIL_STORE_DIR="./proofrail_store"

proofrail-evidence-api
```

Open:
- `http://127.0.0.1:8000/docs` — Swagger UI
- `http://127.0.0.1:8000/console/` — Analyst console (pilot UI)

### Docker

```bash
cp .env.example .env  # edit values
docker compose up -d --build
```

---

## API Reference

### v1 — Screening

```bash
# Create API key (admin)
curl -sS -X POST "http://localhost:8000/v1/admin/keys" \
  -H "x-admin-key: dev-admin" \
  -H "content-type: application/json" \
  -d '{"customer_id":"demo","scopes":["write:screen","read:evidence"]}'

# Screen a subject
curl -sS -X POST "http://localhost:8000/v1/sanctions/screen" \
  -H "x-api-key: <paste-key>" \
  -H "content-type: application/json" \
  -d '{"subject":{"name":"John Doe","country":"US"}}'
```

### v2 — Cases + Bundles

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/v2/screenings` | Create screening |
| `GET` | `/v2/cases?status=needs_review` | Case queue |
| `GET` | `/v2/cases/{case_id}` | Case detail |
| `POST` | `/v2/cases/{case_id}/events` | Add case event |
| `GET` | `/v2/evidence-packs/{id}/export?format=pdf\|json` | Export evidence |
| `GET` | `/v2/cases/{case_id}/bundle` | Verifiable case bundle |
| `POST` | `/v2/cases/bundles/verify` | Verify bundle integrity |

### Webhooks (v2)

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/v2/webhooks/subscriptions` | Create subscription |
| `GET` | `/v2/webhooks/subscriptions` | List subscriptions |
| `DELETE` | `/v2/webhooks/subscriptions/{id}` | Remove subscription |
| `POST` | `/v1/admin/webhooks/deliveries/run` | Trigger delivery run |

---

## Compliance

| Framework | Coverage |
|---|---|
| FATF Recommendations | Sanctions screening evidence and audit trail |
| MiCA (EU) | Crypto asset transfer traceability |
| GDPR | Append-only timeline, no data mutation |
| SOC2 | Tamper-evident evidence packs, key rotation |

---

## Deployment

See [`docs/cloudflare-r2-setup.md`](docs/cloudflare-r2-setup.md) for storage setup, [`railway.env.example`](railway.env.example) for all environment variables, and [`DEPLOYMENT.md`](DEPLOYMENT.md) for full deployment guide.

To deploy on Railway: add `RAILWAY_TOKEN` to GitHub → Settings → Secrets → Actions, then push to `main`.

---

## Security

- API keys scoped per customer with explicit permission grants
- Signing keys support rotation via `key_id`
- Analyst console (`/console`) should be disabled or protected at the edge in production
- See [`SECRETS_SETUP.md`](SECRETS_SETUP.md) for all secret configuration

---

## License

Proprietary. Contact for licensing terms.
