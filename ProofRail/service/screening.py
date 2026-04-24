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


def screen_subject_name(
    subject_name: str,
    sanctions_name_sets: dict[str, set[str]],
    *,
    subject_country: str | None = None,
    subject_dob: str | None = None,
) -> ScreeningResult:
    name_norm = normalise_name(subject_name)
    hits = sorted([src for src, names in sanctions_name_sets.items() if name_norm in names])

    def trigrams(s: str) -> set[str]:
        s2 = f" {s} "
        return {s2[i : i + 3] for i in range(len(s2) - 2)}

    def trigram_jaccard(a: str, b: str) -> float:
        ta = trigrams(a)
        tb = trigrams(b)
        if not ta and not tb:
            return 1.0
        denom = len(ta | tb)
        return (len(ta & tb) / denom) if denom else 0.0

    decision: Decision
    match_type: str
    score: int
    reason_codes: list[str]

    if hits:
        decision = "block"
        match_type = "exact_normalized_name"
        score = 100
        reason_codes = ["sanctions_exact_name_match"]
    else:
        # Pilot-friendly fuzzy matching: if a very close name exists anywhere in any set, return REVIEW.
        best_src: str | None = None
        best_sim = 0.0
        for src, names in sanctions_name_sets.items():
            for cand in names:
                sim = trigram_jaccard(name_norm, cand)
                if sim > best_sim:
                    best_sim = sim
                    best_src = src
        score = int(round(best_sim * 100))
        if best_src is not None and score >= 90:
            decision = "review"
            hits = [best_src]
            match_type = "fuzzy_name_trigram"
            reason_codes = ["sanctions_fuzzy_name_match"]
        else:
            decision = "allow"
            match_type = "none"
            reason_codes = ["no_match"]
    if decision in {"block", "review"}:
        if subject_country:
            reason_codes.append("subject_country_provided")
        if subject_dob:
            reason_codes.append("subject_dob_provided")

    return ScreeningResult(
        decision=decision,
        hits=hits,
        name_norm=name_norm,
        match_type=match_type,
        score=score,
        reason_codes=reason_codes,
    )
