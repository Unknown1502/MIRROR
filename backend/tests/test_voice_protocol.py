"""Voice relay against a local fake of the AssemblyAI Voice Agent WebSocket.

The fake speaks the documented event sequence (session.update -> session.ready,
transcript.user, reply.started, tool.call, reply.done, session.resume). It is a
protocol conformance harness, not a live AssemblyAI test.
"""

import asyncio
import json
from dataclasses import replace

import pytest
import websockets

from app.core.container import build_container
from app.voice.relay import VoiceBridge


class FakeAssemblyAI:
    def __init__(self):
        self.inbox: asyncio.Queue = asyncio.Queue()
        self.conn = None
        self.connections = 0
        self.connected = asyncio.Event()
        self.auth_headers: list[str] = []
        self.first_messages: list[dict] = []

    async def handler(self, ws):
        self.conn = ws
        self.connections += 1
        self.auth_headers.append(ws.request.headers.get("Authorization"))
        self.connected.set()
        first = True
        try:
            async for raw in ws:
                msg = json.loads(raw)
                if first:
                    self.first_messages.append(msg)
                    first = False
                await self.inbox.put(msg)
        except websockets.ConnectionClosed:
            pass

    async def send(self, **event):
        await self.conn.send(json.dumps(event))

    async def expect(self, kind: str, timeout: float = 5.0) -> dict:
        async def _next():
            while True:
                msg = await self.inbox.get()
                if msg["type"] == kind:
                    return msg

        return await asyncio.wait_for(_next(), timeout)

    async def nothing(self, kind: str, wait: float = 0.6) -> bool:
        try:
            await self.expect(kind, timeout=wait)
            return False
        except asyncio.TimeoutError:
            return True


async def user_turn(fake, item_id, text, tool=None, reply="r"):
    await fake.send(type="input.speech.started")
    await fake.send(type="transcript.user.delta", item_id=item_id, text=text[: len(text) // 2])
    await fake.send(type="input.speech.stopped")
    await fake.send(type="transcript.user", item_id=item_id, text=text)
    if tool is None:
        return None
    name, args = tool
    call_id = f"call_{item_id}"
    await fake.send(type="reply.started", reply_id=f"{reply}_{item_id}")
    await fake.send(type="reply.audio", data="AAAA")
    await fake.send(type="tool.call", call_id=call_id, name=name, arguments=args)
    await fake.send(type="reply.done", reply_id=f"fc-{call_id}", status="completed")
    result = await fake.expect("tool.result")
    assert result["call_id"] == call_id
    return json.loads(result["result"])


async def start_bridge(container, sid, fake):
    bridge = VoiceBridge(container, sid)
    task = asyncio.create_task(bridge.start())
    await asyncio.wait_for(fake.connected.wait(), 5)
    update = await fake.expect("session.update")
    await fake.send(type="session.ready", session_id="sess_fake_1")
    await task
    return bridge, update


def run(coro):
    return asyncio.run(coro)


@pytest.fixture()
def voice_env(settings):
    def make(port):
        s = replace(settings, assemblyai_api_key="test-key", assemblyai_agent_url=f"ws://127.0.0.1:{port}")
        return build_container(s)

    return make


def test_acceptance_2_voice_flow_and_acceptance_5_interruption(voice_env):
    async def main():
        fake = FakeAssemblyAI()
        server = await websockets.serve(fake.handler, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        c = voice_env(port)
        sid, _ = c.runtime.create_session()
        events = c.bus.subscribe(sid)
        bridge, update = await start_bridge(c, sid, fake)

        # Session config follows the documented shape; the key is sent as a Bearer header, server-side only.
        assert fake.auth_headers[0] == "Bearer test-key"
        session = update["session"]
        assert session["input"]["format"]["encoding"] == "audio/pcm"
        assert "XR-17" in session["input"]["keyterms"]
        names = [t["name"] for t in session["tools"]]
        assert "propose_transfer" in names and "commit_transfer" not in names  # not revealed yet
        assert all(t["type"] == "function" and t["parameters"]["type"] == "object" for t in session["tools"])

        # 1. The model wrongly resolves "the Pune site" to Site 1. MIRROR forks anyway.
        r = await user_turn(fake, "item_1", "Move 700 units of XR-17 from Mumbai to the Pune site.",
                            ("propose_transfer", {"product": "XR-17", "quantity": 700, "source_location": "Mumbai",
                                                  "destination_location": "Pune Site 1"}))
        assert r["state"] == "CLARIFYING" and len(r["futures"]) == 2
        assert r["say"].startswith("There are two Pune sites.")

        # 2. Clarify -> preview; commit tool is now revealed.
        r = await user_turn(fake, "item_2", "Site 2.", ("clarify_transfer", {"answer": "site 2"}))
        assert r["state"] == "PREVIEW_READY"

        r = await user_turn(fake, "item_3", "What if I move 900?", ("simulate_transfer", {"quantity": 900}))
        assert r["state"] == "CONSEQUENCE_REVIEW" and r["real_world_changed"] is False
        r = await user_turn(fake, "item_4", "What about 500?", ("simulate_transfer", {"quantity": 500}))
        assert r["state"] == "PREVIEW_READY"
        v_500 = r["version"]

        # 3. Interruption: agent is mid-reply with a commit tool call, user barges in.
        await fake.send(type="transcript.user", item_id="item_5", text="Commit 500.")
        await fake.send(type="reply.started", reply_id="r_5")
        await fake.send(type="reply.audio", data="AAAA")
        await fake.send(type="tool.call", call_id="call_5", name="commit_transfer", arguments={"quantity": 500})
        await fake.send(type="input.speech.started")
        await fake.send(type="reply.done", reply_id="fc-call_5", status="interrupted")
        assert await fake.nothing("tool.result")  # stale result discarded
        assert c.erp.operations() == []  # and the commit never executed

        # 4. New turn supersedes the version; the old commit authorization dies with it.
        r = await user_turn(fake, "item_6", "Wait, make that 450.", ("amend_transfer", {"quantity": 450}))
        assert r["state"] == "PREVIEW_READY" and r["version"] == v_500 + 1

        # 5. Explicit commit -> ERP -> verification.
        r = await user_turn(fake, "item_7", "Commit 450.", ("commit_transfer", {"quantity": 450}))
        assert r["state"] == "VERIFIED" and r["real_world_changed"] is True
        assert c.erp.read_snapshot().cell("MUMBAI-DC", "XR-17").on_hand == 1550

        seen = []
        while not events.empty():
            seen.append(events.get_nowait()["type"])
        for t in ("VOICE_STARTED", "TRANSCRIPT_DELTA", "USER_TURN", "AGENT_AUDIO", "TOOL_CALL_PROPOSED",
                  "AUDIO_FLUSH", "TOOL_CALL_DISCARDED", "REPLY_INTERRUPTED", "COMMIT_TOKEN_INVALIDATED", "VERIFIED"):
            assert t in seen, t

        await bridge.stop()
        server.close()
        c.engine.dispose()
        c.erp.engine.dispose()

    run(main())


def test_model_cannot_commit_without_user_saying_commit(voice_env):
    async def main():
        fake = FakeAssemblyAI()
        server = await websockets.serve(fake.handler, "127.0.0.1", 0)
        c = voice_env(server.sockets[0].getsockname()[1])
        sid, _ = c.runtime.create_session()
        bridge, _ = await start_bridge(c, sid, fake)
        await user_turn(fake, "i1", "Move 500 units of XR-17 from Mumbai to Pune Site 2",
                        ("propose_transfer", {"product": "XR-17", "quantity": 500, "source_location": "Mumbai",
                                              "destination_location": "Pune Site 2"}))
        r = await user_turn(fake, "i2", "that looks fine I guess", ("commit_transfer", {"quantity": 500}))
        assert r["ok"] is False and r["error_code"] == "INSUFFICIENT_EVIDENCE"
        r = await user_turn(fake, "i3", "don't commit yet", ("commit_transfer", {}))
        assert r["ok"] is False
        assert c.erp.operations() == []
        await bridge.stop()
        server.close()
        c.engine.dispose()
        c.erp.engine.dispose()

    run(main())


def test_model_invented_quantity_requires_confirmation(voice_env):
    async def main():
        fake = FakeAssemblyAI()
        server = await websockets.serve(fake.handler, "127.0.0.1", 0)
        c = voice_env(server.sockets[0].getsockname()[1])
        sid, _ = c.runtime.create_session()
        bridge, _ = await start_bridge(c, sid, fake)
        r = await user_turn(fake, "i1", "Move XR-17 from Mumbai to Pune Site 2",
                            ("propose_transfer", {"product": "XR-17", "quantity": 700, "source_location": "Mumbai",
                                                  "destination_location": "Pune Site 2"}))
        assert r["state"] == "CLARIFYING"  # 700 came from the model, not the user
        assert "quantity" in r["say"]
        await bridge.stop()
        server.close()
        c.engine.dispose()
        c.erp.engine.dispose()

    run(main())


def test_session_resume_after_network_drop(voice_env):
    async def main():
        fake = FakeAssemblyAI()
        server = await websockets.serve(fake.handler, "127.0.0.1", 0)
        c = voice_env(server.sockets[0].getsockname()[1])
        sid, _ = c.runtime.create_session()
        bridge, _ = await start_bridge(c, sid, fake)
        assert c.runtime.voice_resume_info(sid)[0] == "sess_fake_1"

        fake.connected.clear()
        await fake.conn.close()  # drop without session.ended
        await asyncio.wait_for(fake.connected.wait(), 5)
        resume = await fake.expect("session.resume")
        assert resume == {"type": "session.resume", "session_id": "sess_fake_1"}
        await fake.send(type="session.ready", session_id="sess_fake_1")
        await asyncio.wait_for(bridge.ready.wait(), 5)
        assert fake.connections == 2
        await bridge.stop()
        server.close()
        c.engine.dispose()
        c.erp.engine.dispose()

    run(main())


def test_upstream_item_ids_do_not_collide_across_sessions(voice_env):
    """AssemblyAI item ids are per upstream session; commits in two MIRROR sessions must not collide."""

    async def main():
        fake = FakeAssemblyAI()
        server = await websockets.serve(fake.handler, "127.0.0.1", 0)
        c = voice_env(server.sockets[0].getsockname()[1])
        for n in range(2):
            c.runtime.reset_demo()
            sid, _ = c.runtime.create_session()
            fake.connected.clear()
            bridge, _ = await start_bridge(c, sid, fake)
            await user_turn(fake, "item_1", "Move 500 units of XR-17 from Mumbai to Pune Site 2",
                            ("propose_transfer", {"product": "XR-17", "quantity": 500, "source_location": "Mumbai",
                                                  "destination_location": "Pune Site 2"}))
            r = await user_turn(fake, "item_2", "Commit 500.", ("commit_transfer", {"quantity": 500}))
            assert r["ok"] is True and r["state"] == "VERIFIED", (n, r)
            await bridge.stop()
        server.close()
        c.engine.dispose()
        c.erp.engine.dispose()

    run(main())


def test_resume_refused_falls_back_to_fresh_session_with_context(voice_env):
    """Live AssemblyAI answers session.resume with session_not_found. MIRROR must recover without
    duplicate sessions, without greeting again, and with the open transaction restored as context."""

    async def main():
        fake = FakeAssemblyAI()
        server = await websockets.serve(fake.handler, "127.0.0.1", 0)
        c = voice_env(server.sockets[0].getsockname()[1])
        sid, _ = c.runtime.create_session()
        bridge, update = await start_bridge(c, sid, fake)
        assert "greeting" in update["session"]
        await user_turn(fake, "i1", "Move 500 units of XR-17 from Mumbai to Pune Site 2",
                        ("propose_transfer", {"product": "XR-17", "quantity": 500, "source_location": "Mumbai",
                                              "destination_location": "Pune Site 2"}))
        fake.connected.clear()
        await fake.conn.close()  # network drop

        await asyncio.wait_for(fake.connected.wait(), 5)
        assert (await fake.expect("session.resume"))["session_id"] == "sess_fake_1"
        await fake.send(type="session.error", code="session_not_found", message="Session not found or grace window has expired")
        await fake.conn.close(code=1008)

        fake.connected.clear()
        await asyncio.wait_for(fake.connected.wait(), 5)
        fresh = await fake.expect("session.update")
        assert "greeting" not in fresh["session"]  # no second greeting
        await fake.send(type="session.ready", session_id="sess_fake_2")
        restore = await fake.expect("conversation.message")
        assert restore["role"] == "system" and "PREVIEW_READY" in restore["content"]
        assert fake.connections == 3  # original, refused resume, one fresh session: no cascade

        # The conversation continues on the fresh session against the same transaction.
        r = await user_turn(fake, "i2", "Commit 500.", ("commit_transfer", {"quantity": 500}))
        assert r["state"] == "VERIFIED"
        await asyncio.sleep(0.5)
        assert fake.connections == 3
        await bridge.stop()
        server.close()
        c.engine.dispose()
        c.erp.engine.dispose()

    run(main())


def test_dropped_reply_never_executes_its_tool_call(voice_env):
    """A tool call whose reply never completes (socket dropped mid-reply) must not run."""

    async def main():
        fake = FakeAssemblyAI()
        server = await websockets.serve(fake.handler, "127.0.0.1", 0)
        c = voice_env(server.sockets[0].getsockname()[1])
        sid, _ = c.runtime.create_session()
        bridge, _ = await start_bridge(c, sid, fake)
        await user_turn(fake, "i1", "Move 500 units of XR-17 from Mumbai to Pune Site 2",
                        ("propose_transfer", {"product": "XR-17", "quantity": 500, "source_location": "Mumbai",
                                              "destination_location": "Pune Site 2"}))
        await fake.send(type="transcript.user", item_id="i2", text="Commit 500.")
        await fake.send(type="reply.started", reply_id="r2")
        await fake.send(type="tool.call", call_id="c2", name="commit_transfer", arguments={"quantity": 500})
        fake.connected.clear()
        await fake.conn.close()  # dropped before reply.done
        await asyncio.wait_for(fake.connected.wait(), 5)
        await fake.expect("session.resume")
        await fake.send(type="session.ready", session_id="sess_fake_1")
        await asyncio.sleep(0.5)
        await fake.send(type="reply.done", reply_id="fc-c2", status="completed")  # even if something completes later
        assert await fake.nothing("tool.result")
        assert c.erp.operations() == []
        await bridge.stop()
        server.close()
        c.engine.dispose()
        c.erp.engine.dispose()

    run(main())


def test_model_swapped_roles_are_not_evidence(voice_env):
    """User: from Mumbai to Delhi. Model: source=Delhi, destination=Mumbai. The places were said; the roles were not."""

    async def main():
        fake = FakeAssemblyAI()
        server = await websockets.serve(fake.handler, "127.0.0.1", 0)
        c = voice_env(server.sockets[0].getsockname()[1])
        sid, _ = c.runtime.create_session()
        bridge, _ = await start_bridge(c, sid, fake)
        r = await user_turn(fake, "i1", "Move 100 units of XR-17 from Mumbai to Delhi",
                            ("propose_transfer", {"product": "XR-17", "quantity": 100, "source_location": "Delhi",
                                                  "destination_location": "Mumbai"}))
        assert r["state"] == "CLARIFYING"  # never a preview of the swapped transfer
        view = c.runtime.view(c.runtime.active_transaction_id(sid))
        draft = view["versions"][-1]["intent"]["drafts"][0]
        assert draft["source"]["status"] == "INFERRED" and "not the source" in draft["source"]["note"]
        assert c.erp.operations() == []
        await bridge.stop()
        server.close()
        c.engine.dispose()
        c.erp.engine.dispose()

    run(main())


def test_correction_through_propose_tool_still_forks_on_role(voice_env):
    """'Actually I meant Mumbai' routed by the model to propose_transfer with both locations filled in."""

    async def main():
        fake = FakeAssemblyAI()
        server = await websockets.serve(fake.handler, "127.0.0.1", 0)
        c = voice_env(server.sockets[0].getsockname()[1])
        sid, _ = c.runtime.create_session()
        bridge, _ = await start_bridge(c, sid, fake)
        await user_turn(fake, "i1", "Move 700 units of XR-17 from Delhi to Pune Site 2",
                        ("propose_transfer", {"product": "XR-17", "quantity": 700, "source_location": "Delhi",
                                              "destination_location": "Pune Site 2"}))
        txn = c.runtime.active_transaction_id(sid)
        r = await user_turn(fake, "i2", "Actually I meant Mumbai.",
                            ("propose_transfer", {"product": "XR-17", "quantity": 700, "source_location": "Mumbai",
                                                  "destination_location": "Pune Site 2"}))
        assert c.runtime.active_transaction_id(sid) == txn  # amended, not replaced
        assert r["state"] == "CLARIFYING"
        view = c.runtime.view(txn)
        assert view["versions"][-1]["clarification"]["field"] == "role"
        await bridge.stop()
        server.close()
        c.engine.dispose()
        c.erp.engine.dispose()

    run(main())
