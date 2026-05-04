# R2 Custom Domain Setup

Make evidence files publicly downloadable via a clean URL like `https://evidence.yourdomain.com`.

## Steps

1. **Cloudflare Dashboard** → R2 → `proofrail-evidence` bucket → **Settings** → **Public Access**
2. Click **Connect Domain**
3. Enter subdomain: `evidence.yourdomain.com`
4. Cloudflare adds the DNS record automatically if domain is on Cloudflare
5. Add to Railway variables:
   ```env
   PROOFRAIL_S3_PUBLIC_URL=https://evidence.yourdomain.com
   ```

## CORS

For browser access, apply the CORS policy in `docs/r2-cors.json`:

1. R2 bucket → **Settings** → **CORS Policy**
2. Paste contents of `docs/r2-cors.json`
3. Replace `your-proofrail.up.railway.app` with your actual Railway domain
4. Save

> Note: `AllowedOrigins` must be exact `scheme://host` — no trailing slash, no path.
