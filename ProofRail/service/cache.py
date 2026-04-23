from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from ProofRail.service.models import IngestArtifact


@dataclass
class CacheEntry:
    artifact: IngestArtifact
    stored_at_s: float


class IngestionCache:
    """In-process cache keyed by customer_id.

    Keeps the most recent ingest artifact for a customer to avoid re-fetching sources
    on every screening request.
    """

    def __init__(self, ttl_seconds: int) -> None:
        self.ttl_seconds = ttl_seconds
        self._lock = threading.Lock()
        self._entries: dict[str, CacheEntry] = {}

    def get(self, customer_id: str) -> IngestArtifact | None:
        now = time.time()
        with self._lock:
            entry = self._entries.get(customer_id)
            if entry is None:
                return None
            if now - entry.stored_at_s > self.ttl_seconds:
                return None
            return entry.artifact

    def put(self, customer_id: str, artifact: IngestArtifact) -> None:
        with self._lock:
            self._entries[customer_id] = CacheEntry(artifact=artifact, stored_at_s=time.time())

    def get_or_refresh(
        self, customer_id: str, refresh_func: Callable[[], IngestArtifact]
    ) -> IngestArtifact:
        cached = self.get(customer_id)
        if cached is not None:
            return cached
        artifact = refresh_func()
        self.put(customer_id, artifact)
        return artifact
