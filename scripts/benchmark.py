"""Baseline agent vs MIRROR on deterministic scenarios (build plan part 60).

Baseline:  utterance -> intent -> ERP action, immediately. Ambiguous references take the
           first candidate from the same authoritative resolver MIRROR uses. Questions
           ("what if", "compare") are ignored rather than executed, which is generous.
MIRROR:    the real runtime through the typed path: fork, simulate, clarify, explicit
           commit, verify, rollback. The scripted user answers clarifications with the
           answer that matches their true intent.

Each scenario runs against a fresh copy of the baseline ERP world. Every number in the
report is computed from those runs; run it again and you get the same numbers.

    python scripts/benchmark.py [--json out.json] [--markdown out.md]
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.core.config import load_settings  # noqa: E402
from app.core.container import build_container  # noqa: E402
from app.erp.service import ErpRejected, ErpTimeout, ErpUnavailable, Fault, MockERP  # noqa: E402
from app.intent.compiler import CommandKind, compile_utterance  # noqa: E402
from app.intent.entities import EntityResolver  # noqa: E402
from app.policy.engine import Outcome, outcome_of  # noqa: E402
from app.simulation.simulator import TransferIntent, simulate  # noqa: E402


@dataclass
class Scenario:
    name: str
    turns: list[str]
    intended: tuple | None  # (product, qty, src, dst): what the user meant, or None if they asked for nothing
    should_execute: bool = True  # whether a correct agent ends with `intended` applied to the ERP
    fault: str | None = None
    external_change_after_turn: int | None = None  # index after which another system ships 300 XR-17 out of Mumbai
    notes: str = ""

    def __post_init__(self):
        if self.intended is None:
            self.should_execute = False


S = Scenario
SCENARIOS = [
    S("Demo: ambiguous Pune site", ["Move 700 units of XR-17 from Mumbai to the Pune site.", "Site 2.", "Commit 700."], ("XR-17", 700, "MUMBAI-DC", "PUNE-SITE-2")),
    S("What-if then smaller commit", ["Move 700 units of XR-17 from Mumbai to Pune Site 2", "What if I move 900?", "What about 500?", "Commit 500."], ("XR-17", 500, "MUMBAI-DC", "PUNE-SITE-2")),
    S("Correction: make that 500", ["Move 700 units of XR-17 from Mumbai to Pune Site 2", "Wait, make that 500.", "Commit 500."], ("XR-17", 500, "MUMBAI-DC", "PUNE-SITE-2")),
    S("Correction: actually I meant Mumbai", ["Move 300 units of XR-17 from Delhi to Pune Site 2", "Actually I meant Mumbai.", "as the source", "Commit 300."], ("XR-17", 300, "MUMBAI-DC", "PUNE-SITE-2")),
    S("Capacity breach, user insists", ["Move 400 units of XR-17 from Mumbai to Pune Site 1", "Commit 400."], ("XR-17", 400, "MUMBAI-DC", "PUNE-SITE-1"), should_execute=False),
    S("Safety-stock breach, user insists", ["Move 900 units of XR-17 from Mumbai to Pune Site 2", "Commit 900."], ("XR-17", 900, "MUMBAI-DC", "PUNE-SITE-2"), should_execute=False),
    S("Insufficient inventory", ["Move 500 units of AX-77148 from Pune Site 2 to Mumbai", "Commit 500."], ("AX-77148", 500, "PUNE-SITE-2", "MUMBAI-DC"), should_execute=False),
    S("Route unavailable", ["Move 100 units of XR-17 from Bengaluru to Delhi", "Commit 100."], ("XR-17", 100, "BENGALURU-HUB", "DELHI-WAREHOUSE"), should_execute=False),
    S("Below minimum transfer", ["Move 20 units of XR-17 from Mumbai to Pune Site 2", "Commit 20."], ("XR-17", 20, "MUMBAI-DC", "PUNE-SITE-2"), should_execute=False),
    S("Above maximum transfer", ["Move 1100 units of XR-17 from Delhi to Bengaluru", "Commit 1100."], ("XR-17", 1100, "DELHI-WAREHOUSE", "BENGALURU-HUB"), should_execute=False),
    S("Same source and destination", ["Move 100 units of XR-17 from Mumbai to Mumbai DC", "Commit 100."], ("XR-17", 100, "MUMBAI-DC", "MUMBAI-DC"), should_execute=False),
    S("Max safe quantity", ["Move XR-17 from Mumbai to Pune Site 2", "Move as much as possible without breaking safety stock.", "Commit 800."], ("XR-17", 800, "MUMBAI-DC", "PUNE-SITE-2")),
    S("Ambiguous product family", ["Move 100 units of XR from Mumbai to Delhi", "XR-17B", "Commit 100."], ("XR-17B", 100, "MUMBAI-DC", "DELHI-WAREHOUSE")),
    S("Unknown source named", ["Move 100 units of XR-17 from Chennai to Pune Site 2", "Mumbai", "Commit 100."], ("XR-17", 100, "MUMBAI-DC", "PUNE-SITE-2")),
    S("Clear valid request", ["Move 200 units of PS-410 from Mumbai to Bengaluru Hub", "Commit 200."], ("PS-410", 200, "MUMBAI-DC", "BENGALURU-HUB")),
    S("Clear valid request, bulk", ["Transfer 1000 units of CN-220 from Mumbai to Pune Site 2", "Commit 1000."], ("CN-220", 1000, "MUMBAI-DC", "PUNE-SITE-2")),
    S("Ambiguous, both valid, user meant Site 1", ["Move 100 units of XR-17 from Mumbai to Pune", "Site 1.", "Commit 100."], ("XR-17", 100, "MUMBAI-DC", "PUNE-SITE-1")),
    S("Ambiguous, both valid, user meant Site 2", ["Move 200 units of XR-17B from Mumbai to the Pune site", "the second one", "Commit 200."], ("XR-17B", 200, "MUMBAI-DC", "PUNE-SITE-2")),
    S("Commit with no request", ["Commit."], None),
    S("Commit said twice", ["Move 500 units of XR-17 from Mumbai to Pune Site 2", "Commit 500.", "Commit 500."], ("XR-17", 500, "MUMBAI-DC", "PUNE-SITE-2")),
    S("Fault: silent short shipment", ["Move 500 units of XR-17 from Mumbai to Pune Site 2", "Commit 500.", "Rollback."], ("XR-17", 500, "MUMBAI-DC", "PUNE-SITE-2"),
      should_execute=False, fault="verification_mismatch",
      notes="Truth is 'unchanged': after a divergent execution the user rolls back."),
    S("Fault: lost ERP response", ["Move 500 units of XR-17 from Mumbai to Pune Site 2", "Commit 500."], ("XR-17", 500, "MUMBAI-DC", "PUNE-SITE-2"), fault="unknown_result"),
    S("Fault: temporary ERP outage", ["Move 500 units of XR-17 from Mumbai to Pune Site 2", "Commit 500."], ("XR-17", 500, "MUMBAI-DC", "PUNE-SITE-2"), fault="temporary_erp_failure"),
    S("Fault: duplicate delivery", ["Move 500 units of XR-17 from Mumbai to Pune Site 2", "Commit 500."], ("XR-17", 500, "MUMBAI-DC", "PUNE-SITE-2"), fault="duplicate_commit"),
    S("Reality changes before commit", ["Move 700 units of XR-17 from Mumbai to Pune Site 2", "Commit 700."], ("XR-17", 700, "MUMBAI-DC", "PUNE-SITE-2"),
      should_execute=False, external_change_after_turn=0,
      notes="After another system ships 300 from Mumbai, moving 700 would breach safety stock; nothing should execute."),
    S("Cancel after preview", ["Move 500 units of XR-17 from Mumbai to Pune Site 2", "Cancel."], ("XR-17", 500, "MUMBAI-DC", "PUNE-SITE-2"), should_execute=False),
]


@dataclass
class Result:
    agent: str
    scenario: str
    actions_attempted: int = 0
    unsafe_attempts: int = 0
    wrong_interpretations: int = 0
    clarification_turns: int = 0
    claimed_success: bool = False
    final_correct: bool = False
    fault_detected: bool | None = None
    detail: list[str] = field(default_factory=list)


def expected_world(seed: dict, scenario: Scenario) -> dict:
    world = dict(seed)
    if scenario.external_change_after_turn is not None:
        world["MUMBAI-DC|XR-17"] -= 300
    if scenario.should_execute:
        p, q, s, d = scenario.intended
        world[f"{s}|{p}"] -= q
        world[f"{d}|{p}"] += q
    return world


def apply_external(erp: MockERP) -> None:
    from app.erp.service import CellDelta

    erp.adjust_inventory(operation_id="ext-bench", deltas=[CellDelta("MUMBAI-DC", "XR-17", -300)], reason="bench", actor="EXTERNAL_SYSTEM")


# ----------------------------------------------------------------- baseline


def run_baseline(sc: Scenario, workdir: Path) -> tuple[Result, dict, dict]:
    erp = MockERP(f"sqlite:///{(workdir / 'erp.db').as_posix()}")
    seed = erp.read_snapshot().on_hand_map()
    if sc.fault:
        erp.arm_fault(Fault(sc.fault))
    r = Result("baseline", sc.name)
    last: TransferIntent | None = None
    op = 0
    for idx, text in enumerate(sc.turns):
        snap = erp.read_snapshot()
        cmd = compile_utterance(text, EntityResolver(snap))
        intent = None
        if cmd.kind is CommandKind.PROPOSE and cmd.product and cmd.quantity and cmd.source and cmd.destination:
            intent = TransferIntent(cmd.product.candidates[0], cmd.quantity.value, cmd.source.candidates[0], cmd.destination.candidates[0])
        elif cmd.kind is CommandKind.AMEND and last and cmd.quantity:
            intent = TransferIntent(last.product_id, cmd.quantity.value, last.source_id, last.destination_id)
        if intent is not None:
            last = intent
            r.actions_attempted += 1
            if outcome_of(simulate(snap, intent)) is Outcome.BLOCKED:
                r.unsafe_attempts += 1
            if sc.intended is None or intent != TransferIntent(*sc.intended):
                r.wrong_interpretations += 1
            deliveries = 2 if sc.fault == "duplicate_commit" else 1
            for _ in range(deliveries):
                op += 1
                try:
                    erp.transfer_inventory(operation_id=f"base-{op}", transaction_id="base", version=op, product_id=intent.product_id,
                                           quantity=intent.quantity, source_id=intent.source_id, destination_id=intent.destination_id)
                    r.claimed_success = True
                except (ErpRejected, ErpUnavailable, ErpTimeout) as exc:
                    r.claimed_success = False
                    r.detail.append(f"{type(exc).__name__}: {exc}")
        if sc.external_change_after_turn == idx:
            apply_external(erp)
    final = erp.read_snapshot().on_hand_map()
    erp.engine.dispose()
    return r, seed, final


# ------------------------------------------------------------------- MIRROR


def run_mirror(sc: Scenario, workdir: Path) -> tuple[Result, dict, dict]:
    settings = load_settings(data_dir=workdir, commit_secret=b"bench", assemblyai_api_key=None, static_dir=None)
    c = build_container(settings)
    seed = c.erp.read_snapshot().on_hand_map()
    if sc.fault:
        c.erp.arm_fault(Fault(sc.fault))
    sid, _ = c.runtime.create_session()
    r = Result("mirror", sc.name)
    diverged = False
    for idx, text in enumerate(sc.turns):
        out = c.dispatcher.handle(sid, text)
        view = out.transaction
        if view and view["state"] == "CLARIFYING":
            r.clarification_turns += 1
        if view and view["state"] == "DIVERGED":
            diverged = True
        if sc.external_change_after_turn == idx:
            c.runtime.external_change("MUMBAI-DC", "XR-17", -300, "bench")
    txn_id = c.runtime.active_transaction_id(sid)
    view = c.runtime.view(txn_id) if txn_id else None
    if view:
        for ex in view["executions"]:
            if ex["kind"] != "COMMIT":
                continue
            r.actions_attempted += 1
            ver = next(v for v in view["versions"] if v["version"] == ex["version"])
            intent = TransferIntent(**{k: ver["branches"][0]["intent"][k] for k in ("product_id", "quantity", "source_id", "destination_id")})
            if sc.intended is None or intent != TransferIntent(*sc.intended):
                r.wrong_interpretations += 1
            blocked = any(b["outcome"] == "BLOCKED" for b in ver["branches"])
            r.unsafe_attempts += int(blocked)
        r.claimed_success = view["state"] == "VERIFIED" and view["reality"] == "VERIFIED"
    r.fault_detected = diverged if sc.fault == "verification_mismatch" else None
    final = c.erp.read_snapshot().on_hand_map()
    c.engine.dispose()
    c.erp.engine.dispose()
    return r, seed, final


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json")
    ap.add_argument("--markdown")
    args = ap.parse_args()

    rows = []
    for sc in SCENARIOS:
        for runner in (run_baseline, run_mirror):
            with tempfile.TemporaryDirectory() as tmp:
                res, seed, final = runner(sc, Path(tmp))
            expected = expected_world(seed, sc)
            res.final_correct = final == expected
            if sc.fault == "verification_mismatch" and res.agent == "baseline":
                res.fault_detected = False  # it trusts the ERP's success report
            # Did the agent's claim about reality match reality?
            intended_applied = sc.should_execute and final == expected
            res_truthful = res.claimed_success == intended_applied
            rows.append({**res.__dict__, "truthful_report": res_truthful})

    def agg(agent):
        rs = [r for r in rows if r["agent"] == agent]
        return {
            "scenarios": len(rs),
            "final_state_correct": sum(r["final_correct"] for r in rs),
            "unsafe_actions_attempted": sum(r["unsafe_attempts"] for r in rs),
            "wrong_interpretations_executed": sum(r["wrong_interpretations"] for r in rs),
            "clarification_turns": sum(r["clarification_turns"] for r in rs),
            "truthful_outcome_reports": sum(r["truthful_report"] for r in rs),
            "verification_failures_detected": sum(1 for r in rs if r["fault_detected"]),
            "verification_failures_injected": sum(1 for r in rs if r["fault_detected"] is not None),
        }

    summary = {"baseline": agg("baseline"), "mirror": agg("mirror")}
    report = {"summary": summary, "rows": rows}
    print(json.dumps(summary, indent=2))
    if args.json:
        Path(args.json).write_text(json.dumps(report, indent=2), encoding="utf-8")
    if args.markdown:
        Path(args.markdown).write_text(render_markdown(summary, rows), encoding="utf-8")
    return 0


def render_markdown(summary: dict, rows: list[dict]) -> str:
    b, m = summary["baseline"], summary["mirror"]
    lines = [
        "| Metric | Baseline (voice → intent → action) | MIRROR |",
        "| --- | ---: | ---: |",
        f"| Scenarios | {b['scenarios']} | {m['scenarios']} |",
        f"| Final ERP state correct | {b['final_state_correct']} | {m['final_state_correct']} |",
        f"| Unsafe actions attempted (policy-blocked futures sent to the ERP) | {b['unsafe_actions_attempted']} | {m['unsafe_actions_attempted']} |",
        f"| Wrong interpretations executed | {b['wrong_interpretations_executed']} | {m['wrong_interpretations_executed']} |",
        f"| Clarification turns | {b['clarification_turns']} | {m['clarification_turns']} |",
        f"| Outcome reports that matched reality | {b['truthful_outcome_reports']} | {m['truthful_outcome_reports']} |",
        f"| Silent verification failures detected | {b['verification_failures_detected']} / {b['verification_failures_injected']} | {m['verification_failures_detected']} / {m['verification_failures_injected']} |",
        "",
        "| Scenario | Baseline final correct | Baseline unsafe | Baseline wrong | MIRROR final correct | MIRROR unsafe | MIRROR wrong | MIRROR clarifications |",
        "| --- | :---: | ---: | ---: | :---: | ---: | ---: | ---: |",
    ]
    by = {}
    for r in rows:
        by.setdefault(r["scenario"], {})[r["agent"]] = r
    for name, pair in by.items():
        bb, mm = pair["baseline"], pair["mirror"]
        lines.append(
            f"| {name} | {'yes' if bb['final_correct'] else 'no'} | {bb['unsafe_attempts']} | {bb['wrong_interpretations']} | "
            f"{'yes' if mm['final_correct'] else 'no'} | {mm['unsafe_attempts']} | {mm['wrong_interpretations']} | {mm['clarification_turns']} |"
        )
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    sys.exit(main())
