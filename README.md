## ProofRail Evidence API (SaaS core)

FastAPI service for **audit-grade sanctions screening evidence packs**.

### What it does

ProofRail is an **evidence-first sanctions screening API** for fintech/crypto onboarding workflows.

You send a subject (name + optional metadata) and get back:

- **A decision**: `allow | block | review`
- **An evidence pack id** (content-addressed) you can export as **JSON or auditor-ready PDF**
- A **case workflow** (v2) so an analyst can record a decision and leave an append-only timeline
- Optional **verifiable bundles**: a signed bundle containing the evidence pack + case timeline, plus a tamper-evident hash-chain over case events

### Why it matters

Most screening APIs return only a decision. ProofRail also returns the **portable proof** of how that decision was reached, so you can share it with:

- banking partners
- auditors
- internal compliance review

### Core artifacts

- **Evidence Pack**: deterministic JSON payload stored as a content hash; exportable as PDF.
- **Case timeline**: append-only events (screening created, comments, assignment, review decision).
- **Case bundle (v2)**: `{bundle, signature}` where `bundle` includes the evidence pack + case + chained events, and `signature` includes a `key_id` for key rotation.

### UI (today)

There is no separate web dashboard yet. The interactive UI is Swagger:

- `http://127.0.0.1:8000/docs`

### Investor demo (recommended)

For the deterministic offline investor demo (review workflow + PDF + verifiable bundle), use:

- `INVESTOR_QUICKSTART.md`

### Quickstart (venv)

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -U pip
python -m pip install -e ".[dev]"

export PROOFRAIL_ADMIN_KEY="dev-admin"
export PROOFRAIL_SIGNING_SECRET="dev-signing-secret"
export PROOFRAIL_SIGNING_KEYS="k1:dev-signing-secret"
export PROOFRAIL_SIGNING_KEY_CURRENT="k1"
export PROOFRAIL_DB_PATH="./proofrail.db"
export PROOFRAIL_STORE_DIR="./proofrail_store"

proofrail-evidence-api --help
proofrail-evidence-api
```

### Docker

```bash
export PROOFRAIL_ADMIN_KEY="change-me"
export PROOFRAIL_SIGNING_SECRET="dev-signing-secret"
export PROOFRAIL_SIGNING_KEYS="k1:dev-signing-secret"
export PROOFRAIL_SIGNING_KEY_CURRENT="k1"
export PROOFRAIL_DEMO_MODE="1"

docker compose up -d --build
```

### Create an API key (admin)

```bash
curl -sS -X POST "http://localhost:8000/v1/admin/keys" \
  -H "x-admin-key: dev-admin" \
  -H "content-type: application/json" \
  -d '{"customer_id":"demo","scopes":["write:screen","read:evidence"]}'
```

### Screen a subject

```bash
curl -sS -X POST "http://localhost:8000/v1/sanctions/screen" \
  -H "x-api-key: <paste-key>" \
  -H "content-type: application/json" \
  -d '{"subject":{"name":"John Doe","country":"US"}}'
```

### v2 workflow (cases + bundle)

- Create screening: `POST /v2/screenings`
- Case queue: `GET /v2/cases?status=needs_review`
- Case detail: `GET /v2/cases/{case_id}`
- Add case event: `POST /v2/cases/{case_id}/events`
- Export evidence: `GET /v2/evidence-packs/{id}/export?format=pdf|json`
- Verifiable case bundle: `GET /v2/cases/{case_id}/bundle`
- Verify bundle: `POST /v2/cases/bundles/verify`

### Webhooks (v2)

- Subscriptions:
  - `POST /v2/webhooks/subscriptions`
  - `GET /v2/webhooks/subscriptions`
  - `DELETE /v2/webhooks/subscriptions/{subscription_id}`
- Delivery runner (admin, cron-friendly):
  - `POST /v1/admin/webhooks/deliveries/run`
