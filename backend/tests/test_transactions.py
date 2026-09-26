"""The MIRROR lifecycle through the deterministic (typed) path, plus the dangerous paths."""

import threading

import pytest
from sqlalchemy import text

from app.core.errors import ErrorCode, MirrorError
from app.erp.service import Fault
from app.intent.evidence import SourceType
from app.runtime.transaction_runtime import CommitEvidence


def current(view):
    return next(v for v in view["versions"] if v["version"] == view["current_version"])


def ui_commit(rt, view, key="k1", **kw):
    return rt.commit(
        view["id"],
        version=kw.get("version", view["current_version"]),
        token=kw.get("token", (view.get("commit") or {}).get("token", "")),
        idempotency_key=key,
        evidence=CommitEvidence(True, SourceType.USER_SELECTION, text_span="commit button"),
    )


def reach_preview_500(say):
    say("Move 700 units of XR-17 from Mumbai to the Pune site.")
    say("Site 2.")
    say("What if I move 900?")
    return say("What about 500?")


# ------------------------------------------------------------------ happy path


def test_ambiguous_command_forks_into_two_simulated_futures(say, container):
    r = say("Move 700 units of XR-17 from Mumbai to the Pune site.")
    view = r.transaction
    assert view["state"] == "CLARIFYING"
    v1 = current(view)
    outcomes = {b["intent"]["destination_id"]: b["outcome"] for b in v1["branches"]}
    assert outcomes == {"PUNE-SITE-1": "BLOCKED", "PUNE-SITE-2": "VALID"}
    assert r.reply == (
        "There are two Pune sites. Site 1 would exceed capacity by 400 units; Site 2 remains valid. Which one do you mean?"
    )
    assert v1["clarification"]["scores"][0]["field"] == "destination"
    assert view["real_world_changed"] is False and view["live_writes"] == 0
    assert v1["state_trail"] == ["INTERPRETING", "FORKING", "SIMULATING", "CONSEQUENCE_REVIEW", "CLARIFYING"]


def test_full_demo_sequence_commit_and_verify(say, container):
    say("Move 700 units of XR-17 from Mumbai to the Pune site.")
    r = say("Site 2.")
    assert r.transaction["state"] == "PREVIEW_READY"
    dest = current(r.transaction)["intent"]["drafts"][0]["destination"]
    assert dest["value"] == "PUNE-SITE-2" and dest["status"] in ("EXPLICIT", "RESOLVED")

    r = say("What if I move 900?")
    assert r.transaction["state"] == "CONSEQUENCE_REVIEW"
    assert r.reply == "Moving 900 would leave Mumbai 100 units below its safety-stock threshold. Nothing has changed."

    r = say("What about 500?")
    assert r.transaction["state"] == "PREVIEW_READY"

    r = say("Compare 500 and 900.")
    cols = {c["name"]: c for c in r.comparison["columns"]}
    assert (cols["500"]["source_after"], cols["500"]["safety_stock_ok"], cols["500"]["destination_after"], cols["500"]["outcome"]) == (1500, True, 800, "VALID")
    assert (cols["900"]["source_after"], cols["900"]["safety_stock_ok"], cols["900"]["destination_after"], cols["900"]["outcome"]) == (1100, False, 1200, "BLOCKED")
    assert container.erp.operations() == []  # nothing so far touched reality

    r = say("Commit 500.")
    view = r.transaction
    assert view["state"] == "VERIFIED" and view["reality"] == "VERIFIED"
    world = container.erp.read_snapshot()
    assert world.cell("MUMBAI-DC", "XR-17").on_hand == 1500
    assert world.cell("PUNE-SITE-2", "XR-17").on_hand == 800
    assert view["executions"][0]["report"]["match"] is True
    assert view["live_writes"] == 1


def test_optimizer_as_much_as_possible(say):
    say("Move 700 units of XR-17 from Mumbai to Pune Site 2")
    r = say("Move as much as possible without breaking safety stock.")
    v = current(r.transaction)
    assert v["kind"] == "OPTIMIZATION"
    assert v["branches"][0]["intent"]["quantity"] == 800
    assert r.reply.startswith("The maximum safe transfer is 800 units.")
    qty = v["intent"]["drafts"][0]["quantity"]
    assert qty["status"] == "RESOLVED" and qty["source"]["type"] == "RUNTIME_OPTIMIZER"


# ------------------------------------------------------------- adversarial paths


def test_commit_without_valid_preview_is_refused(say, container):
    say("Move 700 units of XR-17 from Mumbai to the Pune site.")
    r = say("Commit")
    assert r.error["code"] == "INVALID_TRANSACTION"
    assert container.erp.operations() == []


def test_commit_of_blocked_future_is_refused(say, container):
    say("Move 900 units of XR-17 from Mumbai to Pune Site 2")
    r = say("Commit 900")
    assert r.error["code"] == "INVALID_TRANSACTION"
    assert container.erp.operations() == []


def test_commit_quantity_must_match_preview(say, container):
    reach_preview_500(say)
    r = say("Commit 900.")
    assert r.error["code"] == "STALE_TRANSACTION"
    assert container.erp.operations() == []


def test_wait_make_that_500_supersedes_evidence(say):
    say("Move 700 units of XR-17 from Mumbai to Pune Site 2")
    r = say("Wait - make that 500.")
    view = r.transaction
    qty = [e for e in view["evidence"] if e["field"] == "quantity"]
    assert [(e["candidates"], e["lifecycle"]) for e in qty] == [([700], "SUPERSEDED"), ([500], "ACTIVE")]
    assert qty[1]["supersedes"] == qty[0]["evidence_id"]
    assert current(view)["branches"][0]["intent"]["quantity"] == 500


def test_actually_i_meant_mumbai_forks_on_role(say):
    say("Move 700 units of XR-17 from Delhi to Pune Site 2")
    r = say("Actually I meant Mumbai.")
    v = current(r.transaction)
    assert r.transaction["state"] == "CLARIFYING"
    assert v["clarification"]["field"] == "role"
    roles = {(b["intent"]["source_id"], b["intent"]["destination_id"]) for b in v["branches"]}
    assert roles == {("MUMBAI-DC", "PUNE-SITE-2"), ("DELHI-WAREHOUSE", "MUMBAI-DC")}
    r = say("as the source")
    assert r.transaction["state"] == "PREVIEW_READY"
    assert current(r.transaction)["branches"][0]["intent"]["source_id"] == "MUMBAI-DC"


def test_stale_version_token_is_rejected(rt, say, container):
    view = say("Move 700 units of XR-17 from Mumbai to Pune Site 2").transaction
    old_token, old_version = view["commit"]["token"], view["current_version"]
    say("Wait - make that 500.")
    with pytest.raises(MirrorError) as e:
        rt.commit(view["id"], version=old_version, token=old_token, idempotency_key="old",
                  evidence=CommitEvidence(True, SourceType.USER_SELECTION))
    assert e.value.code is ErrorCode.STALE_TRANSACTION
    fresh = rt.view(view["id"])
    with pytest.raises(MirrorError) as e:
        rt.commit(view["id"], version=fresh["current_version"], token=old_token, idempotency_key="old2",
                  evidence=CommitEvidence(True, SourceType.USER_SELECTION))
    assert e.value.code is ErrorCode.STALE_TRANSACTION  # token bound to the old version
    assert container.erp.operations() == []


def test_forged_token_is_rejected(rt, say, container):
    view = say("Move 500 units of XR-17 from Mumbai to Pune Site 2").transaction
    body, sig = view["commit"]["token"].split(".")
    with pytest.raises(MirrorError) as e:
        ui_commit(rt, view, token=body + "." + "0" * len(sig))
    assert e.value.code is ErrorCode.INVALID_COMMIT_TOKEN
    assert container.erp.operations() == []


def test_commit_requires_explicit_user_evidence(rt, say, container):
    view = say("Move 500 units of XR-17 from Mumbai to Pune Site 2").transaction
    with pytest.raises(MirrorError) as e:
        rt.commit(view["id"], version=view["current_version"], token=view["commit"]["token"], idempotency_key="x",
                  evidence=CommitEvidence(False, SourceType.MODEL_PROPOSAL))
    assert e.value.code is ErrorCode.INSUFFICIENT_EVIDENCE
    assert container.erp.operations() == []


def test_same_commit_twice_is_idempotent(rt, say, container):
    view = say("Move 500 units of XR-17 from Mumbai to Pune Site 2").transaction
    first = ui_commit(rt, view, key="same")
    second = ui_commit(rt, view, key="same")
    assert first["state"] == second["state"] == "VERIFIED"
    assert second["idempotent_replay"] is True
    assert len(container.erp.operations()) == 1
    assert container.erp.read_snapshot().cell("MUMBAI-DC", "XR-17").on_hand == 1500


def test_second_commit_with_new_key_is_duplicate(rt, say, container):
    view = say("Move 500 units of XR-17 from Mumbai to Pune Site 2").transaction
    ui_commit(rt, view, key="a")
    with pytest.raises(MirrorError) as e:
        ui_commit(rt, view, key="b")
    assert e.value.code is ErrorCode.DUPLICATE_COMMIT
    assert len(container.erp.operations()) == 1


def test_concurrent_double_click_executes_once(rt, say, container):
    view = say("Move 500 units of XR-17 from Mumbai to Pune Site 2").transaction
    results, errors = [], []

    def click(key):
        try:
            results.append(ui_commit(rt, view, key=key))
        except MirrorError as exc:
            errors.append(exc.code)

    threads = [threading.Thread(target=click, args=(f"k{i}",)) for i in range(6)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert len(results) == 1 and set(errors) == {ErrorCode.DUPLICATE_COMMIT}
    assert len(container.erp.operations()) == 1


def test_world_changed_after_preview_makes_commit_stale(rt, say, container):
    view = say("Move 700 units of XR-17 from Mumbai to Pune Site 2").transaction
    rt.external_change("MUMBAI-DC", "XR-17", -300, "clerk shipped 300 units to a customer")
    with pytest.raises(MirrorError) as e:
        ui_commit(rt, view)
    assert e.value.code is ErrorCode.STALE_TRANSACTION
    fresh = rt.view(view["id"])
    assert fresh["current_version"] == view["current_version"] + 1
    assert current(fresh)["kind"] == "RESIMULATION"
    # Mumbai now 1700; moving 700 leaves 1000 < 1200 safety stock: the refreshed future is blocked.
    assert fresh["state"] == "CONSEQUENCE_REVIEW"
    assert not [op for op in container.erp.operations() if op["actor"] == "MIRROR"]
    # The user was told before trying to commit: the preview was re-simulated the moment reality moved.
    alert = next(e for e in rt.audit(view["id"])["events"] if e["event_type"] == "REALITY_CHANGED")
    assert alert["payload"]["previous_status"] == "VALID" and alert["payload"]["new_status"] == "BLOCKED"
    assert alert["payload"]["say"].startswith("Heads up: reality changed under your preview.")


def test_amending_after_commit_is_refused(say):
    reach_preview_500(say)
    say("Commit 500.")
    r = say("What if I move 600?")
    assert r.error["code"] == "INVALID_TRANSACTION"


# -------------------------------------------------- verification / divergence


def test_fault_injection_diverges_and_is_never_verified(rt, say, container):
    reach_preview_500(say)
    rt.arm_fault(Fault.VERIFICATION_MISMATCH)
    r = say("Commit 500.")
    view = r.transaction
    assert view["state"] == "DIVERGED" and view["reality"] == "DIVERGED"
    ex = view["executions"][0]
    assert ex["erp_reported"]["applied_quantity"] == 500  # the executor claims success...
    focus = {f["location_id"]: (f["expected"], f["actual"]) for f in ex["report"]["focus"]}
    assert focus == {"MUMBAI-DC": (1500, 1550), "PUNE-SITE-2": (800, 750)}  # ...reality disagrees
    assert ex["verification"] == "DIVERGED"
    assert r.reply.startswith("Reality diverged.")


def test_rollback_after_divergence_restores_and_verifies(rt, say, container):
    reach_preview_500(say)
    rt.arm_fault(Fault.VERIFICATION_MISMATCH)
    say("Commit 500.")
    r = say("Rollback.")
    view = r.transaction
    assert view["state"] == "VERIFIED" and view["reality"] == "RESTORED"
    rb = view["executions"][-1]
    assert rb["kind"] == "ROLLBACK" and rb["verification"] == "VERIFIED"
    world = container.erp.read_snapshot()
    assert world.cell("MUMBAI-DC", "XR-17").on_hand == 2000
    assert world.cell("PUNE-SITE-2", "XR-17").on_hand == 300
    # Compensation used what actually happened (450), not the plan (500).
    rollback_op = container.erp.operations_for_transaction(view["id"])[-1]
    assert rollback_op["request"]["quantity"] == 450


def test_rollback_of_verified_commit(rt, say, container):
    reach_preview_500(say)
    say("Commit 500.")
    r = say("Roll back")
    assert r.transaction["reality"] == "RESTORED"
    assert container.erp.read_snapshot().cell("MUMBAI-DC", "XR-17").on_hand == 2000
    r = say("Roll back")
    assert r.error["code"] == "ROLLBACK_FAILED"  # never twice


def test_rollback_refuses_to_overwrite_later_changes(rt, say, container):
    reach_preview_500(say)
    say("Commit 500.")
    rt.external_change("PUNE-SITE-2", "XR-17", -100, "consumed on the line")
    r = say("Rollback")
    assert r.error["code"] == "ROLLBACK_FAILED"
    assert container.erp.read_snapshot().cell("PUNE-SITE-2", "XR-17").on_hand == 700


def test_temporary_erp_failure_retries_idempotently(rt, say, container):
    reach_preview_500(say)
    rt.arm_fault(Fault.TEMPORARY_ERP_FAILURE)
    view = say("Commit 500.").transaction
    assert view["state"] == "VERIFIED"
    assert view["executions"][0]["attempts"] == 2
    assert len(container.erp.operations()) == 1


def test_unknown_execution_result_is_resolved_by_reading_reality(rt, say, container):
    reach_preview_500(say)
    rt.arm_fault(Fault.UNKNOWN_RESULT)
    view = say("Commit 500.").transaction
    # The ERP applied the change but the response was lost; only verification can tell.
    assert view["state"] == "VERIFIED"
    audit_types = [e["event_type"] for e in rt.audit(view["id"])["events"]]
    completed = [e for e in rt.audit(view["id"])["events"] if e["event_type"] == "EXECUTION_COMPLETED"][0]
    assert completed["payload"]["execution_status"] == "UNKNOWN"
    assert audit_types.index("EXECUTION_COMPLETED") < audit_types.index("VERIFIED")


def test_duplicate_delivery_is_applied_once(rt, say, container):
    reach_preview_500(say)
    rt.arm_fault(Fault.DUPLICATE_COMMIT)
    view = say("Commit 500.").transaction
    assert view["state"] == "VERIFIED"
    assert len(container.erp.operations()) == 1
    types = [e["event_type"] for e in rt.audit(view["id"])["events"]]
    assert "DUPLICATE_DELIVERY_SUPPRESSED" in types


def test_abort_after_commit_is_refused(rt, say):
    reach_preview_500(say)
    view = say("Commit 500.").transaction
    with pytest.raises(MirrorError) as e:
        rt.abort(view["id"])
    assert e.value.code is ErrorCode.INVALID_TRANSACTION


# ------------------------------------------------------------------- audit


def test_audit_chain_verifies_and_detects_tampering(rt, say, container):
    reach_preview_500(say)
    say("Commit 500.")
    assert rt.verify_ledger()["valid"] is True
    with container.engine.begin() as conn:
        # An attacker with file access must first defeat the append-only trigger...
        conn.execute(text("DROP TRIGGER audit_events_no_update"))
        conn.execute(text("UPDATE audit_events SET payload_json = replace(payload_json, '500', '50') WHERE event_type='COMMIT_ACCEPTED'"))
    report = rt.verify_ledger()
    assert report["valid"] is False and "altered" in report["reason"]


def test_history_tables_are_append_only(rt, say, container):
    say("Move 700 units of XR-17 from Mumbai to the Pune site.")
    for stmt in (
        "UPDATE transaction_versions SET status='VALID'",
        "DELETE FROM evidence",
        "UPDATE audit_events SET event_type='X'",
        "DELETE FROM simulation_runs",
    ):
        with pytest.raises(Exception) as e:
            with container.engine.begin() as conn:
                conn.execute(text(stmt))
        assert "append-only" in str(e.value)


def test_every_lifecycle_step_is_audited(rt, say):
    view = reach_preview_500(say).transaction
    say("Commit 500.")
    types = [e["event_type"] for e in rt.audit(view["id"])["events"]]
    for expected in (
        "INTENT_CREATED", "BRANCH_CREATED", "SIMULATION_STARTED", "SIMULATION_COMPLETED", "CONSEQUENCES_UPDATED",
        "CLARIFICATION_REQUIRED", "WHAT_IF_STARTED", "PREVIEW_READY", "COMMIT_TOKEN_INVALIDATED", "COMMIT_REQUESTED",
        "COMMIT_ACCEPTED", "EXECUTION_STARTED", "EXECUTION_COMPLETED", "VERIFICATION_STARTED", "VERIFIED",
    ):
        assert expected in types, expected
