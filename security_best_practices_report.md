# ProofRail Evidence API — Practical Security Audit

## Executive summary

The Evidence API is generally structured with **API-key auth + scope checks** and uses **HMAC-SHA256** for signatures. The highest-risk gaps are around **evidence pack integrity guarantees (tamper/corruption detection)** and **rate-limit bypass/DoS surfaces for unauthenticated/invalid-key traffic**. There are also smaller issues around **admin key comparison**, **unbounded in-memory rate limiter state**, and **request validation limits** for large bodies.

This report focuses on: auth bypasses, secret management, request validation, rate-limit weaknesses, signing verification pitfalls, evidence pack tamper risks, and PII logging/handling.

---

## Critical findings

### PR-001 — Evidence pack tamper/corruption is not detected on read (signing can “bless” a tampered file)

- **Severity**: Critical
- **Location**: `ProofRail/service/storage.py` `EvidenceStore.get_pack` (L66–L68) and `EvidenceStore.put_pack` (L58–L64); signing endpoint `ProofRail/service/app.py` `get_evidence_pack_signature` (L173–L188)
- **Evidence**:

```66:68:ProofRail/service/storage.py
    def get_pack(self, evidence_pack_id: str) -> dict[str, Any]:
        path = self.evidence_pack_path(evidence_pack_id)
        return json.loads(path.read_text(encoding="utf-8"))
```

```58:64:ProofRail/service/storage.py
    def put_pack(self, pack: dict[str, Any]) -> EvidencePackRef:
        payload = canonical_json_bytes(pack)
        pack_id = sha256_hex(payload)
        path = self.evidence_pack_path(pack_id)
        if not path.exists():
            path.write_bytes(payload)
```

```181:187:ProofRail/service/app.py
        pack = store.get_pack(evidence_pack_id)
        ...
        payload = canonical_json_bytes(pack)
        return {"evidence_pack_id": evidence_pack_id, "signature": sign_bytes(signing_secret, payload)}
```

- **Impact**: If an attacker (or accident) modifies a stored pack file on disk, the API will return the tampered pack and can generate a fresh “valid” signature over the tampered content, undermining evidence immutability and non-repudiation.
- **Fix (smallest viable change)**:
  - **Fail closed when reading packs**: in `EvidenceStore.get_pack`, recompute the canonical hash and ensure it equals `evidence_pack_id`. If not, raise an error (mapped to 500/404) indicating corruption/tamper.
  - Optional defense-in-depth: also validate `schema_version` presence and expected fields before returning.
- **Mitigation (if immediate fix is hard)**:
  - Store packs on **append-only / immutable** storage (e.g., object store with versioning + write-once bucket policy).
  - Maintain an **external transparency log** of pack IDs.
- **False positive notes**: This is about **integrity at rest**. If your deployment environment guarantees immutability (e.g., WORM storage), the risk is reduced, but the app code still has no detection.

---

## High findings

### PR-002 — Rate limiting is skipped for missing/invalid API keys (cheap DoS + key-guess traffic not throttled)

- **Severity**: High
- **Location**: `ProofRail/service/app.py` middleware `request_log_and_limits` (L105–L139)
- **Evidence**:

```111:119:ProofRail/service/app.py
            if request.url.path.startswith("/v1/") and not request.url.path.startswith("/v1/admin/"):
                api_key = request.headers.get("x-api-key")
                if not api_key:
                    return JSONResponse(status_code=401, content={"detail": "missing_api_key"})
                resolved = db.resolve_api_key(hash_api_key(api_key))
                if resolved is None:
                    return JSONResponse(status_code=401, content={"detail": "invalid_api_key"})
```

- **Impact**: Attackers can send high-rate requests with **no key** or **random keys** and never hit the token bucket (because the limiter is only checked after key resolution). This still burns CPU and, importantly, triggers a DB lookup for every invalid key.
- **Fix (smallest viable change)**:
  - Add a lightweight **pre-auth limiter** keyed by **client IP** (or `X-Forwarded-For` only if trusted proxy is configured) that applies to `/v1/*` before resolving the API key.
  - Alternatively (even smaller): apply the existing limiter to a constant key like `"__unauth__"` when the API key is missing/invalid, but **IP-based** is strongly preferred to avoid one attacker starving others.
- **Mitigation**:
  - Enforce rate limits at the edge (CDN/WAF) for 401s and for `/v1/*`.
- **False positive notes**: If an API gateway already rate-limits 401s, this may already be mitigated; it’s not visible in app code.

---

### PR-003 — Unbounded in-memory limiter state can grow without limit (memory DoS with many keys/customers)

- **Severity**: High
- **Location**: `ProofRail/service/app.py` (L76–L79, L130–L137) and `ProofRail/service/ratelimit.py` (L26–L45)
- **Evidence**:

```76:79:ProofRail/service/app.py
    limiter = RateLimiter(capacity=rpm, refill_per_s=rpm / 60.0)
    limiter_by_customer: dict[str, RateLimiter] = {}
    limiter_lock = threading.Lock()
```

```31:45:ProofRail/service/ratelimit.py
        self._buckets: dict[str, TokenBucket] = {}
...
            bucket = self._buckets.get(key)
            if bucket is None:
                bucket = TokenBucket(...)
                self._buckets[key] = bucket
            return bucket.allow(cost=cost)
```

- **Impact**: A large number of distinct API keys (or customers, via plan overrides) grows `_buckets` and `limiter_by_customer` indefinitely. If a key is abused or many keys exist, memory can grow without bound.
- **Fix (smallest viable change)**:
  - Add **bucket eviction**: e.g., store `last_seen` and prune buckets not used in \(N\) hours, or cap the dict size with LRU behavior.
  - Consider using a shared external rate-limit store (Redis) for multi-process deployments.
- **False positive notes**: If this API runs as a single small instance with few keys, it may be acceptable short-term; it’s still a latent DoS vector.

---

## Medium findings

### PR-004 — Admin key comparison is not constant-time (minor, but easy to harden)

- **Severity**: Medium
- **Location**: `ProofRail/service/app.py` `require_admin` (L84–L89)
- **Evidence**:

```84:89:ProofRail/service/app.py
    def require_admin(x_admin_key: str | None = Header(default=None)) -> None:
        if admin_key is None:
            raise HTTPException(status_code=503, detail="admin_not_configured")
        if x_admin_key != admin_key:
            raise HTTPException(status_code=401, detail="admin_unauthorized")
```

- **Impact**: In some settings, naive string comparison can leak timing signals for brute forcing. Usually low risk over networks, but constant-time comparison is a cheap improvement.
- **Fix (smallest viable change)**: Use `hmac.compare_digest(x_admin_key or "", admin_key)` and ensure `admin_key` is a string.

---

### PR-005 — Signature verification endpoint accepts arbitrary JSON without size/shape limits (DoS + ambiguity)

- **Severity**: Medium
- **Location**: `ProofRail/service/app.py` `verify_evidence_pack` (L189–L202)
- **Evidence**:

```189:202:ProofRail/service/app.py
    def verify_evidence_pack(
        evidence_pack: dict[str, Any],
        signature: str,
        principal: ApiPrincipal = Depends(principal_from_request),
    ) -> dict[str, bool]:
        ...
        payload = canonical_json_bytes(evidence_pack)
        return {"valid": verify_bytes(signing_secret, payload, signature)}
```

- **Impact**: Clients can submit very large nested objects causing CPU/memory overhead during JSON parsing and canonicalization. Also, a free-form dict makes it harder to enforce consistent schema and can introduce subtle canonicalization mismatches across clients.
- **Fix (smallest viable change)**:
  - Add a Pydantic model for the request with an **upper bound** on signature length and (if feasible) enforce that `evidence_pack` includes expected keys (at least `schema_version`, `customer_id`, `created_at`).
  - Configure request body size limits at the server/edge (FastAPI/Uvicorn itself doesn’t provide a simple global limit without middleware/ASGI server config; most teams enforce at proxy).

---

### PR-006 — Evidence signing secret is global (tenant separation is policy-only, not cryptographic)

- **Severity**: Medium
- **Location**: `ProofRail/service/app.py` `signing_secret` loaded once (L66–L67) and used for all customers (L184–L201)
- **Evidence**:

```65:67:ProofRail/service/app.py
    admin_key = os.environ.get("PROOFRAIL_ADMIN_KEY")
    signing_secret = os.environ.get("PROOFRAIL_SIGNING_SECRET", "").encode("utf-8")
```

- **Impact**: If `PROOFRAIL_SIGNING_SECRET` is compromised, signatures for all customers are forgeable. This is acceptable for a single-tenant deployment, but weaker for multi-tenant evidence guarantees.
- **Fix (smallest viable change)**:
  - Derive a per-customer signing key: `HMAC(master_secret, customer_id)` and sign with derived bytes. This keeps a single master secret but cryptographically partitions tenants.
- **False positive notes**: If ProofRail is intentionally single-tenant per deployment, this may be a non-issue; confirm deployment model.

---

## Low findings / observations

### PR-007 — PII in logs: currently minimal (good), but add guardrails

- **Severity**: Low
- **Location**: `ProofRail/service/app.py` middleware logs usage events (L144–L156) and does not log request bodies.
- **Evidence**:

```144:156:ProofRail/service/app.py
        # Minimal structured log line; replace with proper logger later.
        duration_ms = (time.perf_counter() - t0) * 1000.0
        response.headers["x-proofrail-latency-ms"] = f"{duration_ms:.2f}"
        if principal is not None and request.url.path.startswith("/v1/") and not request.url.path.startswith("/v1/admin/"):
            db.insert_usage_event(
                ts=utc_now_iso(),
                api_key_id=principal.api_key_id,
                customer_id=principal.customer_id,
                route=request.url.path,
                status_code=getattr(response, "status_code", 0),
                latency_ms=duration_ms,
                request_id=getattr(request.state, "request_id", None),
            )
```

- **Impact**: Current logging avoids subject names and evidence contents (good). Risk arises if future debug logging prints request bodies/headers (which include `x-api-key` and subject names).
- **Fix (smallest viable change)**:
  - Centralize logging and **explicitly redact** `x-api-key`, `authorization`, and request bodies by default.
  - Add tests or lint rules preventing logging of sensitive headers.

---

## Prioritized “smallest viable changes” (recommended order)

1) **Detect evidence pack corruption/tamper on read** (`EvidenceStore.get_pack` verifies hash == filename).  
2) **Add pre-auth rate limiting** for `/v1/*` (IP-based) to throttle 401/invalid-key traffic.  
3) **Bound limiter memory growth** (bucket eviction/LRU).  
4) **Harden admin key compare** with constant-time compare.  
5) **Add schema/size validation** for `/v1/evidence-packs/verify` and enforce body size limits at edge.  
6) **Optionally derive per-customer signing keys** from a master secret for multi-tenant cryptographic separation.

---

## Reconciliation (enterprise hardening pass, 2026-04)

The following items from this report were **re-verified against current code** and addressed where gaps remained:

| ID | Status | Notes |
|----|--------|-------|
| PR-001 | **Mitigated** | `EvidenceStore.get_pack` and `EvidenceStoreS3.get_pack` recompute SHA256 of stored bytes and raise `evidence_pack_integrity_failed` on mismatch. |
| PR-002 | **Mitigated** | Per-IP **pre-auth** rate limit (`preauth_limiter`, `PROOFRAIL_PREAUTH_RPM`) runs **before** `resolve_api_key` when `x-api-key` is present, throttling invalid-key probing without requiring a successful key lookup first. |
| PR-003 | **Mitigated** | `RateLimiter` uses an LRU-ordered bucket map with configurable cap (`PROOFRAIL_RATELIMIT_MAX_BUCKETS`, default 50000). |
| PR-004 | **Mitigated** | Admin auth uses `hmac.compare_digest` in `middleware.require_admin_factory`. |
| PR-005 | **Mitigated** | `VerifyEvidencePackRequest` caps canonical JSON size (512 KiB) and signature length; `Content-Length` guard remains in middleware. |
| PR-006 | **Open / by design** | Global signing secret unless deployment uses per-tenant secrets via env rotation; v2 bundle signing uses keyed map when configured. |
| PR-007 | **Observation** | No change; retain redaction guardrails for future logging. |

