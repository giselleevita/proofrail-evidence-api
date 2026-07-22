# Key Rotation Procedures: Ed25519 Signing Key Lifecycle

This runbook covers generating, deploying, rotating, and retiring Ed25519 signing keys for ProofRail in a way that:
- ✅ Maintains zero downtime
- ✅ Keeps old bundles verifiable forever
- ✅ Enables third-party audit with historical public keys
- ✅ Prevents accidental key leakage

---

## Prerequisites

- `scripts/generate_signing_key.py` available (creates keypairs)
- `scripts/verify_bundle.py` available (for testing)
- Access to production secrets manager (Vault, AWS Secrets Manager, or equivalent)
- Audit trail of all keys and rotations in a secure log
- 30-day notice window before old key expires

---

## Phase 1: Planning (30 Days Before Rotation)

### Step 1.1: Schedule Rotation

**Decide:**
- **Rotation date:** First Monday of the quarter (e.g., Oct 1, 2026)
- **Grace period for old key:** 30 days (Oct 1–Oct 31)
- **Retirement date:** 30 days after grace period ends (Oct 31, 2026)

**Notify stakeholders:**
```email
To: compliance-team@bank-a.com, security@bank-b.com, audit@fca-office.com
Subject: ProofRail Signing Key Rotation Schedule — Q4 2026

We will rotate our evidence signing keys on 2026-10-01 as part of our quarterly 
security program.

Timeline:
  2026-09-01: Generate new key, begin testing
  2026-10-01: Deploy new key to production (old key remains active)
  2026-10-01–10-31: Grace period (both keys valid)
  2026-11-01: Retire old key (remove from active signing)
  2026-11-01+: Historical bundles remain verifiable via archived public key

No action required from you. Your verifiers will automatically accept both keys 
during the grace period.

Questions? security@proofrail.example.com
```

---

## Phase 2: Generation (3 Weeks Before Rotation)

### Step 2.1: Generate New Key

**Environment:** Secure, air-gapped workstation (optional but recommended for production).

```bash
cd proofrail-evidence-api

# Generate new Ed25519 keypair
python scripts/generate_signing_key.py \
  --key-id "k1_prod_2026_q4" \
  --output "keys/" \
  --verbose
```

**Output:**
```
✅ Ed25519 key generated

Key ID: k1_prod_2026_q4
Created: 2026-09-15T10:00:00Z
Expires: 2026-12-31T23:59:59Z (optional, set in config)

Private seed (KEEP SECRET):
  k1_prod_2026_q4:abc123def456789abc123def456789abc123def456789abc123def456789abc123

Public key (share with auditors):
  ed_pub_abc123def456789abc123def456789abc123def456789abc123def456789

Save the private seed to your secrets manager immediately.
Archive the public key in your audit log.
```

### Step 2.2: Store Private Key in Secrets Manager

**Option A: AWS Secrets Manager**

```bash
aws secretsmanager create-secret \
  --name proofrail/signing-keys/k1_prod_2026_q4 \
  --description "ProofRail Ed25519 signing key Q4 2026 (active 2026-10-01 to 2026-12-31)" \
  --secret-string "abc123def456789abc123def456789abc123def456789abc123def456789abc123"

# Tag it for tracking
aws secretsmanager tag-resource \
  --secret-id proofrail/signing-keys/k1_prod_2026_q4 \
  --tags Key=rotation-date,Value=2026-10-01 Key=status,Value=pending
```

**Option B: HashiCorp Vault**

```bash
vault kv put secret/proofrail/signing-keys/k1_prod_2026_q4 \
  seed="abc123def456789abc123def456789abc123def456789abc123def456789abc123" \
  key_id="k1_prod_2026_q4" \
  status="pending" \
  rotation_date="2026-10-01"
```

**Option C: Environment File (Development Only)**

```bash
# DO NOT use this in production. Use Secrets Manager.
echo "k1_prod_2026_q4:abc123def456789abc123def456789abc123def456789abc123def456789abc123" >> .env.prod.new
chmod 600 .env.prod.new
```

### Step 2.3: Archive Public Key in Audit Log

**Create immutable record:**

```json
{
  "key_id": "k1_prod_2026_q4",
  "public_key": "ed_pub_abc123def456789abc123def456789abc123def456789abc123def456789",
  "status": "pending",
  "created_at": "2026-09-15T10:00:00Z",
  "scheduled_activation": "2026-10-01T00:00:00Z",
  "scheduled_expiry": "2026-12-31T23:59:59Z",
  "generated_by": "security-team@proofrail.example.com",
  "audit_log_id": "audit_key_gen_xyz789"
}
```

**Store in:**
- Compliance documentation repository (versioned)
- Audit trail database
- Email to compliance team (with PGP signature, optional)

---

## Phase 3: Testing (2 Weeks Before Rotation)

### Step 3.1: Load New Key into Staging

**Staging environment configuration:**

```bash
export PROOFRAIL_ED25519_KEYS="
  k1_prod_2026_q3:def456789abc123def456789abc123def456789abc123def456789abc123def456,
  k1_prod_2026_q4:abc123def456789abc123def456789abc123def456789abc123def456789abc123
"

export PROOFRAIL_SIGNING_KEY_CURRENT="k1_prod_2026_q4"  # New key is current in staging

# Restart staging API
docker compose -f staging/docker-compose.yml up -d proofrail-api-staging
```

### Step 3.2: Create Test Screening + Bundle

```bash
# Create screening in staging
curl -sS -X POST "https://staging-proofrail.example.com/v2/screenings" \
  -H "content-type: application/json" \
  -H "x-api-key: sk_staging_abc123" \
  -d '{
    "screening_type": "test",
    "subject": {"name": "Test Subject — Key Rotation Validation"}
  }' | jq -r '.case_id' > /tmp/test_case_id.txt

TEST_CASE_ID=$(cat /tmp/test_case_id.txt)

# Get signed bundle
curl -sS "https://staging-proofrail.example.com/v2/cases/${TEST_CASE_ID}/bundle" \
  -H "x-api-key: sk_staging_abc123" > /tmp/test_bundle.json

# Verify signature uses new key
jq '.key_id' /tmp/test_bundle.json
# Output: "k1_prod_2026_q4" ✅
```

### Step 3.3: Test Offline Verification

```bash
# Extract public key from bundle
PUBLIC_KEY=$(jq -r '.public_key' /tmp/test_bundle.json)

# Run offline verifier
python scripts/verify_bundle.py \
  --bundle /tmp/test_bundle.json \
  --public-key "${PUBLIC_KEY}"

# Expected output:
# ✅ Bundle signature valid (Ed25519)
# ✅ Hash chain integrity verified (2 events)
# ✅ Key rotation chain clean
# Key ID in bundle: k1_prod_2026_q4
```

### Step 3.4: Test Old Key Can Still Verify Old Bundles

**Load both keys in staging (old as current):**

```bash
export PROOFRAIL_ED25519_KEYS="
  k1_prod_2026_q3:def456789abc123def456789abc123def456789abc123def456789abc123def456,
  k1_prod_2026_q4:abc123def456789abc123def456789abc123def456789abc123def456789abc123
"

export PROOFRAIL_SIGNING_KEY_CURRENT="k1_prod_2026_q3"  # Switch back to old key

# Restart API
docker compose -f staging/docker-compose.yml up -d proofrail-api-staging

# Create bundle with old key
curl -sS -X POST "https://staging-proofrail.example.com/v2/screenings" \
  -H "x-api-key: sk_staging_abc123" \
  -d '{"screening_type":"test","subject":{"name":"Old Key Test"}}' | jq -r '.case_id' > /tmp/old_case_id.txt

OLD_CASE_ID=$(cat /tmp/old_case_id.txt)

curl -sS "https://staging-proofrail.example.com/v2/cases/${OLD_CASE_ID}/bundle" \
  -H "x-api-key: sk_staging_abc123" > /tmp/old_bundle.json

# Verify with old key
python scripts/verify_bundle.py --bundle /tmp/old_bundle.json

# Expected: ✅ Signature valid
```

### Step 3.5: Publish Keys via Endpoint (Staging)

```bash
# Verify /v2/signing/public-keys includes both keys
curl -sS "https://staging-proofrail.example.com/v2/signing/public-keys" | jq '.keys[]|{key_id,status}'

# Expected output:
# {
#   "key_id": "k1_prod_2026_q3",
#   "status": "active"
# }
# {
#   "key_id": "k1_prod_2026_q4",
#   "status": "active"
# }
```

### Step 3.6: Sign Off on Testing

**Checklist:**

- [ ] New key generated and stored in secrets manager
- [ ] New key loaded in staging without errors
- [ ] Bundles signed with new key verify offline
- [ ] Bundles signed with old key still verify offline
- [ ] Public keys endpoint includes both keys
- [ ] Audit log records key generation event
- [ ] Compliance team notified and acknowledged
- [ ] Security team approved rotation plan

---

## Phase 4: Production Deployment (Rotation Date)

### Step 4.1: Pre-Deployment Checklist

**Run morning of 2026-10-01:**

```bash
# Confirm both keys in secrets manager
aws secretsmanager list-secrets \
  --filters Key=name,Values=proofrail/signing-keys

# Confirm current production config (still using Q3 key)
echo $PROOFRAIL_SIGNING_KEY_CURRENT
# Output: k1_prod_2026_q3

# Backup current config
cp .env.prod .env.prod.2026-09-30.backup
git commit -m "Backup config before key rotation"
```

### Step 4.2: Deploy New Key to Production

**Fetch new key from secrets manager:**

```bash
# Pull new key from AWS Secrets Manager
NEW_KEY=$(aws secretsmanager get-secret-value \
  --secret-id proofrail/signing-keys/k1_prod_2026_q4 \
  --query SecretString --output text)

# Append to current keys (keeping Q3 key for backward compat)
CURRENT_KEYS="${PROOFRAIL_ED25519_KEYS},k1_prod_2026_q4:${NEW_KEY}"

# Update environment
export PROOFRAIL_ED25519_KEYS="${CURRENT_KEYS}"
export PROOFRAIL_SIGNING_KEY_CURRENT="k1_prod_2026_q4"
```

**Rolling restart (zero downtime):**

```bash
# Use your deployment tool (Kubernetes, Docker Compose, Fly, Railway, etc.)

# Kubernetes example:
kubectl set env deployment/proofrail-api \
  PROOFRAIL_ED25519_KEYS="${CURRENT_KEYS}" \
  PROOFRAIL_SIGNING_KEY_CURRENT="k1_prod_2026_q4"

# Wait for rollout
kubectl rollout status deployment/proofrail-api

# Docker Compose example:
docker compose -f prod/docker-compose.yml up -d --no-deps proofrail-api proofrail-worker

# Railway/Fly example:
# Update your deployment config (railway.toml / fly.toml) with new env vars
# Push to main branch: git push origin main
# CD pipeline handles rolling restart
```

### Step 4.3: Health Checks

**Post-deployment validation:**

```bash
# 1. API is responding
curl -sf "https://proofrail.example.com/readyz" || exit 1

# 2. New key is active
curl -s "https://proofrail.example.com/v2/signing/public-keys" \
  | jq '.keys[] | select(.key_id == "k1_prod_2026_q4")'

# 3. Create test screening (will be signed with new key)
TEST_RESPONSE=$(curl -sS -X POST "https://proofrail.example.com/v2/screenings" \
  -H "x-api-key: ${PROD_API_KEY}" \
  -d '{"screening_type":"test","subject":{"name":"Rotation Validation"}}')

TEST_CASE_ID=$(echo ${TEST_RESPONSE} | jq -r '.case_id')

# 4. Verify new bundle signature
curl -s "https://proofrail.example.com/v2/cases/${TEST_CASE_ID}/bundle" \
  -H "x-api-key: ${PROD_API_KEY}" | jq '.key_id'
# Output: k1_prod_2026_q4 ✅

# 5. Offline verification works
curl -s "https://proofrail.example.com/v2/cases/${TEST_CASE_ID}/bundle" \
  -H "x-api-key: ${PROD_API_KEY}" > /tmp/prod_bundle.json

python scripts/verify_bundle.py --bundle /tmp/prod_bundle.json
# Output: ✅ Bundle signature valid
```

### Step 4.4: Public Key Distribution

**Update all known places:**

```bash
# 1. Publish via API (automatic via /v2/signing/public-keys)
# 2. Email all auditors with new public key
# 3. Update internal wiki/documentation
# 4. Commit to version control (documentation)
git add docs/audit/public-keys.md
git commit -m "Update public keys: k1_prod_2026_q4 active"
git push origin main
```

**Sample email to auditors:**

```email
Subject: ProofRail Key Rotation Complete — Q4 2026

The key rotation is now complete. As scheduled, we activated our Q4 2026 signing key.

ACTIVE: k1_prod_2026_q4 (active through 2026-12-31)
GRACE: k1_prod_2026_q3 (valid through 2026-10-31)
RETIRED: k1_prod_2026_q2 (archived; bundles from this period still verifiable)

All NEW evidence bundles starting 2026-10-01 are signed with k1_prod_2026_q4.
EXISTING bundles remain verifiable with their original keys.

Your offline verifier will automatically accept both active keys.

Timeline for next rotation:
  2026-12-01: Announce Q1 2027 key rotation
  2027-01-01: Deploy new key
  2027-01-31: Retire Q4 2026 key

Questions? security@proofrail.example.com
```

### Step 4.5: Update Audit Log

```json
{
  "event": "signing_key_rotation_completed",
  "timestamp": "2026-10-01T00:00:00Z",
  "old_key_id": "k1_prod_2026_q3",
  "new_key_id": "k1_prod_2026_q4",
  "status": "success",
  "deployed_by": "security-automation",
  "verification_status": "passed",
  "audit_log_id": "audit_rotation_xyz789"
}
```

---

## Phase 5: Grace Period (Oct 1–Oct 31)

### Step 5.1: Monitor Key Usage

**Daily metric check:**

```bash
# Prometheus query: what percentage of bundles use each key?
curl -s 'http://prometheus.example.com/query?query=increase(proofrail_bundles_signed_total{key_id="k1_prod_2026_q4"}[1d])'

# Expected: 100% of NEW bundles use k1_prod_2026_q4
#           0% use k1_prod_2026_q3 (unless old requests still coming in)
```

### Step 5.2: Verify Auditor Readiness

**Spot-check with external auditors:**

```bash
# Email to Bank A compliance:
# "Can you verify that new bundles from us (signed with k1_prod_2026_q4) 
#  verify successfully with your offline verifier?"

# Bank A response: "✅ Yes, verified successfully"
```

### Step 5.3: Weekly Backup

```bash
# Backup both active keys
git commit -m "Scheduled key backup 2026-10-08"
# (Obviously don't commit actual key material; just the metadata)
```

---

## Phase 6: Key Retirement (Nov 1)

### Step 6.1: Stop Signing with Old Key

**Remove Q3 key from active signing:**

```bash
# Keep Q3 key for verification, but don't use it for NEW signatures
export PROOFRAIL_ED25519_KEYS="
  k1_prod_2026_q4:abc123def456789abc123def456789abc123def456789abc123def456789abc123,
  k1_prod_2026_q3:def456789abc123def456789abc123def456789abc123def456789abc123def456
"

export PROOFRAIL_SIGNING_KEY_CURRENT="k1_prod_2026_q4"  # (unchanged)

# Deploy (Q3 still verifiable, just not used for new signatures)
docker compose -f prod/docker-compose.yml up -d proofrail-api
```

### Step 6.2: Archive Old Key

**Move to historical record:**

```json
{
  "key_id": "k1_prod_2026_q3",
  "public_key": "ed_pub_def456789abc123...",
  "status": "retired",
  "created_at": "2026-07-01T00:00:00Z",
  "activated": "2026-07-01T00:00:00Z",
  "retired": "2026-11-01T00:00:00Z",
  "reason": "Quarterly key rotation",
  "verification_status": "still_active",
  "archive_location": "s3://proofrail-backup/keys/k1_prod_2026_q3.json"
}
```

**Keep forever for:**
- Verifying bundles from Q3 (July-September)
- Compliance audits
- Historical evidence review (7-year retention)

### Step 6.3: Notify Stakeholders

```email
Subject: ProofRail Q3 2026 Key Retired

As scheduled, we have retired the Q3 2026 signing key. It is no longer used 
for signing NEW bundles.

IMPORTANT: Existing bundles signed with k1_prod_2026_q3 remain verifiable.
We retain the public key permanently for audit purposes.

Key timeline:
  k1_prod_2026_q3: Retired (no new signatures)
  k1_prod_2026_q4: Active (all new signatures through Dec 31)
  k1_prod_2027_q1: Coming Jan 1, 2027

No action required from you.
```

---

## Testing Checklist (All Phases)

- [ ] Private key securely stored in secrets manager
- [ ] Public key distributed to all auditors
- [ ] Staging environment tested with both old and new keys
- [ ] Production deployment successful
- [ ] Zero downtime confirmed
- [ ] Health checks passed
- [ ] New bundles verify offline
- [ ] Old bundles still verify offline
- [ ] Audit log updated
- [ ] Stakeholders notified
- [ ] Compliance team acknowledged
- [ ] Metrics show 100% new bundles using new key
- [ ] External auditors confirm verification successful

---

## Troubleshooting

### New Key Not Working After Deploy

**Symptom:** `POST /v2/screenings` fails with "signing_error"

**Check:**
```bash
# Verify key is in environment
echo $PROOFRAIL_ED25519_KEYS | grep k1_prod_2026_q4

# Verify key format (should be 64 hex chars)
echo "abc123def456789abc123def456789abc123def456789abc123def456789abc123" | wc -c
# Output: 65 (64 hex + newline)

# Test key directly
python -c "
from ProofRail.service.signing import ed25519_public_key_hex
seed = bytes.fromhex('abc123def456789abc123def456789abc123def456789abc123def456789abc123')
print(ed25519_public_key_hex(seed))
"
```

### Old Bundle Verification Fails After Key Retirement

**Symptom:** `python verify_bundle.py` fails: "Key k1_prod_2026_q3 not found"

**Fix:** Keep all retired keys in `PROOFRAIL_ED25519_KEYS` permanently:

```bash
# DO NOT remove keys; only change PROOFRAIL_SIGNING_KEY_CURRENT
# Old keys stay for verification

export PROOFRAIL_ED25519_KEYS="
  k1_prod_2026_q4:abc123...,
  k1_prod_2026_q3:def456...,  # ← Keep forever
  k1_prod_2026_q2:ghi789...   # ← Keep forever
"
```

### Auditor Can't Verify Bundle

**Symptom:** External auditor reports: "Public key mismatch"

**Check:**
1. Public key in bundle matches `/v2/signing/public-keys` response
2. Public key generation was correct:
   ```bash
   python scripts/generate_signing_key.py --key-id k1_prod_2026_q4 --verify
   ```
3. Bundle was signed correctly:
   ```bash
   # Re-sign the bundle locally and compare signature
   ```

---

## Related Documentation

- [WORKFLOW_PRODUCTION.md](./WORKFLOW_PRODUCTION.md) — End-to-end screening workflow with bundle verification
- [MULTI_CUSTOMER_HARDENING.md](./MULTI_CUSTOMER_HARDENING.md) — Multi-customer key rotation strategies
- [docs/security/threat_model.md](./docs/security/threat_model.md) — Key compromise threats and mitigations
- [scripts/generate_signing_key.py](./scripts/generate_signing_key.py) — Key generation tool
- [scripts/verify_bundle.py](./scripts/verify_bundle.py) — Offline bundle verifier
