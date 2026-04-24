# ProofRail — 60 second live demo (speaker notes)

## Setup (before the call)

- Run:
  - `docker compose up -d --build`
  - `./scripts/demo_investor.sh`
- Have the generated PDF open from `./output/`.

## Script (talk track)

1. **Problem**: “Fintech onboarding needs sanctions screening, but auditors and banking partners ask for proof: what data did you screen, what did you decide, and can that decision be reproduced?”
2. **What we do**: “ProofRail returns a decision **and** a cryptographically-addressed **evidence pack**. The evidence pack is the audit artifact.”
3. **Live**: “I run one command. It creates an API key, runs three screenings: no-hit, review, and a known hit. Then it records an analyst approval and downloads a PDF evidence pack.”
4. **Show PDF**:
   - “Decision + Hits”
   - “Why section”
   - “Analyst sign-off”
   - “Ingestion + sources + determinism hash”
5. **Show verifiable bundle**:
   - “We fetch a case bundle (evidence pack + case timeline) and verify the signature. This is the portable audit artifact for banking partners.”
6. **Why it matters**: “This makes compliance portable. You can share evidence with partners and auditors without rebuilding your whole compliance platform.”

## One-liner

**Evidence-first compliance API**: reproducible JSON + auditor-ready PDFs for onboarding screening, with verification and analyst sign-off.

