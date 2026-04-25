from __future__ import annotations

import hmac
import queue
import time
import uuid
from typing import Any

from fastapi import Header, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from ProofRail.service.api.schemas import ErrorResponse
from ProofRail.service.auth import ApiPrincipal, hash_api_key, parse_scopes
from ProofRail.service.metrics import UsageEvent
from ProofRail.service.ratelimit import RateLimiter
from ProofRail.service.state import AppState
from ProofRail.service.utils import utc_now_iso


def error_payload(
    *,
    code: str,
    message: str | None = None,
    request_id: str | None = None,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return ErrorResponse(
        error={
            "code": code,
            "message": message or code,
            "request_id": request_id,
            "details": details,
        }
    ).model_dump()


def error_response(
    *,
    status_code: int,
    code: str,
    request_id: str | None,
    message: str | None = None,
    details: dict[str, Any] | None = None,
) -> JSONResponse:
    resp = JSONResponse(
        status_code=status_code,
        content=error_payload(code=code, message=message, request_id=request_id, details=details),
    )
    if request_id:
        resp.headers["x-request-id"] = request_id
    if status_code == 429:
        resp.headers.setdefault("retry-after", "1")
    return resp


def raise_http_error(
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


def require_admin_factory(state: AppState):
    def require_admin(x_admin_key: str | None = Header(default=None)) -> None:
        if state.cfg.admin_key is None:
            raise_http_error(status_code=503, code="admin_not_configured")
        if x_admin_key is None or not hmac.compare_digest(x_admin_key, state.cfg.admin_key):
            raise_http_error(status_code=401, code="admin_unauthorized")

    return require_admin


def principal_from_request(request: Request) -> ApiPrincipal:
    principal = getattr(request.state, "principal", None)
    if principal is None:
        raise_http_error(status_code=401, code="missing_api_key")
    return principal


def install_exception_handlers(app, state: AppState) -> None:
    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException):
        request_id = getattr(request.state, "request_id", None)
        detail = exc.detail
        if isinstance(detail, dict) and "code" in detail:
            code = str(detail.get("code"))
            message = str(detail.get("message") or code)
            details = detail.get("details")
            if details is not None and not isinstance(details, dict):
                details = {"detail": details}
            return error_response(
                status_code=exc.status_code,
                code=code,
                message=message,
                request_id=request_id,
                details=details,
            )
        if isinstance(detail, str):
            return error_response(
                status_code=exc.status_code, code=detail, message=detail, request_id=request_id
            )
        return error_response(
            status_code=exc.status_code,
            code="http_error",
            message="http_error",
            request_id=request_id,
            details={"detail": detail},
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError):
        request_id = getattr(request.state, "request_id", None)
        return error_response(
            status_code=422,
            code="validation_error",
            message="validation_error",
            request_id=request_id,
            details={"errors": exc.errors()},
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception):
        request_id = getattr(request.state, "request_id", None)
        return error_response(status_code=500, code="internal_error", request_id=request_id)


def install_request_middleware(app, state: AppState) -> None:
    @app.middleware("http")
    async def request_log_and_limits(request: Request, call_next):
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex
        request.state.request_id = request_id

        # Request size guard (best-effort; relies on Content-Length).
        cl = request.headers.get("content-length")
        if cl is not None:
            try:
                if int(cl) > state.cfg.max_request_bytes:
                    return error_response(
                        status_code=413,
                        code="request_too_large",
                        request_id=request_id,
                        details={"max_bytes": state.cfg.max_request_bytes},
                    )
            except ValueError:
                pass

        t0 = time.perf_counter()
        principal: ApiPrincipal | None = None

        try:
            is_api = request.url.path.startswith("/v1/") or request.url.path.startswith("/v2/")
            is_admin = request.url.path.startswith("/v1/admin/")
            if is_api and not is_admin:
                api_key = request.headers.get("x-api-key")
                if not api_key:
                    anon_key = request.client.host if request.client else "unknown"
                    if not state.limiter.allow(f"anon:{anon_key}"):
                        return error_response(
                            status_code=429, code="rate_limited", request_id=request_id
                        )
                    return error_response(
                        status_code=401, code="missing_api_key", request_id=request_id
                    )

                preauth_key = request.client.host if request.client else "unknown"
                if not state.preauth_limiter.allow(f"preauth:{preauth_key}"):
                    return error_response(
                        status_code=429, code="rate_limited", request_id=request_id
                    )

                resolved = state.db.resolve_api_key(hash_api_key(api_key))
                if resolved is None:
                    anon_key = request.client.host if request.client else "unknown"
                    if not state.limiter.allow(f"anon:{anon_key}"):
                        return error_response(
                            status_code=429, code="rate_limited", request_id=request_id
                        )
                    return error_response(
                        status_code=401, code="invalid_api_key", request_id=request_id
                    )

                api_key_id, customer_id, scopes = resolved
                principal = ApiPrincipal(
                    customer_id=customer_id,
                    api_key_id=api_key_id,
                    scopes=parse_scopes(scopes),
                )
                request.state.principal = principal

                plan = state.db.get_plan(customer_id)
                effective_rpm = int(plan["rpm"]) if plan and "rpm" in plan else state.cfg.rpm
                if effective_rpm == state.cfg.rpm:
                    effective_limiter = state.limiter
                else:
                    with state.limiter_lock:
                        effective_limiter = state.limiter_by_customer.get(customer_id)
                        if (
                            effective_limiter is None
                            or int(effective_limiter.capacity) != effective_rpm
                        ):
                            effective_limiter = RateLimiter(
                                capacity=effective_rpm,
                                refill_per_s=effective_rpm / 60.0,
                                max_buckets=state.cfg.ratelimit_max_buckets,
                            )
                            state.limiter_by_customer[customer_id] = effective_limiter

                if not effective_limiter.allow(api_key_id):
                    return error_response(
                        status_code=429, code="rate_limited", request_id=request_id
                    )

            response = await call_next(request)
        except HTTPException as exc:
            response = await app.exception_handlers[HTTPException](request, exc)  # type: ignore[index]

        duration_ms = (time.perf_counter() - t0) * 1000.0
        response.headers["x-request-id"] = request_id
        response.headers["x-proofrail-latency-ms"] = f"{duration_ms:.2f}"

        if principal is not None and is_api and not is_admin:
            try:
                state.usage_queue.put_nowait(
                    UsageEvent(
                        ts=utc_now_iso(),
                        api_key_id=principal.api_key_id,
                        customer_id=principal.customer_id,
                        route=request.url.path,
                        status_code=int(getattr(response, "status_code", 0)),
                        latency_ms=float(duration_ms),
                        request_id=request_id,
                    )
                )
            except queue.Full:
                state.usage_stats.dropped += 1

        return response
