"""Public-demo guardrails (rate limits, voice caps) and spoken reality alerts."""

import asyncio
import json
from dataclasses import replace

import pytest
import websockets
from fastapi.testclient import TestClient

from app.core.container import build_container
from app.core.errors import MirrorError
from app.main import create_app
from app.voice.relay import VoiceRegistry
from test_voice_protocol import FakeAssemblyAI, run, user_turn


def test_session_creation_is_rate_limited(settings):
    from app.api.ratelimit import RULES

    limit = next(r[2] for r in RULES if r[1].pattern == r"^/api/v1/sessions$")
    app = create_app(replace(settings, rate_limit=True))
    with TestClient(app) as c:
        codes = [c.post("/api/v1/sessions").status_code for _ in range(limit + 1)]
        assert codes[:limit] == [200] * limit and codes[limit] == 429
        r = c.post("/api/v1/sessions")
        assert r.json()["error"]["code"] == "RATE_LIMITED" and int(r.headers["Retry-After"]) > 0
    app.state.container.dispose()


async def _registry_start(registry, fake, sid):
    fake.connected.clear()
    task = asyncio.create_task(registry.start(sid))
    await asyncio.wait_for(fake.connected.wait(), 5)
    await fake.expect("session.update")
    await fake.send(type="session.ready", session_id=f"sess_fake_{sid[-4:]}")
    return await task


def _env(settings, port, **kw):
    s = replace(settings, assemblyai_api_key="test-key", assemblyai_agent_url=f"ws://127.0.0.1:{port}", **kw)
    return build_container(s)


def test_voice_concurrency_cap(settings):
    async def main():
        fake = FakeAssemblyAI()
        server = await websockets.serve(fake.handler, "127.0.0.1", 0)
        c = _env(settings, server.sockets[0].getsockname()[1], voice_max_concurrent=1)
        reg = VoiceRegistry(c)
        a, _ = c.runtime.create_session()
        b, _ = c.runtime.create_session()
        await _registry_start(reg, fake, a)
        with pytest.raises(MirrorError) as e:
            await reg.start(b)
        assert e.value.details["reason"] == "voice_concurrency_limit"
        await reg.stop(a)
        server.close()
        c.dispose()

    run(main())


def test_voice_time_cap_stops_and_blocks_restart(settings):
    async def main():
        fake = FakeAssemblyAI()
        server = await websockets.serve(fake.handler, "127.0.0.1", 0)
        c = _env(settings, server.sockets[0].getsockname()[1], voice_max_session_seconds=6)
        reg = VoiceRegistry(c)
        sid, _ = c.runtime.create_session()
        events = c.bus.subscribe(sid)
        await _registry_start(reg, fake, sid)
        await asyncio.sleep(6.8)
        assert sid not in reg.bridges  # the watchdog ended it
        seen = []
        while not events.empty():
            seen.append(events.get_nowait())
        assert any(e["type"] == "ERROR" and e["data"].get("reason") == "voice_time_limit" for e in seen)
        with pytest.raises(MirrorError) as e:
            await reg.start(sid)
        assert e.value.details["reason"] == "voice_time_limit"
        server.close()
        c.dispose()

    run(main())


def test_reality_change_is_announced_with_reply_create(settings):
    async def main():
        fake = FakeAssemblyAI()
        server = await websockets.serve(fake.handler, "127.0.0.1", 0)
        c = _env(settings, server.sockets[0].getsockname()[1])
        reg = VoiceRegistry(c)
        sid, _ = c.runtime.create_session()
        await _registry_start(reg, fake, sid)
        await user_turn(fake, "i1", "Move 700 units of XR-17 from Mumbai to Pune Site 2",
                        ("propose_transfer", {"product": "XR-17", "quantity": 700, "source_location": "Mumbai",
                                              "destination_location": "Pune Site 2"}))
        # Reality moves from a worker thread, the way a sync HTTP route calls the runtime.
        await asyncio.to_thread(c.runtime.external_change, "MUMBAI-DC", "XR-17", -300, "another team shipped 300", sid)
        msg = await fake.expect("reply.create")
        assert "Heads up: reality changed under your preview" in msg["instructions"]
        assert "below its safety-stock threshold" in msg["instructions"]
        await reg.stop(sid)
        server.close()
        c.dispose()

    run(main())


def test_announcement_waits_for_the_agent_to_finish_its_reply(settings):
    async def main():
        fake = FakeAssemblyAI()
        server = await websockets.serve(fake.handler, "127.0.0.1", 0)
        c = _env(settings, server.sockets[0].getsockname()[1])
        reg = VoiceRegistry(c)
        sid, _ = c.runtime.create_session()
        bridge = await _registry_start(reg, fake, sid)
        await fake.send(type="reply.started", reply_id="r1")
        await asyncio.sleep(0.2)
        await bridge.announce("Heads up: test")
        assert await fake.nothing("reply.create", wait=0.5)  # never talks over itself
        await fake.send(type="reply.done", reply_id="r1", status="completed")
        msg = await fake.expect("reply.create")
        assert msg["instructions"].endswith("Heads up: test")
        await reg.stop(sid)
        server.close()
        c.dispose()

    run(main())


def test_client_ip_is_the_one_the_proxy_saw():
    from app.api.ratelimit import client_ip

    spoofed = {"x-forwarded-for": "1.2.3.4, 203.0.113.9"}  # client wrote 1.2.3.4; the proxy appended the real peer
    assert client_ip(spoofed, "10.0.0.1", hops=1) == "203.0.113.9"
    assert client_ip(spoofed, "10.0.0.1", hops=0) == "10.0.0.1"
    assert client_ip({}, "10.0.0.1", hops=1) == "10.0.0.1"
    assert client_ip({"x-forwarded-for": "203.0.113.9"}, None, hops=2) == "203.0.113.9"


def test_frame_ancestors_are_configurable(settings):
    app = create_app(replace(settings, frame_ancestors="https://huggingface.co"))
    with TestClient(app) as c:
        r = c.get("/api/v1/health")
        assert "frame-ancestors https://huggingface.co" in r.headers["content-security-policy"]
        assert "x-frame-options" not in r.headers
    app.state.container.dispose()
    app = create_app(settings)
    with TestClient(app) as c:
        r = c.get("/api/v1/health")
        assert "frame-ancestors 'none'" in r.headers["content-security-policy"] and r.headers["x-frame-options"] == "DENY"
    app.state.container.dispose()
