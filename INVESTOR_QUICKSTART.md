# Investor Quickstart (60s demo)

This repo can run a deterministic demo **offline** (no network required) that produces an evidence PDF.

## 1) Start the API (Docker)

```bash
export PROOFRAIL_ADMIN_KEY="change-me"
export PROOFRAIL_SIGNING_SECRET="dev-signing-secret"
export PROOFRAIL_SIGNING_KEYS="k1:dev-signing-secret"
export PROOFRAIL_SIGNING_KEY_CURRENT="k1"
export PROOFRAIL_DEMO_MODE="1"

docker compose up -d --build
```

## 2) Run the end-to-end demo

```bash
./scripts/demo_investor.sh
```

Expected output:
- Creates an API key
- Runs a v2 screening for a no-hit name
- Runs a v2 screening for a known-hit name (`John Doe`)
- Fetches a verifiable **case bundle** and verifies its signature (`bundle_valid`)
- Downloads `./output/evidence-pack-<id>.pdf`

