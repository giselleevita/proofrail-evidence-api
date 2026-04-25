from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


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
            # Default for integration keys: screening + evidence access.
            # Analyst/case keys should include "write:cases" explicitly.
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
        return CreateKeyRequest._normalize_scopes(v)


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


_VERIFY_EVIDENCE_PACK_MAX_CANONICAL_BYTES = 512_000


class VerifyEvidencePackRequest(BaseModel):
    evidence_pack: dict[str, Any]
    signature: str = Field(min_length=1, max_length=1024)

    @field_validator("evidence_pack")
    @classmethod
    def _evidence_pack_size(cls, v: dict[str, Any]) -> dict[str, Any]:
        from ProofRail.service.storage import canonical_json_bytes

        if len(canonical_json_bytes(v)) > _VERIFY_EVIDENCE_PACK_MAX_CANONICAL_BYTES:
            raise ValueError("evidence_pack_too_large")
        return v


class VerifyEvidencePackResponse(BaseModel):
    valid: bool


class V2ScreeningSubject(BaseModel):
    name: str = Field(min_length=1, max_length=512)
    country: str | None = Field(default=None, max_length=2)
    dob: str | None = None
    external_id: str | None = Field(default=None, max_length=128)


class V2CreateScreeningRequest(BaseModel):
    screening_type: Literal["onboarding"] = "onboarding"
    subject: V2ScreeningSubject
    retrieval_ts: str | None = None


class V2CreateScreeningResponse(BaseModel):
    screening_id: str
    decision: Literal["allow", "block", "review"]
    hits: list[str]
    match_type: str
    score: int
    reason_codes: list[str]
    list_version: str
    evidence_pack_id: str


class V2ReviewDecisionRequest(BaseModel):
    outcome: Literal["approve", "reject"]
    note: str | None = Field(default=None, max_length=4000)


class V2ReviewDecisionResponse(BaseModel):
    screening_id: str
    decided_at: str
    outcome: Literal["approve", "reject"]
    note: str | None = None


class V2CaseEvent(BaseModel):
    ts: str
    actor: str
    event_type: str
    note: str | None = None


class V2CaseSummary(BaseModel):
    case_id: str
    created_at: str
    updated_at: str
    status: str
    assignee: str | None = None
    screening_id: str
    evidence_pack_id: str
    subject_name: str | None = None
    decision: Literal["allow", "block", "review"] | None = None


class V2CaseDetail(BaseModel):
    case: V2CaseSummary
    events: list[V2CaseEvent]


class V2CreateCaseEventRequest(BaseModel):
    event_type: Literal["comment", "status_update", "assign"]
    note: str | None = Field(default=None, max_length=4000)
    status: Literal["needs_review", "closed"] | None = None
    assignee: str | None = Field(default=None, max_length=128)


class V2ExportFormat(BaseModel):
    format: Literal["pdf", "json"] = "pdf"


class V2CreateWebhookSubscriptionRequest(BaseModel):
    url: str = Field(min_length=8, max_length=2048)
    secret: str = Field(min_length=8, max_length=256)
    events: list[str] = Field(min_length=1, max_length=50)


class V2WebhookSubscription(BaseModel):
    subscription_id: str
    url: str
    events: list[str]
    active: bool
    created_at: str


class V1AdminRunWebhookDeliveriesResponse(BaseModel):
    attempted: int
    delivered: int
    retried: int
    failed: int


class V2ChainedCaseEvent(BaseModel):
    ts: str
    actor: str
    event_type: str
    note: str | None = None
    prev_hash: str
    hash: str


class V2CaseEvidenceBundle(BaseModel):
    schema_version: str = "1"
    case_id: str
    customer_id: str
    created_at: str
    evidence_pack: dict[str, Any]
    case: dict[str, Any]
    events: list[V2ChainedCaseEvent]
    chain_head: str


class V2CaseEvidenceBundleSignature(BaseModel):
    key_id: str
    signature: str


class V2CaseEvidenceBundleResponse(BaseModel):
    bundle: V2CaseEvidenceBundle
    signature: V2CaseEvidenceBundleSignature


class V2VerifyCaseEvidenceBundleRequest(BaseModel):
    bundle: dict[str, Any]
    key_id: str
    signature: str


class V2VerifyCaseEvidenceBundleResponse(BaseModel):
    valid: bool


ERROR_RESPONSES = {
    401: {"model": ErrorResponse},
    403: {"model": ErrorResponse},
    404: {"model": ErrorResponse},
    409: {"model": ErrorResponse},
    422: {"model": ErrorResponse},
    429: {"model": ErrorResponse},
    500: {"model": ErrorResponse},
}
