"""Session WebSocket: runtime events out, voice audio and control in.

    wss://host/ws/v1/sessions/{session_id}?after=<sequence>
    first client message: {"type": "auth", "token": "<session_token>"}

The token travels in the first message rather than the URL so it never lands in
proxy or server access logs.
"""

from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..core.errors import MirrorError

log = logging.getLogger("mirror.ws")
router = APIRouter()
MAX_CLIENT_MESSAGE = 300_000
AUTH_TIMEOUT_S = 5


@router.websocket("/ws/v1/sessions/{session_id}")
async def session_socket(ws: WebSocket, session_id: str):
    app = ws.app
    c = app.state.container
    voice = app.state.voice
    await ws.accept()
    try:
        first = json.loads(await asyncio.wait_for(ws.receive_text(), timeout=AUTH_TIMEOUT_S))
        token = first.get("token") if isinstance(first, dict) and first.get("type") == "auth" else None
        await asyncio.to_thread(c.runtime.authenticate, session_id, token)
    except (asyncio.TimeoutError, ValueError, WebSocketDisconnect):
        await ws.close(code=4401, reason="UNAUTHORIZED")
        return
    except MirrorError as err:
        await ws.close(code=4401, reason=err.code.value)
        return
    await ws.send_text(json.dumps({"type": "auth.ok"}))

    queue = c.bus.subscribe(session_id)
    try:
        after = int(ws.query_params.get("after", "0"))
    except ValueError:
        after = 0
    for event in c.bus.replay(session_id, after):
        await ws.send_text(json.dumps(event))

    async def pump():
        while True:
            event = await queue.get()
            await ws.send_text(json.dumps(event))

    pump_task = asyncio.create_task(pump())
    try:
        while True:
            raw = await ws.receive_text()
            if len(raw) > MAX_CLIENT_MESSAGE:
                continue
            try:
                msg = json.loads(raw)
            except ValueError:
                continue
            kind = msg.get("type") if isinstance(msg, dict) else None
            if kind == "voice.audio":
                await voice.audio(session_id, msg.get("audio"))
            elif kind == "voice.start":
                try:
                    await voice.start(session_id)
                except MirrorError as err:
                    c.bus.publish(session_id, "ERROR", err.to_dict())
                except Exception as exc:  # noqa: BLE001 - network failures to AssemblyAI
                    log.warning("voice start failed: %s", exc)
                    c.bus.publish(session_id, "ERROR", {"code": "ASSEMBLYAI_UNAVAILABLE", "message": f"Could not reach AssemblyAI: {exc}"})
            elif kind == "voice.stop":
                await voice.stop(session_id)
                c.bus.publish(session_id, "VOICE_STOPPED", {"reason": "user stopped"})
            elif kind == "ping":
                await ws.send_text(json.dumps({"type": "pong"}))
    except WebSocketDisconnect:
        pass
    finally:
        pump_task.cancel()
        c.bus.unsubscribe(session_id, queue)
        await voice.detach(session_id)
