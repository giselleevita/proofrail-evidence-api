from __future__ import annotations

import threading
import time
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
    def __init__(self, *, capacity: int, refill_per_s: float) -> None:
        self.capacity = float(capacity)
        self.refill_per_s = float(refill_per_s)
        self._lock = threading.Lock()
        self._buckets: dict[str, TokenBucket] = {}

    def allow(self, key: str, cost: float = 1.0) -> bool:
        with self._lock:
            bucket = self._buckets.get(key)
            if bucket is None:
                bucket = TokenBucket(
                    capacity=self.capacity,
                    refill_per_s=self.refill_per_s,
                    tokens=self.capacity,
                    updated_at_s=time.time(),
                )
                self._buckets[key] = bucket
            return bucket.allow(cost=cost)

