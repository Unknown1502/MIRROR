"""Deterministic spoken/text responses generated from runtime state.

The same sentences are shown for typed turns and handed to the voice agent in tool
results, so what the agent says is grounded in what the runtime actually computed.
"""

from __future__ import annotations

from ..core.world import WorldSnapshot
from ..core.errors import ErrorCode, MirrorError


def short_name(snapshot: WorldSnapshot, loc_id: str) -> str:
    loc = snapshot.locations.get(loc_id)
    if loc is None:
        return loc_id
    same_city = [l for l in snapshot.locations.values() if l.city == loc.city]
    return loc.city if len(same_city) == 1 else loc.name


def _current(view: dict) -> dict:
    return next(v for v in view["versions"] if v["version"] == view["current_version"])


def _blocking_sentence(branch: dict, snapshot: WorldSnapshot) -> str:
    intent = branch["intent"]
    q = intent["quantity"]
    for c in branch["simulation"]["consequences"]:
        if c["severity"] not in ("CRITICAL", "HIGH"):
            continue
        code = c["code"]
        if code == "SOURCE_SAFETY_STOCK_BREACH":
            return (
                f"Moving {q} would leave {short_name(snapshot, intent['source_id'])} "
                f"{int(-c['delta'])} units below its safety-stock threshold."
            )
        if code == "DESTINATION_CAPACITY_EXCEEDED":
            return (
                f"Moving {q} would put {short_name(snapshot, intent['destination_id'])} at {int(c['after'])}, "
                f"{int(c['delta'])} over its capacity of {int(c['threshold'])}."
            )
        if code == "SOURCE_INSUFFICIENT_INVENTORY":
            return f"{short_name(snapshot, intent['source_id'])} only has {int(c['before'])}; moving {q} is impossible."
        return f"Moving {q} is blocked: {c['message']}."
    return f"Moving {q} is blocked."


def _effects_sentence(branch: dict, snapshot: WorldSnapshot) -> str:
    parts = [
        f"{short_name(snapshot, e['location_id'])} {e['before']} to {e['after']}"
        for e in branch["simulation"]["effects"]
    ]
    return ", ".join(parts)


def narrate_view(view: dict, snapshot: WorldSnapshot) -> str:
    state = view["state"]
    cur = _current(view)
    if state == "CLARIFYING" and cur.get("clarification"):
        return cur["clarification"]["question"]
    if state == "CONSEQUENCE_REVIEW" and cur["branches"]:
        return _blocking_sentence(cur["branches"][0], snapshot) + " Nothing has changed."
    if state == "PREVIEW_READY" and cur["branches"]:
        b = cur["branches"][0]
        intent = b["intent"]
        head = ""
        if cur["kind"] == "OPTIMIZATION" and cur.get("extra"):
            opt = cur["extra"]["optimization"]
            head = f"The maximum safe transfer is {opt['best_quantity']} units. "
        verdict = "valid" if b["outcome"] == "VALID" else "valid, with a warning"
        return (
            f"{head}Moving {intent['quantity']} {intent['product_id']} from {short_name(snapshot, intent['source_id'])} "
            f"to {short_name(snapshot, intent['destination_id'])} is {verdict}: {_effects_sentence(b, snapshot)}. "
            f"Nothing has changed. Say commit {intent['quantity']} to execute it."
        )
    last = view["executions"][-1] if view["executions"] else None
    if state == "VERIFIED" and last and last["kind"] == "ROLLBACK":
        return "Rollback complete. Reality is restored, and I verified it against the ERP."
    if state == "VERIFIED" and last:
        focus = last["report"]["focus"] if last.get("report") else []
        where = ", ".join(f"{short_name(snapshot, f['location_id'])} {f['actual']}" for f in focus)
        return f"Committed and verified. The ERP now shows {where}, exactly as simulated."
    if state == "DIVERGED" and last:
        focus = last["report"]["focus"]
        exp = ", ".join(f"{short_name(snapshot, f['location_id'])} {f['expected']}" for f in focus)
        act = ", ".join(f"{short_name(snapshot, f['location_id'])} {f['actual']}" for f in focus)
        return f"Reality diverged. I expected {exp}, but the ERP shows {act}. I did not mark this verified. Rollback is available."
    if state == "FAILED" and last and last["verification"] == "NOT_APPLIED":
        return "The ERP did not execute the transfer. I read reality back and nothing changed."
    if state == "FAILED":
        return "The operation failed and reality does not match the plan. Manual review is required."
    if state == "ABORTED":
        return "Cancelled. Nothing changed."
    if state in ("EXECUTING", "VERIFYING"):
        return "Execution result is not yet verified. I'm reading reality back before claiming anything."
    return "Ready."


def narrate_comparison(cmp: dict, snapshot: WorldSnapshot) -> str:
    src = short_name(snapshot, cmp["source_id"])
    parts = []
    for col in cmp["columns"]:
        if col["outcome"] == "BLOCKED":
            if not col["safety_stock_ok"]:
                parts.append(f"{col['name']} is blocked: {src} would drop to {col['source_after']}, below safety stock {col['safety_stock']}")
            elif not col["capacity_ok"]:
                parts.append(f"{col['name']} is blocked by destination capacity")
            else:
                parts.append(f"{col['name']} is blocked")
        else:
            parts.append(f"{col['name']} is valid: {src} ends at {col['source_after']}")
    return ". ".join(parts) + ". Nothing has changed."


def narrate_error(err: MirrorError) -> str:
    if err.code is ErrorCode.DUPLICATE_COMMIT:
        return "That transaction was already committed. It will not execute twice."
    if err.code is ErrorCode.STALE_TRANSACTION:
        return err.message
    if err.code is ErrorCode.INVALID_TRANSACTION and err.details.get("state"):
        state = err.details["state"]
        if state in ("CLARIFYING", "CONSEQUENCE_REVIEW"):
            return "There is no valid preview to commit yet. Resolve the open question or choose a valid future first."
        return err.message
    return err.message
