"""In-process event bus that fans runtime events out to browser WebSockets.

Publishing is thread-safe: the runtime runs in worker threads while each WebSocket
consumes from an asyncio queue on the event loop.
"""

from __future__ import annotations

import asyncio
import threading
from collections import deque
from typing import Any

from .ids import iso, new_id, utcnow

API_EVENT_VERSION = "1"
REPLAY_BUFFER = 1000


class EventBus:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._subscribers: dict[str, list[tuple[asyncio.AbstractEventLoop, asyncio.Queue]]] = {}
        self._sequence: dict[str, int] = {}
        self._buffer: dict[str, deque] = {}

    def subscribe(self, session_id: str) -> asyncio.Queue:
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue(maxsize=5000)
        with self._lock:
            self._subscribers.setdefault(session_id, []).append((loop, queue))
        return queue

    def unsubscribe(self, session_id: str, queue: asyncio.Queue) -> None:
        with self._lock:
            subs = self._subscribers.get(session_id, [])
            self._subscribers[session_id] = [(lp, q) for lp, q in subs if q is not queue]

    def replay(self, session_id: str, after_sequence: int) -> list[dict]:
        with self._lock:
            return [e for e in self._buffer.get(session_id, ()) if e["sequence"] > after_sequence]

    def publish(
        self,
        session_id: str,
        event_type: str,
        data: dict[str, Any] | None = None,
        *,
        transaction_id: str | None = None,
        transaction_version: int | None = None,
        event_id: str | None = None,
        timestamp: str | None = None,
        buffer: bool = True,
    ) -> dict:
        with self._lock:
            seq = self._sequence.get(session_id, 0) + 1
            self._sequence[session_id] = seq
            envelope = {
                "version": API_EVENT_VERSION,
                "event_id": event_id or new_id("evt"),
                "sequence": seq,
                "timestamp": timestamp or iso(utcnow()),
                "session_id": session_id,
                "transaction_id": transaction_id,
                "transaction_version": transaction_version,
                "type": event_type,
                "data": data or {},
            }
            if buffer:
                self._buffer.setdefault(session_id, deque(maxlen=REPLAY_BUFFER)).append(envelope)
            subscribers = list(self._subscribers.get(session_id, ()))
        for loop, queue in subscribers:
            try:
                loop.call_soon_threadsafe(_offer, queue, envelope)
            except RuntimeError:  # loop closed; subscriber is gone
                pass
        return envelope

    def publish_all(self, event_type: str, data: dict[str, Any] | None = None) -> None:
        with self._lock:
            sessions = list(self._subscribers)
        for sid in sessions:
            self.publish(sid, event_type, data)


def _offer(queue: asyncio.Queue, envelope: dict) -> None:
    try:
        queue.put_nowait(envelope)
    except asyncio.QueueFull:  # a stalled client; drop rather than block the runtime
        pass
