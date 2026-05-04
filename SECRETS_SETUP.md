# Secrets Setup — ProofRail

## 1. GitHub Actions Secret

Repository → Settings → Secrets and variables → Actions → New repository secret

| Name | Value |
|---|---|
| `RAILWAY_TOKEN` | Token from [railway.app](https://railway.app) → Account Settings → Tokens → Create Token |

## 2. Railway Variables

Railway → Project → proofrail-api → Variables → Raw Editor

### Required

```env
PROOFRAIL_ADMIN_KEY=<openssl rand -hex 32>
PROOFRAIL_SIGNING_KEYS=k1:<openssl rand -hex 32>
PROOFRAIL_SIGNING_KEY_CURRENT=k1
PROOFRAIL_DB_URL=${{Postgres.DATABASE_URL}}
PROOFRAIL_DB_POOL_MIN=1
PROOFRAIL_DB_POOL_MAX=10
```

### Cloudflare R2 Storage

```env
PROOFRAIL_S3_BUCKET=proofrail-evidence
PROOFRAIL_S3_ENDPOINT_URL=https://<ACCOUNT_ID>.r2.cloudflarestorage.com
PROOFRAIL_S3_REGION=auto
PROOFRAIL_S3_ACCESS_KEY_ID=<R2_ACCESS_KEY_ID>
PROOFRAIL_S3_SECRET_ACCESS_KEY=<R2_SECRET_ACCESS_KEY>
```

### Optional

```env
PROOFRAIL_DEMO_MODE=0
PROOFRAIL_RPM=120
PROOFRAIL_PROMETHEUS_METRICS=1
```

> ⚠️ `PORT` wird von Railway automatisch gesetzt — nicht manuell eintragen.

## 3. Secrets generieren (lokal)

```bash
# Admin Key
openssl rand -hex 32

# Signing Key
openssl rand -hex 32
```
