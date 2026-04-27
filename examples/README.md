# ProofRail integration examples

Small, copy-paste-friendly scripts that mirror the main `README.md` flows. They assume the API is reachable at `http://127.0.0.1:8000` unless you override environment variables.

## Contents

| Path | Purpose |
|------|---------|
| [`curl/demo_flow.sh`](curl/demo_flow.sh) | Admin key → create API key → v2 screening → JSON export |
| [`webhooks/verify_hmac.py`](webhooks/verify_hmac.py) | Verify `x-proofrail-signature` on a webhook delivery body |

## curl demo

```bash
chmod +x examples/curl/demo_flow.sh
export PROOFRAIL_ADMIN_KEY=dev-admin   # must match the running API
examples/curl/demo_flow.sh
```

Override base URL if needed:

```bash
export PROOFRAIL_API_BASE=http://127.0.0.1:8001
examples/curl/demo_flow.sh
```

## Webhook HMAC verification

Deliveries use the subscription `secret` and the raw JSON body bytes. The signature header format is `sha256=<hex>` (see `ProofRail.service.webhooks.sign_webhook`).

```bash
python3 examples/webhooks/verify_hmac.py
```

With a captured request:

```bash
python3 examples/webhooks/verify_hmac.py --secret 'your-subscription-secret' body.json 'sha256=...'
```
