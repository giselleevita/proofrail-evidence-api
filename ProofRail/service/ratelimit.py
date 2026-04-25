from __future__ import annotations

import threading
import time
from collections import OrderedDict
from dataclasses import dataclass


@dataclass
class TokenBucket:
    capacity: float
    refill_per_s: float
    tokens: float
    updated_at_s: float

    def allow(self, cost: float = 1.0) -> bool:
        now = time.time()
        elapsed = max(0.0, now - self.updated_at_s)
        self.tokens = min(self.capacity, self.tokens + elapsed * self.refill_per_s)
        self.updated_at_s = now
        if self.tokens >= cost:
            self.tokens -= cost
            return True
        return False


class RateLimiter:
    def __init__(
        self, *, capacity: int, refill_per_s: float, max_buckets: int | None = 50_000
    ) -> None:
        self.capacity = float(capacity)
        self.refill_per_s = float(refill_per_s)
        self._max_buckets = max_buckets
        self._lock = threading.Lock()
        self._buckets: OrderedDict[str, TokenBucket] = OrderedDict()

    def allow(self, key: str, cost: float = 1.0) -> bool:
        with self._lock:
            bucket = self._buckets.get(key)
            if bucket is None:
                if self._max_buckets is not None:
                    while len(self._buckets) >= self._max_buckets:
                        self._buckets.popitem(last=False)
                bucket = TokenBucket(
                    capacity=self.capacity,
                    refill_per_s=self.refill_per_s,
                    tokens=self.capacity,
                    updated_at_s=time.time(),
                )
                self._buckets[key] = bucket
            else:
                self._buckets.move_to_end(key)
            return bucket.allow(cost=cost)
