from __future__ import annotations

import queue
import threading
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass

from fastapi import FastAPI

from ProofRail.service.cache import IngestionCache
from ProofRail.service.config import AppConfig
from ProofRail.service.db import DbConfig, ProofRailDb
from ProofRail.service.metrics import UsageEvent
from ProofRail.service.ratelimit import RateLimiter
from ProofRail.service.scheduler import RefreshScheduler
from ProofRail.service.screening import NameSetCache
from ProofRail.service.storage import EvidenceStore


@dataclass
class UsageFlushStats:
    dropped: int = 0
    flushed: int = 0


class ScreenCache:
    def __init__(self, *, max_entries: int) -> None:
        from collections import OrderedDict

        self.max_entries = int(max_entries)
        self._lock = threading.Lock()
        self._data: OrderedDict[str, str] = OrderedDict()

    def get(self, key: str) -> str | None:
        with self._lock:
            v = self._data.get(key)
            if v is None:
                return None
            self._data.move_to_end(key)
            return v

    def set(self, key: str, value: str) -> None:
        with self._lock:
            self._data[key] = value
            self._data.move_to_end(key)
            while len(self._data) > self.max_entries:
                self._data.popitem(last=False)


@dataclass
class AppState:
    cfg: AppConfig
    store: EvidenceStore
    db: ProofRailDb
    ingest_cache: IngestionCache
    limiter: RateLimiter
    limiter_by_customer: dict[str, RateLimiter]
    limiter_lock: threading.Lock
    scheduler: RefreshScheduler
    name_sets_cache: NameSetCache
    screen_cache: ScreenCache

    usage_queue: queue.Queue[UsageEvent]
    usage_stop: threading.Event
    usage_thread: threading.Thread | None
    usage_stats: UsageFlushStats


def build_state(cfg: AppConfig) -> AppState:
    store = EvidenceStore(cfg.store_root)
    db = ProofRailDb(DbConfig(path=cfg.db_path))

    ingest_cache = IngestionCache(ttl_seconds=cfg.ingest_ttl_seconds)

    limiter = RateLimiter(capacity=cfg.rpm, refill_per_s=cfg.rpm / 60.0)
    limiter_by_customer: dict[str, RateLimiter] = {}
    limiter_lock = threading.Lock()

    scheduler = RefreshScheduler()
    name_sets_cache = NameSetCache(max_entries=64)
    screen_cache = ScreenCache(max_entries=cfg.screen_cache_max)

    usage_queue: queue.Queue[UsageEvent] = queue.Queue(maxsize=cfg.usage_queue_max)
    usage_stop = threading.Event()
    usage_stats = UsageFlushStats()

    return AppState(
        cfg=cfg,
        store=store,
        db=db,
        ingest_cache=ingest_cache,
        limiter=limiter,
        limiter_by_customer=limiter_by_customer,
        limiter_lock=limiter_lock,
        scheduler=scheduler,
        name_sets_cache=name_sets_cache,
        screen_cache=screen_cache,
        usage_queue=usage_queue,
        usage_stop=usage_stop,
        usage_thread=None,
        usage_stats=usage_stats,
    )


def usage_flusher_loop(
    *,
    db: ProofRailDb,
    stop: threading.Event,
    q: queue.Queue[UsageEvent],
    batch_size: int,
    flush_interval_s: float,
    stats: UsageFlushStats,
) -> None:
    batch: list[UsageEvent] = []
    last_flush = time.time()

    def flush() -> None:
        nonlocal batch, last_flush
        if not batch:
            return
        payloads = [
            {
                "ts": e.ts,
                "api_key_id": e.api_key_id,
                "customer_id": e.customer_id,
                "route": e.route,
                "status_code": e.status_code,
                "latency_ms": e.latency_ms,
                "request_id": e.request_id,
            }
            for e in batch
        ]
        try:
            db.insert_usage_events(payloads)
            stats.flushed += len(batch)
        except Exception:
            # best-effort; drop on errors
            pass
        batch = []
        last_flush = time.time()

    while not stop.is_set():
        timeout = max(0.05, flush_interval_s - (time.time() - last_flush))
        try:
            ev = q.get(timeout=timeout)
            batch.append(ev)
        except queue.Empty:
            pass

        if len(batch) >= batch_size or (batch and (time.time() - last_flush) >= flush_interval_s):
            flush()

    flush()


def attach_lifespan(app: FastAPI, state: AppState) -> None:
    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        state.usage_thread = threading.Thread(
            target=usage_flusher_loop,
            kwargs={
                "db": state.db,
                "stop": state.usage_stop,
                "q": state.usage_queue,
                "batch_size": state.cfg.usage_batch_size,
                "flush_interval_s": state.cfg.usage_flush_interval_s,
                "stats": state.usage_stats,
            },
            daemon=True,
        )
        state.usage_thread.start()
        try:
            yield
        finally:
            state.usage_stop.set()
            if state.usage_thread is not None:
                state.usage_thread.join(timeout=2.0)

    app.router.lifespan_context = lifespan
