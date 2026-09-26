"""In-process sliding-window rate limits for the public demo.

Single-process by design (like the runtime lock). Limits are generous for a person
clicking around and tight for a script hammering the API.
"""

from __future__ import annotations

import re
import threading
import time
from collections import defaultdict, deque

# (method, path pattern, limit, window seconds, key: "ip" | "session" | "global")
_SESSIONS = re.compile(r"^/api/v1/sessions$")
RULES = [
    ("POST", _SESSIONS, 120, 3600, "ip"),  # page loads; shared office IPs
    ("POST", _SESSIONS, 1500, 3600, "global"),  # bounds sandbox files even if IPs are spoofed
    ("POST", re.compile(r"^/api/v1/sessions/[^/]+/turns$"), 40, 60, "session"),
    ("POST", re.compile(r"^/api/v1/transactions(/[^/]+/(commit|rollback|what-if|clarify|compare|optimize|abort|verify))?$"), 60, 60, "session"),
    ("POST", re.compile(r"^/api/v1/demo/"), 30, 60, "ip"),
]


class RateLimiter:
    def __init__(self) -> None:
        self._hits: dict[str, deque] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str, limit: int, window: float) -> tuple[bool, float]:
        now = time.monotonic()
        with self._lock:
            q = self._hits[key]
            while q and q[0] <= now - window:
                q.popleft()
            if len(q) >= limit:
                return False, window - (now - q[0])
            q.append(now)
            return True, 0.0

    def check(self, method: str, path: str, ip: str, session: str | None) -> tuple[bool, float]:
        for m, pattern, limit, window, keyed in RULES:
            if method == m and pattern.match(path):
                who = "*" if keyed == "global" else session if keyed == "session" and session else ip
                ok, retry = self.allow(f"{pattern.pattern}|{keyed}|{who}", limit, window)
                if not ok:
                    return False, retry
        return True, 0.0


def client_ip(headers, fallback: str | None, hops: int = 1) -> str:
    """The address the nearest trusted proxy saw. Entries to its left are client-supplied, so
    trusting the first one would let anyone pick a fresh IP per request."""
    forwarded = headers.get("x-forwarded-for")
    if hops > 0 and forwarded:
        parts = [p.strip() for p in forwarded.split(",") if p.strip()]
        if parts:
            return parts[-hops] if len(parts) >= hops else parts[0]
    return fallback or "unknown"
