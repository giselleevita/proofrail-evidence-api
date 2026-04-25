# Supply chain — dependency audit policy

## CI enforcement

- Every push and pull request to `main` runs **`pip-audit .`** after installing the package (see `.github/workflows/ci.yml`).
- The audit uses the **declared project dependencies** from the repository root (not the developer’s global site-packages).

## Exceptions

If `pip-audit` reports a vulnerability with **no fix** or an **accepted risk** (e.g., transitive dev-only tooling):

1. Open a security ticket with CVE ID, affected package, severity, and blast radius for this service.
2. Document compensating controls (e.g., service not exposed to untrusted input).
3. Add an **`--ignore-vuln`** entry to the CI `pip-audit` invocation **only** after security sign-off, with a link to the ticket and expiry date in the commit message.

## Local reproduction

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pip install pip-audit
pip-audit .
```

## Dependabot

- This repo includes [`.github/dependabot.yml`](../../.github/dependabot.yml) for **version updates** (weekly PRs for pip + GitHub Actions).
- In GitHub repository settings, enable **Dependabot security updates** (alert-driven) if not already on; that complements version PRs. Record the enablement date in your compliance tracker.
