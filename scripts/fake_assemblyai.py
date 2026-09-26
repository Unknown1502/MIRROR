"""Scripted stand-in for the AssemblyAI Voice Agent WebSocket. TEST/DEV ONLY - this is a mock.

It speaks the documented protocol (session.update -> session.ready, transcript.user.delta,
transcript.user, reply.started, reply.audio, tool.call, reply.done, tool.result) and plays a
fixed conversation, so the full browser -> backend -> "AssemblyAI" voice path can be
exercised without an API key. The conversation starts only after real input.audio from
the browser has arrived, which proves the microphone pipeline works end to end.

    python scripts/fake_assemblyai.py --port 8765 --report report.json
    ASSEMBLYAI_API_KEY=fake ASSEMBLYAI_AGENT_URL=ws://127.0.0.1:8765 uvicorn ...
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import math
import struct
from pathlib import Path

import websockets

AUDIO_CHUNKS_BEFORE_SCRIPT = 20  # ~1 s of 50 ms chunks
TURN_PAUSE_S = 3.0


def tone(seconds: float, freq: float = 440.0) -> str:
    rate = 24000
    frames = b"".join(
        struct.pack("<h", int(6000 * math.sin(2 * math.pi * freq * i / rate))) for i in range(int(rate * seconds))
    )
    return base64.b64encode(frames).decode()


class Session:
    def __init__(self, ws, report: dict, report_path: Path | None = None):
        self.ws = ws
        self.report = report
        self.report_path = report_path
        self.inbox: asyncio.Queue = asyncio.Queue()
        self.audio_chunks = 0
        self.audio_bytes = 0
        self.enough_audio = asyncio.Event()

    async def send(self, **event):
        await self.ws.send(json.dumps(event))

    async def expect(self, kind: str, timeout: float = 10.0) -> dict:
        async def _next():
            while True:
                msg = await self.inbox.get()
                if msg["type"] == kind:
                    return msg

        return await asyncio.wait_for(_next(), timeout)

    async def turn(self, item: str, text: str, tool: tuple | None, interrupt: bool = False) -> dict | None:
        words = text.split()
        await self.send(type="input.speech.started")
        for n in range(2, len(words) + 1, 2):
            await self.send(type="transcript.user.delta", item_id=item, text=" ".join(words[:n]))
            await asyncio.sleep(0.08)
        await self.send(type="input.speech.stopped")
        await self.send(type="transcript.user", item_id=item, text=text)
        if tool is None:
            return None
        name, args = tool
        call_id = f"call_{item}"
        await self.send(type="reply.started", reply_id=f"r_{item}", item_id=item)
        await self.send(type="reply.audio", data=tone(1.5 if interrupt else 0.25))
        await self.send(type="tool.call", call_id=call_id, name=name, arguments=args)
        if interrupt:
            await asyncio.sleep(0.3)
            await self.send(type="input.speech.started")
            await self.send(type="transcript.agent", text="Committing", reply_id=f"r_{item}", item_id=item, interrupted=True)
            await self.send(type="reply.done", reply_id=f"fc-{call_id}", status="interrupted")
            try:
                await self.expect("tool.result", timeout=1.0)
                self.report["interrupted_result_received"] = True
            except asyncio.TimeoutError:
                self.report["interrupted_result_received"] = False
            return None
        await self.send(type="reply.done", reply_id=f"fc-{call_id}", status="completed")
        msg = await self.expect("tool.result")
        result = json.loads(msg["result"])
        self.report.setdefault("tool_results", []).append({"name": name, "ok": result.get("ok"), "state": result.get("state")})
        await self.send(type="reply.started", reply_id=f"r2_{item}", item_id=item)
        await self.send(type="reply.audio", data=tone(0.25, 520))
        await self.send(type="transcript.agent", text=result.get("say", ""), reply_id=f"r2_{item}", item_id=item, interrupted=False)
        await self.send(type="reply.done", reply_id=f"r2_{item}", status="completed")
        return result

    async def script(self):
        await self.enough_audio.wait()
        pause = lambda: asyncio.sleep(TURN_PAUSE_S)  # noqa: E731 - human pacing between turns
        await self.turn("item_1", "Move 700 units of XR-17 from Mumbai to the Pune site.",
                        ("propose_transfer", {"product": "XR-17", "quantity": 700, "source_location": "Mumbai", "destination_location": "the Pune site"}))
        await pause()
        await self.turn("item_2", "Site 2.", ("clarify_transfer", {"answer": "site 2"}))
        await pause()
        await self.turn("item_3", "Commit 700.", ("commit_transfer", {"quantity": 700}), interrupt=True)
        await pause()
        await self.turn("item_4", "Wait, make that 500.", ("amend_transfer", {"quantity": 500}))
        await pause()
        await self.turn("item_5", "Commit 500.", ("commit_transfer", {"quantity": 500}))
        self.report["script_complete"] = True
        if self.report_path:
            self.report_path.write_text(json.dumps(self.report, indent=2), encoding="utf-8")


async def main(port: int, report_path: Path | None) -> None:
    report: dict = {"connections": 0}

    async def handler(ws):
        report["connections"] += 1
        report["authorization"] = ws.request.headers.get("Authorization", "")[:7] + "…"
        sess = Session(ws, report, report_path)
        task = None
        try:
            async for raw in ws:
                msg = json.loads(raw)
                if msg["type"] == "session.update" and task is None:
                    report["session_update"] = {"tools": [t["name"] for t in msg["session"].get("tools", [])],
                                                "input_format": msg["session"].get("input", {}).get("format")}
                    await sess.send(type="session.ready", session_id="sess_fake_browser")
                    task = asyncio.create_task(sess.script())
                    continue
                if msg["type"] == "input.audio":
                    pcm = base64.b64decode(msg["audio"])
                    sess.audio_chunks += 1
                    sess.audio_bytes += len(pcm)
                    report["audio_chunks"] = sess.audio_chunks
                    report["audio_seconds"] = round(sess.audio_bytes / 2 / 24000, 2)
                    if sess.audio_chunks >= AUDIO_CHUNKS_BEFORE_SCRIPT:
                        sess.enough_audio.set()
                    continue
                if msg["type"] == "session.end":
                    await sess.send(type="session.ended", session_duration_seconds=0, audio_duration_seconds=report.get("audio_seconds"), timestamp=0)
                    await ws.close()
                    break
                if msg["type"] == "session.update":
                    report.setdefault("tool_reveals", []).append([t["name"] for t in msg["session"].get("tools", [])])
                await sess.inbox.put(msg)
                if report_path:
                    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        finally:
            if task:
                task.cancel()
            if report_path:
                report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    async with websockets.serve(handler, "127.0.0.1", port, max_size=2**23):
        print(f"fake AssemblyAI listening on ws://127.0.0.1:{port}", flush=True)
        await asyncio.Future()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--report", type=Path)
    a = ap.parse_args()
    asyncio.run(main(a.port, a.report))
