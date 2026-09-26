"""Typed-command path: utterance -> deterministic compiler -> runtime operation.

The deterministic compiler handles everything it can classify. Only when it cannot does
AssemblyAI's LLM Gateway propose an interpretation, and that proposal goes through the
same proposal tools and transcript-evidence checks as the voice agent's tool calls.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..core.errors import ErrorCode, MirrorError
from ..intent.compiler import Command, CommandKind, compile_utterance, has_commit_language
from ..intent.entities import EntityResolver, Mention
from ..intent.evidence import EvidenceStatus, FieldEvidence, SourceType, Utterance, from_mention, unknown
from .narration import narrate_comparison, narrate_error, narrate_view, short_name
from .transaction_runtime import CommitEvidence, Draft, Runtime

_YES = re.compile(r"^\s*(yes|yeah|yep|correct|confirm(ed)?|that'?s right|right|exactly|sure)\b", re.I)
_VERSION_REF = re.compile(r"\bv(?:ersion)?\s?(\d+)\b", re.I)


@dataclass
class TurnResult:
    reply: str
    command: dict
    transaction: dict | None = None
    comparison: dict | None = None
    error: dict | None = None
    interpreted_by: dict | None = None

    def as_dict(self) -> dict:
        return {
            "reply": self.reply,
            "command": self.command,
            "transaction": self.transaction,
            "comparison": self.comparison,
            "error": self.error,
            "interpreted_by": self.interpreted_by,
        }


def draft_from_command(cmd: Command, utt: Utterance) -> Draft:
    src = utt.source_type
    fields = {
        "product": from_mention("product", cmd.product, utt, src) if cmd.product else unknown("product"),
        "quantity": from_mention("quantity", cmd.quantity, utt, src) if cmd.quantity else unknown("quantity"),
        "source": from_mention("source", cmd.source, utt, src) if cmd.source else unknown("source"),
        "destination": from_mention("destination", cmd.destination, utt, src) if cmd.destination else unknown("destination"),
    }
    # A single unassigned location with an explicit source means it is the destination, and vice versa.
    if len(cmd.bare_locations) == 1:
        loc = cmd.bare_locations[0]
        if fields["destination"].status is EvidenceStatus.UNKNOWN and cmd.source:
            fields["destination"] = from_mention("destination", loc, utt, src)
        elif fields["source"].status is EvidenceStatus.UNKNOWN and cmd.destination:
            fields["source"] = from_mention("source", loc, utt, src)
    return Draft(fields)


_LLM_TOOL = {
    "propose": "propose_transfer", "what_if": "simulate_transfer", "amend": "amend_transfer", "clarify": "clarify_transfer",
    "compare": "compare_branches", "optimize": "find_max_safe_quantity", "commit": "commit_transfer",
    "rollback": "rollback_transfer", "cancel": "cancel_transfer", "query": "get_inventory",
}
HELP = "I can plan inventory transfers: move, what if, compare, as much as possible, commit, roll back, or cancel."


class TurnDispatcher:
    def __init__(self, runtime: Runtime, interpreter=None):
        self.rt = runtime
        self.interpreter = interpreter

    def handle(self, session_id: str, text: str, context_version: int | None = None) -> TurnResult:
        text = text.strip()[:500]
        if not text:
            raise MirrorError(ErrorCode.INVALID_REQUEST, "Empty command")
        utt = self.rt.record_utterance(session_id, text, "TEXT")
        snapshot = self.rt.erp_for(session_id).read_snapshot()
        resolver = EntityResolver(snapshot)
        cmd = compile_utterance(text, resolver)
        txn_id = self.rt.active_transaction_id(session_id)
        try:
            result = self._dispatch(session_id, cmd, utt, txn_id, context_version, resolver)
        except MirrorError as err:
            view = self.rt.view(txn_id) if txn_id else None
            result = TurnResult(narrate_error(err), cmd.summary(), view, error=err.to_dict())
        self.rt.record_utterance(session_id, result.reply, "TEXT", role="agent")
        return result

    # ------------------------------------------------------------------

    def _dispatch(self, session_id, cmd: Command, utt: Utterance, txn_id, context_version, resolver) -> TurnResult:
        rt, snap = self.rt, resolver.snapshot
        view = rt.view(txn_id) if txn_id else None
        state = view["state"] if view else None
        amendable = state in ("CLARIFYING", "CONSEQUENCE_REVIEW", "PREVIEW_READY")

        def done(v: dict) -> TurnResult:
            return TurnResult(narrate_view(v, snap), cmd.summary(), v)

        kind = cmd.kind
        # "Move it to site 2" while a request is open is a change, not a brand-new request.
        if kind is CommandKind.PROPOSE and amendable and cmd.product is None and cmd.quantity is None:
            kind = CommandKind.AMEND

        if kind is CommandKind.PROPOSE or (kind is CommandKind.WHAT_IF and not amendable and cmd.product):
            v = rt.propose(session_id, [draft_from_command(cmd, utt)], label=utt.text)
            return done(v)

        if kind is CommandKind.QUERY:
            return TurnResult(self._answer_query(cmd, snap), cmd.summary(), view)

        if kind in (CommandKind.COMMIT, CommandKind.ROLLBACK, CommandKind.ABORT, CommandKind.COMPARE,
                    CommandKind.OPTIMIZE, CommandKind.WHAT_IF, CommandKind.AMEND) and not txn_id:
            return TurnResult("There is no open transaction yet. Try: move 700 units of XR-17 from Mumbai to the Pune site.", cmd.summary())

        if kind is CommandKind.COMMIT:
            ev = CommitEvidence(
                explicit=has_commit_language(utt.text),
                source_type=utt.source_type,
                transcript_item_id=utt.item_id,
                text_span=utt.text,
                quantity=cmd.quantity.value if cmd.quantity else None,
            )
            token = (view.get("commit") or {}).get("token") or ""
            version = context_version if context_version is not None else view["current_version"]
            v = rt.commit(txn_id, version=version, token=token, idempotency_key=f"turn:{session_id}:{utt.item_id}", evidence=ev,
                          session_id=session_id)
            return done(v)

        if kind is CommandKind.ROLLBACK:
            return done(rt.rollback(txn_id, idempotency_key=f"turn:{session_id}:rollback:{utt.item_id}", session_id=session_id))

        if kind is CommandKind.ABORT:
            return done(rt.abort(txn_id))

        if kind is CommandKind.COMPARE:
            versions = [int(m) for m in _VERSION_REF.findall(utt.text)]
            quantities = [q.value for q in cmd.quantities]
            cmp = rt.compare(txn_id, quantities=quantities, versions=versions)
            return TurnResult(narrate_comparison(cmp, snap), cmd.summary(), rt.view(txn_id), comparison=cmp)

        if kind is CommandKind.OPTIMIZE:
            span = re.search(r"as (much|many) as [a-z ]+|max(imum)? [a-z]+", utt.text, re.I)
            request = FieldEvidence(
                "quantity", (), EvidenceStatus.RESOLVED, utt.source_type,
                transcript_item_id=utt.item_id, text_span=span.group() if span else utt.text,
            )
            return done(rt.optimize(txn_id, request, label=utt.text))

        if state == "CLARIFYING" and kind in (CommandKind.UNKNOWN, CommandKind.AMEND):
            answered = self._answer_clarification(txn_id, view, cmd, utt, resolver)
            if answered is not None:
                return done(answered)

        if kind in (CommandKind.WHAT_IF, CommandKind.AMEND):
            if not amendable:
                raise MirrorError(ErrorCode.INVALID_TRANSACTION, "This transaction can no longer be changed; start a new request", state=state)
            changes, role_fork = self._changes(cmd, utt, view)
            if role_fork is not None:
                return done(rt.fork_roles(txn_id, role_fork, label=utt.text))
            if not changes:
                llm = self._llm_fallback(session_id, cmd, utt, txn_id, context_version)
                if llm is not None:
                    return llm
                return TurnResult("What should change: the quantity, the product, or a location?", cmd.summary(), view)
            if kind is CommandKind.WHAT_IF:
                return done(rt.what_if(txn_id, changes, label=utt.text))
            return done(rt.modify(txn_id, changes, label=utt.text))

        llm = self._llm_fallback(session_id, cmd, utt, txn_id, context_version)
        if llm is not None:
            return llm
        return TurnResult(self._help(), cmd.summary(), view)

    def _help(self) -> str:
        interp = self.interpreter
        if interp is not None and interp.enabled and interp.last_outcome == "rate_limited":
            return (HELP + " Free-form understanding (AssemblyAI LLM Gateway) is paused for about "
                    f"{max(1, round(interp.rate_limited_for()))} s by this account's rate limit; standard phrasings always work.")
        return HELP

    def _llm_fallback(self, session_id, cmd: Command, utt: Utterance, txn_id, context_version) -> TurnResult | None:
        """Ask LLM Gateway for a proposal, then run it through the voice agent's proposal tools.

        Those tools re-check every value against the user's own words, and commit/rollback still
        require explicit user language, so the model cannot invent a value or authorize anything.
        """
        if self.interpreter is None or not self.interpreter.enabled:
            return None
        view = self.rt.view(txn_id) if txn_id else None
        context = "no open request"
        if view:
            cur = next(v for v in view["versions"] if v["version"] == view["current_version"])
            question = (cur.get("clarification") or {}).get("question") if view["state"] == "CLARIFYING" else None
            context = f"open request state {view['state']}" + (f"; the system asked: {question}" if question else "")
        proposal = self.interpreter.interpret(utt.text, context)
        if proposal is None or proposal.action not in _LLM_TOOL:
            return None
        tool = _LLM_TOOL[proposal.action]
        args = {"product": proposal.product, "quantity": proposal.quantity,
                "source_location": proposal.source, "destination_location": proposal.destination}
        if tool == "compare_branches":
            args = {"quantities": list(proposal.quantities)}
        elif tool == "clarify_transfer":
            args = {"answer": proposal.answer or utt.text}
        elif tool == "get_inventory":
            args = {"product": proposal.product, "location": proposal.source or proposal.destination}
        elif tool == "propose_transfer":
            args["_new_request"] = True  # the model read this as a new request, not a correction
        args = {k: v for k, v in args.items() if v not in (None, "", [])}

        from ..voice.tools import VoiceToolExecutor  # local import: voice.tools depends on this module

        version = context_version if context_version is not None else (view["current_version"] if view else 0)
        outcome = VoiceToolExecutor(self.rt, self).execute(session_id, tool, args, {utt.item_id: version})
        new_txn = self.rt.active_transaction_id(session_id)
        # Recorded against the transaction the proposal produced (or the open one), with the gateway request_id.
        self.rt.record_event(session_id, "LLM_INTERPRETATION", {
            **proposal.as_dict(), "text": utt.text, "tool": tool, "accepted": not outcome.is_error,
            "note": "a proposal only; every value is checked against the user's words",
        }, txn_id=new_txn or txn_id)
        result = outcome.result
        return TurnResult(
            result.get("say") or HELP,
            {**cmd.summary(), "llm": proposal.as_dict()},
            self.rt.view(new_txn) if new_txn else None,
            comparison=None,
            error={"code": result.get("error_code"), "message": result.get("error")} if outcome.is_error else None,
            interpreted_by={"model": proposal.model, "request_id": proposal.request_id, "latency_ms": round(proposal.latency_ms),
                            "action": proposal.action},
        )

    def _changes(self, cmd: Command, utt: Utterance, view: dict) -> tuple[list[FieldEvidence], FieldEvidence | None]:
        src = utt.source_type
        changes: list[FieldEvidence] = []
        if cmd.quantity:
            changes.append(from_mention("quantity", cmd.quantity, utt, src))
        if cmd.product:
            changes.append(from_mention("product", cmd.product, utt, src))
        if cmd.source:
            changes.append(from_mention("source", cmd.source, utt, src))
        if cmd.destination:
            changes.append(from_mention("destination", cmd.destination, utt, src))
        role_fork = None
        if len(cmd.bare_locations) == 1:
            loc = cmd.bare_locations[0]
            role = self._role_for_bare_location(loc, cmd, view)
            if role is None:
                role_fork = from_mention("source", loc, utt, src)
            else:
                changes.append(from_mention(role, loc, utt, src))
        return changes, role_fork

    def _role_for_bare_location(self, loc: Mention, cmd: Command, view: dict) -> str | None:
        if cmd.role_word in ("source", "origin"):
            return "source"
        if cmd.role_word in ("destination", "target"):
            return "destination"
        cur = next(v for v in view["versions"] if v["version"] == view["current_version"])
        draft = cur["intent"]["drafts"][0]
        for role in ("destination", "source"):
            f = draft[role]
            if f["status"] in ("UNKNOWN", "INFERRED") or f["ambiguous"]:
                return role
        snap = self.rt.erp_for(view["session_id"]).read_snapshot()
        cities = {snap.locations[c].city for c in loc.candidates if c in snap.locations}
        for role in ("destination", "source"):
            value = draft[role]["value"]
            if value and value in snap.locations and snap.locations[value].city in cities:
                return role
        return None

    def _answer_clarification(self, txn_id, view, cmd: Command, utt: Utterance, resolver: EntityResolver):
        cur = next(v for v in view["versions"] if v["version"] == view["current_version"])
        clar = cur.get("clarification") or {}
        field = clar.get("field")
        options = clar.get("options") or []
        src = utt.source_type
        if not field:
            return None

        if field == "role":
            index = None
            if cmd.role_word in ("source", "origin"):
                index = 0
            elif cmd.role_word in ("destination", "target"):
                index = 1
            elif cmd.ordinal:
                index = cmd.ordinal[0]
            if index is None:
                return None
            return self.rt.clarify(txn_id, field="role", draft_index=index, label=utt.text)

        if clar.get("kind") == "CONFIRM" and _YES.search(utt.text):
            return self.rt.clarify(
                txn_id, field=field, confirm_fields=[field], label=utt.text,
                confirm_source=src, confirm_item_id=utt.item_id, confirm_span=utt.text,
            )

        mentions: list[Mention] = []
        if field in ("source", "destination"):
            mentions = [m for m in (cmd.source, cmd.destination, *cmd.bare_locations) if m]
        elif field == "product" and cmd.product:
            mentions = [cmd.product]
        elif field == "quantity" and cmd.quantity:
            mentions = [cmd.quantity]

        option_values = [o["value"] for o in options]
        for m in mentions:
            ev = from_mention(field, m, utt, src)
            if option_values:
                narrowed = tuple(c for c in ev.candidates if c in option_values)
                if len(narrowed) == 1 and len(ev.candidates) > 1:
                    # The answer names one of the offered options only via the options themselves.
                    ev = FieldEvidence(field, narrowed, EvidenceStatus.RESOLVED, src, utt.item_id, m.text,
                                       note="answer matches exactly one offered option")
                elif not narrowed:
                    continue
            if len(ev.candidates) >= 1:
                return self.rt.clarify(txn_id, field=field, evidence=ev, label=utt.text)

        if cmd.ordinal and options and 0 <= cmd.ordinal[0] < len(options):
            opt = options[cmd.ordinal[0]]
            ev = FieldEvidence(field, (opt["value"],), EvidenceStatus.RESOLVED, src, utt.item_id, cmd.ordinal[1],
                               note=f"chose option {cmd.ordinal[0] + 1} ({opt['label']})")
            return self.rt.clarify(txn_id, field=field, evidence=ev, label=utt.text)
        return None

    def _answer_query(self, cmd: Command, snap) -> str:
        locs = [m for m in (cmd.source, cmd.destination, *cmd.bare_locations) if m]
        product = cmd.product.candidates[0] if cmd.product and cmd.product.unique else None
        if not product:
            return "Which product? For example: how much XR-17 is in Mumbai?"
        lines = []
        loc_ids = [c for m in locs for c in m.candidates] or sorted(snap.locations)
        for lid in loc_ids:
            cell = snap.cell(lid, product)
            if cell:
                lines.append(f"{short_name(snap, lid)} has {cell.on_hand} {product} (capacity {cell.capacity}, safety stock {cell.safety_stock})")
        return ". ".join(lines) + "." if lines else f"No stock records for {product}."


def selection_evidence(field: str, value, label: str) -> FieldEvidence:
    return FieldEvidence(field, (value,), EvidenceStatus.EXPLICIT, SourceType.USER_SELECTION, text_span=label,
                         note="selected in the MIRROR interface")
