"""Deterministic execution policy.

The simulator reports facts. The policy engine decides which facts block execution,
and whether the evidence behind the intent is strong enough to act on. Nothing the
language model says can change a decision made here.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from ..simulation.simulator import SimulationResult

# Consequence codes that make a future non-executable.
BLOCKING_CODES = frozenset(
    {
        "UNKNOWN_PRODUCT",
        "UNKNOWN_LOCATION",
        "INVALID_QUANTITY",
        "SAME_LOCATION",
        "BELOW_MINIMUM_TRANSFER",
        "ABOVE_MAXIMUM_TRANSFER",
        "NO_ROUTE",
        "ROUTE_UNAVAILABLE",
        "SOURCE_NOT_STOCKED",
        "DESTINATION_NOT_STOCKED",
        "SOURCE_INSUFFICIENT_INVENTORY",
        "SOURCE_SAFETY_STOCK_BREACH",
        "DESTINATION_CAPACITY_EXCEEDED",
    }
)
WARNING_CODES = frozenset({"SOURCE_AT_SAFETY_FLOOR", "DESTINATION_NEAR_CAPACITY"})

# Evidence statuses that may authorize execution (build plan part 35).
EXECUTABLE_EVIDENCE = frozenset({"EXPLICIT", "RESOLVED"})


class Outcome(str, Enum):
    VALID = "VALID"
    WARNING = "WARNING"
    BLOCKED = "BLOCKED"


class Decision(str, Enum):
    ALLOW = "ALLOW"
    REQUIRE_CLARIFICATION = "REQUIRE_CLARIFICATION"
    BLOCK = "BLOCK"


@dataclass(frozen=True)
class RuleResult:
    rule: str
    passed: bool
    detail: str

    def as_dict(self) -> dict:
        return {"rule": self.rule, "passed": self.passed, "detail": self.detail}


@dataclass(frozen=True)
class PolicyResult:
    decision: Decision
    outcome: Outcome
    rules: tuple[RuleResult, ...]
    blocking_codes: tuple[str, ...]

    def as_dict(self) -> dict:
        return {
            "decision": self.decision.value,
            "outcome": self.outcome.value,
            "rules": [r.as_dict() for r in self.rules],
            "blocking_codes": list(self.blocking_codes),
        }


def outcome_of(sim: SimulationResult) -> Outcome:
    codes = sim.codes()
    if codes & BLOCKING_CODES:
        return Outcome.BLOCKED
    if codes & WARNING_CODES:
        return Outcome.WARNING
    return Outcome.VALID


def evaluate(sim: SimulationResult, evidence_status: dict[str, str], candidate_count: int) -> PolicyResult:
    """Evaluate one branch. `evidence_status` maps field -> EXPLICIT/RESOLVED/INFERRED/UNKNOWN."""
    blocking = tuple(sorted(sim.codes() & BLOCKING_CODES))
    weak = sorted(f for f, s in evidence_status.items() if s not in EXECUTABLE_EVIDENCE)
    rules = (
        RuleResult(
            "SIMULATION_CONSTRAINTS",
            not blocking,
            "no blocking consequences" if not blocking else "blocked by " + ", ".join(blocking),
        ),
        RuleResult(
            "EVIDENCE_ELIGIBILITY",
            not weak,
            "every field is explicit or deterministically resolved"
            if not weak
            else "needs clarification: " + ", ".join(f"{f}={evidence_status[f]}" for f in weak),
        ),
        RuleResult(
            "SINGLE_INTERPRETATION",
            candidate_count == 1,
            "one candidate future" if candidate_count == 1 else f"{candidate_count} candidate futures",
        ),
    )
    outcome = outcome_of(sim)
    if blocking:
        decision = Decision.BLOCK
    elif weak or candidate_count != 1:
        decision = Decision.REQUIRE_CLARIFICATION
    else:
        decision = Decision.ALLOW
    return PolicyResult(decision, outcome, rules, blocking)
