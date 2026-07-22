# Multi-Customer Hardening: Operating ProofRail Across Banking Partners

This guide covers operational security and customer isolation when running ProofRail across multiple banking partners (e.g., Tier 1 bank, fintech custodian, credit union network).

---

## Trust Model

```
┌─────────────────────────────────────────────────────┐
│ Your Compliance Infrastructure (ProofRail)          │
│ - Manages evidence for multiple customers           │
│ - Each customer is cryptographically isolated       │
│ - Signing keys rotated quarterly per customer plan  │
└────────┬────────────────────────────────┬───────────┘
         │                                │
    ┌────▼─────┐                    ┌────▼─────┐
    │ Bank A    │                    │ Bank B    │
    │ (FDIC)    │                    │ (OCC)     │
    │           │                    │           │
    │ API key:  │                    │ API key:  │
    │ sk_prod_a │                    │ sk_prod_b │
    │ Scope:    │                    │ Scope:    │
    │ write:*   │                    │ read:*    │
    │ rate:1000 │                    │ rate:100  │
    └───────────┘                    └───────────┘
    (submit screenings)               (audit bundles)
```

---

## Customer Isolation

### API Key Scoping

**Every customer has exactly one API key** with explicit scopes:

| Customer | Scopes | RPM | Use Case |
|----------|--------|-----|----------|
| bank_a | `write:screen` `read:evidence` `write:cases` | 1000 | Submit screenings, manage cases |
| bank_b | `read:evidence` | 100 | Audit evidence (read-only) |
| fca_audit | `read:evidence` (compliance officer role) | 50 | Regulatory inspection |
| fintech_x | `write:screen` | 500 | High-volume onboarding |

**Never:** Create a global admin API key for normal operations. Only use `x-admin-key` for infrastructure setup (schema migrations, key rotation) in a locked-down environment.

**Verification:**
```bash
# List API keys for audit
curl -sS "https://proofrail.example.com/v1/admin/keys" \
  -H "x-admin-key: ${ADMIN_KEY}" \
  -H "x-customer-id: bank_a" | jq '.[]|{customer_id, scopes, created_at}'
```

---

### Database Row-Level Isolation

**ProofRail stores all data in a single `screenings` table, partitioned by `customer_id`:**

```sql
-- Customer A can only see their own screenings
SELECT * FROM screenings 
WHERE customer_id = 'bank_a' 
  AND created_at > now() - interval '7 days';
```

**Access control enforced at:**
1. **API layer** — `principal_from_request()` checks `customer_id` from API key
2. **Database layer** — Every query includes `WHERE customer_id = ?` (defense in depth)
3. **Object storage layer** — S3 prefix isolation: `s3://proofrail-bucket/bank_a/evidence/...`

**Audit trail:**
```sql
-- Track access across customers
SELECT customer_id, api_key_id, route, status_code, latency_ms 
FROM usage_events 
WHERE timestamp > now() - interval '1 day' 
ORDER BY customer_id, timestamp DESC;
```

---

## Signing Key Management (Multi-Customer)

### Per-Customer Key Rotation Schedule

**Policy:** Each customer may have different key rotation cadence based on their compliance requirements.

| Customer | Key ID | Created | Expires | Status |
|----------|--------|---------|---------|--------|
| bank_a | `k1_a_2026_q3` | 2026-07-01 | 2026-09-30 | Active |
| bank_a | `k1_a_2026_q2` | 2026-04-01 | 2026-07-01 | Retired |
| bank_b | `k1_b_2026_semi_a` | 2026-01-01 | 2026-06-30 | Retired |
| bank_b | `k1_b_2026_semi_b` | 2026-07-01 | 2026-12-31 | Active |
| fintech_x | `k1_fx_prod` | 2025-01-01 | ∞ (no expiry) | Active |

**Deployment:**
```bash
# Environment variable format for multiple keys
export PROOFRAIL_ED25519_KEYS="
  k1_a_2026_q3:<64-hex-seed>,
  k1_a_2026_q2:<64-hex-seed>,
  k1_b_2026_semi_b:<64-hex-seed>,
  k1_b_2026_semi_a:<64-hex-seed>,
  k1_fx_prod:<64-hex-seed>
"

# Current key for each customer (rotates independently)
# Note: ProofRail currently uses ONE global current key for all customers.
# For true per-customer key rotation, extend the config:
export PROOFRAIL_SIGNING_KEY_CURRENT="k1_a_2026_q3"  # For now, single global key
```

**Future enhancement:** Map `customer_id` → `key_id_current` in config to enable truly independent rotation per customer.

---

### Key Rotation Ceremony (Multi-Customer)

**Step 1: Generate new key for Bank A Q4**

```bash
python scripts/generate_signing_key.py --key-id k1_a_2026_q4 --output keys/

# Output:
# ✅ Private seed (keep secret):
#    k1_a_2026_q4:abc123def456...xyz789
# ✅ Public key:
#    ed_pub_abc123...xyz789
# ✅ Add to PROOFRAIL_ED25519_KEYS environment variable.
```

**Step 2: Deploy new key (non-disruptive)**

```bash
# Update config (add new key, update current pointer)
export PROOFRAIL_ED25519_KEYS="$PROOFRAIL_ED25519_KEYS,k1_a_2026_q4:abc123def456...xyz789"
export PROOFRAIL_SIGNING_KEY_CURRENT="k1_a_2026_q4"  # If using single global current

# Trigger rolling restart (zero-downtime deployment)
docker compose -f prod/docker-compose.yml up -d --no-deps --build proofrail-api
```

**Step 3: Publish new public key**

The `/v2/signing/public-keys` endpoint automatically includes the new key:

```json
{
  "keys": [
    {
      "key_id": "k1_a_2026_q4",
      "public_key": "ed_pub_abc123...xyz789",
      "created_at": "2026-10-01T00:00:00Z",
      "expires_at": null,
      "status": "active"
    },
    {
      "key_id": "k1_a_2026_q3",
      "public_key": "ed_pub_old123...xyz789",
      "created_at": "2026-07-01T00:00:00Z",
      "expires_at": "2026-10-31T00:00:00Z",
      "status": "retiring"
    }
  ]
}
```

**Step 4: Notify customers (30-day notice)**

Send email to all auditors:
```
Subject: ProofRail Key Rotation: Q4 2026

We are rotating our evidence signing keys on 2026-10-01 as part of our quarterly 
security hardening.

New public key active: k1_a_2026_q4
Old public key retired: k1_a_2026_q3 (valid through 2026-10-31)

All NEW evidence bundles will be signed with k1_a_2026_q4.
EXISTING bundles signed with k1_a_2026_q3 remain verifiable.

No action required. Your offline verifier (`verify_bundle.py`) will automatically 
accept both keys.

Questions? security@proofrail.example.com
```

**Step 5: Retire old key (30 days after expiry)**

```bash
# Remove old key from PROOFRAIL_ED25519_KEYS if it's past expiry window
# Keep it for 7+ years for historical bundle verification
```

---

## Rate Limiting Across Customers

### Pre-Auth Rate Limit (Before API Key Validation)

**Protects against:** Invalid-key probing attacks.

**Configuration:**
```bash
PROOFRAIL_PREAUTH_RPM=60  # 60 requests/minute per source IP before key lookup
```

**Behavior:**
```
Customer IP: 203.0.113.45
  Request 1: ✅ x-api-key: invalid-key-1 (preauth limiter: 1/60)
  Request 2: ✅ x-api-key: invalid-key-2 (preauth limiter: 2/60)
  ...
  Request 60: ✅ x-api-key: invalid-key-60 (preauth limiter: 60/60)
  Request 61: ❌ 429 Too Many Requests (preauth limit hit)
```

**Recommendation:** Use edge WAF (Cloudflare, AWS WAF) with stricter per-IP limits (10 req/min) to discourage probing.

### Per-Customer Rate Limit (After API Key Validation)

**Protects against:** Misbehaving customer draining resources.

**Configuration:**
```bash
PROOFRAIL_RPM=120  # Default rate limit for all customers

# Override per customer (future feature):
# store in database: api_keys.rate_limit_rpm = 1000
```

**Example:**
- Bank A: 1000 RPM (high-volume screening)
- Bank B: 100 RPM (read-only audits)
- FCA Audit: 50 RPM (inspection queries)

**Monitor:**
```bash
# Check rate limit usage
curl -sS "https://proofrail.example.com/v1/admin/metrics" \
  -H "x-admin-key: ${ADMIN_KEY}" | grep rate_limit
```

---

## Webhook Security (Multi-Customer)

### Webhook SSRF Prevention

**ProofRail validates webhook URLs to prevent SSRF attacks:**

**Blocked:**
- `http://127.0.0.1/...` (loopback)
- `http://169.254.169.254/...` (AWS metadata)
- `http://::1/...` (IPv6 loopback)
- `http://internal-service.local/...` (private DNS)
- HTTP (must be HTTPS)

**Allowed:**
- `https://bank-a-compliance.example.com/webhooks`
- `https://public-auditor-network.example.com/events`

**Code:**
```python
# ProofRail/service/webhooks/validation.py
def validate_webhook_url(url: str):
    parsed = urlparse(url)
    
    # Must be HTTPS
    if parsed.scheme != 'https':
        raise ValueError("Webhook URLs must use HTTPS")
    
    # Resolve hostname and check IP
    try:
        ip = socket.gethostbyname(parsed.hostname)
        if ipaddress.ip_address(ip).is_private:
            raise ValueError("Webhook URLs must be public IPs")
    except socket.gaierror:
        raise ValueError("Webhook hostname does not resolve")
```

### Per-Subscription Webhook Secrets

**Each webhook subscription has its own secret** (not shared across subscriptions):

```json
{
  "subscription_id": "sub_bank_a_1",
  "customer_id": "bank_a",
  "url": "https://bank-a-compliance.example.com/webhooks",
  "secret": "whsec_bank_a_1_xyz789abc123...",  // 32-byte random
  "events": ["screening.created", "case.updated"],
  "created_at": "2026-01-15T10:00:00Z"
}
```

**Delivery includes HMAC signature:**

```bash
# Webhook delivery (sent from worker)
POST https://bank-a-compliance.example.com/webhooks

Headers:
  Content-Type: application/json
  X-ProofRail-Signature: sha256=abc123...  // HMAC-SHA256(secret, body)
  X-ProofRail-Delivery-ID: webhook_delivery_xyz789
  X-ProofRail-Timestamp: 2026-07-22T15:30:45Z

Body:
{
  "delivery_id": "webhook_delivery_xyz789",
  "subscription_id": "sub_bank_a_1",
  "event_type": "screening.created",
  "data": {...}
}
```

**Receiver validates:**

```python
# Webhook receiver at Bank A
import hmac
import hashlib

def verify_webhook(body: bytes, signature: str, secret: str):
    expected = "sha256=" + hmac.new(
        secret.encode(),
        body,
        hashlib.sha256
    ).hexdigest()
    
    return hmac.compare_digest(expected, signature)
```

---

## Audit Logging & Compliance Reports

### Audit Trail for All Customers

**ProofRail logs every action to `audit_logs` table:**

```sql
SELECT 
  timestamp,
  customer_id,
  api_key_id,
  action,
  resource_id,
  status_code,
  request_id
FROM audit_logs
WHERE customer_id = 'bank_a'
  AND timestamp > now() - interval '90 days'
ORDER BY timestamp DESC;
```

**Example output:**
```
2026-07-22 14:32:15 | bank_a | key_prod_a | POST /v2/screenings | screening_7d4e9f2a | 201 | req_abc123
2026-07-22 14:45:33 | bank_a | key_prod_a | POST /v2/cases/.../events | evt_decision | 201 | req_def456
2026-07-22 15:00:00 | bank_b | key_audit_b | GET /v2/cases/case_8f1a6c3b/bundle | case_8f1a6c3b | 200 | req_ghi789
```

### Export for Regulators

**ProofRail can generate a compliance package for inspection:**

```bash
# Export all Bank A screenings + cases for last 90 days
curl -sS "https://proofrail.example.com/v1/admin/export/compliance" \
  -H "x-admin-key: ${ADMIN_KEY}" \
  -H "content-type: application/json" \
  -d '{
    "customer_id": "bank_a",
    "start_date": "2026-04-24",
    "end_date": "2026-07-22",
    "format": "json"
  }' > bank_a_compliance_export_2026q2.json
```

**Contents:**
- All screenings (subject names, risk scores, decisions)
- Case timelines (who reviewed, when, what decision)
- Evidence pack hashes (for verification)
- Signature verification checksums (for auditor to re-verify)
- Digital signatures (proof of non-repudiation)

---

## Incident Response Checklist

### If API key is leaked

```bash
# 1. Immediately revoke the key
curl -sS -X DELETE "https://proofrail.example.com/v1/admin/keys/${KEY_ID}" \
  -H "x-admin-key: ${ADMIN_KEY}"

# 2. Notify customer
EMAIL: "Your API key ${KEY_ID} was compromised and has been revoked."

# 3. Review access logs
curl -sS "https://proofrail.example.com/v1/admin/audit" \
  -H "x-admin-key: ${ADMIN_KEY}" \
  -d '{"api_key_id": "${KEY_ID}"}'

# 4. Customer generates new key
curl -sS -X POST "https://proofrail.example.com/v1/admin/keys" \
  -H "x-admin-key: ${ADMIN_KEY}" \
  -H "x-customer-id: bank_a" \
  -d '{"customer_id":"bank_a","scopes":["write:screen","read:evidence"]}'

# 5. Update integrations to use new key
# (Customer responsibility)
```

### If signing private key is leaked (critical)

```bash
# 1. Immediately rotate signing key (see Key Rotation Ceremony above)
# 2. Publish incident notice with new key ID
# 3. Allow 30-day grace period for old key (bundles still verifiable)
# 4. Notify all customers + auditors
# 5. Re-sign all recent bundles (last 30 days) with new key
```

### If evidence is tampered

```bash
# 1. Bundle verification will fail:
python scripts/verify_bundle.py --bundle compromised_bundle.json
# Output: ❌ Hash chain mismatch at event 3

# 2. Investigate via audit logs
curl -sS "https://proofrail.example.com/v1/admin/audit" \
  -H "x-admin-key: ${ADMIN_KEY}" \
  -d '{"case_id":"case_8f1a6c3b","event_types":["mutation_attempt"]}'

# 3. If internal tampering: revoke actor's access
# 4. If external tampering: evidence was signed, hash chain will catch it
# 5. Escalate to compliance team and regulatory contact
```

---

## Metrics & Monitoring

### Key Metrics for Multi-Customer Operations

```bash
# ProofRail exposes at /v1/admin/metrics:

proofrail_screenings_total{customer_id="bank_a"} 15342
proofrail_screenings_total{customer_id="bank_b"} 2104

proofrail_case_events_total{customer_id="bank_a"} 42891
proofrail_bundle_verifications_total{customer_id="bank_b"} 312

proofrail_api_requests_total{customer_id="bank_a",status="200"} 18234
proofrail_api_requests_total{customer_id="bank_a",status="401"} 1234  # ← watch this
proofrail_api_requests_total{customer_id="bank_a",status="429"} 0

proofrail_rate_limit_hit_total{customer_id="bank_a"} 0
proofrail_rate_limit_hit_total{customer_id="bank_b"} 0

proofrail_signing_key_rotations_total 3
proofrail_signing_keys_active 5

proofrail_webhook_deliveries_total{customer_id="bank_a",status="delivered"} 8234
proofrail_webhook_deliveries_total{customer_id="bank_a",status="failed"} 12
```

### Alerting Thresholds

```yaml
# prometheus-rules.yml
groups:
  - name: proofrail
    rules:
      - alert: AuthenticationFailureRate
        expr: rate(proofrail_api_requests_total{status="401"}[5m]) > 0.5  # >0.5 req/s
        annotations:
          summary: "High authentication failure rate for {{ $labels.customer_id }}"
          
      - alert: RateLimitHit
        expr: rate(proofrail_rate_limit_hit_total[5m]) > 0
        annotations:
          summary: "Rate limit triggered for {{ $labels.customer_id }}"
          
      - alert: WebhookDeliveryFailure
        expr: rate(proofrail_webhook_deliveries_total{status="failed"}[5m]) > 0
        annotations:
          summary: "Webhook delivery failures for {{ $labels.customer_id }}"
```

---

## Summary

Multi-customer ProofRail requires:

1. **API key scoping:** Each customer one key with explicit scopes
2. **Database isolation:** Row-level access control + S3 prefix isolation
3. **Signing key management:** Track key lifecycle per customer
4. **Rate limiting:** Pre-auth + per-customer limits
5. **Webhook security:** HTTPS-only, SSRF prevention, per-subscription secrets
6. **Audit logging:** All actions logged with customer_id
7. **Incident response:** Procedures for key leak, tampering, key rotation

This architecture enables banking partners to audit ProofRail's compliance with their requirements independently (using offline verification), while you maintain secure multi-tenant operations.
