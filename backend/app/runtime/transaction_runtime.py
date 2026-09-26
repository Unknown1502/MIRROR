"""The MIRROR transaction runtime.

    LLM / typed command -> PROPOSAL
                        -> evidence + deterministic entity resolution
                        -> forked candidate futures
                        -> shadow simulation (read-only snapshot)
                        -> policy
                        -> clarification | preview
                        -> explicit, version-bound commit
                        -> ERP execution
                        -> verification against a fresh read of reality
                        -> rollback (itself verified)

All mutating operations are serialized by one lock, and every step is written to
the hash-chained audit ledger before it is published to clients.
"""

from __future__ import annotations

import hashlib
import json
import logging
import secrets
import threading
import time
from dataclasses import dataclass, replace
from datetime import timedelta, timezone
from itertools import product as cartesian

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from ..audit.ledger import AuditLedger, event_to_dict
from ..clarification.optimizer import BranchSummary, build_clarification, evidence_clarification
from ..commit import tokens
from ..core.config import Settings
from ..core.db import (
    AuditEventRow,
    BranchRow,
    ConsequenceRow,
    EvidenceRow,
    ExecutionRow,
    SessionRow,
    SimulationRunRow,
    TranscriptItem,
    TransactionRow,
    TransactionVersionRow,
)
from ..core.errors import ErrorCode, MirrorError
from ..core.events import EventBus
from ..core.hashing import canonical_json, hash_value
from ..core.ids import iso, new_id, utcnow
from ..core.state_machine import (
    AMENDABLE_STATES,
    POST_COMMIT_STATES,
    TERMINAL_STATES,
    TxState,
    check_transition,
    walk,
)
from ..erp.service import CellDelta, ErpRejected, ErpTimeout, ErpUnavailable, Fault, MockERP
from ..erp.worlds import WorldRegistry
from ..intent.evidence import (
    FIELDS,
    EvidenceStatus,
    FieldEvidence,
    SourceType,
    Utterance,
    evidence_hash_input,
)
from ..policy.engine import BLOCKING_CODES, Decision, evaluate
from ..simulation.optimizer import max_safe_quantity
from ..simulation.simulator import SimulationResult, TransferIntent, predicted_world, simulate
from ..verification.rollback import plan_compensation
from ..verification.verifier import compare_worlds

log = logging.getLogger("mirror.runtime")

MAX_BRANCHES = 8
BRANCH_KEYS = "abcdefgh"
MAX_EXECUTION_ATTEMPTS = 2
SESSION_TTL = timedelta(hours=12)


class Kind:
    PROPOSAL = "PROPOSAL"
    AMENDMENT = "AMENDMENT"
    CLARIFICATION = "CLARIFICATION"
    WHAT_IF = "WHAT_IF"
    OPTIMIZATION = "OPTIMIZATION"
    RESIMULATION = "RESIMULATION"


class Reality:
    UNCHANGED = "UNCHANGED"
    CHANGED = "CHANGED"
    VERIFIED = "VERIFIED"
    DIVERGED = "DIVERGED"
    RESTORED = "RESTORED"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class Draft:
    fields: dict  # field name -> FieldEvidence

    def with_field(self, ev: FieldEvidence) -> "Draft":
        return Draft({**self.fields, ev.field: ev})


@dataclass(frozen=True)
class CommitEvidence:
    """Proof that the *user* asked to commit - never the model."""

    explicit: bool
    source_type: SourceType
    transcript_item_id: str | None = None
    text_span: str | None = None
    quantity: int | None = None

    def as_dict(self) -> dict:
        return {
            "explicit": self.explicit,
            "source_type": self.source_type.value,
            "transcript_item_id": self.transcript_item_id,
            "text_span": self.text_span,
            "quantity": self.quantity,
        }


def _ev_from_dict(d: dict) -> FieldEvidence:
    return FieldEvidence(
        field=d["field"],
        candidates=tuple(d["candidates"]),
        status=EvidenceStatus(d["status"]),
        source_type=SourceType(d["source"]["type"]),
        transcript_item_id=d["source"]["transcript_item_id"],
        text_span=d["source"]["text_span"],
        note=d.get("note", ""),
        evidence_id=d.get("evidence_id"),
    )


def _intent_of(branch_intent: dict) -> TransferIntent:
    return TransferIntent(
        branch_intent["product_id"], int(branch_intent["quantity"]), branch_intent["source_id"], branch_intent["destination_id"]
    )


class _Unit:
    """One database transaction plus the events to publish once it commits."""

    def __init__(self, rt: "Runtime"):
        self.rt = rt
        self.db = rt.session_factory()
        self.pending: list[AuditEventRow] = []

    def emit(self, event_type: str, payload: dict, *, session_id=None, txn_id=None, version=None) -> None:
        row = self.rt.ledger.append(
            self.db, event_type, payload, session_id=session_id, transaction_id=txn_id, transaction_version=version
        )
        self.pending.append(row)

    def commit(self) -> None:
        self.db.commit()
        for row in self.pending:
            if row.session_id:
                self.rt.bus.publish(
                    row.session_id,
                    row.event_type,
                    {**json.loads(row.payload_json), "audit_sequence": row.sequence, "audit_hash": row.hash},
                    transaction_id=row.transaction_id,
                    transaction_version=row.transaction_version,
                    event_id=row.event_id,
                    timestamp=row.timestamp,
                )
        self.pending.clear()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, *_):
        if exc_type is not None:
            self.db.rollback()
            self.pending.clear()
        self.db.close()


class Runtime:
    def __init__(self, settings: Settings, session_factory, erp: MockERP, ledger: AuditLedger, bus: EventBus,
                 worlds: WorldRegistry | None = None):
        self.settings = settings
        self.session_factory = session_factory
        self.erp = erp  # the shared world; with sandboxes on, each session gets its own (see erp_for)
        self.worlds = worlds or WorldRegistry(erp, settings.data_dir, per_session=False)
        self.ledger = ledger
        self.bus = bus
        self._lock = threading.RLock()
        # Set by the voice layer: called with (session_id, sentence) when reality moves under a preview.
        self.on_reality_changed = None

    def erp_for(self, session_id: str | None) -> MockERP:
        return self.worlds.for_session(session_id)

    def _erp_for_txn_id(self, txn_id: str) -> MockERP:
        with self.session_factory() as db:
            return self.erp_for(self._get_txn(db, txn_id).session_id)

    # ================================================================ sessions

    def create_session(self) -> tuple[str, str]:
        session_id = new_id("sess")
        token = secrets.token_urlsafe(32)
        with self._lock, _Unit(self) as u:
            u.db.add(
                SessionRow(
                    id=session_id,
                    token_hash=hashlib.sha256(token.encode()).hexdigest(),
                    created_at=utcnow(),
                    voice_state=TxState.IDLE.value,
                )
            )
            u.emit("SESSION_READY", {"voice_available": self.settings.voice_available}, session_id=session_id)
            u.commit()
        return session_id, token

    def authenticate(self, session_id: str, token: str | None) -> None:
        with self.session_factory() as db:
            row = db.get(SessionRow, session_id)
        if row is None:
            raise MirrorError(ErrorCode.SESSION_EXPIRED, "Unknown or expired session")
        presented = hashlib.sha256((token or "").encode()).hexdigest()
        if not secrets.compare_digest(presented, row.token_hash):
            raise MirrorError(ErrorCode.UNAUTHORIZED, "Session token is missing or invalid")
        created = row.created_at if row.created_at.tzinfo else row.created_at.replace(tzinfo=timezone.utc)
        if utcnow() - created > SESSION_TTL:
            raise MirrorError(ErrorCode.SESSION_EXPIRED, "Session expired; start a new one")

    def session_info(self, session_id: str) -> dict:
        with self.session_factory() as db:
            row = db.get(SessionRow, session_id)
            if row is None:
                raise MirrorError(ErrorCode.SESSION_EXPIRED, "Unknown or expired session")
            turns = db.execute(
                select(TranscriptItem).where(TranscriptItem.session_id == session_id).order_by(TranscriptItem.id)
            ).scalars().all()
        return {
            "id": row.id,
            "created_at": iso(row.created_at),
            "voice_state": row.voice_state,
            "voice_available": self.settings.voice_available,
            "assemblyai_session_id": row.assemblyai_session_id,
            "active_transaction_id": row.active_transaction_id,
            "transcript": [
                {"item_id": t.item_id, "role": t.role, "source": t.source, "text": t.text, "at": iso(t.created_at)}
                for t in turns
            ],
        }

    def record_utterance(self, session_id: str, text: str, source: str, item_id: str | None = None, role: str = "user") -> Utterance:
        item_id = item_id or new_id("item")
        with self._lock, _Unit(self) as u:
            u.db.add(TranscriptItem(session_id=session_id, item_id=item_id, source=source, role=role, text=text, created_at=utcnow()))
            if role == "user":
                u.emit("USER_TURN", {"item_id": item_id, "text": text, "source": source}, session_id=session_id)
            u.commit()
        return Utterance(item_id, text, source)

    def recent_utterances(self, session_id: str, limit: int = 6) -> list[Utterance]:
        with self.session_factory() as db:
            rows = db.execute(
                select(TranscriptItem)
                .where(TranscriptItem.session_id == session_id, TranscriptItem.role == "user")
                .order_by(TranscriptItem.id.desc())
                .limit(limit)
            ).scalars().all()
        return [Utterance(r.item_id, r.text, r.source) for r in rows]

    def set_voice_state(self, session_id: str, state: TxState, assemblyai_session_id: str | None = None) -> None:
        with self._lock, self.session_factory() as db:
            row = db.get(SessionRow, session_id)
            if row is None:
                return
            row.voice_state = state.value
            if assemblyai_session_id is not None:
                row.assemblyai_session_id = assemblyai_session_id or None
            db.commit()

    def record_event(self, session_id: str, event_type: str, payload: dict, txn_id: str | None = None,
                     version: int | None = None) -> None:
        """Append an externally observed event (e.g. from the voice layer) to the ledger and publish it."""
        with self._lock, _Unit(self) as u:
            u.emit(event_type, payload, session_id=session_id, txn_id=txn_id, version=version)
            u.commit()

    def remember_voice_disconnect(self, session_id: str, when) -> None:
        with self._lock, self.session_factory() as db:
            row = db.get(SessionRow, session_id)
            if row is not None:
                row.assemblyai_disconnected_at = when
                db.commit()

    def voice_resume_info(self, session_id: str) -> tuple[str | None, object]:
        with self.session_factory() as db:
            row = db.get(SessionRow, session_id)
            return (row.assemblyai_session_id, row.assemblyai_disconnected_at) if row else (None, None)

    def active_transaction_id(self, session_id: str) -> str | None:
        with self.session_factory() as db:
            row = db.get(SessionRow, session_id)
            return row.active_transaction_id if row else None

    # ============================================================ transactions

    def propose(self, session_id: str, drafts: list[Draft], label: str) -> dict:
        """Start a new transaction. Any open proposal in the session is aborted first."""
        with self._lock:
            with _Unit(self) as u:
                sess = u.db.get(SessionRow, session_id)
                if sess is None:
                    raise MirrorError(ErrorCode.SESSION_EXPIRED, "Unknown or expired session")
                if sess.active_transaction_id:
                    prev = u.db.get(TransactionRow, sess.active_transaction_id)
                    if prev is not None and TxState(prev.state) in AMENDABLE_STATES:
                        self._set_state(u, prev, TxState.ABORTED, reason="superseded by a new request")
                now = utcnow()
                txn = TransactionRow(
                    id=new_id("txn"),
                    session_id=session_id,
                    current_version=0,
                    state=TxState.INTERPRETING.value,
                    reality=Reality.UNCHANGED,
                    created_at=now,
                    updated_at=now,
                )
                u.db.add(txn)
                sess.active_transaction_id = txn.id
                u.db.flush()
                self._derive(u, txn, drafts, Kind.PROPOSAL, label, entry=None)
                u.commit()
                return self.view(txn.id)

    def amend(self, txn_id: str, drafts: list[Draft], label: str, kind: str = Kind.AMENDMENT) -> dict:
        with self._lock:
            with _Unit(self) as u:
                txn = self._load_amendable(u, txn_id)
                self._derive(u, txn, drafts, kind, label)
                u.commit()
                return self.view(txn_id)

    def current_drafts(self, txn_id: str) -> list[Draft]:
        with self.session_factory() as db:
            txn = db.get(TransactionRow, txn_id)
            if txn is None:
                raise MirrorError(ErrorCode.NOT_FOUND, f"Transaction {txn_id} not found")
            ver = self._version_row(db, txn_id, txn.current_version)
            return self._drafts_of(ver)

    def what_if(self, txn_id: str, changes: list[FieldEvidence], label: str) -> dict:
        with self._lock:
            drafts = [self._apply_changes(d, changes) for d in self.current_drafts(txn_id)]
            return self.amend(txn_id, drafts, label, kind=Kind.WHAT_IF)

    def modify(self, txn_id: str, changes: list[FieldEvidence], label: str) -> dict:
        with self._lock:
            drafts = [self._apply_changes(d, changes) for d in self.current_drafts(txn_id)]
            return self.amend(txn_id, drafts, label, kind=Kind.AMENDMENT)

    def fork_roles(self, txn_id: str, location: FieldEvidence, label: str) -> dict:
        """'Actually I meant Mumbai' with no role: fork on which field the correction replaces."""
        with self._lock:
            drafts = self.current_drafts(txn_id)
            if len(drafts) != 1:
                raise MirrorError(ErrorCode.AMBIGUOUS_INTENT, "Settle the current question before this correction")
            base = drafts[0]
            as_source = base.with_field(replace(location, field="source", evidence_id=None))
            as_destination = base.with_field(replace(location, field="destination", evidence_id=None))
            return self.amend(txn_id, [as_source, as_destination], label, kind=Kind.AMENDMENT)

    def clarify(
        self,
        txn_id: str,
        *,
        field: str,
        label: str,
        evidence: FieldEvidence | None = None,
        draft_index: int | None = None,
        confirm_fields: list[str] | None = None,
        confirm_source: SourceType = SourceType.USER_TEXT,
        confirm_item_id: str | None = None,
        confirm_span: str | None = None,
    ) -> dict:
        """Resolve the pending clarification. The caller supplies evidence for the answer."""
        with self._lock:
            drafts = self.current_drafts(txn_id)
            if field == "role":
                if draft_index is None or not (0 <= draft_index < len(drafts)):
                    raise MirrorError(ErrorCode.AMBIGUOUS_INTENT, "Say whether you meant the source or the destination")
                drafts = [drafts[draft_index]]
            if evidence is not None:
                drafts = [d.with_field(evidence) for d in drafts]
            for name in confirm_fields or []:
                drafts = [
                    d.with_field(
                        replace(
                            d.fields[name],
                            status=EvidenceStatus.EXPLICIT,
                            source_type=confirm_source,
                            transcript_item_id=confirm_item_id,
                            text_span=confirm_span,
                            note=f"user confirmed {d.fields[name].value}",
                            evidence_id=None,
                        )
                    )
                    for d in drafts
                ]
            return self.amend(txn_id, drafts, label, kind=Kind.CLARIFICATION)

    def select_branch(self, txn_id: str, branch_key: str) -> dict:
        """The user picked one future in the interface: every undecided field takes that future's value."""
        with self._lock:
            view = self.view(txn_id)
            cur = next(v for v in view["versions"] if v["version"] == view["current_version"])
            branch = next((b for b in cur["branches"] if b["key"] == branch_key), None)
            if branch is None:
                raise MirrorError(ErrorCode.INVALID_REQUEST, f"No future '{branch_key}' in v{cur['version']}")
            drafts = self.current_drafts(txn_id)
            draft = drafts[branch["intent"].get("draft_index", 0)]
            label = f"Selected future {branch_key.upper()}"
            for name, key in (("product", "product_id"), ("quantity", "quantity"), ("source", "source_id"), ("destination", "destination_id")):
                fe = draft.fields[name]
                if fe.ambiguous or fe.status.value not in ("EXPLICIT", "RESOLVED"):
                    draft = draft.with_field(
                        FieldEvidence(name, (branch["intent"][key],), EvidenceStatus.EXPLICIT, SourceType.USER_SELECTION,
                                      text_span=label, note="future selected in the MIRROR interface")
                    )
            return self.amend(txn_id, [draft], label, kind=Kind.CLARIFICATION)

    def optimize(self, txn_id: str, request: FieldEvidence, label: str) -> dict:
        with self._lock:
            return self._optimize(txn_id, request, label)

    def _optimize(self, txn_id: str, request: FieldEvidence, label: str) -> dict:
        drafts = self.current_drafts(txn_id)
        if len(drafts) != 1:
            raise MirrorError(ErrorCode.AMBIGUOUS_INTENT, "Resolve which location you meant before optimizing")
        d = drafts[0]
        for name in ("product", "source", "destination"):
            if len(d.fields[name].candidates) != 1:
                raise MirrorError(
                    ErrorCode.AMBIGUOUS_INTENT,
                    f"The {name} must be settled before searching for a maximum safe quantity",
                    field=name,
                )
        snapshot = self._erp_for_txn_id(txn_id).read_snapshot()
        result = max_safe_quantity(
            snapshot, d.fields["product"].value, d.fields["source"].value, d.fields["destination"].value
        )
        if result.best_quantity is None:
            raise MirrorError(
                ErrorCode.POLICY_VIOLATION,
                "No quantity in the allowed transfer range is safe for this route",
                optimization=result.as_dict(),
            )
        quantity = FieldEvidence(
            "quantity",
            (result.best_quantity,),
            EvidenceStatus.RESOLVED,
            SourceType.RUNTIME_OPTIMIZER,
            transcript_item_id=request.transcript_item_id,
            text_span=request.text_span,
            note=(
                f"largest non-blocked quantity by exhaustive search over {result.search_range[0]}..{result.search_range[1]}"
                + (f"; {result.best_quantity + 1} hits {result.binding_constraint}" if result.binding_constraint else "")
            ),
        )
        with self._lock:
            with _Unit(self) as u:
                txn = self._load_amendable(u, txn_id)
                self._derive(u, txn, [d.with_field(quantity)], Kind.OPTIMIZATION, label, extra={"optimization": result.as_dict()})
                u.commit()
        return self.view(txn_id)

    def compare(self, txn_id: str, quantities: list[int] | None = None, versions: list[int] | None = None) -> dict:
        """Side-by-side futures, simulated fresh against one snapshot. Read-only."""
        with self._lock:
            drafts = self.current_drafts(txn_id)
            if len(drafts) != 1 or any(len(drafts[0].fields[f].candidates) != 1 for f in ("product", "source", "destination")):
                raise MirrorError(ErrorCode.AMBIGUOUS_INTENT, "Settle which future you mean before comparing quantities")
            base = drafts[0]
            snapshot = self._erp_for_txn_id(txn_id).read_snapshot()
            variants: list[tuple[str, TransferIntent]] = []
            if versions:
                with self.session_factory() as db:
                    for v in versions:
                        ver = self._version_row(db, txn_id, v)
                        vd = self._drafts_of(ver)
                        if len(vd) != 1 or any(len(vd[0].fields[f].candidates) != 1 for f in FIELDS):
                            raise MirrorError(ErrorCode.AMBIGUOUS_INTENT, f"v{v} has more than one future; pick one first")
                        variants.append((f"v{v}", self._single_intent(vd[0])))
            for q in quantities or []:
                variants.append(
                    (str(q), TransferIntent(base.fields["product"].value, int(q), base.fields["source"].value, base.fields["destination"].value))
                )
            if len(variants) < 2:
                raise MirrorError(ErrorCode.INVALID_REQUEST, "Comparison needs at least two alternatives")

            columns = []
            for name, intent in variants:
                sim = simulate(snapshot, intent)
                policy = evaluate(sim, {f: "EXPLICIT" for f in FIELDS}, 1)
                src = next((e for e in sim.effects if e.location_id == intent.source_id), None)
                dst = next((e for e in sim.effects if e.location_id == intent.destination_id), None)
                columns.append(
                    {
                        "name": name,
                        "intent": intent.as_dict(),
                        "outcome": policy.outcome.value,
                        "source_after": src.after if src else None,
                        "source_before": src.before if src else None,
                        "safety_stock": src.safety_stock if src else None,
                        "safety_stock_ok": "SOURCE_SAFETY_STOCK_BREACH" not in sim.codes() and "SOURCE_INSUFFICIENT_INVENTORY" not in sim.codes(),
                        "destination_after": dst.after if dst else None,
                        "destination_before": dst.before if dst else None,
                        "capacity": dst.capacity if dst else None,
                        "capacity_ok": "DESTINATION_CAPACITY_EXCEEDED" not in sim.codes(),
                        "estimated_cost": sim.transport["estimated_cost"] if sim.transport else None,
                        "blocking_codes": list(policy.blocking_codes),
                        "result_hash": sim.result_hash,
                    }
                )
            comparison = {
                "snapshot_revision": snapshot.revision,
                "source_id": base.fields["source"].value,
                "destination_id": base.fields["destination"].value,
                "product_id": base.fields["product"].value,
                "columns": columns,
                "live_writes": 0,
            }
            with _Unit(self) as u:
                txn = u.db.get(TransactionRow, txn_id)
                u.emit("COMPARISON_READY", comparison, session_id=txn.session_id, txn_id=txn_id, version=txn.current_version)
                u.commit()
            return comparison

    def abort(self, txn_id: str, reason: str = "user aborted") -> dict:
        with self._lock:
            with _Unit(self) as u:
                txn = self._get_txn(u.db, txn_id)
                state = TxState(txn.state)
                if state in POST_COMMIT_STATES:
                    raise MirrorError(
                        ErrorCode.INVALID_TRANSACTION,
                        "This transaction already touched reality; roll it back instead of aborting",
                        state=state.value,
                    )
                if state in TERMINAL_STATES:
                    return self.view(txn_id)
                self._set_state(u, txn, TxState.ABORTED, reason=reason)
                u.commit()
        return self.view(txn_id)

    # ================================================================ commit

    def commit(
        self,
        txn_id: str,
        *,
        version: int,
        token: str,
        idempotency_key: str,
        evidence: CommitEvidence,
        session_id: str | None = None,
    ) -> dict:
        if not idempotency_key or len(idempotency_key) > 200:
            raise MirrorError(ErrorCode.INVALID_REQUEST, "idempotency_key is required")
        with self._lock:
            with self.session_factory() as db:
                prior = db.execute(select(ExecutionRow).where(ExecutionRow.idempotency_key == idempotency_key)).scalar_one_or_none()
            if prior is not None:
                if prior.transaction_id != txn_id or prior.version != version or prior.kind != "COMMIT":
                    raise MirrorError(ErrorCode.INVALID_REQUEST, "idempotency_key was already used for a different operation")
                view = self.view(txn_id)
                view["idempotent_replay"] = True
                return view

            with _Unit(self) as u:
                txn = self._get_txn(u.db, txn_id)
                if session_id is not None and txn.session_id != session_id:
                    raise MirrorError(ErrorCode.UNAUTHORIZED, "Transaction belongs to another session")
                state = TxState(txn.state)
                if txn.committed_version is not None or state in POST_COMMIT_STATES:
                    raise MirrorError(
                        ErrorCode.DUPLICATE_COMMIT,
                        f"Transaction was already committed at v{txn.committed_version}; it will not execute twice",
                        committed_version=txn.committed_version,
                        state=state.value,
                    )
                # A stale version is the more precise refusal for a client holding an old preview.
                if version != txn.current_version:
                    raise MirrorError(
                        ErrorCode.STALE_TRANSACTION,
                        f"v{version} is no longer current; the transaction is at v{txn.current_version}",
                        requested_version=version,
                        current_version=txn.current_version,
                    )
                if state is not TxState.PREVIEW_READY:
                    raise MirrorError(
                        ErrorCode.INVALID_TRANSACTION,
                        "There is no valid preview to commit",
                        state=state.value,
                    )
                ver = self._version_row(u.db, txn_id, version)
                tokens.verify(self.settings.commit_secret, token, txn_id, version, ver.evidence_hash, ver.simulation_hash or "")
                if not evidence.explicit:
                    raise MirrorError(
                        ErrorCode.INSUFFICIENT_EVIDENCE,
                        "Commit requires an explicit instruction from the user (e.g. 'commit 500')",
                    )
                draft = self._drafts_of(ver)[0]
                intent = self._single_intent(draft)
                if evidence.quantity is not None and evidence.quantity != intent.quantity:
                    raise MirrorError(
                        ErrorCode.STALE_TRANSACTION,
                        f"The current preview is for {intent.quantity} units, not {evidence.quantity}",
                        preview_quantity=intent.quantity,
                        requested_quantity=evidence.quantity,
                    )
                u.emit(
                    "COMMIT_REQUESTED",
                    {"version": version, "idempotency_key_hash": hash_value(idempotency_key)[:16], "evidence": evidence.as_dict()},
                    session_id=txn.session_id, txn_id=txn_id, version=version,
                )

                # Re-simulate against reality *now*. If anything relevant moved, the preview is stale.
                erp = self.erp_for(txn.session_id)
                snapshot = erp.read_snapshot()
                sim = simulate(snapshot, intent)
                statuses = {f: draft.fields[f].status.value for f in FIELDS}
                policy = evaluate(sim, statuses, 1)
                fresh_hash = self._simulation_hash([("a", sim, policy.outcome.value)])
                if fresh_hash != ver.simulation_hash or policy.decision is not Decision.ALLOW:
                    u.emit(
                        "STALE_TRANSACTION",
                        {"version": version, "reason": "reality changed since the preview; re-simulated", "world_revision": snapshot.revision},
                        session_id=txn.session_id, txn_id=txn_id, version=version,
                    )
                    self._derive(u, txn, [draft], Kind.RESIMULATION, "Re-simulated: reality changed since the preview")
                    u.commit()
                    raise MirrorError(
                        ErrorCode.STALE_TRANSACTION,
                        "Reality changed since this preview. MIRROR re-simulated it as a new version; review and commit again.",
                        new_version=txn.current_version,
                    )

                pre = snapshot.on_hand_map()
                predicted = predicted_world(snapshot, sim)
                execution = ExecutionRow(
                    id=new_id("exec"),
                    transaction_id=txn_id,
                    version=version,
                    kind="COMMIT",
                    idempotency_key=idempotency_key,
                    status="PENDING",
                    operations_json="[]",
                    pre_json=canonical_json(pre),
                    predicted_json=canonical_json(predicted),
                    created_at=utcnow(),
                )
                u.db.add(execution)
                try:
                    u.db.flush()
                except IntegrityError:
                    raise MirrorError(ErrorCode.DUPLICATE_COMMIT, "A commit with this idempotency key already exists")
                txn.committed_version = version
                self._set_state(u, txn, TxState.COMMIT_ARMED)
                u.emit(
                    "COMMIT_ACCEPTED",
                    {"version": version, "execution_id": execution.id, "intent": intent.as_dict()},
                    session_id=txn.session_id, txn_id=txn_id, version=version,
                )
                self._set_state(u, txn, TxState.EXECUTING)
                txn.reality = Reality.CHANGED
                u.emit(
                    "EXECUTION_STARTED",
                    {"operation": "TRANSFER_INVENTORY", "intent": intent.as_dict(), "real_world_changed": True},
                    session_id=txn.session_id, txn_id=txn_id, version=version,
                )
                u.commit()
                execution_id = execution.id

            self._execute_commit(txn_id, version, execution_id, intent, snapshot, erp)
        return self.view(txn_id)

    def _execute_commit(self, txn_id: str, version: int, execution_id: str, intent: TransferIntent, snapshot, erp: MockERP) -> None:
        operation_id = f"{txn_id}:v{version}:commit"
        src_key = f"{intent.source_id}|{intent.product_id}"
        dst_key = f"{intent.destination_id}|{intent.product_id}"
        pre = snapshot.on_hand_map()
        preconditions = {src_key: pre[src_key], dst_key: pre[dst_key]}

        status, reported, error, attempts, notes = "UNKNOWN", None, None, 0, []
        started = time.perf_counter()
        deliver_twice = erp.consume_fault(Fault.DUPLICATE_COMMIT)
        while attempts < MAX_EXECUTION_ATTEMPTS:
            attempts += 1
            try:
                receipt = erp.transfer_inventory(
                    operation_id=operation_id,
                    transaction_id=txn_id,
                    version=version,
                    product_id=intent.product_id,
                    quantity=intent.quantity,
                    source_id=intent.source_id,
                    destination_id=intent.destination_id,
                    preconditions=preconditions,
                )
                status, reported = "APPLIED", receipt.reported
                if deliver_twice:
                    again = erp.transfer_inventory(
                        operation_id=operation_id,
                        transaction_id=txn_id,
                        version=version,
                        product_id=intent.product_id,
                        quantity=intent.quantity,
                        source_id=intent.source_id,
                        destination_id=intent.destination_id,
                        preconditions=preconditions,
                    )
                    notes.append({"event": "ERP_DUPLICATE_SUPPRESSED", "duplicate": again.duplicate})
                break
            except ErpUnavailable as exc:
                status, error = "NOT_APPLIED", {"reason": "ERP_UNAVAILABLE", "message": str(exc)}
                notes.append({"event": "EXECUTION_RETRY", "attempt": attempts, "message": str(exc)})
            except ErpTimeout as exc:
                status, error = "UNKNOWN", {"reason": "ERP_TIMEOUT", "message": str(exc)}
                break
            except ErpRejected as exc:
                status, error = "NOT_APPLIED", {"reason": exc.reason, "message": str(exc)}
                break
        exec_ms = (time.perf_counter() - started) * 1000

        with self._lock, _Unit(self) as u:
            txn = self._get_txn(u.db, txn_id)
            ex = u.db.get(ExecutionRow, execution_id)
            ex.status, ex.attempts, ex.exec_latency_ms = status, attempts, exec_ms
            ex.operations_json = canonical_json([operation_id])
            ex.reported_json = canonical_json(reported) if reported is not None else None
            ex.error_json = canonical_json(error) if error else None
            for note in notes:
                if note["event"] == "EXECUTION_RETRY" and status == "APPLIED":
                    u.emit("EXECUTION_RETRY", note, session_id=txn.session_id, txn_id=txn_id, version=version)
                elif note["event"] == "ERP_DUPLICATE_SUPPRESSED":
                    u.emit(
                        "DUPLICATE_DELIVERY_SUPPRESSED",
                        {"operation_id": operation_id, "message": "Operation delivered twice; ERP idempotency applied it once"},
                        session_id=txn.session_id, txn_id=txn_id, version=version,
                    )
            u.emit(
                "EXECUTION_COMPLETED",
                {
                    "execution_status": status,
                    "erp_reported": reported,
                    "error": error,
                    "attempts": attempts,
                    "latency_ms": round(exec_ms, 2),
                    "note": "The ERP's report is recorded but not trusted; reality is read back next.",
                },
                session_id=txn.session_id, txn_id=txn_id, version=version,
            )
            self._set_state(u, txn, TxState.VERIFYING)
            u.emit("VERIFICATION_STARTED", {"execution_id": execution_id}, session_id=txn.session_id, txn_id=txn_id, version=version)
            u.commit()

        self._verify_execution(txn_id, execution_id, erp)

    def _verify_execution(self, txn_id: str, execution_id: str, erp: MockERP) -> None:
        started = time.perf_counter()
        try:
            actual_snapshot = erp.read_snapshot()
        except Exception as exc:  # the read itself failed: we genuinely do not know
            with self._lock, _Unit(self) as u:
                txn = self._get_txn(u.db, txn_id)
                txn.reality = Reality.UNKNOWN
                u.emit(
                    "ERROR",
                    {"code": ErrorCode.VERIFICATION_FAILED.value, "message": f"Could not read reality: {exc}", "reality": Reality.UNKNOWN},
                    session_id=txn.session_id, txn_id=txn_id, version=txn.current_version,
                )
                u.commit()
            return
        actual = actual_snapshot.on_hand_map()
        verify_ms = (time.perf_counter() - started) * 1000

        with self._lock, _Unit(self) as u:
            txn = self._get_txn(u.db, txn_id)
            ex = u.db.get(ExecutionRow, execution_id)
            pre = json.loads(ex.pre_json)
            predicted = json.loads(ex.predicted_json)
            report = compare_worlds(pre, predicted, actual)
            unchanged = actual == pre

            ex.actual_json = canonical_json(actual)
            ex.diff_json = canonical_json(report.as_dict())
            ex.verify_latency_ms = verify_ms
            ex.completed_at = utcnow()
            payload = {
                "execution_id": execution_id,
                "execution_status": ex.status,
                "report": report.as_dict(),
                "world_revision": actual_snapshot.revision,
                "latency_ms": round(verify_ms, 2),
            }
            v = ex.version
            if ex.status in ("APPLIED", "UNKNOWN") and report.match:
                ex.verification = "VERIFIED"
                if ex.status == "UNKNOWN":
                    ex.status = "APPLIED"
                txn.reality = Reality.VERIFIED
                self._set_state(u, txn, TxState.VERIFIED)
                u.emit("VERIFIED", {**payload, "reality": Reality.VERIFIED}, session_id=txn.session_id, txn_id=txn_id, version=v)
            elif ex.status in ("NOT_APPLIED", "UNKNOWN") and unchanged:
                ex.verification = "NOT_APPLIED"
                ex.status = "NOT_APPLIED"
                txn.reality = Reality.UNCHANGED
                self._set_state(u, txn, TxState.FAILED)
                u.emit(
                    "ERROR",
                    {
                        **payload,
                        "code": ErrorCode.EXECUTION_FAILED.value,
                        "message": "Execution did not happen. Reality was read back and is unchanged.",
                        "reality": Reality.UNCHANGED,
                    },
                    session_id=txn.session_id, txn_id=txn_id, version=v,
                )
            else:
                ex.verification = "DIVERGED"
                txn.reality = Reality.DIVERGED
                self._set_state(u, txn, TxState.DIVERGED)
                u.emit(
                    "REALITY_DIVERGED",
                    {**payload, "reality": Reality.DIVERGED, "rollback_available": True},
                    session_id=txn.session_id, txn_id=txn_id, version=v,
                )
            u.commit()
            changed = {f["cell"] for f in report.focus if f["before"] != f["actual"]}
            session_id = txn.session_id
        if changed:
            self._reality_watch(session_id, changed, "another transaction just changed the ERP", exclude_txn=txn_id)

    def reverify(self, txn_id: str) -> dict:
        """Re-run verification for a transaction whose post-state is still unknown."""
        with self._lock:
            with self.session_factory() as db:
                txn = self._get_txn(db, txn_id)
                if TxState(txn.state) is not TxState.VERIFYING:
                    raise MirrorError(ErrorCode.INVALID_TRANSACTION, "Only a transaction in VERIFYING can be re-verified")
                ex = db.execute(
                    select(ExecutionRow).where(ExecutionRow.transaction_id == txn_id).order_by(ExecutionRow.created_at.desc())
                ).scalars().first()
            if ex is None:
                raise MirrorError(ErrorCode.INVALID_TRANSACTION, "No execution to verify")
            self._verify_execution(txn_id, ex.id, self.erp_for(txn.session_id))
        return self.view(txn_id)

    # ============================================================== rollback

    def rollback(self, txn_id: str, *, idempotency_key: str, session_id: str | None = None) -> dict:
        if not idempotency_key:
            raise MirrorError(ErrorCode.INVALID_REQUEST, "idempotency_key is required")
        with self._lock:
            with self.session_factory() as db:
                prior = db.execute(select(ExecutionRow).where(ExecutionRow.idempotency_key == idempotency_key)).scalar_one_or_none()
            if prior is not None:
                if prior.transaction_id != txn_id or prior.kind != "ROLLBACK":
                    raise MirrorError(ErrorCode.INVALID_REQUEST, "idempotency_key was already used for a different operation")
                view = self.view(txn_id)
                view["idempotent_replay"] = True
                return view

            with _Unit(self) as u:
                txn = self._get_txn(u.db, txn_id)
                if session_id is not None and txn.session_id != session_id:
                    raise MirrorError(ErrorCode.UNAUTHORIZED, "Transaction belongs to another session")
                state = TxState(txn.state)
                if state not in (TxState.VERIFIED, TxState.DIVERGED):
                    raise MirrorError(
                        ErrorCode.ROLLBACK_FAILED,
                        "Rollback is only possible after a commit has been verified or has diverged",
                        state=state.value,
                    )
                executions = u.db.execute(
                    select(ExecutionRow).where(ExecutionRow.transaction_id == txn_id).order_by(ExecutionRow.created_at)
                ).scalars().all()
                if any(e.kind == "ROLLBACK" and e.verification == "VERIFIED" for e in executions):
                    raise MirrorError(ErrorCode.ROLLBACK_FAILED, "This transaction was already rolled back and verified")
                commit_ex = next((e for e in executions if e.kind == "COMMIT" and e.actual_json), None)
                if commit_ex is None:
                    raise MirrorError(ErrorCode.ROLLBACK_FAILED, "No verified post-state to roll back from")

                erp = self.erp_for(txn.session_id)
                snapshot = erp.read_snapshot()
                current = snapshot.on_hand_map()
                pre, post = json.loads(commit_ex.pre_json), json.loads(commit_ex.actual_json)
                try:
                    plan = plan_compensation(pre, post, current)
                except MirrorError as exc:
                    u.emit(
                        "ERROR",
                        {"code": exc.code.value, "message": exc.message, "details": exc.details},
                        session_id=txn.session_id, txn_id=txn_id, version=txn.current_version,
                    )
                    u.commit()
                    raise

                execution = ExecutionRow(
                    id=new_id("exec"),
                    transaction_id=txn_id,
                    version=txn.current_version,
                    kind="ROLLBACK",
                    idempotency_key=idempotency_key,
                    status="PENDING",
                    operations_json="[]",
                    pre_json=canonical_json(current),
                    predicted_json=canonical_json(plan.expected_after),
                    created_at=utcnow(),
                )
                u.db.add(execution)
                try:
                    u.db.flush()
                except IntegrityError:
                    raise MirrorError(ErrorCode.DUPLICATE_COMMIT, "A rollback with this idempotency key already exists")
                self._set_state(u, txn, TxState.ROLLBACK)
                u.emit(
                    "ROLLBACK_STARTED",
                    {"plan": plan.as_dict(), "based_on": "verified actual post-state, not the original plan"},
                    session_id=txn.session_id, txn_id=txn_id, version=txn.current_version,
                )
                u.commit()
                execution_id = execution.id
                rollback_number = sum(1 for e in executions if e.kind == "ROLLBACK") + 1

            op_id = f"{txn_id}:rollback:{rollback_number}"
            preconditions = {k: current[k] for k in plan.affected}
            status, reported, error = "UNKNOWN", None, None
            started = time.perf_counter()
            try:
                if plan.kind == "TRANSFER":
                    t = plan.transfer
                    receipt = erp.transfer_inventory(
                        operation_id=op_id,
                        transaction_id=txn_id,
                        version=-1,
                        product_id=t["product_id"],
                        quantity=t["quantity"],
                        source_id=t["source_id"],
                        destination_id=t["destination_id"],
                        preconditions=preconditions,
                    )
                else:
                    receipt = erp.adjust_inventory(
                        operation_id=op_id,
                        deltas=[CellDelta(a["location_id"], a["product_id"], a["delta"]) for a in plan.adjustments],
                        reason="ROLLBACK_COMPENSATION",
                        preconditions=preconditions,
                    )
                status, reported = "APPLIED", receipt.reported
            except ErpTimeout as exc:
                status, error = "UNKNOWN", {"reason": "ERP_TIMEOUT", "message": str(exc)}
            except (ErpUnavailable, ErpRejected) as exc:
                status, error = "NOT_APPLIED", {"reason": getattr(exc, "reason", "ERP_UNAVAILABLE"), "message": str(exc)}
            exec_ms = (time.perf_counter() - started) * 1000

            actual_snapshot = erp.read_snapshot()
            actual = actual_snapshot.on_hand_map()
            with _Unit(self) as u:
                txn = self._get_txn(u.db, txn_id)
                ex = u.db.get(ExecutionRow, execution_id)
                report = compare_worlds(current, plan.expected_after, actual)
                ex.status, ex.exec_latency_ms = status, exec_ms
                ex.operations_json = canonical_json([op_id])
                ex.reported_json = canonical_json(reported) if reported else None
                ex.error_json = canonical_json(error) if error else None
                ex.actual_json = canonical_json(actual)
                ex.diff_json = canonical_json(report.as_dict())
                ex.completed_at = utcnow()
                payload = {
                    "execution_id": execution_id,
                    "execution_status": status,
                    "erp_reported": reported,
                    "error": error,
                    "report": report.as_dict(),
                    "world_revision": actual_snapshot.revision,
                }
                if report.match:
                    ex.verification = "VERIFIED"
                    txn.reality = Reality.RESTORED
                    self._set_state(u, txn, TxState.VERIFIED)
                    u.emit(
                        "ROLLBACK_COMPLETED",
                        {**payload, "verified": True, "reality": Reality.RESTORED},
                        session_id=txn.session_id, txn_id=txn_id, version=txn.current_version,
                    )
                    u.emit(
                        "VERIFIED",
                        {**payload, "phase": "ROLLBACK", "reality": Reality.RESTORED},
                        session_id=txn.session_id, txn_id=txn_id, version=txn.current_version,
                    )
                else:
                    ex.verification = "DIVERGED"
                    txn.reality = Reality.DIVERGED
                    self._set_state(u, txn, TxState.FAILED)
                    u.emit(
                        "ERROR",
                        {**payload, "code": ErrorCode.ROLLBACK_FAILED.value, "message": "Rollback did not restore reality", "reality": Reality.DIVERGED},
                        session_id=txn.session_id, txn_id=txn_id, version=txn.current_version,
                    )
                u.commit()
                owner = txn.session_id
            moved = {f["cell"] for f in report.focus if f["before"] != f["actual"]}
            if moved:
                self._reality_watch(owner, moved, "a rollback just changed the ERP", exclude_txn=txn_id)
        return self.view(txn_id)

    # ============================================================ world/demo
    #
    # With sandboxes on, demo controls act on the caller's own world and only that session sees
    # the events. With one shared world, they act on everyone.

    def _scope(self, session_id: str | None) -> list[str] | None:
        return [session_id] if (self.worlds.per_session and session_id) else None

    def world(self, session_id: str | None = None) -> dict:
        erp = self.erp_for(session_id)
        snap = erp.read_snapshot()
        return {
            **snap.to_public(),
            "armed_fault": erp.armed_fault().value,
            "recent_operations": erp.operations(15),
            "sandbox": bool(self._scope(session_id)),
        }

    def reset_demo(self, session_id: str | None = None) -> dict:
        erp = self.erp_for(session_id)
        scope = self._scope(session_id)
        with self._lock:
            erp.reset_world()
            with _Unit(self) as u:
                txq, sq = select(TransactionRow), select(SessionRow)
                if scope:
                    txq = txq.where(TransactionRow.session_id.in_(scope))
                    sq = sq.where(SessionRow.id.in_(scope))
                aborted = []
                for txn in u.db.execute(txq).scalars().all():
                    if TxState(txn.state) in AMENDABLE_STATES:
                        self._set_state(u, txn, TxState.ABORTED, reason="demo reset")
                        aborted.append(txn.id)
                for sess in u.db.execute(sq).scalars().all():
                    sess.active_transaction_id = None
                u.emit("DEMO_RESET", {"world_revision": erp.revision(), "aborted": aborted, "sandbox": bool(scope)},
                       session_id=session_id if scope else None)
                u.commit()
        if not scope:
            self.bus.publish_all("DEMO_RESET", {"world_revision": erp.revision()})
        return self.world(session_id)

    def arm_fault(self, fault: Fault, session_id: str | None = None) -> dict:
        scope = self._scope(session_id)
        with self._lock:
            self.erp_for(session_id).arm_fault(fault)
            with _Unit(self) as u:
                u.emit("FAULT_ARMED", {"fault": fault.value, "one_shot": True, "sandbox": bool(scope)},
                       session_id=session_id if scope else None)
                u.commit()
        if not scope:
            self.bus.publish_all("FAULT_ARMED", {"fault": fault.value})
        return {"armed_fault": fault.value}

    def external_change(self, location_id: str, product_id: str, delta: int, note: str,
                        session_id: str | None = None) -> dict:
        """Demo: another system changes reality underneath open transactions."""
        erp = self.erp_for(session_id)
        scope = self._scope(session_id)
        with self._lock:
            before = erp.read_snapshot().cell(location_id, product_id)
            try:
                receipt = erp.adjust_inventory(
                    operation_id=new_id("ext"),
                    deltas=[CellDelta(location_id, product_id, delta)],
                    reason=note,
                    actor="EXTERNAL_SYSTEM",
                )
            except ErpRejected as exc:
                raise MirrorError(ErrorCode.INVALID_REQUEST, f"ERP rejected the change: {exc}", reason=exc.reason)
            with _Unit(self) as u:
                u.emit("EXTERNAL_WORLD_CHANGE",
                       {"location_id": location_id, "product_id": product_id, "delta": delta, "note": note, "revision": receipt.revision},
                       session_id=session_id if scope else None)
                u.commit()
        if scope:
            self.bus.publish(session_id, "WORLD_CHANGED", {"revision": receipt.revision, "cause": "EXTERNAL_SYSTEM"})
        else:
            self.bus.publish_all("WORLD_CHANGED", {"revision": receipt.revision, "cause": "EXTERNAL_SYSTEM"})
        name = erp.read_snapshot().locations[location_id].name if before else location_id
        cause = f"{name} {product_id} went from {before.on_hand if before else '?'} to {(before.on_hand + delta) if before else '?'} ({note})"
        self._reality_watch(session_id, {f"{location_id}|{product_id}"}, cause)
        return self.world(session_id)

    def _reality_watch(self, session_id: str | None, cells: set[str], cause: str, exclude_txn: str | None = None) -> list[str]:
        """Reality moved: re-simulate every open preview that depended on the changed cells, and say so.

        Previews are never silently left stale. The commit gate would still refuse a stale token,
        but the user should hear about it before they try.
        """
        scope = self._scope(session_id)
        touched: list[tuple[str, str, list[str], str]] = []
        try:
            with self._lock, _Unit(self) as u:
                q = select(TransactionRow).where(TransactionRow.state.in_([st.value for st in AMENDABLE_STATES]))
                if scope:
                    q = q.where(TransactionRow.session_id.in_(scope))
                for txn in u.db.execute(q).scalars().all():
                    if txn.id == exclude_txn or not txn.current_version:
                        continue
                    ver = self._version_row(u.db, txn.id, txn.current_version)
                    rows = u.db.execute(
                        select(BranchRow).where(BranchRow.transaction_id == txn.id, BranchRow.version == ver.version)
                    ).scalars().all()
                    depends = set()
                    for b in rows:
                        i = json.loads(b.intent_json)
                        depends |= {f"{i['source_id']}|{i['product_id']}", f"{i['destination_id']}|{i['product_id']}"}
                    hit = sorted(depends & cells)
                    if not hit:
                        continue
                    previous = ver.status
                    self._derive(u, txn, self._drafts_of(ver), Kind.RESIMULATION, f"Re-simulated: {cause}")
                    touched.append((txn.id, txn.session_id, hit, previous))
                u.commit()
        except Exception:  # noqa: BLE001 - a failed watch must never break the write that triggered it
            log.exception("reality watch failed")
            return []

        from .narration import narrate_view  # local import: narration depends on runtime views only

        for txn_id, sid, hit, previous in touched:
            view = self.view(txn_id)
            cur = next(v for v in view["versions"] if v["version"] == view["current_version"])
            say = f"Heads up: reality changed under your preview. {cause}. I re-simulated it. " + narrate_view(view, self.erp_for(sid).read_snapshot())
            self.record_event(
                sid, "REALITY_CHANGED",
                {"cells": hit, "cause": cause, "previous_status": previous, "new_status": cur["status"],
                 "new_version": view["current_version"], "state": view["state"], "say": say},
                txn_id=txn_id, version=view["current_version"],
            )
            if self.on_reality_changed is not None:
                try:
                    self.on_reality_changed(sid, say)
                except Exception:  # noqa: BLE001
                    log.exception("reality-changed hook failed")
        return [t[0] for t in touched]

    # ================================================================ views

    def view(self, txn_id: str) -> dict:
        with self.session_factory() as db:
            txn = self._get_txn(db, txn_id)
            versions = db.execute(
                select(TransactionVersionRow).where(TransactionVersionRow.transaction_id == txn_id).order_by(TransactionVersionRow.version)
            ).scalars().all()
            branches = db.execute(select(BranchRow).where(BranchRow.transaction_id == txn_id)).scalars().all()
            sims = {
                s.id: s
                for s in db.execute(select(SimulationRunRow).where(SimulationRunRow.transaction_id == txn_id)).scalars().all()
            }
            evidence_rows = db.execute(
                select(EvidenceRow).where(EvidenceRow.transaction_id == txn_id).order_by(EvidenceRow.created_at)
            ).scalars().all()
            executions = db.execute(
                select(ExecutionRow).where(ExecutionRow.transaction_id == txn_id).order_by(ExecutionRow.created_at)
            ).scalars().all()
            last_cmp = db.execute(
                select(AuditEventRow)
                .where(AuditEventRow.transaction_id == txn_id, AuditEventRow.event_type == "COMPARISON_READY")
                .order_by(AuditEventRow.sequence.desc())
                .limit(1)
            ).scalar_one_or_none()

        by_version: dict[int, list[BranchRow]] = {}
        for b in branches:
            by_version.setdefault(b.version, []).append(b)

        current_ids: set[str] = set()
        version_views = []
        for ver in versions:
            intent = json.loads(ver.intent_json)
            if ver.version == txn.current_version:
                for d in intent["drafts"]:
                    current_ids |= {f["evidence_id"] for f in d.values() if f.get("evidence_id")}
            bviews = []
            for b in sorted(by_version.get(ver.version, []), key=lambda x: x.branch_key):
                sim = json.loads(sims[b.simulation_run_id].result_json) if b.simulation_run_id in sims else None
                bviews.append(
                    {
                        "branch_id": b.id,
                        "key": b.branch_key,
                        "intent": json.loads(b.intent_json),
                        "evidence_status": json.loads(b.evidence_status_json),
                        "outcome": b.outcome,
                        "policy": json.loads(b.policy_json),
                        "simulation": sim,
                    }
                )
            version_views.append(
                {
                    "version": ver.version,
                    "parent_version": ver.parent_version,
                    "kind": ver.kind,
                    "label": ver.label,
                    "status": ver.status,
                    "state_trail": json.loads(ver.state_trail_json),
                    "intent": intent,
                    "evidence_hash": ver.evidence_hash,
                    "simulation_hash": ver.simulation_hash,
                    "clarification": json.loads(ver.clarification_json) if ver.clarification_json else None,
                    "extra": json.loads(ver.extra_json) if ver.extra_json else None,
                    "world_revision": ver.world_revision,
                    "created_at": iso(ver.created_at),
                    "superseded": ver.version < txn.current_version,
                    "committed": txn.committed_version == ver.version,
                    "branches": bviews,
                }
            )

        state = TxState(txn.state)
        commit_block = None
        if state is TxState.PREVIEW_READY and versions:
            cur = versions[-1]
            commit_block = {
                "version": cur.version,
                "token": tokens.mint(self.settings.commit_secret, txn.id, cur.version, cur.evidence_hash, cur.simulation_hash or ""),
                "evidence_hash": cur.evidence_hash,
                "simulation_hash": cur.simulation_hash,
            }

        erp_ops = self.erp_for(txn.session_id).operations_for_transaction(txn.id)
        return {
            "id": txn.id,
            "session_id": txn.session_id,
            "state": txn.state,
            "reality": txn.reality,
            "real_world_changed": bool(erp_ops),
            "live_writes": len(erp_ops),
            "current_version": txn.current_version,
            "committed_version": txn.committed_version,
            "created_at": iso(txn.created_at),
            "updated_at": iso(txn.updated_at),
            "versions": version_views,
            "commit": commit_block,
            "executions": [self._execution_view(e) for e in executions],
            "comparison": {**json.loads(last_cmp.payload_json), "version": last_cmp.transaction_version} if last_cmp else None,
            "evidence": [
                {
                    "evidence_id": e.id,
                    "version": e.version,
                    "field": e.field,
                    "candidates": json.loads(e.candidates_json),
                    "status": e.status,
                    "source": {"type": e.source_type, "transcript_item_id": e.transcript_item_id, "text_span": e.text_span},
                    "note": e.note,
                    "supersedes": e.supersedes_id,
                    "lifecycle": "ACTIVE" if e.id in current_ids else "SUPERSEDED",
                    "timestamp": iso(e.created_at),
                }
                for e in evidence_rows
            ],
            "erp_operations": erp_ops,
        }

    def audit(self, txn_id: str) -> dict:
        with self.session_factory() as db:
            self._get_txn(db, txn_id)
            events = self.ledger.events_for_transaction(db, txn_id)
            chain = self.ledger.verify_chain(db)
        return {"transaction_id": txn_id, "events": events, "chain": chain.as_dict()}

    def verify_ledger(self) -> dict:
        with self.session_factory() as db:
            return self.ledger.verify_chain(db).as_dict()

    def recent_audit(self, limit: int = 60) -> list[dict]:
        with self.session_factory() as db:
            rows = db.execute(select(AuditEventRow).order_by(AuditEventRow.sequence.desc()).limit(limit)).scalars().all()
        return [event_to_dict(r) for r in rows]

    def metrics(self, session_id: str | None = None) -> dict:
        with self.session_factory() as db:
            txq = select(TransactionRow)
            if session_id:
                txq = txq.where(TransactionRow.session_id == session_id)
            txns = db.execute(txq).scalars().all()
            ids = [t.id for t in txns]
            if not ids:
                return {"transactions": 0, "branches_explored": 0, "clarifications": 0, "what_if_simulations": 0,
                        "commit_versions": 0, "commits": 0, "verified": 0, "diverged": 0, "rollbacks": 0,
                        "simulation_latency_ms": None, "execution_latency_ms": None, "verification_latency_ms": None}
            versions = db.execute(select(TransactionVersionRow).where(TransactionVersionRow.transaction_id.in_(ids))).scalars().all()
            branch_count = len(db.execute(select(BranchRow.id).where(BranchRow.transaction_id.in_(ids))).all())
            sims = db.execute(select(SimulationRunRow.latency_ms).where(SimulationRunRow.transaction_id.in_(ids))).scalars().all()
            execs = db.execute(select(ExecutionRow).where(ExecutionRow.transaction_id.in_(ids))).scalars().all()

        def avg(xs):
            xs = [x for x in xs if x is not None]
            return round(sum(xs) / len(xs), 3) if xs else None

        commits = [e for e in execs if e.kind == "COMMIT"]
        return {
            "transactions": len(txns),
            "branches_explored": branch_count,
            "clarifications": sum(1 for v in versions if v.status in ("AMBIGUOUS", "NEEDS_CONFIRMATION", "INCOMPLETE")),
            "what_if_simulations": sum(1 for v in versions if v.kind in (Kind.WHAT_IF, Kind.OPTIMIZATION)),
            "commit_versions": len(versions),
            "commits": len(commits),
            "verified": sum(1 for e in commits if e.verification == "VERIFIED"),
            "diverged": sum(1 for e in commits if e.verification == "DIVERGED"),
            "rollbacks": sum(1 for e in execs if e.kind == "ROLLBACK"),
            "simulation_latency_ms": avg(sims),
            "execution_latency_ms": avg([e.exec_latency_ms for e in execs]),
            "verification_latency_ms": avg([e.verify_latency_ms for e in execs]),
        }

    # ============================================================= internals

    def _derive(self, u: _Unit, txn: TransactionRow, drafts: list[Draft], kind: str, label: str,
                entry: TxState | None = None, extra: dict | None = None) -> None:
        """Create the next immutable version: interpret -> fork -> simulate -> review -> rest."""
        start_state = TxState(txn.state)
        entry = entry or (TxState.WHAT_IF if kind in (Kind.WHAT_IF, Kind.OPTIMIZATION) else TxState.INTERPRETING)
        path = [] if start_state is entry else [entry]
        prev_version = txn.current_version
        new_version = prev_version + 1
        v = new_version
        sid = txn.session_id

        prev_ids: dict[str, str] = {}
        if prev_version:
            prev_row = self._version_row(u.db, txn.id, prev_version)
            for d in self._drafts_of(prev_row):
                for name, ev in d.fields.items():
                    if ev.evidence_id:
                        prev_ids.setdefault(name, ev.evidence_id)

        # Persist new evidence; carried-forward evidence keeps its id (never rewritten).
        persisted: list[Draft] = []
        for d in drafts:
            fields = {}
            for name in FIELDS:
                ev = d.fields[name]
                if ev.evidence_id is None:
                    eid = new_id("ev")
                    u.db.add(
                        EvidenceRow(
                            id=eid,
                            transaction_id=txn.id,
                            version=v,
                            field=name,
                            candidates_json=canonical_json(list(ev.candidates)),
                            status=ev.status.value,
                            source_type=ev.source_type.value,
                            transcript_item_id=ev.transcript_item_id,
                            text_span=ev.text_span,
                            note=ev.note,
                            supersedes_id=prev_ids.get(name),
                            created_at=utcnow(),
                        )
                    )
                    ev = ev.with_id(eid)
                fields[name] = ev
            persisted.append(Draft(fields))
        drafts = persisted
        evidence_hash = hash_value([evidence_hash_input(d.fields) for d in drafts])
        intent_json = {"drafts": [{n: ev.as_dict() for n, ev in d.fields.items()} for d in drafts]}

        if prev_version and TxState(txn.state) is TxState.PREVIEW_READY:
            u.emit(
                "COMMIT_TOKEN_INVALIDATED",
                {"version": prev_version, "reason": f"superseded by v{v}"},
                session_id=sid, txn_id=txn.id, version=prev_version,
            )

        if kind in (Kind.WHAT_IF, Kind.OPTIMIZATION):
            u.emit("WHAT_IF_STARTED", {"label": label, "kind": kind}, session_id=sid, txn_id=txn.id, version=v)
        u.emit(
            "INTENT_CREATED",
            {"kind": kind, "label": label, "parent_version": prev_version or None, "intent": intent_json, "evidence_hash": evidence_hash},
            session_id=sid, txn_id=txn.id, version=v,
        )

        snapshot = self.erp_for(txn.session_id).read_snapshot()
        missing = [(n, d.fields[n]) for d in drafts for n in FIELDS if not d.fields[n].candidates]
        branches_out: list[tuple[str, int, TransferIntent, dict, SimulationResult, object]] = []
        clarification = None
        sim_hash = None

        if missing:
            name, ev = missing[0]
            path.append(TxState.CLARIFYING)
            status = "INCOMPLETE"
            clarification = evidence_clarification(name, None, "UNKNOWN", snapshot)
        else:
            path += [TxState.FORKING, TxState.SIMULATING, TxState.CONSEQUENCE_REVIEW]
            expanded = self._expand(drafts)
            u.emit(
                "SIMULATION_STARTED",
                {"branches": len(expanded), "world_revision": snapshot.revision, "snapshot_hash": snapshot.hash},
                session_id=sid, txn_id=txn.id, version=v,
            )
            for idx, (draft_index, intent, statuses) in enumerate(expanded):
                key = BRANCH_KEYS[idx]
                sim = simulate(snapshot, intent)
                policy = evaluate(sim, statuses, len(expanded))
                branches_out.append((key, draft_index, intent, statuses, sim, policy))

            summaries = [
                BranchSummary(key, intent.as_dict(), policy.outcome.value, tuple(c.as_dict() for c in sim.consequences), di)
                for key, di, intent, _, sim, policy in branches_out
            ]
            sim_hash = self._simulation_hash([(k, s, p.outcome.value) for k, _, _, _, s, p in branches_out])

            if len(branches_out) > 1:
                path.append(TxState.CLARIFYING)
                status = "AMBIGUOUS"
                clarification = build_clarification(summaries, snapshot, drafts=len(drafts))
            else:
                key, _, intent, statuses, sim, policy = branches_out[0]
                if policy.decision is Decision.BLOCK:
                    status = "BLOCKED"
                elif policy.decision is Decision.REQUIRE_CLARIFICATION:
                    path.append(TxState.CLARIFYING)
                    status = "NEEDS_CONFIRMATION"
                    weak = next(n for n in FIELDS if statuses[n] not in ("EXPLICIT", "RESOLVED"))
                    clarification = evidence_clarification(weak, drafts[0].fields[weak].value, statuses[weak], snapshot)
                else:
                    path.append(TxState.PREVIEW_READY)
                    status = policy.outcome.value

        trail = walk(start_state, path)
        for key, di, intent, statuses, sim, policy in branches_out:
            run_id = new_id("sim")
            u.db.add(
                SimulationRunRow(
                    id=run_id,
                    transaction_id=txn.id,
                    version=v,
                    branch_key=key,
                    world_revision=snapshot.revision,
                    result_json=canonical_json(sim.as_dict()),
                    result_hash=sim.result_hash,
                    latency_ms=sim.latency_ms,
                    created_at=utcnow(),
                )
            )
            u.db.flush()  # consequences reference the run
            for c in sim.consequences:
                u.db.add(
                    ConsequenceRow(
                        simulation_run_id=run_id, code=c.code, severity=c.severity.value, blocking=c.code in BLOCKING_CODES,
                        message=c.message, subject=c.subject, before=c.before, after=c.after, threshold=c.threshold, delta=c.delta,
                    )
                )
            u.db.add(
                BranchRow(
                    id=new_id("br"),
                    transaction_id=txn.id,
                    version=v,
                    branch_key=key,
                    intent_json=canonical_json({**intent.as_dict(), "draft_index": di}),
                    evidence_status_json=canonical_json(statuses),
                    outcome=policy.outcome.value,
                    policy_json=canonical_json(policy.as_dict()),
                    simulation_run_id=run_id,
                    created_at=utcnow(),
                )
            )
            u.emit(
                "BRANCH_CREATED",
                {"branch_key": key, "intent": intent.as_dict(), "draft_index": di},
                session_id=sid, txn_id=txn.id, version=v,
            )
            u.emit(
                "SIMULATION_COMPLETED",
                {
                    "branch_key": key,
                    "outcome": policy.outcome.value,
                    "decision": policy.decision.value,
                    "effects": [e.as_dict() for e in sim.effects],
                    "result_hash": sim.result_hash,
                    "latency_ms": round(sim.latency_ms, 3),
                    "live_writes": 0,
                },
                session_id=sid, txn_id=txn.id, version=v,
            )
        if branches_out:
            u.emit(
                "CONSEQUENCES_UPDATED",
                {"branches": {k: [c.code for c in s.consequences] for k, _, _, _, s, _ in branches_out}},
                session_id=sid, txn_id=txn.id, version=v,
            )

        u.db.add(
            TransactionVersionRow(
                id=new_id("ver"),
                transaction_id=txn.id,
                version=v,
                parent_version=prev_version or None,
                kind=kind,
                label=label[:500],
                intent_json=canonical_json(intent_json),
                evidence_hash=evidence_hash,
                simulation_hash=sim_hash,
                status=status,
                state_trail_json=canonical_json([s.value for s in trail]),
                clarification_json=canonical_json(clarification) if clarification else None,
                extra_json=canonical_json(extra) if extra else None,
                world_revision=snapshot.revision,
                created_at=utcnow(),
            )
        )
        txn.current_version = v
        txn.state = trail[-1].value
        txn.updated_at = utcnow()
        u.emit(
            "STATE_CHANGED",
            {"from": start_state.value, "to": trail[-1].value, "trail": [s.value for s in trail], "status": status},
            session_id=sid, txn_id=txn.id, version=v,
        )
        final = trail[-1]
        if final is TxState.CLARIFYING:
            u.emit("CLARIFICATION_REQUIRED", {"status": status, **(clarification or {})}, session_id=sid, txn_id=txn.id, version=v)
        elif final is TxState.PREVIEW_READY:
            u.emit(
                "PREVIEW_READY",
                {"status": status, "simulation_hash": sim_hash, "evidence_hash": evidence_hash, "real_world_changed": False},
                session_id=sid, txn_id=txn.id, version=v,
            )
        u.db.flush()

    @staticmethod
    def _expand(drafts: list[Draft]) -> list[tuple[int, TransferIntent, dict]]:
        seen, out = set(), []
        for di, d in enumerate(drafts):
            f = d.fields
            for prod, qty, src, dst in cartesian(
                f["product"].candidates, f["quantity"].candidates, f["source"].candidates, f["destination"].candidates
            ):
                intent = TransferIntent(prod, int(qty), src, dst)
                if intent in seen:
                    continue
                seen.add(intent)
                statuses = {n: (EvidenceStatus.INFERRED.value if f[n].ambiguous else f[n].status.value) for n in FIELDS}
                out.append((di, intent, statuses))
                if len(out) >= MAX_BRANCHES:
                    return out
        return out

    @staticmethod
    def _simulation_hash(entries: list[tuple[str, SimulationResult, str]]) -> str:
        return hash_value([{"key": k, "result_hash": s.result_hash, "outcome": o} for k, s, o in entries])

    @staticmethod
    def _apply_changes(draft: Draft, changes: list[FieldEvidence]) -> Draft:
        for ev in changes:
            draft = draft.with_field(ev)
        return draft

    @staticmethod
    def _single_intent(draft: Draft) -> TransferIntent:
        f = draft.fields
        return TransferIntent(f["product"].value, int(f["quantity"].value), f["source"].value, f["destination"].value)

    @staticmethod
    def _drafts_of(ver: TransactionVersionRow) -> list[Draft]:
        data = json.loads(ver.intent_json)
        return [Draft({n: _ev_from_dict(ev) for n, ev in d.items()}) for d in data["drafts"]]

    @staticmethod
    def _version_row(db, txn_id: str, version: int) -> TransactionVersionRow:
        row = db.execute(
            select(TransactionVersionRow).where(
                TransactionVersionRow.transaction_id == txn_id, TransactionVersionRow.version == version
            )
        ).scalar_one_or_none()
        if row is None:
            raise MirrorError(ErrorCode.NOT_FOUND, f"Version {version} of {txn_id} not found")
        return row

    @staticmethod
    def _get_txn(db, txn_id: str) -> TransactionRow:
        txn = db.get(TransactionRow, txn_id)
        if txn is None:
            raise MirrorError(ErrorCode.NOT_FOUND, f"Transaction {txn_id} not found")
        return txn

    def _load_amendable(self, u: _Unit, txn_id: str) -> TransactionRow:
        txn = self._get_txn(u.db, txn_id)
        state = TxState(txn.state)
        if state in POST_COMMIT_STATES:
            raise MirrorError(
                ErrorCode.INVALID_TRANSACTION,
                "This transaction has already been committed; start a new request instead",
                state=state.value,
            )
        if state not in AMENDABLE_STATES:
            raise MirrorError(ErrorCode.INVALID_TRANSACTION, f"Transaction is {state.value} and cannot be changed", state=state.value)
        return txn

    def _set_state(self, u: _Unit, txn: TransactionRow, target: TxState, reason: str | None = None) -> None:
        current = TxState(txn.state)
        check_transition(current, target)
        txn.state = target.value
        txn.updated_at = utcnow()
        payload = {"from": current.value, "to": target.value, "trail": [current.value, target.value]}
        if reason:
            payload["reason"] = reason
        u.emit("STATE_CHANGED", payload, session_id=txn.session_id, txn_id=txn.id, version=txn.current_version)

    @staticmethod
    def _execution_view(e: ExecutionRow) -> dict:
        def j(x):
            return json.loads(x) if x else None

        return {
            "id": e.id,
            "kind": e.kind,
            "version": e.version,
            "status": e.status,
            "verification": e.verification,
            "attempts": e.attempts,
            "operations": j(e.operations_json),
            "erp_reported": j(e.reported_json),
            "error": j(e.error_json),
            "report": j(e.diff_json),
            "exec_latency_ms": round(e.exec_latency_ms, 2) if e.exec_latency_ms is not None else None,
            "verify_latency_ms": round(e.verify_latency_ms, 2) if e.verify_latency_ms is not None else None,
            "created_at": iso(e.created_at),
            "completed_at": iso(e.completed_at),
        }
