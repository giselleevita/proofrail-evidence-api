# ProofRail — Production Runbook

## Deploy

```bash
# Trigger via git push
git push origin main

# Monitor in GitHub Actions
# https://github.com/giselleevita/proofrail-evidence-api/actions
```

## Healthcheck

Railway activates the deployment only after this returns HTTP 200:

```bash
curl -i https://your-proofrail.up.railway.app/health
```

Expected response:
```json
{"status": "ok"}
```

## Smoke Test — Screen a subject

```bash
# 1. Create API key
curl -sS -X POST https://your-proofrail.up.railway.app/v1/admin/keys \
  -H "x-admin-key: $PROOFRAIL_ADMIN_KEY" \
  -H "content-type: application/json" \
  -d '{"customer_id":"smoke","scopes":["write:screen","read:evidence"]}'

# 2. Screen a subject
curl -sS -X POST https://your-proofrail.up.railway.app/v1/sanctions/screen \
  -H "x-api-key: <key-from-step-1>" \
  -H "content-type: application/json" \
  -d '{"subject":{"name":"John Doe","country":"US"}}'
```

Expected: `{"decision": "allow" | "block" | "review", "evidence_pack_id": "..."}`

## Storage Verify

After smoke test → check Cloudflare R2 bucket `proofrail-evidence` for new objects.

## Rollback

1. Railway → Project → proofrail-api → Deployments
2. Click previous successful deployment → **Redeploy**

## Metrics

```bash
curl https://your-proofrail.up.railway.app/metrics
```
