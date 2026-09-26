"""Server-side relay between the browser and the AssemblyAI Voice Agent API.

    browser mic --PCM16/24k--> MIRROR backend --input.audio--> AssemblyAI
    browser     <--AGENT_AUDIO-- MIRROR backend <--reply.audio-- AssemblyAI
                                  |  tool.call (a proposal)
                                  v
                          evidence check -> runtime -> tool.result

The API key never leaves the server, and transcripts used as evidence come straight
from AssemblyAI rather than from the browser.

Tool-call protocol (per AssemblyAI docs): accumulate tool.call; send tool.result only
when reply.done is the latest event; on reply.done status="interrupted", discard the
pending calls. MIRROR goes one step further: *no tool call touches the runtime until
its reply completes without barge-in*, so an interrupted "commit" can never execute.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field

import websockets
from websockets.exceptions import ConnectionClosed

from ..core.container import Container
from ..core.errors import ErrorCode, MirrorError
from ..core.ids import utcnow
from ..core.state_machine import TxState
from .agent_config import initial_session, keyterms_for, tools_for_state
from .tools import VoiceToolExecutor

log = logging.getLogger("mirror.voice")

RESUME_WINDOW_S = 28  # AssemblyAI holds a dropped session for 30 s
RESUME_FAILURE_CODES = {"session_not_found", "session_forbidden", "session_expired"}
RETRYABLE_CODES = {"at_capacity", "concurrency_exceeded", "internal_error"}
FATAL_CODES = {"UNAUTHORIZED", "FORBIDDEN", "unauthorized", "agent_init_failed", "agent_not_found", "invalid_config", "invalid_value"}
MAX_CONNECT_FAILURES = 4
MAX_AUDIO_B64 = 256_000


@dataclass
class PendingCall:
    call_id: str
    name: str
    arguments: dict
    reply_id: str | None


@dataclass
class VoiceMetrics:
    """Measured at the relay from AssemblyAI event arrival times.

    time_to_first_audio_ms: transcript.user -> first reply.audio of the answer (model + tools + speech).
    Speech-start/stop -> transcript deltas are not reported: the live service emits those events
    back to back, so the difference says nothing about latency. User-perceived latency is measured
    end to end by scripts/live_voice_test.py.
    """

    user_turns: int = 0
    replies: int = 0
    interruptions: int = 0
    tool_calls: int = 0
    tool_calls_discarded: int = 0
    samples: dict = field(default_factory=lambda: {"audio": []})

    def as_dict(self) -> dict:
        def avg(xs):
            return round(sum(xs) / len(xs), 1) if xs else None

        return {
            "time_to_first_audio_ms": avg(self.samples["audio"]),
            "user_turns": self.user_turns,
            "replies": self.replies,
            "interruptions": self.interruptions,
            "tool_calls": self.tool_calls,
            "tool_calls_discarded": self.tool_calls_discarded,
        }


class VoiceBridge:
    def __init__(self, container: Container, session_id: str):
        self.c = container
        self.sid = session_id
        self.rt = container.runtime
        self.settings = container.settings
        self.tools = VoiceToolExecutor(self.rt, container.dispatcher)
        self.ws = None
        self.ready = asyncio.Event()
        self.supervisor: asyncio.Task | None = None
        self._first_ready: asyncio.Future | None = None
        self._outcome: str | None = None
        self._greeted = False
        self._restore_pending = False
        self.last_event: str | None = None
        self.pending: list[PendingCall] = []
        self.version_at_turn: dict[str, int] = {}
        self.upstream_session_id: str | None = None
        self.stopping = False
        self.metrics = VoiceMetrics()
        self._config_key: str | None = None
        self._send_lock = asyncio.Lock()
        self._drain_lock = asyncio.Lock()
        self._t_user_final: float | None = None
        self._awaiting_first_audio = False
        self._resuming = False
        self._announcement: str | None = None

    # ------------------------------------------------------------ lifecycle
    #
    # One supervisor task owns the upstream connection. It tries session.resume after a drop (as
    # documented) and, if AssemblyAI refuses, opens a fresh session and hands the agent the open
    # transaction from MIRROR's own database. The transaction never depended on the voice session.

    async def start(self) -> None:
        if not self.settings.assemblyai_api_key:
            raise MirrorError(ErrorCode.ASSEMBLYAI_UNAVAILABLE, "Voice is unavailable: the server has no ASSEMBLYAI_API_KEY")
        resume_id, disconnected_at = await asyncio.to_thread(self.rt.voice_resume_info, self.sid)
        resumable = bool(
            resume_id and disconnected_at and (utcnow() - _aware(disconnected_at)).total_seconds() < RESUME_WINDOW_S
        )
        self.stopping = False
        self._greeted = resumable  # a returning user already heard the greeting
        self._first_ready = asyncio.get_running_loop().create_future()
        self.supervisor = asyncio.create_task(self._supervise(resume_id if resumable else None))
        try:
            await asyncio.wait_for(asyncio.shield(self._first_ready), timeout=25)
        except asyncio.TimeoutError:
            await self.close(end_session=False)
            raise MirrorError(ErrorCode.ASSEMBLYAI_UNAVAILABLE, "AssemblyAI did not become ready within 25 s")

    async def _supervise(self, resume_id: str | None) -> None:
        failures = 0
        while not self.stopping:
            self._outcome = None
            self._resuming = bool(resume_id)
            self.ready.clear()
            try:
                ws = await websockets.connect(
                    self.settings.assemblyai_agent_url,
                    additional_headers={"Authorization": f"Bearer {self.settings.assemblyai_api_key}"},
                    max_size=2**23,
                    open_timeout=10,
                )
            except Exception as exc:  # noqa: BLE001 - DNS, TLS, HTTP errors, timeouts
                failures += 1
                if failures > MAX_CONNECT_FAILURES or not self._first_ready.done():
                    await self._give_up(f"Could not reach AssemblyAI: {exc}")
                    return
                await asyncio.sleep(min(8.0, 0.5 * 2**failures))
                continue

            self.ws = ws
            if resume_id:
                await self._send({"type": "session.resume", "session_id": resume_id})
            else:
                await self._send({"type": "session.update", "session": initial_session(self.settings.assemblyai_voice, greeting=not self._greeted)})
            await self._read(ws)
            self.ws = None
            self.ready.clear()
            if self._outcome == "ready_seen":
                failures = 0

            if self.stopping or self._outcome == "ended":
                break
            if self._outcome == "resume_failed":
                await self._audit(
                    self.sid, "VOICE_RESUME_REFUSED",
                    {"assemblyai_session_id": resume_id, "fallback": "fresh session; transaction context restored from MIRROR"},
                )
                resume_id = None
                self._restore_pending = True
                continue
            if self._outcome == "fatal":
                await self._give_up("AssemblyAI refused the session")
                return

            # The socket dropped without session.ended: a network failure. Record why.
            await self._audit(self.sid, "VOICE_CONNECTION_LOST", {
                "assemblyai_session_id": self.upstream_session_id,
                "close_code": getattr(ws, "close_code", None),
                "close_reason": (getattr(ws, "close_reason", None) or "")[:200],
                "last_event": self.last_event,
            })
            log.warning("AssemblyAI socket closed: code=%s reason=%r last_event=%s",
                        getattr(ws, "close_code", None), getattr(ws, "close_reason", None), self.last_event)
            failures += 1
            self._audio_flush("connection lost")
            self.pending.clear()  # a dropped reply can never complete, so its tool calls must not run
            await asyncio.to_thread(self.rt.set_voice_state, self.sid, TxState.IDLE)
            if failures > MAX_CONNECT_FAILURES:
                await self._give_up("Voice connection lost and could not be re-established")
                return
            resume_id = self.upstream_session_id
            await asyncio.sleep(min(4.0, 0.5 * 2 ** (failures - 1)))

    async def _give_up(self, reason: str) -> None:
        await self._publish_error(ErrorCode.ASSEMBLYAI_UNAVAILABLE, reason)
        self.c.bus.publish(self.sid, "VOICE_STOPPED", {"reason": reason})
        if not self._first_ready.done():
            self._first_ready.set_exception(MirrorError(ErrorCode.ASSEMBLYAI_UNAVAILABLE, reason))

    async def stop(self) -> None:
        """User pressed stop: end the upstream session cleanly (not resumable)."""
        self.stopping = True
        if self.ws is not None:
            await self._send({"type": "session.end"})
            if self.supervisor is not None:
                try:
                    await asyncio.wait_for(asyncio.shield(self.supervisor), timeout=3)
                except (asyncio.TimeoutError, Exception):  # noqa: BLE001 - best effort; closed below either way
                    pass
        await self.close(end_session=True)

    async def detach(self) -> None:
        """Browser went away: drop the socket without session.end so a quick return can try to resume."""
        self.stopping = True
        await asyncio.to_thread(self.rt.remember_voice_disconnect, self.sid, utcnow())
        await self.close(end_session=False)

    async def close(self, end_session: bool) -> None:
        self.stopping = True
        if self.supervisor and not self.supervisor.done() and asyncio.current_task() is not self.supervisor:
            self.supervisor.cancel()
        if self.ws is not None:
            try:
                await self.ws.close()
            except Exception:  # noqa: BLE001
                pass
        self.ws = None
        await asyncio.to_thread(self.rt.set_voice_state, self.sid, TxState.IDLE, assemblyai_session_id="" if end_session else None)

    async def send_audio(self, audio_b64: str) -> None:
        if not isinstance(audio_b64, str) or len(audio_b64) > MAX_AUDIO_B64:
            return
        if self.ws is None or not self.ready.is_set():
            return  # audio before session.ready is discarded by the server anyway
        await self._send({"type": "input.audio", "audio": audio_b64})

    # --------------------------------------------------------------- reading

    async def _read(self, ws) -> None:
        try:
            async for raw in ws:
                try:
                    event = json.loads(raw)
                except ValueError:
                    continue
                await self._handle(event)
        except ConnectionClosed:
            pass
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            log.exception("voice reader failed")
            await self._publish_error(ErrorCode.ASSEMBLYAI_UNAVAILABLE, f"Voice relay error: {exc}")

    async def _handle(self, e: dict) -> None:
        t = e.get("type")
        bus = self.c.bus

        if t == "session.ready":
            resumed = self._resuming and e.get("session_id") == self.upstream_session_id
            self.upstream_session_id = e.get("session_id")
            self._outcome = "ready_seen"
            self._greeted = True
            await asyncio.to_thread(self.rt.set_voice_state, self.sid, TxState.LISTENING, assemblyai_session_id=self.upstream_session_id or "")
            await self._audit(self.sid, "VOICE_STARTED", {"assemblyai_session_id": self.upstream_session_id, "resumed": resumed})
            self.ready.set()
            if not self._first_ready.done():
                self._first_ready.set_result(True)
            await self._refresh_config(force=True)
            if self._restore_pending:
                self._restore_pending = False
                await self._restore_context()
            return
        # The live server sends session.updated *before* session.ready on a fresh session, so only a
        # resumed session may treat other traffic as readiness (audio before session.ready is discarded).
        if self._resuming and not self.ready.is_set() and t not in ("session.error", "session.updated"):
            self.ready.set()

        if t == "session.updated":
            return
        if t == "session.error":
            code = e.get("code", "unknown")
            if code in RESUME_FAILURE_CODES and self._resuming:
                self._outcome = "resume_failed"  # the server closes the socket next; the supervisor starts fresh
                return
            if code in FATAL_CODES and not self.ready.is_set():
                self._outcome = "fatal"
            mapped = ErrorCode.SESSION_EXPIRED if code in RESUME_FAILURE_CODES else ErrorCode.ASSEMBLYAI_UNAVAILABLE
            await self._publish_error(mapped, e.get("message", "AssemblyAI error"), upstream_code=code, retryable=code in RETRYABLE_CODES)
            return
        if t == "session.ended":
            self._outcome = "ended"
            await asyncio.to_thread(self.rt.set_voice_state, self.sid, TxState.IDLE, assemblyai_session_id="")
            await self._audit(self.sid, "VOICE_STOPPED", {"session_duration_seconds": e.get("session_duration_seconds")})
            return

        if t == "input.speech.started":
            self.last_event = t
            bus.publish(self.sid, "VOICE_ACTIVITY", {"speaking": True}, buffer=False)
            return
        if t == "input.speech.stopped":
            bus.publish(self.sid, "VOICE_ACTIVITY", {"speaking": False}, buffer=False)
            return
        if t == "transcript.user.delta":
            # `text` is the full transcript so far for this item: overwrite, never concatenate.
            text = e.get("text") if e.get("text") is not None else e.get("delta", "")
            bus.publish(self.sid, "TRANSCRIPT_DELTA", {"item_id": e.get("item_id"), "text": text}, buffer=False)
            return
        if t == "transcript.user":
            text = (e.get("text") or "").strip()
            if not text:
                return
            self._t_user_final = time.perf_counter()
            self._awaiting_first_audio = True
            self.metrics.user_turns += 1
            item_id = e.get("item_id") or None
            utt = await asyncio.to_thread(self.rt.record_utterance, self.sid, text, "VOICE", item_id)
            # Bind this utterance to the version the user was looking at when they spoke.
            txn_id = self.rt.active_transaction_id(self.sid)
            if txn_id:
                view = await asyncio.to_thread(self.rt.view, txn_id)
                self.version_at_turn[utt.item_id] = view["current_version"]
            return

        if t == "reply.started":
            self.last_event = t
            self.metrics.replies += 1
            bus.publish(self.sid, "AGENT_REPLY_STARTED", {"reply_id": e.get("reply_id")}, buffer=False)
            return
        if t == "reply.audio":
            if self._awaiting_first_audio and self._t_user_final is not None:
                self.metrics.samples["audio"].append((time.perf_counter() - self._t_user_final) * 1000)
                self._awaiting_first_audio = False
            bus.publish(self.sid, "AGENT_AUDIO", {"data": e.get("data", "")}, buffer=False)
            return
        if t == "transcript.agent.delta":
            bus.publish(self.sid, "AGENT_TRANSCRIPT_DELTA", {"reply_id": e.get("reply_id"), "delta": e.get("delta", "")}, buffer=False)
            return
        if t == "transcript.agent":
            text = e.get("text") or ""
            if text:
                await asyncio.to_thread(self.rt.record_utterance, self.sid, text, "VOICE", None, "agent")
            bus.publish(self.sid, "AGENT_TRANSCRIPT", {"text": text, "interrupted": bool(e.get("interrupted")), "reply_id": e.get("reply_id")})
            return

        if t == "tool.call":
            call = PendingCall(e.get("call_id", ""), e.get("name", ""), e.get("arguments") or {}, e.get("reply_id"))
            self.metrics.tool_calls += 1
            self.pending.append(call)
            txn_id = self.rt.active_transaction_id(self.sid)
            await self._audit(
                self.sid, "TOOL_CALL_PROPOSED",
                {"call_id": call.call_id, "name": call.name, "arguments": call.arguments,
                 "note": "a proposal from the model; held until the reply completes"},
                txn_id=txn_id,
            )
            if self.last_event == "reply.done":
                await self._drain()
            return

        if t == "reply.done":
            self.last_event = t
            if e.get("status") == "interrupted":
                await self._on_interrupted(e.get("reply_id"))
            else:
                await self._drain()
                if self._announcement and not self.pending:
                    text, self._announcement = self._announcement, None
                    await self._speak_announcement(text)
            bus.publish(self.sid, "VOICE_METRICS", self.metrics.as_dict(), buffer=False)
            return

    # --------------------------------------------------------- announcements

    async def announce(self, text: str) -> None:
        """Speak up without being asked (reply.create): used when reality moves under a preview.

        If the agent is mid-reply or a tool call is pending, the announcement waits for the next
        completed reply so it never talks over itself or races a tool result.
        """
        if self.ws is None or not self.ready.is_set():
            return
        idle = self.last_event in (None, "reply.done") and not self.pending and not self._drain_lock.locked()
        if idle:
            await self._speak_announcement(text)
        else:
            self._announcement = text

    async def _speak_announcement(self, text: str) -> None:
        await self._refresh_config()  # the re-simulated state may have changed which tools exist
        await self._send({"type": "reply.create", "instructions": f"Tell the user exactly this, then stop and wait for them: {text}"})
        await self._audit(self.sid, "VOICE_ANNOUNCEMENT", {"text": text, "via": "reply.create"},
                          txn_id=self.rt.active_transaction_id(self.sid))

    # --------------------------------------------------------- interruptions

    async def _on_interrupted(self, reply_id: str | None) -> None:
        self.metrics.interruptions += 1
        self._audio_flush("user barge-in")
        discarded = self.pending
        self.pending = []
        txn_id = self.rt.active_transaction_id(self.sid)
        for call in discarded:
            self.metrics.tool_calls_discarded += 1
            await self._audit(
                self.sid, "TOOL_CALL_DISCARDED",
                {"call_id": call.call_id, "name": call.name, "arguments": call.arguments,
                 "reason": "user interrupted before the reply completed; the call never reached the runtime"},
                txn_id=txn_id,
            )
        await self._audit(
            self.sid, "REPLY_INTERRUPTED",
            {"reply_id": reply_id, "discarded_tool_calls": [c.name for c in discarded]},
            txn_id=txn_id,
        )

    def _audio_flush(self, reason: str) -> None:
        self.c.bus.publish(self.sid, "AUDIO_FLUSH", {"reason": reason}, buffer=False)

    # ------------------------------------------------------------ tool calls

    async def _drain(self) -> None:
        async with self._drain_lock:
            while self.pending and self.last_event == "reply.done":
                call = self.pending.pop(0)
                outcome = await asyncio.to_thread(
                    self.tools.execute, self.sid, call.name, call.arguments, dict(self.version_at_turn)
                )
                txn_id = self.rt.active_transaction_id(self.sid)
                await self._refresh_config()
                if self.last_event == "reply.done" and self.ws is not None:
                    await self._send(
                        {"type": "tool.result", "call_id": call.call_id, "result": json.dumps(outcome.result), "is_error": outcome.is_error}
                    )
                    await self._audit(
                        self.sid, "TOOL_RESULT_SENT",
                        {"call_id": call.call_id, "name": call.name, "ok": not outcome.is_error,
                         "say": outcome.result.get("say"), "error_code": outcome.result.get("error_code")},
                        txn_id=txn_id,
                    )
                else:
                    # A new user turn began while we executed. The protocol result is stale, but the
                    # runtime effect is real, so tell the agent through context instead of silence.
                    if self.ws is not None:
                        await self._send(
                            {"type": "conversation.message", "role": "system",
                             "content": f"[MIRROR backend] {call.name} finished: {outcome.result.get('say', '')}"}
                        )
                    await self._audit(
                        self.sid, "TOOL_RESULT_WITHHELD",
                        {"call_id": call.call_id, "name": call.name, "reason": "new user turn started"},
                        txn_id=txn_id,
                    )

    async def _refresh_config(self, force: bool = False) -> None:
        """Progressive tool reveal + dynamic keyterms, driven by authoritative runtime state."""
        txn_id = self.rt.active_transaction_id(self.sid)
        state, clarification, can_rollback = None, None, False
        if txn_id:
            view = await asyncio.to_thread(self.rt.view, txn_id)
            state = view["state"]
            cur = next((v for v in view["versions"] if v["version"] == view["current_version"]), None)
            clarification = cur.get("clarification") if cur and state == "CLARIFYING" else None
            rolled_back = any(x["kind"] == "ROLLBACK" and x["verification"] == "VERIFIED" for x in view["executions"])
            can_rollback = state in ("VERIFIED", "DIVERGED") and not rolled_back
        tools = tools_for_state(state, can_rollback)
        keyterms = keyterms_for(clarification, state)
        key = json.dumps([[t["name"] for t in tools], keyterms])
        if key == self._config_key and not force:
            return
        self._config_key = key
        if self.ws is not None:
            await self._send({"type": "session.update", "session": {"tools": tools, "input": {"keyterms": keyterms}}})
            self.c.bus.publish(
                self.sid, "VOICE_TOOLS_UPDATED", {"tools": [t["name"] for t in tools], "keyterms": len(keyterms), "state": state}, buffer=False
            )

    async def _restore_context(self) -> None:
        txn_id = self.rt.active_transaction_id(self.sid)
        if not txn_id or self.ws is None:
            return
        brief = await asyncio.to_thread(self.tools._brief, txn_id)
        await self._send(
            {"type": "conversation.message", "role": "system",
             "content": "[MIRROR backend] Voice reconnected. Open transaction: " + json.dumps(brief)}
        )

    # ------------------------------------------------------------------ io

    async def _send(self, message: dict) -> None:
        if self.ws is None:
            return
        async with self._send_lock:
            try:
                await self.ws.send(json.dumps(message))
            except ConnectionClosed:
                pass

    async def _publish_error(self, code: ErrorCode, message: str, **details) -> None:
        await self._audit(self.sid, "ERROR", {"code": code.value, "message": message, **details})

    async def _audit(self, session_id: str, event_type: str, payload: dict, txn_id: str | None = None) -> None:
        await asyncio.to_thread(self.rt.record_event, session_id, event_type, payload, txn_id)


def _aware(dt):
    from datetime import timezone

    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


class VoiceRegistry:
    """Owns every live voice bridge, and the limits that keep a public demo from draining credits.

    - per-session voice time (VOICE_MAX_SESSION_SECONDS), summed across reconnects
    - concurrent voice sessions (VOICE_MAX_CONCURRENT)
    - optional daily budget across all sessions (VOICE_DAILY_BUDGET_SECONDS), persisted to disk
    """

    def __init__(self, container: Container):
        self.c = container
        self.settings = container.settings
        self.bridges: dict[str, VoiceBridge] = {}
        self.used: dict[str, float] = {}
        self._started: dict[str, float] = {}
        self._watchers: dict[str, asyncio.Task] = {}
        self.loop: asyncio.AbstractEventLoop | None = None
        self._usage_path = self.settings.data_dir / "voice_usage.json"
        self._day, self._day_used = self._load_usage()
        container.runtime.on_reality_changed = self.announce_threadsafe

    # ------------------------------------------------------------- limits

    def _load_usage(self) -> tuple[str, float]:
        today = time.strftime("%Y-%m-%d", time.gmtime())
        try:
            data = json.loads(self._usage_path.read_text(encoding="utf-8"))
            if data.get("day") == today:
                return today, float(data.get("seconds", 0.0))
        except (OSError, ValueError):
            pass
        return today, 0.0

    def _add_usage(self, session_id: str, seconds: float) -> None:
        self.used[session_id] = self.used.get(session_id, 0.0) + seconds
        today = time.strftime("%Y-%m-%d", time.gmtime())
        if today != self._day:
            self._day, self._day_used = today, 0.0
        self._day_used += seconds
        try:
            self._usage_path.write_text(json.dumps({"day": self._day, "seconds": round(self._day_used, 1)}), encoding="utf-8")
        except OSError:
            pass

    def remaining_seconds(self, session_id: str) -> float | None:
        cap = self.settings.voice_max_session_seconds
        if cap <= 0:
            return None
        live = time.monotonic() - self._started[session_id] if session_id in self._started else 0.0
        return max(0.0, cap - self.used.get(session_id, 0.0) - live)

    def _live_count(self) -> int:
        return sum(1 for b in self.bridges.values() if b.supervisor is not None and not b.supervisor.done())

    def _check_limits(self, session_id: str) -> float | None:
        remaining = self.remaining_seconds(session_id)
        if remaining is not None and remaining < 5:
            raise MirrorError(
                ErrorCode.ASSEMBLYAI_UNAVAILABLE,
                f"This demo session has used its {self.settings.voice_max_session_seconds // 60} minutes of voice. "
                "Typed commands still drive the same runtime.",
                reason="voice_time_limit",
            )
        if self._live_count() >= self.settings.voice_max_concurrent:
            raise MirrorError(
                ErrorCode.ASSEMBLYAI_UNAVAILABLE,
                "All voice lines are busy right now. Try again in a minute, or type your commands.",
                reason="voice_concurrency_limit",
            )
        budget = self.settings.voice_daily_budget_seconds
        if budget > 0 and self._day_used >= budget:
            raise MirrorError(
                ErrorCode.ASSEMBLYAI_UNAVAILABLE,
                "Today's voice budget for this demo is used up. Typed commands still drive the same runtime.",
                reason="voice_daily_budget",
            )
        return remaining

    async def _watch(self, session_id: str, remaining: float) -> None:
        try:
            await asyncio.sleep(remaining)
        except asyncio.CancelledError:
            return
        bridge = self.bridges.get(session_id)
        if bridge is None:
            return
        self.c.bus.publish(session_id, "ERROR", {
            "code": ErrorCode.ASSEMBLYAI_UNAVAILABLE.value,
            "message": "Voice time for this demo session is up. Typed commands still drive the same runtime.",
            "reason": "voice_time_limit",
        })
        await self.stop(session_id)
        self.c.bus.publish(session_id, "VOICE_STOPPED", {"reason": "voice time limit"})

    def _begin(self, session_id: str, remaining: float | None) -> None:
        self._started[session_id] = time.monotonic()
        if remaining is not None:
            self._watchers[session_id] = asyncio.create_task(self._watch(session_id, remaining))

    def _end(self, session_id: str) -> None:
        started = self._started.pop(session_id, None)
        if started is not None:
            self._add_usage(session_id, time.monotonic() - started)
        watcher = self._watchers.pop(session_id, None)
        if watcher is not None and watcher is not asyncio.current_task():
            watcher.cancel()

    # ----------------------------------------------------------- lifecycle

    async def start(self, session_id: str) -> VoiceBridge:
        self.loop = asyncio.get_running_loop()
        existing = self.bridges.get(session_id)
        if existing and existing.supervisor is not None and not existing.supervisor.done():
            return existing
        remaining = self._check_limits(session_id)
        bridge = VoiceBridge(self.c, session_id)
        await bridge.start()
        self.bridges[session_id] = bridge
        self._begin(session_id, remaining)
        return bridge

    async def audio(self, session_id: str, audio_b64: str) -> None:
        bridge = self.bridges.get(session_id)
        if bridge:
            await bridge.send_audio(audio_b64)

    async def stop(self, session_id: str) -> None:
        bridge = self.bridges.pop(session_id, None)
        self._end(session_id)
        if bridge:
            await bridge.stop()

    async def detach(self, session_id: str) -> None:
        bridge = self.bridges.pop(session_id, None)
        self._end(session_id)
        if bridge:
            await bridge.detach()

    def announce_threadsafe(self, session_id: str, text: str) -> None:
        """Called by the runtime (from a worker thread) when reality moves under a session's preview."""
        bridge = self.bridges.get(session_id)
        if bridge is None or self.loop is None or self.loop.is_closed():
            return
        asyncio.run_coroutine_threadsafe(bridge.announce(text), self.loop)
