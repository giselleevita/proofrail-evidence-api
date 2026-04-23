from __future__ import annotations

import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator

from ProofRail.service.auth import ApiPrincipal, generate_api_key, hash_api_key, parse_scopes
from ProofRail.service.cache import IngestionCache
from ProofRail.service.db import DbConfig, ProofRailDb
from ProofRail.service.ingestion import ingest_sources
from ProofRail.service.models import Decision
from ProofRail.service.ratelimit import RateLimiter
from ProofRail.service.scheduler import RefreshScheduler
from ProofRail.service.screening import NameSetCache, compute_screening_key, screen_subject_name
from ProofRail.service.signing import sign_bytes, verify_bytes
from ProofRail.service.storage import EvidenceStore, canonical_json_bytes, sha256_hex
from ProofRail.service.utils import utc_now_iso


class Subject(BaseModel):
    subject_id: str | None = None
    name: str = Field(min_length=1, max_length=512)


class ScreenRequest(BaseModel):
    subject: Subject
    retrieval_ts: str | None = None


class ScreenResponse(BaseModel):
    decision: Literal["allow", "block", "review"]
    evidence_pack_id: str
    list_version: str
    hits: list[str]


class ErrorInfo(BaseModel):
    code: str
    message: str
    request_id: str | None = None
    details: dict[str, Any] | None = None


class ErrorResponse(BaseModel):
    error: ErrorInfo


class CreateKeyRequest(BaseModel):
    customer_id: str = Field(min_length=1, max_length=128)
    scopes: list[str] | str | None = None

    @field_validator("scopes", mode="before")
    @classmethod
    def _normalize_scopes(cls, v: Any) -> list[str]:
        if v is None:
            return ["write:screen", "read:evidence"]
        if isinstance(v, str):
            return [s.strip() for s in v.split(",") if s.strip()]
        if isinstance(v, list):
            out: list[str] = []
            for item in v:
                if not isinstance(item, str):
                    raise ValueError("scopes must be strings")
                s = item.strip()
                if s:
                    out.append(s)
            return out
        raise ValueError("scopes must be a string, list of strings, or null")


class CreateKeyResponse(BaseModel):
    customer_id: str
    api_key_id: str
    api_key: str
    scopes: list[str]


class RevokeKeyRequest(BaseModel):
    api_key_id: str = Field(min_length=1, max_length=128)


class RotateKeyRequest(BaseModel):
    customer_id: str = Field(min_length=1, max_length=128)
    scopes: list[str] | str | None = None

    @field_validator("scopes", mode="before")
    @classmethod
    def _normalize_scopes(cls, v: Any) -> list[str]:
        if v is None:
            return ["write:screen", "read:evidence"]
        if isinstance(v, str):
            return [s.strip() for s in v.split(",") if s.strip()]
        if isinstance(v, list):
            out: list[str] = []
            for item in v:
                if not isinstance(item, str):
                    raise ValueError("scopes must be strings")
                s = item.strip()
                if s:
                    out.append(s)
            return out
        raise ValueError("scopes must be a string, list of strings, or null")


class EvidencePack(BaseModel):
    schema_version: str
    created_at: str
    customer_id: str
    list_version: str
    screen_key: str
    ingestion: dict[str, Any]
    input: dict[str, Any]
    result: dict[str, Any]
    determinism: dict[str, Any]


class EvidencePackSignatureResponse(BaseModel):
    evidence_pack_id: str
    signature: str


class VerifyEvidencePackRequest(BaseModel):
    evidence_pack: dict[str, Any]
    signature: str


class VerifyEvidencePackResponse(BaseModel):
    valid: bool


def _error_payload(
    *,
    code: str,
    message: str | None = None,
    request_id: str | None = None,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return ErrorResponse(
        error=ErrorInfo(
            code=code,
            message=message or code,
            request_id=request_id,
            details=details,
        )
    ).model_dump()


def _error_response(
    *,
    status_code: int,
    code: str,
    message: str | None = None,
    request_id: str | None = None,
    details: dict[str, Any] | None = None,
) -> JSONResponse:
    resp = JSONResponse(
        status_code=status_code,
        content=_error_payload(code=code, message=message, request_id=request_id, details=details),
    )
    if request_id:
        resp.headers["x-request-id"] = request_id
    return resp


def _raise_http_error(
    *,
    status_code: int,
    code: str,
    message: str | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    raise HTTPException(
        status_code=status_code,
        detail={"code": code, "message": message or code, "details": details},
    )


def create_app(*, ingest_func=ingest_sources) -> FastAPI:
    app = FastAPI(title="ProofRail API", version="0.1.0")

    store_root = Path(os.environ.get("PROOFRAIL_STORE_DIR", "proofrail_store"))
    raw_dir = Path(os.environ.get("PROOFRAIL_RAW_DIR", str(store_root / "raw_fetches")))
    store = EvidenceStore(store_root)

    db_path = Path(os.environ.get("PROOFRAIL_DB_PATH", str(store_root / "proofrail.sqlite3")))
    db = ProofRailDb(DbConfig(path=db_path))

    admin_key = os.environ.get("PROOFRAIL_ADMIN_KEY")
    signing_secret = os.environ.get("PROOFRAIL_SIGNING_SECRET", "").encode("utf-8")

    cache_ttl_s = int(os.environ.get("PROOFRAIL_INGEST_TTL_SECONDS", "3600"))
    ingest_cache = IngestionCache(ttl_seconds=cache_ttl_s)
    global_ingest_key = "__global__"

    # Small in-process cache: screen_key -> evidence_pack_id
    cache: dict[str, str] = {}

    # Rate limiting (per API key id)
    rpm = int(os.environ.get("PROOFRAIL_RPM", "120"))
    limiter = RateLimiter(capacity=rpm, refill_per_s=rpm / 60.0)
    limiter_by_customer: dict[str, RateLimiter] = {}
    limiter_lock = threading.Lock()

    scheduler = RefreshScheduler()
    name_sets_cache = NameSetCache(max_entries=64)

    def require_admin(x_admin_key: str | None = Header(default=None)) -> None:
        if admin_key is None:
            _raise_http_error(status_code=503, code="admin_not_configured")
        if x_admin_key != admin_key:
            _raise_http_error(status_code=401, code="admin_unauthorized")

    def principal_from_request(request: Request) -> ApiPrincipal:
        principal = getattr(request.state, "principal", None)
        if principal is None:
            _raise_http_error(status_code=401, code="missing_api_key")
        return principal

    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException):
        request_id = getattr(request.state, "request_id", None)
        detail = exc.detail
        if isinstance(detail, dict) and "code" in detail:
            code = str(detail.get("code"))
            message = str(detail.get("message") or code)
            details = detail.get("details")
            normalized_details: dict[str, Any] | None
            if details is None or isinstance(details, dict):
                normalized_details = details
            else:
                normalized_details = {"detail": details}
            return _error_response(
                status_code=exc.status_code,
                code=code,
                message=message,
                request_id=request_id,
                details=normalized_details,
            )
        if isinstance(detail, str):
            return _error_response(
                status_code=exc.status_code,
                code=detail,
                message=detail,
                request_id=request_id,
            )
        return _error_response(
            status_code=exc.status_code,
            code="http_error",
            message="http_error",
            request_id=request_id,
            details={"detail": detail},
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError):
        request_id = getattr(request.state, "request_id", None)
        return _error_response(
            status_code=422,
            code="validation_error",
            message="validation_error",
            request_id=request_id,
            details={"errors": exc.errors()},
        )

    @app.middleware("http")
    async def request_log_and_limits(request: Request, call_next):
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex
        request.state.request_id = request_id
        t0 = time.perf_counter()
        principal: ApiPrincipal | None = None
        try:
            # Only enforce rate limiting on /v1/* endpoints that are not admin routes.
            if request.url.path.startswith("/v1/") and not request.url.path.startswith("/v1/admin/"):
                api_key = request.headers.get("x-api-key")
                if not api_key:
                    return _error_response(status_code=401, code="missing_api_key", request_id=request_id)
                resolved = db.resolve_api_key(hash_api_key(api_key))
                if resolved is None:
                    return _error_response(status_code=401, code="invalid_api_key", request_id=request_id)
                api_key_id, customer_id, scopes = resolved
                principal = ApiPrincipal(
                    customer_id=customer_id, api_key_id=api_key_id, scopes=parse_scopes(scopes)
                )
                request.state.principal = principal
                # Plan override (billing hook): rpm can be configured per customer.
                plan = db.get_plan(customer_id)
                effective_rpm = int(plan["rpm"]) if plan and "rpm" in plan else rpm
                if effective_rpm == rpm:
                    effective_limiter = limiter
                else:
                    with limiter_lock:
                        effective_limiter = limiter_by_customer.get(customer_id)
                        if effective_limiter is None or int(effective_limiter.capacity) != effective_rpm:
                            effective_limiter = RateLimiter(
                                capacity=effective_rpm, refill_per_s=effective_rpm / 60.0
                            )
                            limiter_by_customer[customer_id] = effective_limiter
                if not effective_limiter.allow(api_key_id):
                    return _error_response(status_code=429, code="rate_limited", request_id=request_id)
            response = await call_next(request)
        except HTTPException as exc:
            response = await http_exception_handler(request, exc)
        # Minimal structured log line; replace with proper logger later.
        duration_ms = (time.perf_counter() - t0) * 1000.0
        response.headers["x-request-id"] = request_id
        response.headers["x-proofrail-latency-ms"] = f"{duration_ms:.2f}"
        if principal is not None and request.url.path.startswith("/v1/") and not request.url.path.startswith("/v1/admin/"):
            db.insert_usage_event(
                ts=utc_now_iso(),
                api_key_id=principal.api_key_id,
                customer_id=principal.customer_id,
                route=request.url.path,
                status_code=getattr(response, "status_code", 0),
                latency_ms=duration_ms,
            )
        return response

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/v1/usage/summary")
    # response_model dict is fine here; keep simple for SDKs.
    def usage_summary(
        since_ts: str,
        principal: ApiPrincipal = Depends(principal_from_request),
    ) -> dict[str, int]:
        # usage endpoint requires read:evidence scope (same as evidence access).
        if "read:evidence" not in principal.scopes:
            _raise_http_error(status_code=403, code="missing_scope")
        return db.usage_summary(principal.customer_id, since_ts)

    @app.get(
        "/v1/evidence-packs/{evidence_pack_id}/signature",
        response_model=EvidencePackSignatureResponse,
    )
    def get_evidence_pack_signature(
        evidence_pack_id: str, principal: ApiPrincipal = Depends(principal_from_request)
    ) -> EvidencePackSignatureResponse:
        if "read:evidence" not in principal.scopes:
            _raise_http_error(status_code=403, code="missing_scope")
        if not store.has_pack(evidence_pack_id):
            _raise_http_error(status_code=404, code="evidence_pack_not_found")
        pack = store.get_pack(evidence_pack_id)
        if pack.get("customer_id") != principal.customer_id:
            _raise_http_error(status_code=404, code="evidence_pack_not_found")
        if not signing_secret:
            _raise_http_error(status_code=503, code="signing_not_configured")
        payload = canonical_json_bytes(pack)
        return EvidencePackSignatureResponse(
            evidence_pack_id=evidence_pack_id,
            signature=sign_bytes(signing_secret, payload),
        )

    @app.post("/v1/evidence-packs/verify", response_model=VerifyEvidencePackResponse)
    def verify_evidence_pack(
        req: VerifyEvidencePackRequest,
        principal: ApiPrincipal = Depends(principal_from_request),
    ) -> VerifyEvidencePackResponse:
        if "read:evidence" not in principal.scopes:
            _raise_http_error(status_code=403, code="missing_scope")
        if not signing_secret:
            _raise_http_error(status_code=503, code="signing_not_configured")
        # Verify signature only; caller can also check customer_id match themselves.
        payload = canonical_json_bytes(req.evidence_pack)
        return VerifyEvidencePackResponse(valid=verify_bytes(signing_secret, payload, req.signature))

    @app.post("/v1/admin/keys", response_model=CreateKeyResponse, dependencies=[Depends(require_admin)])
    def admin_create_key(req: CreateKeyRequest) -> CreateKeyResponse:
        # Create customer if not exists, then create key.
        db.ensure_customer(req.customer_id, utc_now_iso())
        api_key = generate_api_key()
        api_key_id = sha256_hex(api_key.encode("utf-8"))[:16]
        db.create_api_key(
            api_key_id, req.customer_id, hash_api_key(api_key), ",".join(req.scopes), utc_now_iso()
        )
        return CreateKeyResponse(
            customer_id=req.customer_id, api_key_id=api_key_id, api_key=api_key, scopes=req.scopes
        )

    @app.post("/v1/admin/keys/revoke", dependencies=[Depends(require_admin)])
    def admin_revoke_key(req: RevokeKeyRequest) -> dict[str, str]:
        db.revoke_api_key(req.api_key_id, utc_now_iso())
        return {"status": "revoked"}

    @app.post("/v1/admin/keys/rotate", response_model=CreateKeyResponse, dependencies=[Depends(require_admin)])
    def admin_rotate_key(req: RotateKeyRequest) -> CreateKeyResponse:
        # Rotation creates a new key; caller can revoke old ones separately.
        db.ensure_customer(req.customer_id, utc_now_iso())
        api_key = generate_api_key()
        api_key_id = sha256_hex(api_key.encode("utf-8"))[:16]
        db.create_api_key(
            api_key_id,
            req.customer_id,
            hash_api_key(api_key),
            ",".join(req.scopes),
            utc_now_iso(),
        )
        return CreateKeyResponse(
            customer_id=req.customer_id,
            api_key_id=api_key_id,
            api_key=api_key,
            scopes=req.scopes,
        )

    @app.post("/v1/admin/ingest/refresh", dependencies=[Depends(require_admin)])
    def refresh_ingest(
        customer_id: str | None = None,
        retrieval_ts: str | None = None,
    ) -> dict[str, Any]:
        """Trigger an ingestion refresh in the background."""
        _key = customer_id or global_ingest_key

        def run() -> None:
            artifact = ingest_func(store=store, output_dir=raw_dir / _key, retrieval_ts=retrieval_ts)
            ingest_cache.put(_key, artifact)

        t = threading.Thread(target=run, daemon=True)
        t.start()
        return {"status": "started"}

    @app.post("/v1/admin/ingest/schedule", dependencies=[Depends(require_admin)])
    def schedule_refresh(
        customer_id: str | None = None,
        interval_seconds: int = 3600,
    ) -> dict[str, Any]:
        _key = customer_id or global_ingest_key

        def run() -> None:
            artifact = ingest_func(store=store, output_dir=raw_dir / _key, retrieval_ts=None)
            ingest_cache.put(_key, artifact)

        scheduler.start(_key, interval_seconds, run)
        return {"status": "scheduled", "interval_seconds": interval_seconds, "key": _key}

    @app.post("/v1/admin/ingest/schedule/stop", dependencies=[Depends(require_admin)])
    def stop_schedule(customer_id: str | None = None) -> dict[str, Any]:
        _key = customer_id or global_ingest_key
        scheduler.stop(_key)
        return {"status": "stopped", "key": _key}

    @app.post(
        "/v1/sanctions/screen",
        response_model=ScreenResponse,
        responses={
            401: {"model": ErrorResponse},
            403: {"model": ErrorResponse},
            429: {"model": ErrorResponse},
            422: {"model": ErrorResponse},
            500: {"model": ErrorResponse},
        },
    )
    def sanctions_screen(req: ScreenRequest, principal: ApiPrincipal = Depends(principal_from_request)) -> ScreenResponse:
        if "write:screen" not in principal.scopes:
            _raise_http_error(status_code=403, code="missing_scope")
        artifact = ingest_cache.get_or_refresh(
            global_ingest_key,
            lambda: ingest_func(
                store=store,
                output_dir=raw_dir / global_ingest_key,
                retrieval_ts=req.retrieval_ts,
            ),
        )
        list_version = artifact.list_version
        sanctions_name_sets = name_sets_cache.get(store, artifact.normalized_name_sets_blob_sha256)
        result = screen_subject_name(req.subject.name, sanctions_name_sets)

        screen_key = compute_screening_key(
            list_version=list_version,
            subject={"customer_id": principal.customer_id, "subject": req.subject.model_dump()},
        )
        if screen_key in cache and store.has_pack(cache[screen_key]):
            evidence_pack_id = cache[screen_key]
        else:
            artifact_dict = artifact.to_dict()
            result_dict = {"decision": result.decision, "hits": result.hits, "name_norm": result.name_norm}
            canonical_pack_hash = sha256_hex(
                canonical_json_bytes({"ingestion": artifact_dict, "result": result_dict})
            )
            pack = {
                "schema_version": "1",
                "created_at": utc_now_iso(),
                "customer_id": principal.customer_id,
                "list_version": list_version,
                "screen_key": screen_key,
                "ingestion": artifact_dict,
                "input": {"subject": req.subject.model_dump()},
                "result": result_dict,
                "determinism": {
                    "canonical_pack_hash": canonical_pack_hash,
                },
            }
            evidence_pack_id = store.put_pack(pack).evidence_pack_id
            cache[screen_key] = evidence_pack_id

        decision: Decision = result.decision
        return ScreenResponse(
            decision=decision,
            evidence_pack_id=evidence_pack_id,
            list_version=list_version,
            hits=list(result.hits),
        )

    @app.get(
        "/v1/evidence-packs/{evidence_pack_id}",
        response_model=EvidencePack,
        responses={
            401: {"model": ErrorResponse},
            403: {"model": ErrorResponse},
            404: {"model": ErrorResponse},
            422: {"model": ErrorResponse},
            500: {"model": ErrorResponse},
        },
    )
    def get_evidence_pack(
        evidence_pack_id: str, principal: ApiPrincipal = Depends(principal_from_request)
    ) -> EvidencePack:
        if "read:evidence" not in principal.scopes:
            _raise_http_error(status_code=403, code="missing_scope")
        if not store.has_pack(evidence_pack_id):
            _raise_http_error(status_code=404, code="evidence_pack_not_found")
        pack = store.get_pack(evidence_pack_id)
        if pack.get("customer_id") != principal.customer_id:
            _raise_http_error(status_code=404, code="evidence_pack_not_found")
        return EvidencePack.model_validate(pack)

    return app


app = create_app()


def main() -> int:
    """Run a dev server (uvicorn)."""
    import uvicorn

    host = os.environ.get("PROOFRAIL_HOST", "127.0.0.1")
    port = int(os.environ.get("PROOFRAIL_PORT", "8000"))
    uvicorn.run("ProofRail.service.app:app", host=host, port=port, reload=False)
    return 0

