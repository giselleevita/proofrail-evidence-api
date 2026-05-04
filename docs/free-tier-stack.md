# Free Tier Production Stack

Dieser Stack ist vollständig kostenlos und production-tauglich für Pilots und frühe Kunden.

## Services

| Service | Was | Kosten | Link |
|---|---|---|---|
| **Render.com** | API Hosting (Docker) | Free (schläft nach 15 Min) | [render.com](https://render.com) |
| **Neon.tech** | Postgres Datenbank | Free (0.5 GB) | [neon.tech](https://neon.tech) |
| **Cloudflare R2** | Evidence Storage | Free (10 GB + 10M reads) | [dash.cloudflare.com](https://dash.cloudflare.com) |
| **GitHub Actions** | CI/CD | Free (2000 min/Mo) | [github.com](https://github.com) |

## Deploy auf Render

### Option A: Blueprint (empfohlen)
1. [dashboard.render.com](https://dashboard.render.com) → **New** → **Blueprint**
2. GitHub Repo: `giselleevita/proofrail-evidence-api`
3. Render liest `render.yaml` automatisch
4. Environment Variables setzen (siehe `SECRETS_SETUP.md`)
5. Deploy klicken ✔️

### Option B: Manuell
1. [dashboard.render.com](https://dashboard.render.com) → **New** → **Web Service**
2. GitHub Repo auswählen
3. Runtime: **Docker**
4. Health Check Path: `/health`
5. Environment Variables aus `SECRETS_SETUP.md` eintragen

## Neon Postgres (statt Render DB)

Für mehr Kontrolle und persistente Daten auch bei Render-Restarts:

1. [neon.tech](https://neon.tech) → Sign up (GitHub Login)
2. **New Project** → Region: `eu-central-1` (Frankfurt, näher zu dir)
3. Connection string kopieren
4. Als `PROOFRAIL_DB_URL` in Render eintragen

## Cloudflare R2 einrichten

Siehe `docs/cloudflare-r2-setup.md` für den vollständigen Setup.

## Limits des Free Tiers

- Render Free: Service schläft nach 15 Min ohne Traffic → Kaltstart ~30 Sek
- Neon Free: 0.5 GB Storage, compute pausiert nach Inaktivität
- R2 Free: 10 GB Storage, 1M Writes, 10M Reads/Monat
- GitHub Actions: 2000 Min CI/CD pro Monat

> ✅ Für Investor Demos und erste Kunden ist das mehr als ausreichend.
