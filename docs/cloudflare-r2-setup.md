# Cloudflare R2 Storage Setup

ProofRail uses R2 to store signed evidence bundles (passport PDFs, JSON exports).
R2 is S3-compatible — no code changes needed, only env vars.

## Free tier

| Metric | Free allowance |
|---|---|
| Storage | 10 GB / month |
| Class A operations (writes) | 1M / month |
| Class B operations (reads) | 10M / month |
| **Egress** | **Free (no data transfer cost)** |

More than enough for a pilot or early production.

## Step 1 — Create the bucket

1. Log in to [dash.cloudflare.com](https://dash.cloudflare.com)
2. **R2 Object Storage** → **Create bucket**
3. Name: `proofrail-evidence`
4. Location: **Automatic** (or choose `WEUR` for Europe)
5. Click **Create bucket**

## Step 2 — Create an API Token

1. R2 overview page → **Manage R2 API Tokens** (top right)
2. **Create API Token**
   - Token name: `proofrail-railway`
   - Permissions: **Object Read & Write**
   - Bucket scope: **Specific bucket** → `proofrail-evidence`
   - TTL: no expiry (or set rotation reminder)
3. Click **Create API Token**
4. Copy and save:
   - **Access Key ID** → `PROOFRAIL_S3_ACCESS_KEY_ID`
   - **Secret Access Key** → `PROOFRAIL_S3_SECRET_ACCESS_KEY`

> ⚠️ The secret is shown **once only**. Save it immediately.

## Step 3 — Get your Account ID

Dashboard right sidebar → **Account ID** (32-char hex string).

Endpoint format:
```
https://<ACCOUNT_ID>.r2.cloudflarestorage.com
```

## Step 4 — Set Railway environment variables

Railway → your project → **Variables** → Raw Editor:

```
PROOFRAIL_S3_BUCKET=proofrail-evidence
PROOFRAIL_S3_ENDPOINT_URL=https://<ACCOUNT_ID>.r2.cloudflarestorage.com
PROOFRAIL_S3_REGION=auto
PROOFRAIL_S3_ACCESS_KEY_ID=<your-access-key-id>
PROOFRAIL_S3_SECRET_ACCESS_KEY=<your-secret-access-key>
```

Railway redeploys automatically after saving.

## Step 5 (optional) — Public subdomain for direct downloads

If you want signed passport PDFs to be directly downloadable via a public URL:

1. R2 bucket → **Settings** → **Public access** → **Allow Access**
2. Connect a custom domain: `evidence.yourdomain.com`
3. Add to Railway:
   ```
   PROOFRAIL_S3_PUBLIC_URL=https://evidence.yourdomain.com
   ```

## Verify the connection

After deploy, call the health endpoint:
```bash
curl https://your-proofrail.up.railway.app/health
```

Then create a test screen and check if the passport PDF appears in your R2 bucket.

## CORS (if using web dashboard)

If the web dashboard fetches evidence files directly from R2, add CORS:

R2 bucket → **Settings** → **CORS Policy**:
```json
[
  {
    "AllowedOrigins": ["https://your-proofrail.up.railway.app"],
    "AllowedMethods": ["GET"],
    "AllowedHeaders": ["*"],
    "MaxAgeSeconds": 3600
  }
]
```
