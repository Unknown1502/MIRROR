"""LIVE end-to-end test against the real AssemblyAI Voice Agent API.

Speech is synthesized locally (Windows SAPI, 24 kHz 16-bit mono), streamed in real time
through MIRROR's actual voice relay (VoiceBridge) to wss://agents.assemblyai.com/v1/ws,
and every transcript, tool call, tool result, agent reply and runtime state is recorded.
The agent's spoken audio for each turn is saved as WAV so a person can listen to it.

Requires ASSEMBLYAI_API_KEY in .env. Uses its own temporary data directory, so the
demo database is not touched. Costs real AssemblyAI session time (several minutes).

    python scripts/live_voice_test.py [--out live-run] [--voice "Microsoft Zira Desktop"] [--only section]

Human voices: record each line (any format ffmpeg reads: phone .m4a, .wav, .webm ...) and name
the file after the line (see --list-lines). Recorded lines replace the synthesized speech; lines
without a recording fall back to SAPI, and every turn in the report says which source it used.

    python scripts/live_voice_test.py --list-lines
    python scripts/live_voice_test.py --recordings recordings/alice --out live-run-alice
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import inspect
import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
import wave
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.core.config import load_settings  # noqa: E402
from app.core.container import build_container  # noqa: E402
from app.erp.service import Fault  # noqa: E402
from app.voice.relay import VoiceBridge  # noqa: E402

CHUNK = 2400  # 50 ms of PCM16 at 24 kHz
QUIET_EVENTS = {"VOICE_METRICS", "AGENT_AUDIO", "VOICE_ACTIVITY", "TRANSCRIPT_DELTA", "AGENT_TRANSCRIPT_DELTA", "VOICE_TOOLS_UPDATED"}


# ------------------------------------------------------------------ speech


def synthesize(text: str, path: Path, voice: str) -> bytes:
    if not path.exists():
        ps = (
            "Add-Type -AssemblyName System.Speech;"
            "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer;"
            f"$s.SelectVoice('{voice}');"
            "$f = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(24000,"
            "[System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen,[System.Speech.AudioFormat.AudioChannel]::Mono);"
            f"$s.SetOutputToWaveFile('{path}', $f);"
            f"$s.Speak('{text.replace(chr(39), chr(39) * 2)}');"
            "$s.Dispose()"
        )
        subprocess.run(["powershell", "-NoProfile", "-Command", ps], check=True, capture_output=True)
    with wave.open(str(path), "rb") as w:
        assert w.getframerate() == 24000 and w.getsampwidth() == 2 and w.getnchannels() == 1, path
        return w.readframes(w.getnframes())


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60]


AUDIO_EXT = (".wav", ".m4a", ".mp3", ".ogg", ".webm", ".flac", ".aac", ".opus")


def find_recording(folder: Path, text: str) -> Path | None:
    for ext in AUDIO_EXT:
        hit = folder / f"{slug(text)}{ext}"
        if hit.exists():
            return hit
    return None


def decode_recording(path: Path) -> bytes:
    """Any recording -> 24 kHz mono PCM16, the Voice Agent API's input format."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise SystemExit("ffmpeg is required to use --recordings (winget install Gyan.FFmpeg)")
    out = subprocess.run([ffmpeg, "-v", "error", "-i", str(path), "-ac", "1", "-ar", "24000", "-f", "s16le", "-"],
                         check=True, capture_output=True)
    return out.stdout


def save_wav(path: Path, pcm: bytes) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(24000)
        w.writeframes(pcm)


# ----------------------------------------------------------------- harness


@dataclass
class TurnRecord:
    section: str
    said: str
    heard: str | None = None
    tool_calls: list = field(default_factory=list)
    tool_results: list = field(default_factory=list)
    discarded: list = field(default_factory=list)
    agent_said: list = field(default_factory=list)
    interrupted: bool = False
    state: str | None = None
    version: int | None = None
    reality: str | None = None
    futures: list = field(default_factory=list)
    agent_audio_s: float = 0.0
    turn_latency_s: float | None = None
    speech_end_to_final_transcript_s: float | None = None
    speech_end_to_first_agent_audio_s: float | None = None
    checks: dict = field(default_factory=dict)
    speech: str | None = None  # "human:<file>" or "sapi:<voice>"
    user_wav: str | None = None
    agent_wav: str | None = None
    agent_audio_offset_s: float | None = None  # when the agent started speaking, relative to the end of the user's speech


class Live:
    def __init__(self, container, out: Path, voice: str, recordings: Path | None = None):
        self.c = container
        self.out = out
        self.voice = voice
        self.recordings = recordings
        # SPEAKER.txt marks clips made by an AI voice (scripts/ai_voice_lines.py); anything else is a person.
        marker = recordings / "SPEAKER.txt" if recordings else None
        self.speaker = marker.read_text(encoding="utf-8").strip() if marker and marker.exists() else "human"
        self.last_source: str | None = None
        self.last_pcm: bytes = b""
        self.sid, _ = container.runtime.create_session()
        self.bridge = VoiceBridge(container, self.sid)
        self.events: list[tuple[float, str, dict]] = []
        self.current: bytearray | None = None
        self.played = asyncio.Event()
        self.running = True
        self.turns: list[TurnRecord] = []
        self.n = 0

    async def start(self):
        self.queue = self.c.bus.subscribe(self.sid)
        self.collector = asyncio.create_task(self._collect())
        # What VoiceRegistry wires up in the server: reality changes under a preview are spoken.
        loop = asyncio.get_running_loop()
        self.c.runtime.on_reality_changed = lambda sid, say: (
            asyncio.run_coroutine_threadsafe(self.bridge.announce(say), loop) if sid == self.sid else None)
        await self.bridge.start()
        self.pumper = asyncio.create_task(self._pump())
        await self.wait_quiet(after=0, need_agent=True, timeout=30)  # greeting

    async def _collect(self):
        while True:
            e = await self.queue.get()
            data = e["data"]
            if e["type"] == "AGENT_AUDIO":
                data = {"pcm": base64.b64decode(data.get("data", ""))}
            self.events.append((time.monotonic(), e["type"], data))

    async def _pump(self):
        loop = asyncio.get_running_loop()
        start, n = loop.time(), 0
        silence = b"\x00" * CHUNK
        while self.running:
            if self.current:
                chunk = bytes(self.current[:CHUNK]).ljust(CHUNK, b"\x00")
                del self.current[:CHUNK]
                if not self.current:
                    self.current = None
                    self.played.set()
            else:
                chunk = silence
            await self.bridge.send_audio(base64.b64encode(chunk).decode())
            n += 1
            await asyncio.sleep(max(0.0, start + n * 0.05 - loop.time()))

    async def speak(self, text: str):
        self.n += 1
        pcm, self.last_source = await asyncio.to_thread(self.audio_for, text)
        self.last_pcm = pcm
        self.played.clear()
        self.current = bytearray(pcm)
        await self.played.wait()

    def audio_for(self, text: str) -> tuple[bytes, str]:
        if self.recordings:
            hit = find_recording(self.recordings, text)
            if hit is not None:
                return decode_recording(hit), f"{self.speaker}:{hit.name}"
        path = self.out / "speech" / f"{hashlib.sha1((self.voice + text).encode()).hexdigest()[:12]}.wav"
        return synthesize(text, path, self.voice), f"sapi:{self.voice}"

    def since(self, mark: int, kind: str | None = None):
        return [e for e in self.events[mark:] if kind is None or e[1] == kind]

    async def wait_event(self, kind: str, after: int, timeout: float):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            hit = self.since(after, kind)
            if hit:
                return hit[0]
            await asyncio.sleep(0.05)
        return None

    async def wait_quiet(self, after: int, need_agent: bool, timeout: float, quiet_s: float = 2.0):
        """Settled = the agent spoke, no tool call pending, the last reply finished, and nothing new for quiet_s."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            await asyncio.sleep(0.1)
            ev = self.since(after)
            loud = [e for e in ev if e[1] not in QUIET_EVENTS]
            if need_agent and not any(e[1] == "AGENT_TRANSCRIPT" for e in ev):
                continue
            if self.bridge.pending or self.bridge.last_event not in ("reply.done", None):
                continue
            last = loud[-1][0] if loud else 0
            if time.monotonic() - last < quiet_s:
                continue
            # Let the agent's audio finish "playing" in wall-clock time so the next utterance is not a barge-in.
            audio = [e for e in ev if e[1] == "AGENT_AUDIO"]
            if audio:
                total = sum(len(e[2]["pcm"]) for e in audio) / 2 / 24000
                first = audio[0][0]
                remaining = first + total + 0.8 - time.monotonic()
                if remaining > 0:
                    await asyncio.sleep(remaining)
            return True
        return False

    async def turn(self, section: str, text: str, timeout: float = 60) -> TurnRecord:
        mark = len(self.events)
        t0 = time.monotonic()
        await self.speak(text)
        spoken_end = time.monotonic()
        rec = TurnRecord(section, text, speech=self.last_source)
        rec.user_wav = f"turn{len(self.turns) + 1:02d}-user.wav"
        save_wav(self.out / rec.user_wav, self.last_pcm)
        user = await self.wait_event("USER_TURN", mark, timeout=20)
        if user is not None:
            rec.speech_end_to_final_transcript_s = round(user[0] - spoken_end, 2)
            audio = await self.wait_event("AGENT_AUDIO", self.events.index(user), timeout=30)
            if audio is not None:
                rec.speech_end_to_first_agent_audio_s = round(audio[0] - spoken_end, 2)
        if user is None:
            rec.checks["transcribed"] = False
            self.turns.append(rec)
            return rec
        rec.heard = user[2].get("text")
        settled = await self.wait_quiet(mark, need_agent=True, timeout=timeout)
        rec.turn_latency_s = round(time.monotonic() - t0, 2)
        self._fill(rec, mark)
        if not settled:
            rec.checks["settled"] = False
        self.turns.append(rec)
        return rec

    def _fill(self, rec: TurnRecord, mark: int):
        ev = self.since(mark)
        rec.tool_calls = [{"name": e[2]["name"], "args": e[2]["arguments"]} for e in ev if e[1] == "TOOL_CALL_PROPOSED"]
        rec.tool_results = [{"name": e[2]["name"], "ok": e[2]["ok"], "error_code": e[2].get("error_code")} for e in ev if e[1] == "TOOL_RESULT_SENT"]
        rec.discarded = [e[2]["name"] for e in ev if e[1] == "TOOL_CALL_DISCARDED"]
        rec.agent_said = [e[2]["text"] for e in ev if e[1] == "AGENT_TRANSCRIPT"]
        rec.interrupted = any(e[1] == "REPLY_INTERRUPTED" for e in ev)
        pcm = b"".join(e[2]["pcm"] for e in ev if e[1] == "AGENT_AUDIO")
        rec.agent_audio_s = round(len(pcm) / 2 / 24000, 2)
        if pcm:
            rec.agent_wav = f"turn{len(self.turns) + 1:02d}-agent.wav"
            save_wav(self.out / rec.agent_wav, pcm)
        txn_id = self.c.runtime.active_transaction_id(self.sid)
        if txn_id:
            v = self.c.runtime.view(txn_id)
            rec.state, rec.version, rec.reality = v["state"], v["current_version"], v["reality"]
            cur = next(x for x in v["versions"] if x["version"] == v["current_version"])
            rec.futures = [
                f"{b['key']}: {b['intent']['quantity']} {b['intent']['product_id']} {b['intent']['source_id']}->{b['intent']['destination_id']} {b['outcome']}"
                for b in cur["branches"]
            ]

    def world(self, loc: str, prod: str = "XR-17") -> int:
        return self.c.erp.read_snapshot().cell(loc, prod).on_hand

    async def stop(self):
        self.running = False
        await asyncio.sleep(0.1)
        await self.bridge.stop()
        self.collector.cancel()


def check(rec: TurnRecord, name: str, ok: bool) -> None:
    rec.checks[name] = bool(ok)


def norm(s: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower())


# ---------------------------------------------------------------- sections


async def demo_loop(L: Live):
    r = await L.turn("demo", "Move 700 units of X R 17 from Mumbai to the Pune site.")
    check(r, "heard 700", "700" in norm(r.heard) or "seven hundred" in norm(r.heard))
    check(r, "heard XR-17", "xr 17" in norm(r.heard) or "xr17" in norm(r.heard).replace(" ", ""))
    check(r, "propose_transfer called", any(t["name"] == "propose_transfer" for t in r.tool_calls))
    check(r, "forked into 2 futures", r.state == "CLARIFYING" and len(r.futures) == 2)
    check(r, "real world unchanged", L.world("MUMBAI-DC") == 2000)

    r = await L.turn("demo", "Site 2.")
    check(r, "clarified to Pune Site 2 preview", r.state == "PREVIEW_READY" and "PUNE-SITE-2" in "".join(r.futures))

    r = await L.turn("demo", "What if I move 900?")
    check(r, "what-if 900 blocked", r.state == "CONSEQUENCE_REVIEW" and "900" in "".join(r.futures))
    check(r, "real world unchanged", L.world("MUMBAI-DC") == 2000)

    r = await L.turn("demo", "What about 500?")
    check(r, "500 valid preview", r.state == "PREVIEW_READY" and " 500 " in " " + "".join(r.futures))

    r = await L.turn("demo", "Compare 500 and 900.")
    check(r, "compare_branches called", any(t["name"] == "compare_branches" for t in r.tool_calls))

    r = await L.turn("demo", "Commit 500.")
    check(r, "commit verified", r.state == "VERIFIED" and r.reality == "VERIFIED")
    check(r, "ERP Mumbai 1500 / Pune Site 2 800", L.world("MUMBAI-DC") == 1500 and L.world("PUNE-SITE-2") == 800)


async def divergence(L: Live):
    L.c.runtime.reset_demo()
    r = await L.turn("divergence", "Move 500 units of X R 17 from Mumbai to Pune Site 2.")
    check(r, "preview 500 to Pune Site 2", r.state == "PREVIEW_READY")
    L.c.runtime.arm_fault(Fault.VERIFICATION_MISMATCH)
    r = await L.turn("divergence", "Commit 500.")
    check(r, "reality diverged, never verified", r.state == "DIVERGED" and r.reality == "DIVERGED")
    check(r, "ERP shows short shipment 1550 / 750", L.world("MUMBAI-DC") == 1550 and L.world("PUNE-SITE-2") == 750)
    r = await L.turn("divergence", "Roll back.")
    check(r, "rollback verified, reality restored", r.state == "VERIFIED" and r.reality == "RESTORED")
    check(r, "ERP back to 2000 / 300", L.world("MUMBAI-DC") == 2000 and L.world("PUNE-SITE-2") == 300)


async def restraint(L: Live):
    L.c.runtime.reset_demo()
    r = await L.turn("restraint", "Move 500 units of X R 17 from Mumbai to Pune Site 2.")
    ops_before = len(L.c.erp.operations())
    r = await L.turn("restraint", "That sounds good to me.")
    attempted = [t for t in r.tool_calls if t["name"] == "commit_transfer"]
    refused = all(not x["ok"] for x in r.tool_results if x["name"] == "commit_transfer")
    check(r, "no execution without the word commit", len(L.c.erp.operations()) == ops_before and r.state == "PREVIEW_READY")
    r.checks["model attempted commit (observed)"] = bool(attempted)
    if attempted:
        check(r, "attempted commit refused by backend", refused)


async def optimizer_and_resume(L: Live):
    r = await L.turn("optimizer", "Move as much as possible without breaking safety stock.")
    check(r, "max safe quantity 800", r.state == "PREVIEW_READY" and " 800 " in " " + "".join(r.futures))

    # Resume: drop the upstream socket without session.end, as a network failure would.
    mark = len(L.events)
    old_id = L.bridge.upstream_session_id
    await L.bridge.ws.close()
    started = await L.wait_event("VOICE_STARTED", mark, timeout=25)
    rec = TurnRecord("resume", "(dropped the AssemblyAI socket)")
    rec.checks["voice recovered after drop"] = started is not None
    refused = L.since(mark, "VOICE_RESUME_REFUSED")
    rec.checks["AssemblyAI accepted session.resume (observed)"] = bool(started and started[2].get("resumed") and L.bridge.upstream_session_id == old_id)
    rec.checks["no reconnect cascade (one VOICE_STARTED)"] = len(L.since(mark, "VOICE_STARTED")) == 1
    if refused:
        rec.checks["resume refusal recorded in the ledger"] = True
    L.turns.append(rec)
    await asyncio.sleep(1.0)
    r = await L.turn("resume", "What about 600?")
    check(r, "context kept after resume (600 simulated)", " 600 " in " " + "".join(r.futures))


async def barge_in(L: Live):
    mark = len(L.events)
    t0 = time.monotonic()
    await L.speak("How much X R 17 is at each location?")
    # Wait until the agent is reading the stock levels aloud, then talk over it.
    deadline = time.monotonic() + 40
    started_audio = None
    while time.monotonic() < deadline:
        results = L.since(mark, "TOOL_RESULT_SENT")
        if results:
            after_result = L.events.index(results[0])
            audio = L.since(after_result, "AGENT_AUDIO")
            if audio:
                started_audio = audio[0][0]
                break
        await asyncio.sleep(0.05)
    rec = TurnRecord("barge-in", "How much XR-17 is at each location? + interruption")
    if started_audio is None:
        rec.checks["agent started answering"] = False
        L.turns.append(rec)
        return
    await asyncio.sleep(max(0.0, started_audio + 1.5 - time.monotonic()))
    mark2 = len(L.events)
    await L.speak("Stop. Cancel the transfer.")
    await L.wait_event("USER_TURN", mark2, timeout=20)
    await L.wait_quiet(mark2, need_agent=True, timeout=60)
    L._fill(rec, mark)
    rec.turn_latency_s = round(time.monotonic() - t0, 2)
    rec.checks["reply interrupted (reply.done status=interrupted)"] = rec.interrupted
    rec.checks["audio flushed"] = any(e[1] == "AUDIO_FLUSH" for e in L.since(mark))
    rec.checks["new turn processed: transfer cancelled"] = rec.state == "ABORTED"
    L.turns.append(rec)


def mirror_ops(L: Live) -> int:
    return len([op for op in L.c.erp.operations(200) if op["actor"] == "MIRROR"])


async def adversarial(L: Live):
    L.c.runtime.reset_demo()
    r = await L.turn("adversarial", "Move 700 units of X R 17 from Delhi to Pune Site 2.")
    check(r, "Delhi preview (valid, safety-floor warning)", r.state == "PREVIEW_READY" and "DELHI-WAREHOUSE" in "".join(r.futures))

    r = await L.turn("adversarial", "Actually I meant Mumbai.")
    check(r, "role-ambiguous correction forks source vs destination", r.state == "CLARIFYING" and len(r.futures) == 2)
    r = await L.turn("adversarial", "As the source.")
    check(r, "resolved: Mumbai to Pune Site 2", r.state == "PREVIEW_READY" and "MUMBAI-DC->PUNE-SITE-2" in "".join(r.futures))

    r = await L.turn("adversarial", "Wait, make that 500.")
    check(r, "correction to 500 creates a new preview", r.state == "PREVIEW_READY" and " 500 " in " " + "".join(r.futures))
    txn = L.c.runtime.view(L.c.runtime.active_transaction_id(L.sid))
    check(r, "old commit authorization revoked", any(e["event_type"] == "COMMIT_TOKEN_INVALIDATED" for e in L.c.runtime.audit(txn["id"])["events"]))

    ops = mirror_ops(L)
    r = await L.turn("adversarial", "Commit 900.")
    check(r, "commit of a quantity that is not the preview does not execute", mirror_ops(L) == ops and r.state != "VERIFIED")
    r = await L.turn("adversarial", "Don't commit yet.")
    check(r, "'don't commit yet' does not execute", mirror_ops(L) == ops)

    # Reality moves under the preview while nobody is talking: MIRROR re-simulates and says so unprompted.
    mark = len(L.events)
    txn_id = L.c.runtime.active_transaction_id(L.sid)
    L.c.runtime.external_change("MUMBAI-DC", "XR-17", -300, "another team shipped 300")
    await L.wait_event("AGENT_TRANSCRIPT", mark, timeout=30)
    await L.wait_quiet(mark, need_agent=True, timeout=45)
    rec = TurnRecord("adversarial", "(no one speaks; another team ships 300 from Mumbai)")
    L._fill(rec, mark)
    events = L.c.runtime.audit(txn_id)["events"]
    alert = [e for e in events if e["event_type"] == "REALITY_CHANGED"]
    kinds = [x["kind"] for x in L.c.runtime.view(txn_id)["versions"]]
    check(rec, "preview re-simulated against the new world", "RESIMULATION" in kinds and bool(alert))
    check(rec, "alert spoken via reply.create", any(e["event_type"] == "VOICE_ANNOUNCEMENT" for e in events))
    check(rec, "nothing executed", mirror_ops(L) == ops)
    rec.checks["agent told the user reality changed (observed)"] = any(
        re.search(r"chang|shipped|1,?700|seventeen hundred", s or "", re.I) for s in rec.agent_said)
    L.turns.append(rec)

    r = await L.turn("adversarial", "Commit 500.")
    check(r, "commit of the re-simulated preview verified", r.state == "VERIFIED" and L.world("MUMBAI-DC") == 1200)
    ops = mirror_ops(L)
    r = await L.turn("adversarial", "Commit 500.")
    check(r, "second commit does not execute twice", mirror_ops(L) == ops and L.world("MUMBAI-DC") == 1200)
    r = await L.turn("adversarial", "Roll back.")
    check(r, "rollback restores the post-external-change world", r.reality == "RESTORED" and L.world("MUMBAI-DC") == 1700)

    r = await L.turn("adversarial", "Move 100 units of X R 17 from Chennai to Pune Site 2.")
    check(r, "unknown city is not invented", r.state == "CLARIFYING" and not r.futures)
    r = await L.turn("adversarial", "From Mumbai.")
    check(r, "missing source supplied", r.state == "PREVIEW_READY" and "MUMBAI-DC->PUNE-SITE-2" in "".join(r.futures))

    r = await L.turn("adversarial", "Move 100 units of X R from Mumbai to Delhi.")
    r.checks["heard a product family (observed)"] = "xr 17" not in norm(r.heard)
    if r.checks["heard a product family (observed)"]:
        check(r, "product family forks XR-17 vs XR-17B", r.state == "CLARIFYING" and len(r.futures) == 2)
        r = await L.turn("adversarial", "X R 17 B.")
        check(r, "resolved to XR-17B", r.state == "PREVIEW_READY" and "XR-17B" in "".join(r.futures))


async def commit_barge(L: Live):
    """Talk over the agent right after a spoken commit. Either outcome is acceptable; an unsafe one is not."""
    L.c.runtime.reset_demo()
    await L.turn("commit-barge", "Move 500 units of X R 17 from Mumbai to Pune Site 2.")
    ops = mirror_ops(L)
    mark = len(L.events)
    await L.speak("Commit 500.")
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline and not any(e[1] in ("TOOL_CALL_PROPOSED", "AGENT_REPLY_STARTED") for e in L.since(mark)):
        await asyncio.sleep(0.02)
    mark2 = len(L.events)
    await L.speak("Wait, make that 450.")
    await L.wait_event("USER_TURN", mark2, timeout=20)
    await L.wait_quiet(mark2, need_agent=True, timeout=60)
    rec = TurnRecord("commit-barge", "Commit 500. + immediate 'Wait, make that 450.'")
    L._fill(rec, mark)
    executed = mirror_ops(L) > ops
    rec.checks["commit discarded by the barge-in (observed)"] = "commit_transfer" in rec.discarded
    rec.checks["commit executed before the barge-in (observed)"] = executed
    if executed:
        check(rec, "executed commit was verified, and the late change did not execute", rec.reality == "VERIFIED" and mirror_ops(L) == ops + 1)
    else:
        check(rec, "nothing executed and 450 became the new preview", rec.state == "PREVIEW_READY" and " 450 " in " " + "".join(rec.futures))
    L.turns.append(rec)


_ACTION_CLAIMS = re.compile(r"\b(cancel+ed|committed|rolled back|executed|moved|transferred|undone|saved)\b", re.I)


async def honesty(L: Live):
    """The agent must not claim actions no tool performed."""
    L.c.runtime.reset_demo()
    await L.turn("honesty", "Move 500 units of X R 17 from Mumbai to Pune Site 2.")
    for text in ("Don't commit yet.", "Hold on.", "Wait."):
        r = await L.turn("honesty", text)
        claims = [s for s in r.agent_said if _ACTION_CLAIMS.search(s) and not re.search(r"\b(not|nothing|no)\b", s, re.I)]
        check(r, "no tool call", not r.tool_calls or all(t["name"] in ("get_inventory",) for t in r.tool_calls))
        check(r, "state untouched", r.state == "PREVIEW_READY")
        check(r, "no false claim of an action", not claims)
    r = await L.turn("honesty", "How much X R 17 is in Mumbai right now?")
    check(r, "stock answer grounded in a tool (2000)", any(t["name"] == "get_inventory" for t in r.tool_calls)
          and any(("2000" in s) or ("two thousand" in s.lower()) for s in r.agent_said))
    r = await L.turn("honesty", "Cancel it.")
    check(r, "cancel executed through the tool", any(t["name"] == "cancel_transfer" for t in r.tool_calls) and r.state == "ABORTED")


async def judge(L: Live):
    """The replay on the landing page: one continuous live session, recorded turn by turn.

    scripts/export_replay.py turns the recording into the web replay; the page re-runs the same
    steps against the backend, so the numbers on screen are recomputed, not recorded.
    """
    L.c.runtime.reset_demo()
    r = await L.turn("judge", "Move 700 units of X R 17 from Mumbai to the Pune site.")
    check(r, "forked into 2 futures", r.state == "CLARIFYING" and len(r.futures) == 2)
    r = await L.turn("judge", "Site 2.")
    check(r, "preview 700 to Pune Site 2", r.state == "PREVIEW_READY")
    r = await L.turn("judge", "What if I move 900?")
    check(r, "900 blocked", r.state == "CONSEQUENCE_REVIEW")
    r = await L.turn("judge", "What about 500?")
    check(r, "500 valid", r.state == "PREVIEW_READY" and " 500 " in " " + "".join(r.futures))
    r = await L.turn("judge", "Compare 500 and 900.")
    check(r, "compared", any(t["name"] == "compare_branches" for t in r.tool_calls))

    mark = len(L.events)
    L.c.runtime.external_change("MUMBAI-DC", "XR-17", -300, "another team shipped 300")
    await L.wait_event("AGENT_TRANSCRIPT", mark, timeout=30)
    await L.wait_quiet(mark, need_agent=True, timeout=45)
    rec = TurnRecord("judge", "(another team ships 300 XR-17 out of Mumbai)")
    L._fill(rec, mark)
    check(rec, "reality alert spoken", bool(rec.agent_said) and "RESIMULATION" in [
        v["kind"] for v in L.c.runtime.view(L.c.runtime.active_transaction_id(L.sid))["versions"]])
    L.turns.append(rec)

    L.c.runtime.arm_fault(Fault.VERIFICATION_MISMATCH)
    r = await L.turn("judge", "Commit 500.")
    check(r, "short shipment detected as divergence", r.state == "DIVERGED" and L.world("MUMBAI-DC") == 1250)
    r = await L.turn("judge", "Roll back.")
    check(r, "rolled back to the real pre-commit world", r.reality == "RESTORED" and L.world("MUMBAI-DC") == 1700)


SECTIONS = {
    "demo": demo_loop,
    "honesty": honesty,
    "divergence": divergence,
    "restraint": restraint,
    "optimizer": optimizer_and_resume,
    "barge": barge_in,
    "adversarial": adversarial,
    "commitbarge": commit_barge,
    "judge": judge,
}


def line_sections(sections: list[str]) -> dict[str, list[str]]:
    """Every line the given sections speak, in order, with the sections that use it."""
    lines: dict[str, list[str]] = {}
    for name in sections:
        src = inspect.getsource(SECTIONS[name])
        found = re.findall(r'L\.(?:turn\("[\w-]+", |speak\()"([^"]+)"', src)
        for group in re.findall(r"for text in \(([^)]*)\)", src):
            found += re.findall(r'"([^"]+)"', group)
        for text in found:
            lines.setdefault(text, []).append(name)
    return lines


def spoken_lines(sections: list[str]) -> list[str]:
    return list(line_sections(sections))


async def main_async(args) -> int:
    out = Path(args.out)
    (out / "speech").mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="mirror-live-"))
    settings = load_settings(data_dir=tmp, static_dir=None)
    if not settings.assemblyai_api_key:
        print("ASSEMBLYAI_API_KEY is not set in .env")
        return 2
    c = build_container(settings)
    L = Live(c, out, args.voice, Path(args.recordings) if args.recordings else None)
    started = time.monotonic()
    await L.start()
    try:
        for name, fn in SECTIONS.items():
            if args.only and name not in args.only:
                continue
            print(f"--- {name}", flush=True)
            await fn(L)
            for t in L.turns:
                if not getattr(t, "_printed", False):
                    t._printed = True
                    print(f"  said : {t.said}\n  heard: {t.heard}\n  tools: {[x['name'] for x in t.tool_calls]} results={[(x['name'], x['ok'], x['error_code']) for x in t.tool_results]} discarded={t.discarded}\n"
                          f"  agent: {t.agent_said}\n  state: {t.state} v{t.version} reality={t.reality} futures={t.futures}\n  checks: {t.checks}\n"
                          f"  latency: final transcript +{t.speech_end_to_final_transcript_s}s after speech ended, first agent audio +{t.speech_end_to_first_agent_audio_s}s", flush=True)
    finally:
        await L.stop()
    metrics = L.bridge.metrics.as_dict()
    ledger = c.runtime.verify_ledger()
    all_checks = [(t.section, k, v) for t in L.turns for k, v in t.checks.items() if "(observed)" not in k]
    passed = sum(1 for *_, v in all_checks if v)
    report = {
        "duration_s": round(time.monotonic() - started, 1),
        "voice_metrics": metrics,
        "agent_voice": settings.assemblyai_voice,
        "ledger": ledger,
        "checks_passed": passed,
        "checks_total": len(all_checks),
        "turns": [{k: v for k, v in t.__dict__.items() if k != "_printed"} for t in L.turns],
    }
    (out / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    human = [t for t in L.turns if (t.speech or "").startswith("human:")]
    if human:
        report["human_turns"] = len(human)
        (out / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"human-voice turns: {len(human)} of {len(L.turns)}")
    print(json.dumps({"checks": f"{passed}/{len(all_checks)}", "voice_metrics": metrics, "ledger_valid": ledger["valid"], "duration_s": report["duration_s"]}, indent=2))
    for sec, k, v in all_checks:
        if not v:
            print(f"FAIL [{sec}] {k}")
    c.dispose()
    return 0 if passed == len(all_checks) else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="live-run")
    ap.add_argument("--voice", default="Microsoft Zira Desktop")
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--recordings", help="folder of human recordings named by --list-lines")
    ap.add_argument("--list-lines", action="store_true", help="print every spoken line and its recording file name")
    a = ap.parse_args()
    if a.list_lines:
        lines = line_sections(list(SECTIONS))
        print(f"{len(lines)} distinct lines. Record each one and name the file as shown (any audio format ffmpeg reads).\n")
        for text, used in lines.items():
            print(f"{slug(text) + '.m4a':64} {text:58} [{', '.join(dict.fromkeys(used))}]")
        return 0
    if a.recordings and not Path(a.recordings).is_dir():
        print(f"no such folder: {a.recordings}")
        return 2
    return asyncio.run(main_async(a))


if __name__ == "__main__":
    sys.exit(main())
