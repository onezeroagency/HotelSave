"""Tiny in-process sliding-window rate limiter.

Good enough for one API instance (which is what runs today): it stops
mailbox-bombing and brute enumeration from a single deployment. If the API
ever scales past one instance, move this to the database or Redis — each
process currently keeps its own window, so N instances multiply the limit
by N. That failure mode is loose, not broken.
"""

import time
from collections import defaultdict, deque


class SlidingWindow:
    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def allow(self, key: str, limit: int, window_seconds: int) -> bool:
        """Record a hit for `key` and say whether it is within `limit` hits
        per `window_seconds`. Denied hits are not recorded, so a blocked
        caller does not keep extending their own ban."""
        now = time.monotonic()
        q = self._hits[key]
        while q and now - q[0] > window_seconds:
            q.popleft()
        if len(q) >= limit:
            return False
        q.append(now)
        return True

    def reset(self) -> None:
        """Test hook."""
        self._hits.clear()


magic_link_limiter = SlidingWindow()
