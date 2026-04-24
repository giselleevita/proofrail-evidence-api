from __future__ import annotations

import json
import os
import threading
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import Depends, FastAPI, Request, Response

from ProofRail.service.api.schemas import (
    ERROR_RESPONSES,
    CreateKeyRequest,
    CreateKeyResponse,
    EvidencePack,
    EvidencePackSignatureResponse,
    RevokeKeyRequest,
    RotateKeyRequest,
    ScreenRequest,
    ScreenResponse,
    V1AdminRunWebhookDeliveriesResponse,
    V2CaseDetail,
    V2CaseEvent,
    V2CaseEvidenceBundle,
    V2CaseEvidenceBundleResponse,
    V2CaseEvidenceBundleSignature,
    V2CaseSummary,
    V2ChainedCaseEvent,
    V2CreateCaseEventRequest,
    V2CreateScreeningRequest,
    V2CreateScreeningResponse,
    V2CreateWebhookSubscriptionRequest,
    V2ReviewDecisionRequest,
    V2ReviewDecisionResponse,
    V2VerifyCaseEvidenceBundleRequest,
    V2VerifyCaseEvidenceBundleResponse,
    V2WebhookSubscription,
    VerifyEvidencePackRequest,
    VerifyEvidencePackResponse,
)
from ProofRail.service.auth import ApiPrincipal, generate_api_key, hash_api_key
from ProofRail.service.config import load_config
from ProofRail.service.ingestion import ingest_sources
from ProofRail.service.ingestion_demo import ingest_demo
from ProofRail.service.middleware import (
    install_exception_handlers,
    install_request_middleware,
    principal_from_request,
    raise_http_error,
    require_admin_factory,
)
from ProofRail.service.models import Decision
from ProofRail.service.pdf_export import render_evidence_pack_pdf
from ProofRail.service.screening import compute_screening_key, screen_subject_name
from ProofRail.service.signing import sign_bytes, verify_bytes
from ProofRail.service.state import attach_lifespan, build_state
from ProofRail.service.storage import canonical_json_bytes, sha256_hex
from ProofRail.service.utils import utc_now_iso
from ProofRail.service.webhooks import build_event_payload


def create_app(*, ingest_func=ingest_sources) -> FastAPI:
    cfg = load_config()
    state = build_state(cfg)

    app = FastAPI(title="ProofRail API", version="0.1.0")
    attach_lifespan(app, state)
    app.state.proofrail = state

    install_exception_handlers(app, state)
    install_request_middleware(app, state)

    require_admin = require_admin_factory(state)

    def _emit_webhooks(
        *, customer_id: str, event_type: str, event_id: str, data: dict[str, Any]
    ) -> None:
        payload_json = build_event_payload(
            event_type=event_type, event_id=event_id, customer_id=customer_id, data=data
        )
        subs = state.db.list_webhook_subscriptions(customer_id=customer_id)
        for s in subs:
            if not s.get("active"):
                continue
            evs = s.get("events") or []
            if isinstance(evs, list) and (event_type in evs or "*" in evs):
                delivery_id = state.db.enqueue_webhook_delivery(
                    subscription_id=str(s["subscription_id"]),
                    customer_id=customer_id,
                    event_type=event_type,
                    event_id=event_id,
                    payload_json=payload_json,
                    now=utc_now_iso(),
                )
                # Pilot worker queue: enqueue a job for prompt delivery (retry logic still lives on deliveries table).
                state.db.enqueue_job(
                    job_type="webhook_delivery",
                    payload_json=json.dumps({"delivery_id": int(delivery_id)}),
                    run_at=utc_now_iso(),
                    now=utc_now_iso(),
                )

    @app.post(
        "/v1/admin/webhooks/deliveries/run",
        response_model=V1AdminRunWebhookDeliveriesResponse,
        dependencies=[Depends(require_admin)],
        responses=ERROR_RESPONSES,
    )
    def admin_run_webhook_deliveries(
        request: Request,
        limit: int = 50,
    ) -> V1AdminRunWebhookDeliveriesResponse:
        now = utc_now_iso()
        due = state.db.list_due_webhook_deliveries(now=now, limit=limit)
        enqueued = 0
        for d in due:
            state.db.enqueue_job(
                job_type="webhook_delivery",
                payload_json=json.dumps({"delivery_id": int(d["delivery_id"])}),
                run_at=now,
                now=now,
            )
            enqueued += 1

        state.db.insert_audit_event(
            ts=now,
            actor="admin",
            action="webhooks_deliveries_run",
            request_id=getattr(request.state, "request_id", None),
            details_json=json.dumps({"enqueued": enqueued}),
        )
        return V1AdminRunWebhookDeliveriesResponse(
            attempted=enqueued, delivered=0, retried=0, failed=0
        )

    global_ingest_key = "__global__"
    demo_mode = os.environ.get("PROOFRAIL_DEMO_MODE", "0") == "1"
    if ingest_func is ingest_sources and demo_mode:
        ingest_func = ingest_demo

    def _parse_iso_z(ts: str) -> datetime:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))

    def _gc_raw_fetches(*, cutoff: datetime, customer_id: str | None, dry_run: bool) -> int:
        root = state.cfg.raw_dir / (customer_id or "")
        if not root.exists():
            return 0
        deleted = 0
        for p in root.rglob("*"):
            try:
                if not p.is_file():
                    continue
                mtime = datetime.fromtimestamp(p.stat().st_mtime, tz=UTC)
                if mtime >= cutoff:
                    continue
                if not dry_run:
                    p.unlink()
                deleted += 1
            except Exception:
                continue
        return deleted

    @app.post("/v1/admin/gc/run", dependencies=[Depends(require_admin)], responses=ERROR_RESPONSES)
    def admin_gc_run(
        request: Request,
        customer_id: str | None = None,
        dry_run: bool = True,
        usage_retention_days: int | None = None,
        evidence_retention_days: int | None = None,
    ) -> dict[str, Any]:
        now = datetime.now(UTC)
        usage_days = int(usage_retention_days or state.cfg.usage_retention_days)
        if customer_id is not None:
            plan = state.db.get_plan(customer_id)
            plan_days = int(plan["evidence_retention_days"]) if plan else None
            evidence_days = int(
                evidence_retention_days or plan_days or state.cfg.evidence_retention_days
            )
        else:
            evidence_days = int(evidence_retention_days or state.cfg.evidence_retention_days)

        usage_cutoff = now - timedelta(days=usage_days)
        evidence_cutoff = now - timedelta(days=evidence_days)

        deleted_usage = (
            0
            if dry_run
            else state.db.delete_usage_events_before(
                usage_cutoff.replace(microsecond=0).isoformat().replace("+00:00", "Z")
            )
        )
        deleted_packs = state.store.delete_packs_before(
            cutoff=evidence_cutoff, customer_id=customer_id, dry_run=dry_run
        )
        deleted_raw = _gc_raw_fetches(
            cutoff=evidence_cutoff, customer_id=customer_id, dry_run=dry_run
        )
        deleted_blobs = state.store.delete_unreferenced_blobs(
            cutoff=evidence_cutoff, dry_run=dry_run
        )

        if not dry_run:
            state.db.insert_audit_event(
                ts=utc_now_iso(),
                actor="admin",
                action="gc_run",
                request_id=getattr(request.state, "request_id", None),
                details_json=json.dumps(
                    {
                        "customer_id": customer_id,
                        "usage_retention_days": usage_days,
                        "evidence_retention_days": evidence_days,
                        "deleted": {
                            "usage_events": deleted_usage,
                            "evidence_packs": deleted_packs,
                            "raw_fetch_files": deleted_raw,
                            "unreferenced_blobs": deleted_blobs,
                        },
                    }
                ),
            )

        return {
            "dry_run": bool(dry_run),
            "customer_id": customer_id,
            "usage_retention_days": usage_days,
            "evidence_retention_days": evidence_days,
            "deleted": {
                "usage_events": deleted_usage,
                "evidence_packs": deleted_packs,
                "raw_fetch_files": deleted_raw,
                "unreferenced_blobs": deleted_blobs,
            },
        }

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz", responses=ERROR_RESPONSES)
    def readyz() -> dict[str, str]:
        try:
            # DB ping
            if hasattr(state.db, "_connect"):
                with state.db._connect() as con:  # noqa: SLF001
                    con.execute("SELECT 1").fetchone()
            else:
                # Fallback: best-effort read-only call
                state.db.get_plan("probe")  # type: ignore[arg-type]

            # Store check (filesystem vs S3)
            if hasattr(state.store, "root"):
                probe = state.cfg.store_root / ".readyz"
                probe.write_text("ok", encoding="utf-8")
                probe.unlink(missing_ok=True)
            elif hasattr(state.store, "_s3") and hasattr(state.store, "bucket"):
                # S3-compatible: ensure bucket reachable.
                state.store._s3.head_bucket(Bucket=state.store.bucket)  # noqa: SLF001
        except Exception:
            raise_http_error(status_code=503, code="not_ready")
        return {"status": "ready"}

    @app.get("/v1/usage/summary")
    # response_model dict is fine here; keep simple for SDKs.
    def usage_summary(
        since_ts: str,
        principal=Depends(principal_from_request),
    ) -> dict[str, int]:
        # usage endpoint requires read:evidence scope (same as evidence access).
        if "read:evidence" not in principal.scopes:
            raise_http_error(status_code=403, code="missing_scope")
        return state.db.usage_summary(principal.customer_id, since_ts)

    @app.get(
        "/v1/evidence-packs/{evidence_pack_id}/signature",
        response_model=EvidencePackSignatureResponse,
        responses=ERROR_RESPONSES,
    )
    def get_evidence_pack_signature(
        evidence_pack_id: str, principal=Depends(principal_from_request)
    ) -> EvidencePackSignatureResponse:
        if "read:evidence" not in principal.scopes:
            raise_http_error(status_code=403, code="missing_scope")
        if not state.store.has_pack(evidence_pack_id):
            raise_http_error(status_code=404, code="evidence_pack_not_found")
        try:
            pack = state.store.get_pack(evidence_pack_id)
        except ValueError as exc:
            if str(exc) == "evidence_pack_integrity_failed":
                raise_http_error(status_code=409, code="evidence_pack_integrity_failed")
            raise
        if pack.get("customer_id") != principal.customer_id:
            raise_http_error(status_code=404, code="evidence_pack_not_found")
        if not state.cfg.signing_secret:
            raise_http_error(status_code=503, code="signing_not_configured")
        payload = canonical_json_bytes(pack)
        return EvidencePackSignatureResponse(
            evidence_pack_id=evidence_pack_id,
            signature=sign_bytes(state.cfg.signing_secret, payload),
        )

    @app.get("/v1/evidence-packs/{evidence_pack_id}/export.pdf", responses=ERROR_RESPONSES)
    def export_evidence_pack_pdf(
        evidence_pack_id: str,
        request: Request,
        principal=Depends(principal_from_request),
    ) -> Response:
        if "read:evidence" not in principal.scopes:
            raise_http_error(status_code=403, code="missing_scope")
        if not state.store.has_pack(evidence_pack_id):
            raise_http_error(status_code=404, code="evidence_pack_not_found")
        try:
            pack = state.store.get_pack(evidence_pack_id)
        except ValueError as exc:
            if str(exc) == "evidence_pack_integrity_failed":
                raise_http_error(status_code=409, code="evidence_pack_integrity_failed")
            raise
        if pack.get("customer_id") != principal.customer_id:
            raise_http_error(status_code=404, code="evidence_pack_not_found")

        request_id = getattr(request.state, "request_id", None)
        pdf_bytes = render_evidence_pack_pdf(
            pack=pack,
            evidence_pack_id=evidence_pack_id,
            request_id=request_id,
        )
        headers = {
            "content-disposition": f'attachment; filename="evidence-pack-{evidence_pack_id}.pdf"',
        }
        return Response(content=pdf_bytes, media_type="application/pdf", headers=headers)

    @app.post(
        "/v1/evidence-packs/verify",
        response_model=VerifyEvidencePackResponse,
        responses=ERROR_RESPONSES,
    )
    def verify_evidence_pack(
        req: VerifyEvidencePackRequest,
        principal=Depends(principal_from_request),
    ) -> VerifyEvidencePackResponse:
        if "read:evidence" not in principal.scopes:
            raise_http_error(status_code=403, code="missing_scope")
        if not state.cfg.signing_secret:
            raise_http_error(status_code=503, code="signing_not_configured")
        # Verify signature only; caller can also check customer_id match themselves.
        payload = canonical_json_bytes(req.evidence_pack)
        return VerifyEvidencePackResponse(
            valid=verify_bytes(state.cfg.signing_secret, payload, req.signature)
        )

    @app.post(
        "/v1/admin/keys",
        response_model=CreateKeyResponse,
        dependencies=[Depends(require_admin)],
        responses=ERROR_RESPONSES,
    )
    def admin_create_key(req: CreateKeyRequest, request: Request) -> CreateKeyResponse:
        # Create customer if not exists, then create key.
        state.db.ensure_customer(req.customer_id, utc_now_iso())
        api_key = generate_api_key()
        api_key_id = sha256_hex(api_key.encode("utf-8"))[:16]
        state.db.create_api_key(
            api_key_id, req.customer_id, hash_api_key(api_key), ",".join(req.scopes), utc_now_iso()
        )
        state.db.insert_audit_event(
            ts=utc_now_iso(),
            actor="admin",
            action="key_create",
            request_id=getattr(request.state, "request_id", None),
            details_json=json.dumps({"customer_id": req.customer_id, "api_key_id": api_key_id}),
        )
        return CreateKeyResponse(
            customer_id=req.customer_id, api_key_id=api_key_id, api_key=api_key, scopes=req.scopes
        )

    @app.post(
        "/v1/admin/keys/revoke",
        dependencies=[Depends(require_admin)],
        responses=ERROR_RESPONSES,
    )
    def admin_revoke_key(req: RevokeKeyRequest, request: Request) -> dict[str, str]:
        state.db.revoke_api_key(req.api_key_id, utc_now_iso())
        state.db.insert_audit_event(
            ts=utc_now_iso(),
            actor="admin",
            action="key_revoke",
            request_id=getattr(request.state, "request_id", None),
            details_json=json.dumps({"api_key_id": req.api_key_id}),
        )
        return {"status": "revoked"}

    @app.post(
        "/v1/admin/keys/rotate",
        response_model=CreateKeyResponse,
        dependencies=[Depends(require_admin)],
        responses=ERROR_RESPONSES,
    )
    def admin_rotate_key(req: RotateKeyRequest, request: Request) -> CreateKeyResponse:
        # Rotation creates a new key; caller can revoke old ones separately.
        state.db.ensure_customer(req.customer_id, utc_now_iso())
        api_key = generate_api_key()
        api_key_id = sha256_hex(api_key.encode("utf-8"))[:16]
        state.db.create_api_key(
            api_key_id,
            req.customer_id,
            hash_api_key(api_key),
            ",".join(req.scopes),
            utc_now_iso(),
        )
        state.db.insert_audit_event(
            ts=utc_now_iso(),
            actor="admin",
            action="key_rotate",
            request_id=getattr(request.state, "request_id", None),
            details_json=json.dumps({"customer_id": req.customer_id, "api_key_id": api_key_id}),
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
            artifact = ingest_func(
                store=state.store,
                output_dir=state.cfg.raw_dir / _key,
                retrieval_ts=retrieval_ts,
            )
            state.ingest_cache.put(_key, artifact)

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
            artifact = ingest_func(
                store=state.store, output_dir=state.cfg.raw_dir / _key, retrieval_ts=None
            )
            state.ingest_cache.put(_key, artifact)

        if not state.cfg.enable_scheduler:
            raise_http_error(status_code=409, code="scheduler_disabled")
        state.scheduler.start(_key, interval_seconds, run)
        return {"status": "scheduled", "interval_seconds": interval_seconds, "key": _key}

    @app.post("/v1/admin/ingest/schedule/stop", dependencies=[Depends(require_admin)])
    def stop_schedule(customer_id: str | None = None) -> dict[str, Any]:
        _key = customer_id or global_ingest_key
        if not state.cfg.enable_scheduler:
            raise_http_error(status_code=409, code="scheduler_disabled")
        state.scheduler.stop(_key)
        return {"status": "stopped", "key": _key}

    @app.get(
        "/v1/admin/usage/stats", dependencies=[Depends(require_admin)], responses=ERROR_RESPONSES
    )
    def admin_usage_stats() -> dict[str, Any]:
        return {
            "queue_depth": int(state.usage_queue.qsize()),
            "flushed": int(state.usage_stats.flushed),
            "dropped": int(state.usage_stats.dropped),
        }

    @app.get(
        "/v1/admin/metrics",
        dependencies=[Depends(require_admin)],
        responses=ERROR_RESPONSES,
    )
    def admin_metrics() -> dict[str, Any]:
        webhook_counts = {}
        try:
            webhook_counts = state.db.webhook_delivery_counts()
        except Exception:
            webhook_counts = {}
        return {
            "webhooks": {
                "deliveries_by_status": webhook_counts,
            },
            "usage": {
                "queue_depth": int(state.usage_queue.qsize()),
                "flushed": int(state.usage_stats.flushed),
                "dropped": int(state.usage_stats.dropped),
            },
        }

    @app.get(
        "/v1/admin/webhooks/deliveries/dlq",
        dependencies=[Depends(require_admin)],
        responses=ERROR_RESPONSES,
    )
    def admin_webhook_dlq(limit: int = 100) -> dict[str, Any]:
        items = state.db.list_failed_webhook_deliveries(limit=limit)
        return {"items": items}

    @app.post(
        "/v1/sanctions/screen",
        response_model=ScreenResponse,
        responses=ERROR_RESPONSES,
    )
    def sanctions_screen(
        req: ScreenRequest, principal: ApiPrincipal = Depends(principal_from_request)
    ) -> ScreenResponse:
        if "write:screen" not in principal.scopes:
            raise_http_error(status_code=403, code="missing_scope")
        artifact = state.ingest_cache.get_or_refresh(
            global_ingest_key,
            lambda: ingest_func(
                store=state.store,
                output_dir=state.cfg.raw_dir / global_ingest_key,
                retrieval_ts=req.retrieval_ts,
            ),
        )
        list_version = artifact.list_version
        sanctions_name_sets = state.name_sets_cache.get(
            state.store,
            artifact.normalized_name_sets_blob_sha256,
        )
        result = screen_subject_name(req.subject.name, sanctions_name_sets)

        screen_key = compute_screening_key(
            list_version=list_version,
            subject={"customer_id": principal.customer_id, "subject": req.subject.model_dump()},
        )
        cached = state.screen_cache.get(screen_key)
        if cached is not None and state.store.has_pack(cached):
            evidence_pack_id = cached
        else:
            artifact_dict = artifact.to_dict()
            result_dict = {
                "decision": result.decision,
                "hits": result.hits,
                "name_norm": result.name_norm,
            }
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
            evidence_pack_id = state.store.put_pack(pack).evidence_pack_id
            state.screen_cache.set(screen_key, evidence_pack_id)

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
        responses=ERROR_RESPONSES,
    )
    def get_evidence_pack(
        evidence_pack_id: str, principal=Depends(principal_from_request)
    ) -> EvidencePack:
        if "read:evidence" not in principal.scopes:
            raise_http_error(status_code=403, code="missing_scope")
        if not state.store.has_pack(evidence_pack_id):
            raise_http_error(status_code=404, code="evidence_pack_not_found")
        try:
            pack = state.store.get_pack(evidence_pack_id)
        except ValueError as exc:
            if str(exc) == "evidence_pack_integrity_failed":
                raise_http_error(status_code=409, code="evidence_pack_integrity_failed")
            raise
        if pack.get("customer_id") != principal.customer_id:
            raise_http_error(status_code=404, code="evidence_pack_not_found")
        return EvidencePack.model_validate(pack)

    # --- v2 (breaking API) ---

    @app.post("/v2/screenings", response_model=V2CreateScreeningResponse, responses=ERROR_RESPONSES)
    def v2_create_screening(
        req: V2CreateScreeningRequest,
        principal=Depends(principal_from_request),
    ) -> V2CreateScreeningResponse:
        if "write:screen" not in principal.scopes:
            raise_http_error(status_code=403, code="missing_scope")
        artifact = state.ingest_cache.get_or_refresh(
            global_ingest_key,
            lambda: ingest_func(
                store=state.store,
                output_dir=state.cfg.raw_dir / global_ingest_key,
                retrieval_ts=req.retrieval_ts,
            ),
        )
        list_version = artifact.list_version
        sanctions_name_sets = state.name_sets_cache.get(
            state.store, artifact.normalized_name_sets_blob_sha256
        )
        result = screen_subject_name(
            req.subject.name,
            sanctions_name_sets,
            subject_country=req.subject.country,
            subject_dob=req.subject.dob,
        )
        if demo_mode and req.subject.name.strip().lower() in {"rachel review", "review example"}:
            # Demo-only: allow showcasing a "review" path deterministically.
            result = type(result)(
                decision="review",
                hits=result.hits,
                name_norm=result.name_norm,
                match_type="policy_manual_review",
                score=50,
                reason_codes=["policy_manual_review"],
            )

        screen_key = compute_screening_key(
            list_version=list_version,
            subject={"customer_id": principal.customer_id, "subject": req.subject.model_dump()},
        )
        cached = state.screen_cache.get(screen_key)
        if cached is not None and state.store.has_pack(cached):
            evidence_pack_id = cached
        else:
            artifact_dict = artifact.to_dict()
            result_dict = result.to_dict()
            canonical_pack_hash = sha256_hex(
                canonical_json_bytes({"ingestion": artifact_dict, "result": result_dict})
            )
            pack = {
                "schema_version": "2",
                "created_at": utc_now_iso(),
                "customer_id": principal.customer_id,
                "list_version": list_version,
                "screen_key": screen_key,
                "ingestion": artifact_dict,
                "input": {
                    "subject": req.subject.model_dump(),
                    "screening_type": req.screening_type,
                },
                "result": result_dict,
                "determinism": {"canonical_pack_hash": canonical_pack_hash},
            }
            evidence_pack_id = state.store.put_pack(pack).evidence_pack_id
            state.screen_cache.set(screen_key, evidence_pack_id)

        state.db.insert_screening(
            screening_id=screen_key,
            created_at=utc_now_iso(),
            customer_id=principal.customer_id,
            evidence_pack_id=evidence_pack_id,
            list_version=list_version,
        )
        created_at = utc_now_iso()
        status = "needs_review" if result.decision in {"review", "block"} else "closed"
        state.db.upsert_case(
            case_id=screen_key,
            created_at=created_at,
            updated_at=created_at,
            customer_id=principal.customer_id,
            screening_id=screen_key,
            evidence_pack_id=evidence_pack_id,
            status=status,
        )
        state.db.insert_case_event(
            case_id=screen_key,
            ts=created_at,
            actor="system",
            event_type="screening_created",
            note=f"decision={result.decision}",
        )
        _emit_webhooks(
            customer_id=principal.customer_id,
            event_type="screening.created",
            event_id=screen_key,
            data={
                "screening_id": screen_key,
                "decision": result.decision,
                "evidence_pack_id": evidence_pack_id,
                "list_version": list_version,
            },
        )

        return V2CreateScreeningResponse(
            screening_id=screen_key,
            decision=result.decision,
            hits=list(result.hits),
            match_type=result.match_type,
            score=int(result.score),
            reason_codes=list(result.reason_codes),
            list_version=list_version,
            evidence_pack_id=evidence_pack_id,
        )

    @app.post(
        "/v2/screenings/{screening_id}/decision",
        response_model=V2ReviewDecisionResponse,
        responses=ERROR_RESPONSES,
    )
    def v2_set_review_decision(
        screening_id: str,
        req: V2ReviewDecisionRequest,
        request: Request,
        principal=Depends(principal_from_request),
    ) -> V2ReviewDecisionResponse:
        if "write:screen" not in principal.scopes:
            raise_http_error(status_code=403, code="missing_scope")
        screening = state.db.get_screening(screening_id)
        if screening is None or screening["customer_id"] != principal.customer_id:
            raise_http_error(status_code=404, code="screening_not_found")
        decided_at = utc_now_iso()
        state.db.upsert_screening_review_decision(
            screening_id=screening_id,
            decided_at=decided_at,
            customer_id=principal.customer_id,
            evidence_pack_id=screening["evidence_pack_id"],
            outcome=req.outcome,
            note=req.note,
        )
        state.db.insert_audit_event(
            ts=decided_at,
            actor="customer",
            action="screening_review_decision",
            request_id=getattr(request.state, "request_id", None),
            details_json=json.dumps(
                {
                    "customer_id": principal.customer_id,
                    "screening_id": screening_id,
                    "evidence_pack_id": screening["evidence_pack_id"],
                    "outcome": req.outcome,
                }
            ),
        )
        state.db.upsert_case(
            case_id=screening_id,
            created_at=screening["created_at"],
            updated_at=decided_at,
            customer_id=principal.customer_id,
            screening_id=screening_id,
            evidence_pack_id=screening["evidence_pack_id"],
            status="closed",
        )
        state.db.insert_case_event(
            case_id=screening_id,
            ts=decided_at,
            actor="customer",
            event_type="review_decision_recorded",
            note=f"outcome={req.outcome}",
        )

        _emit_webhooks(
            customer_id=principal.customer_id,
            event_type="screening.review_decision_recorded",
            event_id=screening_id,
            data={
                "screening_id": screening_id,
                "evidence_pack_id": screening["evidence_pack_id"],
                "outcome": req.outcome,
                "decided_at": decided_at,
            },
        )
        return V2ReviewDecisionResponse(
            screening_id=screening_id,
            decided_at=decided_at,
            outcome=req.outcome,
            note=req.note,
        )

    @app.post(
        "/v2/webhooks/subscriptions",
        response_model=V2WebhookSubscription,
        responses=ERROR_RESPONSES,
    )
    def v2_create_webhook_subscription(
        req: V2CreateWebhookSubscriptionRequest,
        principal=Depends(principal_from_request),
    ) -> V2WebhookSubscription:
        if "write:screen" not in principal.scopes:
            raise_http_error(status_code=403, code="missing_scope")
        sub_id = sha256_hex(
            canonical_json_bytes(
                {"customer_id": principal.customer_id, "url": req.url, "created_at": utc_now_iso()}
            )
        )[:32]
        now = utc_now_iso()
        state.db.create_webhook_subscription(
            subscription_id=sub_id,
            customer_id=principal.customer_id,
            url=req.url,
            secret=req.secret,
            events=req.events,
            created_at=now,
            active=True,
        )
        return V2WebhookSubscription(
            subscription_id=sub_id, url=req.url, events=req.events, active=True, created_at=now
        )

    @app.get(
        "/v2/webhooks/subscriptions",
        response_model=list[V2WebhookSubscription],
        responses=ERROR_RESPONSES,
    )
    def v2_list_webhook_subscriptions(
        principal=Depends(principal_from_request),
    ) -> list[V2WebhookSubscription]:
        rows = state.db.list_webhook_subscriptions(customer_id=principal.customer_id)
        return [
            V2WebhookSubscription(
                subscription_id=str(r["subscription_id"]),
                url=str(r["url"]),
                events=list(r.get("events") or []),
                active=bool(r.get("active")),
                created_at=str(r["created_at"]),
            )
            for r in rows
        ]

    @app.delete(
        "/v2/webhooks/subscriptions/{subscription_id}",
        responses=ERROR_RESPONSES,
    )
    def v2_delete_webhook_subscription(
        subscription_id: str,
        principal=Depends(principal_from_request),
    ) -> dict[str, str]:
        ok = state.db.delete_webhook_subscription(
            subscription_id=subscription_id, customer_id=principal.customer_id
        )
        if not ok:
            raise_http_error(status_code=404, code="subscription_not_found")
        return {"status": "deleted"}

    def _hash_hex(obj: Any) -> str:
        return sha256_hex(canonical_json_bytes(obj))

    def _build_case_event_chain(
        *, events: list[dict[str, Any]]
    ) -> tuple[list[V2ChainedCaseEvent], str]:
        prev = "0" * 64
        chained: list[V2ChainedCaseEvent] = []
        for ev in events:
            ev_payload = {
                "ts": ev.get("ts"),
                "actor": ev.get("actor"),
                "event_type": ev.get("event_type"),
                "note": ev.get("note"),
            }
            h = _hash_hex({"prev_hash": prev, "event": ev_payload})
            chained.append(
                V2ChainedCaseEvent(
                    ts=str(ev_payload.get("ts") or ""),
                    actor=str(ev_payload.get("actor") or ""),
                    event_type=str(ev_payload.get("event_type") or ""),
                    note=str(ev_payload.get("note"))
                    if ev_payload.get("note") is not None
                    else None,
                    prev_hash=prev,
                    hash=h,
                )
            )
            prev = h
        return chained, prev

    @app.get(
        "/v2/cases/{case_id}/bundle",
        response_model=V2CaseEvidenceBundleResponse,
        responses=ERROR_RESPONSES,
    )
    def v2_get_case_bundle(
        case_id: str,
        principal=Depends(principal_from_request),
    ) -> V2CaseEvidenceBundleResponse:
        if "read:evidence" not in principal.scopes:
            raise_http_error(status_code=403, code="missing_scope")
        c = state.db.get_case(case_id=case_id)
        if c is None or c["customer_id"] != principal.customer_id:
            raise_http_error(status_code=404, code="case_not_found")

        pack = get_evidence_pack(c["evidence_pack_id"], principal).model_dump()
        events = state.db.list_case_events(case_id=case_id, limit=500)
        chained, head = _build_case_event_chain(events=events)

        bundle = V2CaseEvidenceBundle(
            case_id=case_id,
            customer_id=principal.customer_id,
            created_at=utc_now_iso(),
            evidence_pack=pack,
            case=c,
            events=chained,
            chain_head=head,
        )

        key_id = state.cfg.signing_key_current
        if not key_id or key_id not in state.cfg.signing_keys:
            raise_http_error(status_code=503, code="signing_not_configured")
        secret = state.cfg.signing_keys[key_id]
        payload = canonical_json_bytes(bundle.model_dump())
        sig = sign_bytes(secret, payload)
        return V2CaseEvidenceBundleResponse(
            bundle=bundle,
            signature=V2CaseEvidenceBundleSignature(key_id=key_id, signature=sig),
        )

    @app.post(
        "/v2/cases/bundles/verify",
        response_model=V2VerifyCaseEvidenceBundleResponse,
        responses=ERROR_RESPONSES,
    )
    def v2_verify_case_bundle(
        req: V2VerifyCaseEvidenceBundleRequest,
        principal=Depends(principal_from_request),
    ) -> V2VerifyCaseEvidenceBundleResponse:
        if "read:evidence" not in principal.scopes:
            raise_http_error(status_code=403, code="missing_scope")
        secret = state.cfg.signing_keys.get(req.key_id)
        if secret is None:
            raise_http_error(status_code=404, code="signing_key_not_found")
        payload = canonical_json_bytes(req.bundle)
        return V2VerifyCaseEvidenceBundleResponse(
            valid=verify_bytes(secret, payload, req.signature)
        )

    @app.get("/v2/cases", response_model=list[V2CaseSummary], responses=ERROR_RESPONSES)
    def v2_list_cases(
        status: str | None = None,
        assignee: str | None = None,
        limit: int = 50,
        principal=Depends(principal_from_request),
    ) -> list[V2CaseSummary]:
        if "read:evidence" not in principal.scopes:
            raise_http_error(status_code=403, code="missing_scope")
        rows = state.db.list_cases(
            customer_id=principal.customer_id, status=status, assignee=assignee, limit=limit
        )
        out: list[V2CaseSummary] = []
        for r in rows:
            subject_name: str | None = None
            decision: str | None = None
            try:
                pack = state.store.get_pack(r["evidence_pack_id"])
                subj = (
                    (pack.get("input") or {}).get("subject")
                    if isinstance(pack.get("input"), dict)
                    else None
                )
                if isinstance(subj, dict):
                    subject_name = str(subj.get("name") or "") or None
                res = pack.get("result") if isinstance(pack.get("result"), dict) else None
                if isinstance(res, dict) and res.get("decision"):
                    decision = str(res.get("decision"))
            except Exception:
                pass
            out.append(
                V2CaseSummary(
                    case_id=r["case_id"],
                    created_at=r["created_at"],
                    updated_at=r["updated_at"],
                    status=r["status"],
                    assignee=r.get("assignee") or None,
                    screening_id=r["screening_id"],
                    evidence_pack_id=r["evidence_pack_id"],
                    subject_name=subject_name,
                    decision=decision,  # type: ignore[arg-type]
                )
            )
        return out

    @app.get("/v2/cases/{case_id}", response_model=V2CaseDetail, responses=ERROR_RESPONSES)
    def v2_get_case(
        case_id: str,
        principal=Depends(principal_from_request),
    ) -> V2CaseDetail:
        if "read:evidence" not in principal.scopes:
            raise_http_error(status_code=403, code="missing_scope")
        c = state.db.get_case(case_id=case_id)
        if c is None or c["customer_id"] != principal.customer_id:
            raise_http_error(status_code=404, code="case_not_found")
        events = state.db.list_case_events(case_id=case_id)
        subject_name: str | None = None
        decision: str | None = None
        try:
            pack = state.store.get_pack(c["evidence_pack_id"])
            subj = (
                (pack.get("input") or {}).get("subject")
                if isinstance(pack.get("input"), dict)
                else None
            )
            if isinstance(subj, dict):
                subject_name = str(subj.get("name") or "") or None
            res = pack.get("result") if isinstance(pack.get("result"), dict) else None
            if isinstance(res, dict) and res.get("decision"):
                decision = str(res.get("decision"))
        except Exception:
            pass
        summary = V2CaseSummary(
            case_id=c["case_id"],
            created_at=c["created_at"],
            updated_at=c["updated_at"],
            status=c["status"],
            assignee=c.get("assignee") or None,
            screening_id=c["screening_id"],
            evidence_pack_id=c["evidence_pack_id"],
            subject_name=subject_name,
            decision=decision,  # type: ignore[arg-type]
        )
        return V2CaseDetail(
            case=summary,
            events=[
                V2CaseEvent(
                    ts=e["ts"],
                    actor=e["actor"],
                    event_type=e["event_type"],
                    note=e["note"] or None,
                )
                for e in events
            ],
        )

    @app.post("/v2/cases/{case_id}/events", response_model=V2CaseDetail, responses=ERROR_RESPONSES)
    def v2_add_case_event(
        case_id: str,
        req: V2CreateCaseEventRequest,
        request: Request,
        principal=Depends(principal_from_request),
    ) -> V2CaseDetail:
        if "write:cases" not in principal.scopes:
            raise_http_error(status_code=403, code="missing_scope")
        c = state.db.get_case(case_id=case_id)
        if c is None or c["customer_id"] != principal.customer_id:
            raise_http_error(status_code=404, code="case_not_found")

        ts = utc_now_iso()
        note = req.note
        if req.event_type == "assign" and req.assignee:
            note = f"assignee={req.assignee}" + (f" {note}" if note else "")
        if req.event_type == "status_update" and req.status:
            note = f"status={req.status}" + (f" {note}" if note else "")

        state.db.insert_case_event(
            case_id=case_id,
            ts=ts,
            actor="customer",
            event_type=req.event_type,
            note=note,
        )
        if req.event_type == "status_update" and req.status:
            state.db.upsert_case(
                case_id=case_id,
                created_at=c["created_at"],
                updated_at=ts,
                customer_id=principal.customer_id,
                screening_id=c["screening_id"],
                evidence_pack_id=c["evidence_pack_id"],
                status=req.status,
            )
        if req.event_type == "assign" and req.assignee:
            state.db.upsert_case(
                case_id=case_id,
                created_at=c["created_at"],
                updated_at=ts,
                customer_id=principal.customer_id,
                screening_id=c["screening_id"],
                evidence_pack_id=c["evidence_pack_id"],
                status=c["status"],
                assignee=req.assignee,
            )

        state.db.insert_audit_event(
            ts=ts,
            actor="customer",
            action="case_event",
            request_id=getattr(request.state, "request_id", None),
            details_json=json.dumps(
                {
                    "customer_id": principal.customer_id,
                    "case_id": case_id,
                    "event_type": req.event_type,
                }
            ),
        )
        return v2_get_case(case_id, principal)

    @app.get(
        "/v2/evidence-packs/{evidence_pack_id}",
        response_model=EvidencePack,
        responses=ERROR_RESPONSES,
    )
    def v2_get_evidence_pack(
        evidence_pack_id: str,
        principal=Depends(principal_from_request),
    ) -> EvidencePack:
        return get_evidence_pack(evidence_pack_id, principal)

    @app.get("/v2/evidence-packs/{evidence_pack_id}/export", responses=ERROR_RESPONSES)
    def v2_export(
        evidence_pack_id: str,
        request: Request,
        format: str = "pdf",  # noqa: A002
        principal=Depends(principal_from_request),
    ) -> Response:
        if format not in ("pdf", "json"):
            raise_http_error(
                status_code=422, code="validation_error", details={"format": "pdf|json"}
            )
        if format == "json":
            base = get_evidence_pack(evidence_pack_id, principal).model_dump()
            review = state.db.get_screening_review_decision_by_evidence_pack(
                customer_id=principal.customer_id,
                evidence_pack_id=evidence_pack_id,
            )
            if review is not None:
                base["review_decision"] = review
            case_id = str(base.get("screen_key") or "")
            if case_id:
                base["case_timeline"] = state.db.list_case_events(case_id=case_id)
            return Response(content=canonical_json_bytes(base), media_type="application/json")

        # PDF export: always render a "view" that includes review + case timeline when present.
        pack = get_evidence_pack(evidence_pack_id, principal).model_dump()
        review = state.db.get_screening_review_decision_by_evidence_pack(
            customer_id=principal.customer_id,
            evidence_pack_id=evidence_pack_id,
        )
        if review is not None:
            pack["review_decision"] = review
        case_id = str(pack.get("screen_key") or "")
        if case_id:
            pack["case_timeline"] = state.db.list_case_events(case_id=case_id)

        request_id = getattr(request.state, "request_id", None)
        pdf_bytes = render_evidence_pack_pdf(
            pack=pack,
            evidence_pack_id=evidence_pack_id,
            request_id=request_id,
        )
        headers = {
            "content-disposition": f'attachment; filename="evidence-pack-{evidence_pack_id}.pdf"',
        }
        return Response(content=pdf_bytes, media_type="application/pdf", headers=headers)

    return app


app = create_app()


def main() -> int:
    """Run a dev server (uvicorn)."""
    import uvicorn

    host = os.environ.get("PROOFRAIL_HOST", "127.0.0.1")
    port = int(os.environ.get("PROOFRAIL_PORT", "8000"))
    uvicorn.run("ProofRail.service.app:app", host=host, port=port, reload=False)
    return 0
