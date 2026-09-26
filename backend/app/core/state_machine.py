"""The transaction state machine. Every transition is checked against this table."""

from __future__ import annotations

from enum import Enum

from .errors import ErrorCode, MirrorError


class TxState(str, Enum):
    IDLE = "IDLE"
    LISTENING = "LISTENING"
    INTERPRETING = "INTERPRETING"
    FORKING = "FORKING"
    SIMULATING = "SIMULATING"
    CONSEQUENCE_REVIEW = "CONSEQUENCE_REVIEW"
    CLARIFYING = "CLARIFYING"
    WHAT_IF = "WHAT_IF"
    PREVIEW_READY = "PREVIEW_READY"
    COMMIT_ARMED = "COMMIT_ARMED"
    EXECUTING = "EXECUTING"
    VERIFYING = "VERIFYING"
    VERIFIED = "VERIFIED"
    DIVERGED = "DIVERGED"
    ROLLBACK = "ROLLBACK"
    ABORTED = "ABORTED"
    FAILED = "FAILED"


S = TxState

# IDLE and LISTENING belong to the voice session; a transaction is born in INTERPRETING.
TRANSITIONS: dict[TxState, frozenset[TxState]] = {
    S.IDLE: frozenset({S.LISTENING, S.INTERPRETING}),
    S.LISTENING: frozenset({S.IDLE, S.INTERPRETING}),
    S.INTERPRETING: frozenset({S.FORKING, S.CLARIFYING, S.FAILED, S.ABORTED}),
    S.WHAT_IF: frozenset({S.FORKING, S.CLARIFYING, S.FAILED}),
    S.FORKING: frozenset({S.SIMULATING, S.FAILED}),
    S.SIMULATING: frozenset({S.CONSEQUENCE_REVIEW, S.FAILED}),
    # CONSEQUENCE_REVIEW is also the resting state of a single, blocked future.
    S.CONSEQUENCE_REVIEW: frozenset({S.CLARIFYING, S.PREVIEW_READY, S.INTERPRETING, S.WHAT_IF, S.ABORTED}),
    S.CLARIFYING: frozenset({S.INTERPRETING, S.WHAT_IF, S.ABORTED}),
    S.PREVIEW_READY: frozenset({S.COMMIT_ARMED, S.INTERPRETING, S.WHAT_IF, S.ABORTED}),
    # A commit that fails its gate after arming falls back to a fresh interpretation.
    S.COMMIT_ARMED: frozenset({S.EXECUTING, S.PREVIEW_READY, S.INTERPRETING}),
    S.EXECUTING: frozenset({S.VERIFYING, S.FAILED}),
    S.VERIFYING: frozenset({S.VERIFIED, S.DIVERGED, S.FAILED}),
    S.VERIFIED: frozenset({S.ROLLBACK}),
    S.DIVERGED: frozenset({S.ROLLBACK}),
    # Rollback ends VERIFIED only when the restoration itself is verified.
    S.ROLLBACK: frozenset({S.VERIFIED, S.FAILED}),
    S.ABORTED: frozenset(),
    S.FAILED: frozenset(),
}

# States a transaction can sit in while waiting for the user.
RESTING_STATES = frozenset({S.CLARIFYING, S.CONSEQUENCE_REVIEW, S.PREVIEW_READY})
# States from which a new version may be derived (amend / clarify / what-if).
AMENDABLE_STATES = RESTING_STATES
TERMINAL_STATES = frozenset({S.ABORTED, S.FAILED})
# Once reality has been touched, the transaction is no longer a proposal.
POST_COMMIT_STATES = frozenset({S.COMMIT_ARMED, S.EXECUTING, S.VERIFYING, S.VERIFIED, S.DIVERGED, S.ROLLBACK})


def check_transition(current: TxState, target: TxState) -> None:
    if target not in TRANSITIONS[current]:
        raise MirrorError(
            ErrorCode.INVALID_TRANSACTION,
            f"Transition {current.value} -> {target.value} is not allowed",
            current_state=current.value,
            requested_state=target.value,
        )


def walk(start: TxState, path: list[TxState]) -> list[TxState]:
    """Validate a whole path of transitions and return the full trail including start."""
    trail = [start]
    for nxt in path:
        check_transition(trail[-1], nxt)
        trail.append(nxt)
    return trail
