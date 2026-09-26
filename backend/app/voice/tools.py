"""Voice tool calls are proposals.

Every argument the language model sends is re-checked against the user's own words
(final AssemblyAI transcripts stored by the backend). The model can point at a value;
only the transcript can make it EXPLICIT. Commit and rollback additionally require
explicit, un-negated user language in the latest turn.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..core.errors import ErrorCode, MirrorError
from ..intent.compiler import CommandKind, compile_utterance, has_commit_language
from ..intent.entities import EntityResolver
from ..intent.evidence import EvidenceStatus, FieldEvidence, Utterance, check_model_value, unknown
from ..runtime.dispatcher import TurnDispatcher, draft_from_command
from ..runtime.narration import narrate_comparison, narrate_error, narrate_view, short_name
from ..runtime.transaction_runtime import CommitEvidence, Draft, Runtime

_ROLLBACK_WORDS = re.compile(r"\b(roll\s?back|undo|revert|reverse)\b", re.I)
_ARG_FIELDS = {"product": "product", "quantity": "quantity", "source_location": "source", "destination_location": "destination"}


@dataclass
class ToolOutcome:
    result: dict
    is_error: bool
    mutated: bool  # whether the call created a version or touched reality


class VoiceToolExecutor:
    def __init__(self, runtime: Runtime, dispatcher: TurnDispatcher):
        self.rt = runtime
        self.dispatcher = dispatcher

    def execute(self, session_id: str, name: str, args: dict, version_at_turn: dict[str, int]) -> ToolOutcome:
        args = args if isinstance(args, dict) else {}
        try:
            handler = getattr(self, f"_t_{name}", None)
            if handler is None:
                raise MirrorError(ErrorCode.INVALID_REQUEST, f"Unknown tool {name}")
            return handler(session_id, args, version_at_turn)
        except MirrorError as err:
            txn_id = self.rt.active_transaction_id(session_id)
            return ToolOutcome(
                {
                    "ok": False,
                    "error_code": err.code.value,
                    "error": narrate_error(err),
                    "say": narrate_error(err),
                    "transaction": self._brief(txn_id) if txn_id else None,
                },
                True,
                False,
            )

    # ------------------------------------------------------------ helpers

    def _ctx(self, session_id: str):
        utterances = self.rt.recent_utterances(session_id, limit=4)
        resolver = EntityResolver(self.rt.erp_for(session_id).read_snapshot())
        return utterances, resolver

    def _txn(self, session_id: str, args: dict) -> str:
        active = self.rt.active_transaction_id(session_id)
        requested = args.get("transaction_id")
        if not active:
            raise MirrorError(ErrorCode.INVALID_TRANSACTION, "There is no open transaction. Ask the user what to move.")
        if requested and requested != active:
            raise MirrorError(
                ErrorCode.STALE_TRANSACTION,
                f"Transaction {str(requested)[:40]} is not the open transaction; use {active}.",
                active_transaction_id=active,
            )
        return active

    def _brief(self, txn_id: str) -> dict:
        view = self.rt.view(txn_id)
        cur = next(v for v in view["versions"] if v["version"] == view["current_version"])
        snap = self.rt.erp_for(view["session_id"]).read_snapshot()
        futures = []
        for b in cur["branches"]:
            i = b["intent"]
            futures.append(
                {
                    "future": b["key"].upper(),
                    "move": f"{i['quantity']} {i['product_id']} {short_name(snap, i['source_id'])} -> {short_name(snap, i['destination_id'])}",
                    "outcome": b["outcome"],
                    "blocking": b["policy"]["blocking_codes"],
                }
            )
        return {
            "transaction_id": view["id"],
            "version": view["current_version"],
            "state": view["state"],
            "real_world_changed": view["real_world_changed"],
            "reality": view["reality"],
            "futures": futures,
            "question": (cur.get("clarification") or {}).get("question") if view["state"] == "CLARIFYING" else None,
        }

    def _ok(self, txn_id: str, say: str | None = None, **extra) -> ToolOutcome:
        view = self.rt.view(txn_id)
        snap = self.rt.erp_for(view["session_id"]).read_snapshot()
        return ToolOutcome({"ok": True, "say": say or narrate_view(view, snap), **self._brief(txn_id), **extra}, False, True)

    def _field_changes(self, args: dict, utterances: list[Utterance], resolver: EntityResolver) -> list[FieldEvidence]:
        changes = []
        for arg, field in _ARG_FIELDS.items():
            if args.get(arg) not in (None, ""):
                changes.append(check_model_value(field, args[arg], utterances, resolver))
        return changes

    # -------------------------------------------------------------- tools

    def _t_propose_transfer(self, session_id, args, _vt) -> ToolOutcome:
        utterances, resolver = self._ctx(session_id)
        if not utterances:
            raise MirrorError(ErrorCode.INSUFFICIENT_EVIDENCE, "No user speech has been transcribed yet.")
        latest = utterances[0]
        cmd = compile_utterance(latest.text, resolver)
        active = self.rt.active_transaction_id(session_id)
        if active and not args.get("_new_request") and cmd.kind in (CommandKind.AMEND, CommandKind.UNKNOWN):
            state = self.rt.view(active)["state"]
            if state in ("CLARIFYING", "CONSEQUENCE_REVIEW", "PREVIEW_READY"):
                # "Actually I meant Mumbai" is a correction to the open request, whatever tool the model chose.
                return self._t_amend_transfer(session_id, args, _vt)
        compiled = draft_from_command(cmd, latest)
        fields = {}
        for arg, field in _ARG_FIELDS.items():
            if args.get(arg) not in (None, ""):
                fields[field] = check_model_value(field, args[arg], utterances, resolver)
            else:
                # The model omitted it: use only what the transcript itself supports.
                fields[field] = compiled.fields[field] if compiled.fields[field].candidates else unknown(field)
        view = self.rt.propose(session_id, [Draft(fields)], label=latest.text)
        return self._ok(view["id"])

    def _t_clarify_transfer(self, session_id, args, _vt) -> ToolOutcome:
        txn_id = self._txn(session_id, args)
        utterances, resolver = self._ctx(session_id)
        view = self.rt.view(txn_id)
        if view["state"] != "CLARIFYING":
            raise MirrorError(ErrorCode.INVALID_TRANSACTION, "There is no open question to answer.", state=view["state"])
        latest = utterances[0]
        cmd = compile_utterance(latest.text, resolver)
        answered = self.dispatcher._answer_clarification(txn_id, view, cmd, latest, resolver)
        if answered is None:
            cur = next(v for v in view["versions"] if v["version"] == view["current_version"])
            raise MirrorError(
                ErrorCode.AMBIGUOUS_INTENT,
                "I could not match the user's words to one of the options. Ask again: " + (cur["clarification"] or {}).get("question", ""),
            )
        return self._ok(txn_id)

    def _t_simulate_transfer(self, session_id, args, _vt) -> ToolOutcome:
        txn_id = self._txn(session_id, args)
        utterances, resolver = self._ctx(session_id)
        changes = self._field_changes(args, utterances, resolver)
        if not changes:
            raise MirrorError(ErrorCode.INVALID_REQUEST, "Say what to change for the what-if: a quantity, product or location.")
        self.rt.what_if(txn_id, changes, label=utterances[0].text if utterances else "what if")
        return self._ok(txn_id)

    def _t_amend_transfer(self, session_id, args, _vt) -> ToolOutcome:
        txn_id = self._txn(session_id, args)
        utterances, resolver = self._ctx(session_id)
        latest = utterances[0]
        cmd = compile_utterance(latest.text, resolver)
        view = self.rt.view(txn_id)
        # "Actually I meant Mumbai": the words carry no role, so whichever role the model put it in is an
        # inference. The user's words decide, however many location arguments the model sent.
        if len(cmd.bare_locations) == 1 and not (cmd.source or cmd.destination):
            changes, role_fork = self.dispatcher._changes(cmd, latest, view)
            if role_fork is not None:
                self.rt.fork_roles(txn_id, role_fork, label=latest.text)
                return self._ok(txn_id)
            self.rt.modify(txn_id, changes, label=latest.text)
            return self._ok(txn_id)
        changes = self._field_changes(args, utterances, resolver)
        if not changes:
            raise MirrorError(ErrorCode.INVALID_REQUEST, "Say what to change: the quantity, the product or a location.")
        self.rt.modify(txn_id, changes, label=latest.text)
        return self._ok(txn_id)

    def _t_compare_branches(self, session_id, args, _vt) -> ToolOutcome:
        txn_id = self._txn(session_id, args)
        quantities = [int(q) for q in args.get("quantities") or [] if isinstance(q, (int, float, str)) and str(q).isdigit()]
        cmp = self.rt.compare(txn_id, quantities=quantities)
        snap = self.rt.erp_for(session_id).read_snapshot()
        table = [
            {"quantity": c["name"], "source_after": c["source_after"], "safety_stock_ok": c["safety_stock_ok"],
             "destination_after": c["destination_after"], "outcome": c["outcome"]}
            for c in cmp["columns"]
        ]
        return ToolOutcome({"ok": True, "say": narrate_comparison(cmp, snap), "comparison": table, "real_world_changed": False}, False, False)

    def _t_find_max_safe_quantity(self, session_id, args, _vt) -> ToolOutcome:
        txn_id = self._txn(session_id, args)
        utterances, _ = self._ctx(session_id)
        latest = utterances[0]
        request = FieldEvidence("quantity", (), EvidenceStatus.RESOLVED, latest.source_type, latest.item_id, latest.text)
        self.rt.optimize(txn_id, request, label=latest.text)
        return self._ok(txn_id)

    def _t_commit_transfer(self, session_id, args, version_at_turn) -> ToolOutcome:
        txn_id = self._txn(session_id, args)
        utterances, resolver = self._ctx(session_id)
        if not utterances:
            raise MirrorError(ErrorCode.INSUFFICIENT_EVIDENCE, "Commit requires the user to say commit.")
        latest = utterances[0]
        cmd = compile_utterance(latest.text, resolver)
        evidence = CommitEvidence(
            explicit=has_commit_language(latest.text),
            source_type=latest.source_type,
            transcript_item_id=latest.item_id,
            text_span=latest.text,
            # The quantity comes from the user's words, never from the model's arguments.
            quantity=cmd.quantity.value if cmd.quantity else None,
        )
        view = self.rt.view(txn_id)
        version = version_at_turn.get(latest.item_id, view["current_version"])
        token = (view.get("commit") or {}).get("token") if view["current_version"] == version else None
        self.rt.commit(
            txn_id, version=version, token=token or "", idempotency_key=f"voice:{session_id}:{latest.item_id}",
            evidence=evidence, session_id=session_id,
        )
        return self._ok(txn_id)

    def _t_rollback_transfer(self, session_id, args, _vt) -> ToolOutcome:
        txn_id = self._txn(session_id, args)
        utterances, _ = self._ctx(session_id)
        latest = utterances[0] if utterances else None
        if latest is None or not _ROLLBACK_WORDS.search(latest.text):
            raise MirrorError(ErrorCode.INSUFFICIENT_EVIDENCE, "Rollback requires the user to explicitly ask to roll back.")
        self.rt.rollback(txn_id, idempotency_key=f"voice:{session_id}:rollback:{latest.item_id}", session_id=session_id)
        return self._ok(txn_id)

    def _t_cancel_transfer(self, session_id, args, _vt) -> ToolOutcome:
        txn_id = self._txn(session_id, args)
        self.rt.abort(txn_id)
        return self._ok(txn_id)

    def _t_get_inventory(self, session_id, args, _vt) -> ToolOutcome:
        snap = self.rt.erp_for(session_id).read_snapshot()
        resolver = EntityResolver(snap)
        prod = resolver.resolve_product(str(args.get("product", "")))
        if prod is None or not prod.unique:
            raise MirrorError(ErrorCode.INSUFFICIENT_EVIDENCE, f"'{str(args.get('product'))[:40]}' is not a single known product.")
        pid = prod.candidates[0]
        locs = sorted(snap.locations)
        if args.get("location"):
            m = resolver.resolve_location(str(args["location"]))
            if m is None:
                raise MirrorError(ErrorCode.INSUFFICIENT_EVIDENCE, f"'{str(args['location'])[:40]}' is not a known location.")
            locs = list(m.candidates)
        rows = []
        for lid in locs:
            c = snap.cell(lid, pid)
            if c:
                rows.append({"location": snap.locations[lid].name, "on_hand": c.on_hand, "capacity": c.capacity, "safety_stock": c.safety_stock})
        say = ". ".join(f"{r['location']} has {r['on_hand']}" for r in rows)
        return ToolOutcome({"ok": True, "say": say + ".", "product": pid, "stock": rows, "real_world_changed": False}, False, False)
