from __future__ import annotations

import json
import threading
from collections import OrderedDict
from typing import Any

from ProofRail.sanctions_determinism import normalise_name
from ProofRail.service.models import Decision, ScreeningResult
from ProofRail.service.storage import EvidenceStore, canonical_json_bytes, sha256_hex


def compute_screening_key(list_version: str, subject: dict[str, Any]) -> str:
    return sha256_hex(canonical_json_bytes({"list_version": list_version, "subject": subject}))


def load_sanctions_name_sets(store: EvidenceStore, blob_sha256: str) -> dict[str, set[str]]:
    norm_doc = store.get_blob(blob_sha256).decode("utf-8")
    norm_sets_raw: dict[str, list[str]] = json.loads(norm_doc)
    return {k: set(v) for k, v in norm_sets_raw.items()}


class NameSetCache:
    """Tiny in-process LRU cache for decoded sanctions name sets blobs."""

    def __init__(self, *, max_entries: int = 64) -> None:
        self.max_entries = max_entries
        self._lock = threading.Lock()
        self._lru: OrderedDict[str, dict[str, set[str]]] = OrderedDict()

    def get(self, store: EvidenceStore, blob_sha256: str) -> dict[str, set[str]]:
        with self._lock:
            cached = self._lru.get(blob_sha256)
            if cached is not None:
                self._lru.move_to_end(blob_sha256)
                return cached
        decoded = load_sanctions_name_sets(store, blob_sha256)
        with self._lock:
            self._lru[blob_sha256] = decoded
            self._lru.move_to_end(blob_sha256)
            while len(self._lru) > self.max_entries:
                self._lru.popitem(last=False)
        return decoded


def screen_subject_name(subject_name: str, sanctions_name_sets: dict[str, set[str]]) -> ScreeningResult:
    name_norm = normalise_name(subject_name)
    hits = sorted([src for src, names in sanctions_name_sets.items() if name_norm in names])
    decision: Decision = "block" if hits else "allow"
    return ScreeningResult(decision=decision, hits=hits, name_norm=name_norm)

