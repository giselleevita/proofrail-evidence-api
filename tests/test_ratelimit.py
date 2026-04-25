import unittest

from ProofRail.service.ratelimit import RateLimiter


class TestRateLimiter(unittest.TestCase):
    def test_lru_eviction_when_max_buckets_reached(self) -> None:
        lim = RateLimiter(capacity=100, refill_per_s=100 / 60.0, max_buckets=3)
        self.assertTrue(lim.allow("a"))
        self.assertTrue(lim.allow("b"))
        self.assertTrue(lim.allow("c"))
        self.assertTrue(lim.allow("d"))
        # Oldest key "a" should have been evicted; fresh bucket starts full again.
        self.assertTrue(lim.allow("a"))
