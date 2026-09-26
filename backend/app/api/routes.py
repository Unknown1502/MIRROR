"""Public HTTP API (build plan part 54), plus the extensions the UI and voice path need."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Header, Request

from ..core.errors import ErrorCode, MirrorError
from ..erp.service import Fault
from ..intent.entities import EntityResolver
from ..intent.evidence import EvidenceStatus, FieldEvidence, SourceType, from_mention, unknown
from ..runtime.transaction_runtime import CommitEvidence, Draft
from .schemas import (
    ClarifyRequest,
    CommitRequest,
    CompareRequest,
    CreateTransactionRequest,
    ExternalChangeRequest,
    FaultRequest,
    RollbackRequest,
    TransferAction,
    TurnRequest,
    WhatIfRequest,
)

router = APIRouter(prefix="/api/v1")


def container(request: Request):
    return request.app.state.container


def session_auth(request: Request, x_session_id: str | None = Header(default=None),
                 authorization: str | None = Header(default=None)) -> str:
    if not x_session_id or not authorization or not authorization.startswith("Bearer "):
        raise MirrorError(ErrorCode.UNAUTHORIZED, "X-Session-Id and 'Authorization: Bearer <session_token>' are required")
    container(request).runtime.authenticate(x_session_id, authorization[len("Bearer "):])
    return x_session_id


def _owned(request: Request, txn_id: str, session_id: str) -> None:
    view = container(request).runtime.view(txn_id)
    if view["session_id"] != session_id:
        # Do not reveal that another session's transaction exists.
        raise MirrorError(ErrorCode.NOT_FOUND, f"Transaction {txn_id} not found")


def optional_session(request: Request, x_session_id: str | None = Header(default=None),
                     authorization: str | None = Header(default=None)) -> str | None:
    """The caller's session if they sent valid credentials, else None (shared-world reads)."""
    if not x_session_id or not authorization:
        return None
    return session_auth(request, x_session_id, authorization)


def demo_guard(request: Request, x_demo_token: str | None = Header(default=None),
               session: str | None = Depends(optional_session)) -> str | None:
    """Demo controls. With sandboxes on they act on the caller's own world, so a session is required."""
    s = container(request).settings
    if not s.demo_mode:
        raise MirrorError(ErrorCode.DEMO_DISABLED, "Demo controls are disabled (DEMO_MODE=false)")
    if s.sandbox_per_session:
        if session is None:
            raise MirrorError(ErrorCode.UNAUTHORIZED, "Demo controls act on your own sandbox: send your session credentials")
        return session
    if s.demo_admin_token and x_demo_token != s.demo_admin_token:
        raise MirrorError(ErrorCode.UNAUTHORIZED, "Demo controls require X-Demo-Token")
    return session


def _structured(action: TransferAction, resolver: EntityResolver) -> Draft:
    """Structured input is authoritative about *what was asked*, but names still resolve against ERP data."""
    fields = {}
    if action.quantity is not None:
        fields["quantity"] = FieldEvidence("quantity", (action.quantity,), EvidenceStatus.EXPLICIT, SourceType.STRUCTURED_INPUT,
                                           text_span=str(action.quantity), note="structured input")
    for name, raw, finder in (
        ("product", action.product, resolver.resolve_product),
        ("source", action.source, resolver.resolve_location),
        ("destination", action.destination, resolver.resolve_location),
    ):
        if raw:
            m = finder(raw)
            fields[name] = (
                from_mention(name, m, None, SourceType.STRUCTURED_INPUT) if m else unknown(name, f"'{raw}' is not in the ERP")
            )
    for name in ("product", "quantity", "source", "destination"):
        fields.setdefault(name, unknown(name))
    return Draft(fields)


# ------------------------------------------------------------------ health


@router.get("/health")
def health(request: Request):
    c = container(request)
    return {
        "status": "ok",
        "voice_available": c.settings.voice_available,
        "demo_mode": c.settings.demo_mode,
        "fault_injection": c.settings.demo_fault_injection,
        "world_revision": c.erp.revision(),
        "sandbox_per_session": c.settings.sandbox_per_session,
        "llm_gateway": c.dispatcher.interpreter.status() if c.dispatcher.interpreter else None,
        "ledger": c.runtime.verify_ledger(),
    }


# ---------------------------------------------------------------- sessions


@router.post("/sessions")
def create_session(request: Request):
    c = container(request)
    session_id, token = c.runtime.create_session()
    return {
        "session_id": session_id,
        "session_token": token,
        "voice_available": c.settings.voice_available,
        "demo_mode": c.settings.demo_mode,
        "fault_injection": c.settings.demo_fault_injection,
        "sandbox": c.settings.sandbox_per_session,
        "voice_max_session_seconds": c.settings.voice_max_session_seconds,
        "llm_gateway": c.dispatcher.interpreter.status() if c.dispatcher.interpreter else None,
    }


@router.get("/sessions/{session_id}")
def get_session(session_id: str, request: Request, auth: str = Depends(session_auth)):
    if auth != session_id:
        raise MirrorError(ErrorCode.UNAUTHORIZED, "Token does not belong to this session")
    return container(request).runtime.session_info(session_id)


@router.post("/sessions/{session_id}/turns")
def post_turn(session_id: str, body: TurnRequest, request: Request, auth: str = Depends(session_auth)):
    if auth != session_id:
        raise MirrorError(ErrorCode.UNAUTHORIZED, "Token does not belong to this session")
    return container(request).dispatcher.handle(session_id, body.text, body.context_version).as_dict()


# ------------------------------------------------------------ transactions


@router.post("/transactions")
def create_transaction(body: CreateTransactionRequest, request: Request, auth: str = Depends(session_auth)):
    c = container(request)
    draft = _structured(body.action, EntityResolver(c.runtime.erp_for(auth).read_snapshot()))
    label = body.utterance or "structured request"
    return c.runtime.propose(auth, [draft], label=label)


@router.get("/transactions/{txn_id}")
def get_transaction(txn_id: str, request: Request, auth: str = Depends(session_auth)):
    _owned(request, txn_id, auth)
    return container(request).runtime.view(txn_id)


@router.post("/transactions/{txn_id}/what-if")
def what_if(txn_id: str, body: WhatIfRequest, request: Request, auth: str = Depends(session_auth)):
    _owned(request, txn_id, auth)
    c = container(request)
    draft = _structured(body, EntityResolver(c.runtime.erp_for(auth).read_snapshot()))
    changes = [ev for ev in draft.fields.values() if ev.candidates]
    if not changes:
        raise MirrorError(ErrorCode.INVALID_REQUEST, "A what-if needs at least one changed field")
    return c.runtime.what_if(txn_id, changes, label="What if " + ", ".join(f"{e.field}={e.text_span}" for e in changes))


@router.post("/transactions/{txn_id}/clarify")
def clarify(txn_id: str, body: ClarifyRequest, request: Request, auth: str = Depends(session_auth)):
    _owned(request, txn_id, auth)
    c = container(request)
    if body.branch_key:
        return c.runtime.select_branch(txn_id, body.branch_key)
    if body.field and body.value is not None:
        draft = _structured(TransferAction(**{body.field: body.value}), EntityResolver(c.runtime.erp_for(auth).read_snapshot()))
        ev = draft.fields[body.field]
        return c.runtime.clarify(txn_id, field=body.field, evidence=ev, label=f"Clarified {body.field}")
    raise MirrorError(ErrorCode.INVALID_REQUEST, "Provide branch_key, or field and value")


@router.post("/transactions/{txn_id}/compare")
def compare(txn_id: str, body: CompareRequest, request: Request, auth: str = Depends(session_auth)):
    _owned(request, txn_id, auth)
    return container(request).runtime.compare(txn_id, quantities=body.quantities, versions=body.versions)


@router.post("/transactions/{txn_id}/optimize")
def optimize(txn_id: str, request: Request, auth: str = Depends(session_auth)):
    _owned(request, txn_id, auth)
    req = FieldEvidence("quantity", (), EvidenceStatus.RESOLVED, SourceType.USER_SELECTION, text_span="maximum safe quantity")
    return container(request).runtime.optimize(txn_id, req, label="Maximum safe quantity")


@router.post("/transactions/{txn_id}/commit")
def commit(txn_id: str, body: CommitRequest, request: Request, auth: str = Depends(session_auth)):
    _owned(request, txn_id, auth)
    # The explicit user action here is the authenticated request carrying the token of the preview they saw.
    evidence = CommitEvidence(True, SourceType.USER_SELECTION, text_span="commit request")
    return container(request).runtime.commit(
        txn_id, version=body.version, token=body.commit_token, idempotency_key=body.idempotency_key,
        evidence=evidence, session_id=auth,
    )


@router.post("/transactions/{txn_id}/rollback")
def rollback(txn_id: str, body: RollbackRequest, request: Request, auth: str = Depends(session_auth)):
    _owned(request, txn_id, auth)
    return container(request).runtime.rollback(txn_id, idempotency_key=body.idempotency_key, session_id=auth)


@router.post("/transactions/{txn_id}/abort")
def abort(txn_id: str, request: Request, auth: str = Depends(session_auth)):
    _owned(request, txn_id, auth)
    return container(request).runtime.abort(txn_id)


@router.post("/transactions/{txn_id}/verify")
def reverify(txn_id: str, request: Request, auth: str = Depends(session_auth)):
    _owned(request, txn_id, auth)
    return container(request).runtime.reverify(txn_id)


@router.get("/transactions/{txn_id}/audit")
def audit(txn_id: str, request: Request, auth: str = Depends(session_auth)):
    _owned(request, txn_id, auth)
    return container(request).runtime.audit(txn_id)


# ------------------------------------------------------------ world / audit


@router.get("/world")
def world(request: Request, session: str | None = Depends(optional_session)):
    return container(request).runtime.world(session)


@router.get("/audit/verify")
def verify_ledger(request: Request):
    return container(request).runtime.verify_ledger()


@router.get("/metrics")
def metrics(request: Request, auth: str = Depends(session_auth)):
    c = container(request)
    out = c.runtime.metrics(auth)
    voice = request.app.state.voice
    bridge = voice.bridges.get(auth)
    out["voice"] = bridge.metrics.as_dict() if bridge else None
    out["voice_remaining_seconds"] = voice.remaining_seconds(auth)
    return out


# -------------------------------------------------------------------- demo


@router.get("/demo/status")
def demo_status(request: Request, session: str | None = Depends(optional_session)):
    c = container(request)
    return {
        "demo_mode": c.settings.demo_mode,
        "fault_injection": c.settings.demo_fault_injection,
        "sandbox_per_session": c.settings.sandbox_per_session,
        "armed_fault": c.runtime.erp_for(session).armed_fault().value,
        "faults": [f.value for f in Fault],
    }


@router.post("/demo/reset")
def demo_reset(request: Request, session: str | None = Depends(demo_guard)):
    return container(request).runtime.reset_demo(session)


@router.post("/demo/faults")
def demo_fault(body: FaultRequest, request: Request, session: str | None = Depends(demo_guard)):
    c = container(request)
    if not c.settings.demo_fault_injection:
        raise MirrorError(ErrorCode.DEMO_DISABLED, "Fault injection is disabled (DEMO_FAULT_INJECTION=false)")
    return c.runtime.arm_fault(Fault(body.fault), session)


@router.post("/demo/external-change")
def demo_external_change(body: ExternalChangeRequest, request: Request, session: str | None = Depends(demo_guard)):
    return container(request).runtime.external_change(body.location_id, body.product_id, body.delta, body.note, session)
