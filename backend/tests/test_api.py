"""HTTP + WebSocket API: acceptance test 1 (structured, no voice), auth, idempotency, demo controls."""

import json
import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import create_app


@pytest.fixture()
def client(settings):
    app = create_app(settings)
    with TestClient(app) as c:
        yield c
    app.state.container.engine.dispose()
    app.state.container.erp.engine.dispose()


@pytest.fixture()
def auth(client):
    s = client.post("/api/v1/sessions").json()
    return s["session_id"], {"X-Session-Id": s["session_id"], "Authorization": f"Bearer {s['session_token']}"}


def test_health(client):
    body = client.get("/api/v1/health").json()
    assert body["status"] == "ok" and body["ledger"]["valid"] is True


def test_auth_required(client, auth):
    assert client.post("/api/v1/transactions", json={"action": {}}).status_code == 401
    sid, headers = auth
    bad = {**headers, "Authorization": "Bearer nope"}
    r = client.post("/api/v1/transactions", json={"action": {"quantity": 5}}, headers=bad)
    assert r.status_code == 401 and r.json()["error"]["code"] == "UNAUTHORIZED"


def test_other_session_cannot_see_or_commit_transaction(client, auth):
    _, headers = auth
    txn = client.post("/api/v1/transactions", headers=headers, json={
        "action": {"product": "XR-17", "quantity": 500, "source": "MUMBAI-DC", "destination": "PUNE-SITE-2"}}).json()
    other = client.post("/api/v1/sessions").json()
    other_h = {"X-Session-Id": other["session_id"], "Authorization": f"Bearer {other['session_token']}"}
    assert client.get(f"/api/v1/transactions/{txn['id']}", headers=other_h).status_code == 404
    r = client.post(f"/api/v1/transactions/{txn['id']}/commit", headers=other_h, json={
        "version": txn["current_version"], "commit_token": txn["commit"]["token"], "idempotency_key": "x" * 16})
    assert r.status_code == 404


def test_acceptance_1_structured_flow_without_voice(client, auth):
    _, h = auth
    # structured action -> branches -> simulation -> comparison -> clarification -> preview
    txn = client.post("/api/v1/transactions", headers=h, json={
        "action": {"product": "XR-17", "quantity": 700, "source": "Mumbai", "destination": "Pune"},
        "utterance": "Move 700 units of XR-17 from Mumbai to the Pune site."}).json()
    assert txn["state"] == "CLARIFYING"
    branches = txn["versions"][-1]["branches"]
    assert {b["intent"]["destination_id"]: b["outcome"] for b in branches} == {"PUNE-SITE-1": "BLOCKED", "PUNE-SITE-2": "VALID"}
    tid = txn["id"]

    b_key = next(b["key"] for b in branches if b["intent"]["destination_id"] == "PUNE-SITE-2")
    txn = client.post(f"/api/v1/transactions/{tid}/clarify", headers=h, json={"branch_key": b_key}).json()
    assert txn["state"] == "PREVIEW_READY"

    txn = client.post(f"/api/v1/transactions/{tid}/what-if", headers=h, json={"quantity": 900}).json()
    assert txn["state"] == "CONSEQUENCE_REVIEW" and txn["real_world_changed"] is False

    cmp = client.post(f"/api/v1/transactions/{tid}/compare", headers=h, json={"quantities": [500, 900]}).json()
    assert [c["outcome"] for c in cmp["columns"]] == ["VALID", "BLOCKED"]

    txn = client.post(f"/api/v1/transactions/{tid}/what-if", headers=h, json={"quantity": 500}).json()
    assert txn["state"] == "PREVIEW_READY"
    world_before = client.get("/api/v1/world").json()

    # -> commit -> ERP mutation -> verification
    key = str(uuid.uuid4())
    body = {"version": txn["current_version"], "commit_token": txn["commit"]["token"], "idempotency_key": key}
    done = client.post(f"/api/v1/transactions/{tid}/commit", headers=h, json=body).json()
    assert done["state"] == "VERIFIED" and done["reality"] == "VERIFIED"
    world = client.get("/api/v1/world").json()
    cells = {(c["location_id"], c["product_id"]): c["on_hand"] for c in world["cells"]}
    assert cells[("MUMBAI-DC", "XR-17")] == 1500 and cells[("PUNE-SITE-2", "XR-17")] == 800
    assert world["revision"] == world_before["revision"] + 1

    # same key -> existing result, not a second execution
    again = client.post(f"/api/v1/transactions/{tid}/commit", headers=h, json=body).json()
    assert again["idempotent_replay"] is True
    assert len([op for op in client.get("/api/v1/world").json()["recent_operations"] if op["actor"] == "MIRROR"]) == 1

    audit = client.get(f"/api/v1/transactions/{tid}/audit", headers=h).json()
    assert audit["chain"]["valid"] is True
    assert "VERIFIED" in [e["event_type"] for e in audit["events"]]


def test_error_contract_and_validation(client, auth):
    _, h = auth
    r = client.post("/api/v1/transactions", headers=h, json={"action": {"quantity": -3}})
    assert r.status_code == 400 and r.json()["error"]["code"] == "INVALID_REQUEST"
    r = client.post("/api/v1/transactions", headers=h, json={"action": {}, "sql": "DROP TABLE"})
    assert r.status_code == 400  # unknown fields are rejected
    r = client.get("/api/v1/transactions/txn_missing", headers=h)
    assert r.status_code == 404 and r.json()["error"]["code"] == "NOT_FOUND"


def test_turns_endpoint_and_websocket_events(client, auth):
    sid, h = auth
    token = h["Authorization"].split(" ")[1]
    with client.websocket_connect(f"/ws/v1/sessions/{sid}") as ws:
        ws.send_text(json.dumps({"type": "auth", "token": token}))
        assert json.loads(ws.receive_text())["type"] == "auth.ok"
        r = client.post(f"/api/v1/sessions/{sid}/turns", headers=h, json={"text": "Move 700 units of XR-17 from Mumbai to the Pune site."})
        assert r.json()["reply"].startswith("There are two Pune sites.")
        seen = []
        while "CLARIFICATION_REQUIRED" not in seen:
            e = json.loads(ws.receive_text())
            for k in ("version", "event_id", "sequence", "timestamp", "session_id", "type", "data"):
                assert k in e
            seen.append(e["type"])
        assert {"USER_TURN", "INTENT_CREATED", "BRANCH_CREATED", "SIMULATION_COMPLETED"} <= set(seen)


def test_websocket_rejects_bad_token(client, auth):
    sid, _ = auth
    from starlette.websockets import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(f"/ws/v1/sessions/{sid}") as ws:
            ws.send_text(json.dumps({"type": "auth", "token": "wrong"}))
            ws.receive_text()
    with pytest.raises(WebSocketDisconnect):  # a token in the URL is not accepted as authentication
        with client.websocket_connect(f"/ws/v1/sessions/{sid}?token=anything") as ws:
            ws.send_text(json.dumps({"type": "ping"}))
            ws.receive_text()


def test_voice_start_without_key_reports_unavailable(client, auth):
    sid, h = auth
    token = h["Authorization"].split(" ")[1]
    with client.websocket_connect(f"/ws/v1/sessions/{sid}") as ws:
        ws.send_text(json.dumps({"type": "auth", "token": token}))
        ws.send_text(json.dumps({"type": "voice.start"}))
        while True:
            e = json.loads(ws.receive_text())
            if e["type"] == "ERROR":
                assert e["data"]["code"] == "ASSEMBLYAI_UNAVAILABLE"
                break


def test_demo_fault_divergence_and_rollback_via_api(client, auth):
    sid, h = auth
    client.post("/api/v1/demo/reset")
    turn = lambda text: client.post(f"/api/v1/sessions/{sid}/turns", headers=h, json={"text": text}).json()
    turn("Move 500 units of XR-17 from Mumbai to Pune Site 2")
    assert client.post("/api/v1/demo/faults", json={"fault": "verification_mismatch"}).json()["armed_fault"] == "verification_mismatch"
    r = turn("Commit 500")
    assert r["transaction"]["state"] == "DIVERGED"
    r = turn("Rollback")
    assert r["transaction"]["state"] == "VERIFIED" and r["transaction"]["reality"] == "RESTORED"
    cells = {(c["location_id"], c["product_id"]): c["on_hand"] for c in client.get("/api/v1/world").json()["cells"]}
    assert cells[("MUMBAI-DC", "XR-17")] == 2000 and cells[("PUNE-SITE-2", "XR-17")] == 300


def test_demo_reset_is_repeatable(client, auth):
    sid, h = auth
    for _ in range(3):
        client.post("/api/v1/demo/reset")
        r = client.post(f"/api/v1/sessions/{sid}/turns", headers=h, json={"text": "Move 500 units of XR-17 from Mumbai to Pune Site 2"}).json()
        r = client.post(f"/api/v1/sessions/{sid}/turns", headers=h, json={"text": "Commit 500"}).json()
        assert r["transaction"]["state"] == "VERIFIED"
    cells = {(c["location_id"], c["product_id"]): c["on_hand"] for c in client.get("/api/v1/world").json()["cells"]}
    assert cells[("MUMBAI-DC", "XR-17")] == 1500


def test_demo_controls_disabled_outside_demo_mode(settings):
    from dataclasses import replace

    app = create_app(replace(settings, demo_mode=False))
    with TestClient(app) as c:
        r = c.post("/api/v1/demo/reset")
        assert r.status_code == 403 and r.json()["error"]["code"] == "DEMO_DISABLED"
    app.state.container.engine.dispose()
    app.state.container.erp.engine.dispose()


def test_built_ui_is_served_with_spa_fallback(settings, tmp_path):
    from dataclasses import replace

    web = tmp_path / "web"
    (web / "assets").mkdir(parents=True)
    (web / "replay").mkdir()
    (web / "index.html").write_text("<!doctype html><title>MIRROR</title>")
    (web / "assets" / "app.js").write_text("console.log(1)")
    (web / "replay" / "manifest.json").write_text('{"steps": []}')
    (tmp_path / "secret.txt").write_text("outside the web root")
    app = create_app(replace(settings, static_dir=web))
    with TestClient(app) as c:
        assert "<title>MIRROR</title>" in c.get("/").text
        assert c.head("/").status_code == 200  # link previews and uptime checks use HEAD
        assert c.get("/replay/manifest.json").json() == {"steps": []}
        assert c.get("/assets/app.js").status_code == 200
        assert "<title>MIRROR</title>" in c.get("/some/client/route").text
        missing = c.get("/api/v1/nope")
        assert missing.status_code == 404 and missing.json()["error"]["code"] == "NOT_FOUND"
        assert "outside the web root" not in c.get("/..%2Fsecret.txt").text
    app.state.container.dispose()
