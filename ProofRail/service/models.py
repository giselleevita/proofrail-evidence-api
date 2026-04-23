from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

Decision = Literal["allow", "block", "review"]


@dataclass(frozen=True)
class Subject:
    subject_id: str | None
    name: str


@dataclass(frozen=True)
class ScreenInput:
    customer_id: str
    subject: Subject


@dataclass(frozen=True)
class ScreeningResult:
    decision: Decision
    hits: list[str]
    name_norm: str

    def to_dict(self) -> dict[str, Any]:
        return {"decision": self.decision, "hits": self.hits, "name_norm": self.name_norm}


@dataclass(frozen=True)
class IngestArtifact:
    retrieval_timestamp: str
    list_version: str
    ingestion_run_id: str
    normalized_name_sets_blob_sha256: str
    sources: list[dict[str, Any]]
    entry_counts: dict[str, int]
    transform_id: str
    model_version: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "retrieval_timestamp": self.retrieval_timestamp,
            "list_version": self.list_version,
            "ingestion_run_id": self.ingestion_run_id,
            "normalized_name_sets_blob_sha256": self.normalized_name_sets_blob_sha256,
            "sources": self.sources,
            "entry_counts": self.entry_counts,
            "transform_id": self.transform_id,
            "model_version": self.model_version,
        }

