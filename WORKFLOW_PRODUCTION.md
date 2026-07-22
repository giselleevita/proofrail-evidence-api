# Production Workflow: Screening → Decision → Bundle → Audit

This document walks through a complete evidence lifecycle in a banking partner scenario where the issuer (you) and the auditor (partner bank compliance team) verify the same bundle offline.

---

## Scenario

A fintech company (Customer A) screens a customer for sanctions compliance, makes a risk decision, and produces a signed evidence bundle that the partner bank's compliance team can independently verify without any API call or shared secrets.

---

## Step 1: Screen a Subject (Create Screening)

**Endpoint:** `POST /v2/screenings`  
**Caller:** Customer A's backend service

```bash
CUSTOMER_ID="fintech-customer-a"
API_KEY="sk_live_abc123def456..."

curl -sS -X POST "https://proofrail.example.com/v2/screenings" \
  -H "content-type: application/json" \
  -H "x-api-key: ${API_KEY}" \
  -H "Idempotency-Key: screening-2026-07-22-acme-corp-123" \
  -d '{
    "screening_type": "onboarding",
    "subject": {
      "name": "ACME Corp Holdings",
      "country": "US",
      "incorporation_date": "2020-01-15"
    }
  }'
```

**Response:**
```json
{
  "screening_id": "screening_7d4e9f2a",
  "decision": "review",
  "risk_score": 34,
  "evidence_pack_id": "pack_sha256_abc1234567890def",
  "created_at": "2026-07-22T14:32:15Z",
  "case_id": "case_8f1a6c3b"
}
```

**What happened:**
- Evidence pack (sanctions screening results, name match analysis, risk factors) was created and stored in S3 under a content-addressed path
- Case created with initial "review" decision and evidence link
- Job enqueued to deliver `screening.created` webhooks to all subscribers

---

## Step 2: Analyst Reviews and Makes Decision (Update Case Event)

**Endpoint:** `POST /v2/cases/{case_id}/events`  
**Caller:** Analyst via portal or compliance team API

```bash
CASE_ID="case_8f1a6c3b"

curl -sS -X POST "https://proofrail.example.com/v2/cases/${CASE_ID}/events" \
  -H "content-type: application/json" \
  -H "x-api-key: ${API_KEY}" \
  -H "Idempotency-Key: event-2026-07-22-acme-review-analyst-1" \
  -d '{
    "event_type": "decision",
    "decision": "allow",
    "severity": "low",
    "analyst": "compliance.officer@fintech.com",
    "notes": "Name match false positive. Company verified via Bloomberg. Risk acceptable."
  }'
```

**Response:**
```json
{
  "case_id": "case_8f1a6c3b",
  "decision": "allow",
  "events_count": 2,
  "last_event_at": "2026-07-22T14:45:33Z"
}
```

**What happened:**
- Event added to append-only case timeline
- Case decision updated to "allow"
- Event hash chained to prior event and genesis hash (enabling detection of timeline tampering)

---

## Step 3: Retrieve Signed Evidence Bundle

**Endpoint:** `GET /v2/cases/{case_id}/bundle`  
**Caller:** Customer A backend (or compliance team via API)

```bash
CASE_ID="case_8f1a6c3b"

curl -sS "https://proofrail.example.com/v2/cases/${CASE_ID}/bundle" \
  -H "x-api-key: ${API_KEY}" | jq .
```

**Response:**
```json
{
  "case_id": "case_8f1a6c3b",
  "evidence_pack_id": "pack_sha256_abc1234567890def",
  "evidence_pack_hash": "abc1234567890def...",
  "events": [
    {
      "event_id": "evt_genesis",
      "type": "screening_created",
      "hash": "0000000000000000...",
      "timestamp": "2026-07-22T14:32:15Z"
    },
    {
      "event_id": "evt_decision",
      "type": "decision",
      "hash": "def9876543210abc...",
      "prior_hash": "0000000000000000...",
      "timestamp": "2026-07-22T14:45:33Z"
    }
  ],
  "signature": "ed25519_sig_123456...",
  "algorithm": "ed25519",
  "public_key": "ed_pub_789abcdef...",
  "key_id": "k1_prod_2026_q3"
}
```

**What happened:**
- Bundle assembled: evidence pack + case timeline (genesis → events)
- Hash chain independently verified (each event hash = SHA256(prior_hash || event_data))
- Bundle signed with Ed25519 private key (issuer's signing secret)
- **Public key embedded in response** so auditor can verify offline

---

## Step 4: Auditor Downloads Public Key (No Authentication Required)

**Why:** Public keys are not secrets. An external auditor (or partner bank compliance team) can fetch them without credentials.

**Endpoint:** `GET /v2/signing/public-keys`  
**Caller:** Partner bank compliance system

```bash
curl -sS "https://proofrail.example.com/v2/signing/public-keys" | jq .
```

**Response:**
```json
{
  "keys": [
    {
      "key_id": "k1_prod_2026_q3",
      "public_key": "ed_pub_789abcdef...",
      "created_at": "2026-01-01T00:00:00Z",
      "expires_at": null,
      "status": "active"
    },
    {
      "key_id": "k1_prod_2026_q2",
      "public_key": "ed_pub_oldkey123...",
      "created_at": "2025-10-01T00:00:00Z",
      "expires_at": "2026-07-31T00:00:00Z",
      "status": "retired"
    }
  ]
}
```

---

## Step 5: Auditor Verifies Bundle Offline (Zero Trust, No API Call)

**Why:** The partner bank's compliance team can verify bundle authenticity without calling any API, without possession of any shared secrets, without trusting the issuer's infrastructure.

**Tool:** `scripts/verify_bundle.py`

```bash
# Download bundle from email/S3/portal
curl -sS "https://proofrail-uploads.example.com/bundles/case_8f1a6c3b.json" > bundle.json

# Get public key (from previous endpoint or embed in bundle email)
ISSUER_PUBLIC_KEY="ed_pub_789abcdef..."

# Run offline verifier
python scripts/verify_bundle.py \
  --bundle bundle.json \
  --public-key "${ISSUER_PUBLIC_KEY}"
```

**Output:**
```
✅ Bundle signature valid (Ed25519)
✅ Hash chain integrity verified (5 events)
✅ No timeline reordering detected
✅ Evidence pack hash matches genesis
✅ Key rotation chain clean

Bundle is authentic and tamper-evident.
Metadata:
  Signed at: 2026-07-22T14:45:33Z
  Signed by: k1_prod_2026_q3
  Case ID: case_8f1a6c3b
  Final decision: allow (low risk)
  Events: 2 (screening_created, decision)
```

**What was verified:**
1. **Ed25519 signature valid** — bundle was created by key holder, never forged
2. **Hash chain valid** — no events reordered, no timeline tampering
3. **Evidence pack hash matches** — screening data not mutated
4. **Key ID in bundle** — auditor can match against known public keys

---

## Step 6: Multi-Party Audit Trail (Stakeholder Coordination)

**Use case:** FATF Recommendation 10 (AML/CFT obligations) requires documentation that a bank conducted proper due diligence.

**Who can audit:**
- ✅ Issuer (you) — verify your own evidence at any time
- ✅ Partner bank compliance team — verify without API access
- ✅ Regulatory inspectors — verify with evidence snapshots (no live API required)
- ✅ Internal audit — verify with offline snapshots

**Audit trail includes:**
- Original screening data (sanctions list match logic, name parsing, risk factors)
- Case timeline (who reviewed, when, what decision)
- Digital signature (proof of issuance)
- Hash chain (proof of immutability)

**No audit trail includes:**
- API keys (never logged)
- Webhook secrets (never logged)
- Signing private keys (never logged)

---

## Step 7: Key Rotation (Planned Operations)

**Scenario:** Q3 2026 signing key expires. Rotate to Q4 key.

**Procedure:**
1. Generate new Ed25519 key (`scripts/generate_signing_key.py`)
2. Deploy new key as `PROOFRAIL_ED25519_KEY_CURRENT` (still keep old key in `PROOFRAIL_ED25519_KEYS` for verification)
3. Publish new public key via `/v2/signing/public-keys` endpoint
4. All NEW bundles signed with new key
5. Auditors update their known-keys list (they fetch `/v2/signing/public-keys` periodically)
6. OLD bundles still verifiable with retired key (never revoke old keys while old bundles must be auditable)

**No disruption:** Existing bundles remain verifiable with their embedded public key or via the historic public key record.

---

## Step 8: Compliance Reporting (Export for Regulators)

**Endpoint:** `GET /v2/cases/{case_id}/export?format=pdf`

```bash
curl -sS "https://proofrail.example.com/v2/cases/${CASE_ID}/export?format=pdf" \
  -H "x-api-key: ${API_KEY}" \
  -o case_8f1a6c3b_evidence_pack.pdf
```

**PDF contains:**
- Screening decision and risk score
- Evidence pack content (sanitized for external distribution)
- Case timeline (all analyst actions)
- Signature verification checksum
- Export timestamp and attestation

---

## Compliance Frameworks Demonstrated

| FATF Rec | Evidence | Stored |
|----------|----------|--------|
| R.10 AML CDD | Screening data + case decision + analyst notes | Case timeline |
| R.10 Record | Immutable audit trail | Hash-chained events |
| R.13 Disclosure | Signed evidence for regulators | Ed25519 bundles |

| GDPR | Evidence | Control |
|------|----------|---------|
| Accountability | Full audit trail with timestamps | `audit_log` table |
| Data subject rights | Evidence exportable, PII-sanitizable | Export endpoints |
| Data deletion | Cases soft-deleted (audit trail preserved) | Logical delete |

| SOC2 CC | Evidence | Pattern |
|---------|----------|---------|
| CC6.2 Change control | Key rotation tracked | `signing_keys` table + `/v2/signing/public-keys` |
| CC7.2 User role | Scoped API keys per customer | `api_keys` with `scopes` |
| CC7.4 Sensitive data | Signing keys never logged | Audit exclusions |

---

## Next Steps for Integration

1. **Integrate bundle verification into your workflow:**
   - Download bundle after case closure
   - Verify offline: `python scripts/verify_bundle.py`
   - Archive to compliance repository

2. **Share public keys with partners:**
   - Endpoint: `/v2/signing/public-keys`
   - Frequency: Publish key rotation notices 30 days before key expiry
   - Format: JSON document, verifiable signature (optional: sign the key list itself)

3. **Establish audit SLA:**
   - Auditor can verify any historical bundle within 7 years
   - Key retirement window: 30 days after expiry
   - Public key archive: Keep all keys (never delete, only mark retired)

4. **Monitor evidence integrity:**
   - Hash-chain verification as part of backup validation
   - Periodic re-verification of archived bundles
   - Metrics: `bundle_verifications_total`, `bundle_verification_failures`
