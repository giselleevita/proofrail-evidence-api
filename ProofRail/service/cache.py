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
        self._inflight: dict[str, threading.Condition] = {}
        self._refreshing: set[str] = set()

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

        # Singleflight: only one refresh per key at a time.
        with self._lock:
            cond = self._inflight.get(customer_id)
            if cond is None:
                cond = threading.Condition(self._lock)
                self._inflight[customer_id] = cond
            if customer_id in self._refreshing:
                # wait for the in-flight refresh to complete
                while customer_id in self._refreshing:
                    cond.wait(timeout=5.0)
                cached2 = self.get(customer_id)
                if cached2 is not None:
                    return cached2
            # become the refresher
            self._refreshing.add(customer_id)

        try:
            artifact = refresh_func()
            self.put(customer_id, artifact)
            return artifact
        finally:
            with self._lock:
                self._refreshing.discard(customer_id)
                cond = self._inflight.get(customer_id)
                if cond is not None:
                    cond.notify_all()
