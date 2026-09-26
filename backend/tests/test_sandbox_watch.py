"""Per-visitor sandboxes, the reality watch, and quantity-position evidence."""

from dataclasses import replace

import pytest

from app.core.container import build_container
from app.erp.service import Fault
from app.intent.compiler import compile_utterance
from app.intent.entities import EntityResolver
from app.intent.evidence import EvidenceStatus, Utterance, check_model_value


@pytest.fixture()
def sandboxed(settings):
    c = build_container(replace(settings, sandbox_per_session=True))
    yield c
    c.dispose()


def cell(c, session_id, loc="MUMBAI-DC", prod="XR-17"):
    return c.runtime.erp_for(session_id).read_snapshot().cell(loc, prod).on_hand


# ------------------------------------------------------------------ sandboxes


def test_each_visitor_gets_an_isolated_world(sandboxed):
    c = sandboxed
    a, _ = c.runtime.create_session()
    b, _ = c.runtime.create_session()
    c.dispatcher.handle(a, "Move 500 units of XR-17 from Mumbai to Pune Site 2")
    r = c.dispatcher.handle(a, "Commit 500")
    assert r.transaction["state"] == "VERIFIED"
    assert cell(c, a) == 1500
    assert cell(c, b) == 2000  # B's world never moved
    assert cell(c, None) == 2000  # nor did the shared default world


def test_reset_and_faults_stay_inside_one_sandbox(sandboxed):
    c = sandboxed
    a, _ = c.runtime.create_session()
    b, _ = c.runtime.create_session()
    c.runtime.arm_fault(Fault.VERIFICATION_MISMATCH, a)
    assert c.runtime.erp_for(a).armed_fault() is Fault.VERIFICATION_MISMATCH
    assert c.runtime.erp_for(b).armed_fault() is Fault.NONE

    c.dispatcher.handle(b, "Move 500 units of XR-17 from Mumbai to Pune Site 2")
    r = c.dispatcher.handle(b, "Commit 500")
    assert r.transaction["state"] == "VERIFIED"  # A's armed fault did not hit B

    c.runtime.external_change("MUMBAI-DC", "XR-17", -300, "test", a)
    c.runtime.reset_demo(a)
    assert cell(c, a) == 2000
    assert cell(c, b) == 1500  # A's reset did not undo B's commit
    assert c.runtime.active_transaction_id(b) is not None


def test_sandbox_ids_cannot_escape_the_worlds_directory(sandboxed):
    with pytest.raises(ValueError):
        sandboxed.worlds.for_session("../../etc/passwd")


def test_sandbox_api_requires_session_for_demo_controls(settings):
    from fastapi.testclient import TestClient

    from app.main import create_app

    app = create_app(replace(settings, sandbox_per_session=True))
    with TestClient(app) as client:
        assert client.post("/api/v1/demo/reset").status_code == 401
        s = client.post("/api/v1/sessions").json()
        h = {"X-Session-Id": s["session_id"], "Authorization": f"Bearer {s['session_token']}"}
        assert client.post("/api/v1/demo/faults", json={"fault": "verification_mismatch"}, headers=h).status_code == 200
        world = client.get("/api/v1/world", headers=h).json()
        assert world["sandbox"] is True and world["armed_fault"] == "verification_mismatch"
        assert client.get("/api/v1/world").json()["armed_fault"] == "none"  # the shared world is untouched
    app.state.container.dispose()


# -------------------------------------------------------------- reality watch


def test_external_change_re_simulates_the_open_preview_and_says_so(container, session):
    rt = container.runtime
    heard = []
    rt.on_reality_changed = lambda sid, say: heard.append((sid, say))
    view = container.dispatcher.handle(session, "Move 700 units of XR-17 from Mumbai to Pune Site 2").transaction
    assert view["state"] == "PREVIEW_READY"
    rt.external_change("MUMBAI-DC", "XR-17", -300, "another team shipped 300", session)
    fresh = rt.view(view["id"])
    assert fresh["current_version"] == view["current_version"] + 1
    assert fresh["state"] == "CONSEQUENCE_REVIEW"  # 1700 - 700 = 1000 < 1200 safety stock
    assert heard and heard[0][0] == session
    assert "Mumbai Distribution Center XR-17 went from 2000 to 1700" in heard[0][1]
    assert "below its safety-stock threshold" in heard[0][1]


def test_unrelated_changes_do_not_touch_the_preview(container, session):
    rt = container.runtime
    view = container.dispatcher.handle(session, "Move 700 units of XR-17 from Mumbai to Pune Site 2").transaction
    rt.external_change("DELHI-WAREHOUSE", "CN-220", -100, "unrelated", session)
    assert rt.view(view["id"])["current_version"] == view["current_version"]


def test_one_visitors_commit_alerts_another_visitors_preview_in_a_shared_world(container):
    rt, d = container.runtime, container.dispatcher
    a, _ = rt.create_session()
    b, _ = rt.create_session()
    heard = []
    rt.on_reality_changed = lambda sid, say: heard.append(sid)
    preview_a = d.handle(a, "Move 700 units of XR-17 from Mumbai to Pune Site 2").transaction
    d.handle(b, "Move 500 units of XR-17 from Mumbai to Pune Site 2")
    assert d.handle(b, "Commit 500").transaction["state"] == "VERIFIED"
    fresh = rt.view(preview_a["id"])
    assert heard == [a]
    assert fresh["state"] == "CONSEQUENCE_REVIEW"  # Mumbai is now 1500; 700 more would breach safety stock
    # A's old authorization is useless; the commit gate refuses it.
    r = d.handle(a, "Commit 700")
    assert r.error is not None and container.erp.read_snapshot().cell("MUMBAI-DC", "XR-17").on_hand == 1500


# ---------------------------------------------------------- quantity position


def resolver(c):
    return EntityResolver(c.erp.read_snapshot())


def test_numbers_not_said_as_amounts_are_not_quantities(container):
    r = resolver(container)
    cmd = compile_utterance("It's been 2 weeks, move 700 units of XR-17 from Mumbai to Pune Site 2", r)
    assert cmd.quantity.value == 700
    cmd = compile_utterance("Move XR-17 from Mumbai to Pune Site 2 at 3", r)
    assert cmd.quantity is None  # MIRROR will ask how many, not guess 3


def test_model_cannot_launder_an_unrelated_number_into_a_quantity(container):
    r = resolver(container)
    utts = [Utterance("i1", "It's been 2 weeks, move XR-17 from Mumbai to Pune Site 2", "VOICE")]
    ev = check_model_value("quantity", 2, utts, r)
    assert ev.status is EvidenceStatus.INFERRED and "not as the amount" in ev.note
    utts = [Utterance("i1", "move 700 units of XR-17, it has been 2 weeks", "VOICE")]
    assert check_model_value("quantity", 700, utts, r).status is EvidenceStatus.EXPLICIT


@pytest.mark.parametrize("text,expected", [
    ("Compare 500 and 900.", [500, 900]),
    ("What about 500?", [500]),
    ("Wait, make that 500.", [500]),
    ("Commit 500.", [500]),
    ("what if I move nine hundred", [900]),
    ("500", [500]),
    ("Seven hundred units please", [700]),
    ("Set it to 450", [450]),
])
def test_amount_phrasings_are_recognised(container, text, expected):
    assert [q.value for q in compile_utterance(text, resolver(container)).quantities] == expected
