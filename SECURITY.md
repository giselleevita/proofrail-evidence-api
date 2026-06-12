# Security Policy

## Supported Versions

Security fixes are applied to the latest release and the `main` branch.

## Reporting a Vulnerability

Do not open a public issue for a suspected vulnerability. Use GitHub's private
vulnerability reporting flow:

https://github.com/giselleevita/proofrail-evidence-api/security/advisories/new

Include the affected endpoint or module, reproduction steps, security impact,
required attacker capabilities, and any suggested mitigation. You should receive
an acknowledgement within seven days.

## Security Scope

ProofRail is a reference implementation. Production deployments require managed
secrets, hardened identity and network controls, protected evidence storage,
monitoring, backups, and an operational incident-response process. Review
`docs/architecture.md`, `DEPLOYMENT.md`, and `SECRETS_SETUP.md` before deployment.
