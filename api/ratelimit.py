"""A per-IP limit on the one endpoint that costs money.

Chat is the only route that calls a model; everything else is local geometry. At
a hackathon with 100 planners on one deployment, a runaway client should not be
able to spend the organisers' budget (docs/PLAN.md §11).

In-memory and per-process: enough for one container, and deliberately not a
Redis dependency for an overnight MVP.
"""

from __future__ import annotations

import os
import time
from collections import defaultdict, deque

WINDOW_S = int(os.getenv("NEON_RATE_WINDOW_S", "3600"))
MAX_REQUESTS = int(os.getenv("NEON_RATE_MAX", "60"))

_hits: dict[str, deque[float]] = defaultdict(deque)


def check(client_id: str) -> tuple[bool, int]:
    """(allowed, seconds until a slot frees up)."""
    now = time.monotonic()
    hits = _hits[client_id]
    while hits and now - hits[0] > WINDOW_S:
        hits.popleft()
    if len(hits) >= MAX_REQUESTS:
        return False, int(WINDOW_S - (now - hits[0])) + 1
    hits.append(now)
    return True, 0


def reset() -> None:
    """For tests."""
    _hits.clear()
