"""Which ERP world a session acts on.

Local development and tests use one shared world. A public demo gives every visitor a
private sandbox: their own ERP database, seeded with the same baseline, so one judge's
reset or armed fault can never touch another judge's demo.
"""

from __future__ import annotations

import re
import threading
import time
from collections import OrderedDict
from pathlib import Path

from .service import MockERP

_SAFE_ID = re.compile(r"^[a-z]+_[0-9a-f]{16}$")


class WorldRegistry:
    def __init__(self, default: MockERP, data_dir: Path, per_session: bool, max_open: int = 64, ttl_hours: float = 24.0,
                 cleanup_every_s: float = 900.0):
        self.default = default
        self.cleanup_every_s = cleanup_every_s
        self._last_cleanup = time.monotonic()
        self.per_session = per_session
        self.dir = data_dir / "worlds"
        self.max_open = max_open
        self.ttl_s = ttl_hours * 3600
        self._open: OrderedDict[str, MockERP] = OrderedDict()
        self._lock = threading.Lock()
        if per_session:
            self.dir.mkdir(parents=True, exist_ok=True)
            self.cleanup()

    def for_session(self, session_id: str | None) -> MockERP:
        if not self.per_session or not session_id:
            return self.default
        if not _SAFE_ID.match(session_id):  # the id becomes a file name
            raise ValueError("invalid session id")
        with self._lock:
            if time.monotonic() - self._last_cleanup > self.cleanup_every_s:  # a long-running demo keeps its disk bounded
                self._last_cleanup = time.monotonic()
                self.cleanup()
            erp = self._open.get(session_id)
            if erp is not None:
                self._open.move_to_end(session_id)
                return erp
            path = self.dir / f"{session_id}.db"
            erp = MockERP(f"sqlite:///{path.as_posix()}")  # seeds the baseline on first open
            self._open[session_id] = erp
            while len(self._open) > self.max_open:
                _, old = self._open.popitem(last=False)
                old.engine.dispose()
            return erp

    def cleanup(self) -> int:
        """Delete sandbox worlds nobody has touched for ttl_hours."""
        removed = 0
        cutoff = time.time() - self.ttl_s
        for f in self.dir.glob("*.db*"):
            sid = f.name.split(".db")[0]
            if sid in self._open:
                continue
            try:
                if f.stat().st_mtime < cutoff:
                    f.unlink()
                    removed += 1
            except OSError:
                pass
        return removed

    def dispose(self) -> None:
        with self._lock:
            for erp in self._open.values():
                erp.engine.dispose()
            self._open.clear()
