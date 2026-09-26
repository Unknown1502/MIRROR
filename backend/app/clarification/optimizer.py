"""Consequence-aware clarification (build plan parts 14-15).

For every field that differs between candidate futures:

    score = ambiguity x consequence_divergence x risk_weight

The question asked is the one whose answer most changes the decision, and it names
the consequences instead of just asking "which one?".
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations

from ..core.world import WorldSnapshot

FIELD_ORDER = ("destination", "source", "product", "quantity")
FIELD_RISK = {"destination": 1.0, "source": 1.0, "product": 1.0, "quantity": 0.9}
SEVERITY_RISK = {"CRITICAL": 1.0, "HIGH": 1.0, "MEDIUM": 0.6, "LOW": 0.3, "INFO": 0.2}
FIELD_LABEL = {"destination": "destination", "source": "source location", "product": "product", "quantity": "quantity"}
NUMBER_WORDS = {2: "two", 3: "three", 4: "four", 5: "five", 6: "six", 7: "seven", 8: "eight"}


@dataclass(frozen=True)
class BranchSummary:
    key: str
    intent: dict  # product_id, quantity, source_id, destination_id
    outcome: str
    consequences: tuple[dict, ...]
    draft_index: int = 0

    def value(self, field: str):
        return self.intent[{"product": "product_id", "quantity": "quantity", "source": "source_id", "destination": "destination_id"}[field]]

    def risk_codes(self) -> set[str]:
        return {c["code"] for c in self.consequences if c["severity"] != "INFO"}


def _jaccard_distance(a: set, b: set) -> float:
    if not a and not b:
        return 0.0
    return 1.0 - len(a & b) / len(a | b)


def score_fields(branches: list[BranchSummary]) -> list[dict]:
    scores = []
    for field in FIELD_ORDER:
        values = {b.value(field) for b in branches}
        if len(values) < 2:
            continue
        k = len(values)
        ambiguity = (k - 1) / k
        pairs = [(a, b) for a, b in combinations(branches, 2) if a.value(field) != b.value(field)]
        divergence = sum(
            1.0 if a.outcome != b.outcome else _jaccard_distance(a.risk_codes(), b.risk_codes()) for a, b in pairs
        ) / len(pairs)
        severities = [c["severity"] for b in branches for c in b.consequences if c["severity"] != "INFO"]
        severity_factor = max((SEVERITY_RISK[s] for s in severities), default=SEVERITY_RISK["INFO"])
        risk = FIELD_RISK[field] * severity_factor
        # A floor keeps outcome-identical ambiguity askable; it still ranks last.
        score = ambiguity * max(divergence, 0.05) * risk
        scores.append(
            {
                "field": field,
                "candidates": k,
                "ambiguity": round(ambiguity, 4),
                "consequence_divergence": round(divergence, 4),
                "risk_weight": round(risk, 4),
                "score": round(score, 4),
            }
        )
    scores.sort(key=lambda s: (-s["score"], FIELD_ORDER.index(s["field"])))
    return scores


def outcome_phrase(branch: BranchSummary, snapshot: WorldSnapshot) -> str:
    def name(loc_id: str) -> str:
        loc = snapshot.locations.get(loc_id)
        return loc.name if loc else loc_id

    blocking = [c for c in branch.consequences if c["severity"] in ("CRITICAL", "HIGH")]
    if branch.outcome == "BLOCKED" and blocking:
        c = blocking[0]
        code, delta = c["code"], c.get("delta")
        if code == "DESTINATION_CAPACITY_EXCEEDED":
            return f"would exceed capacity by {int(delta)} units"
        if code == "SOURCE_SAFETY_STOCK_BREACH":
            return f"would leave {name(branch.intent['source_id'])} {int(-delta)} units below safety stock"
        if code == "SOURCE_INSUFFICIENT_INVENTORY":
            return f"needs {int(-delta)} more units than {name(branch.intent['source_id'])} holds"
        if code == "ROUTE_UNAVAILABLE":
            return "has no available route right now"
        if code == "NO_ROUTE":
            return "has no route"
        if code == "SAME_LOCATION":
            return "would move stock onto itself"
        if code == "ABOVE_MAXIMUM_TRANSFER":
            return f"exceeds the maximum single transfer of {int(c['threshold'])}"
        if code == "BELOW_MINIMUM_TRANSFER":
            return f"is below the minimum transfer of {int(c['threshold'])}"
        return "is blocked: " + c["message"]
    if branch.outcome == "WARNING":
        warn = next((c for c in branch.consequences if c["severity"] == "MEDIUM"), None)
        if warn and warn["code"] == "SOURCE_AT_SAFETY_FLOOR":
            return f"is valid but leaves {name(branch.intent['source_id'])} at its safety-stock floor"
        if warn and warn["code"] == "DESTINATION_NEAR_CAPACITY":
            return f"is valid but nearly fills {name(branch.intent['destination_id'])}"
        return "is valid with warnings"
    return "remains valid"


def _option_labels(field: str, values: list, snapshot: WorldSnapshot) -> tuple[dict, str | None]:
    """Human labels for each candidate value, plus a group phrase like 'two Pune sites'."""
    if field in ("source", "destination"):
        locs = [snapshot.locations[v] for v in values if v in snapshot.locations]
        cities = {loc.city for loc in locs}
        if len(cities) == 1 and len(locs) == len(values):
            city = next(iter(cities))
            kinds = {loc.kind for loc in locs}
            noun = "sites" if kinds == {"SITE"} else "locations"
            labels = {loc.id: (loc.name[len(city):].strip() or loc.name) for loc in locs}
            return labels, f"{NUMBER_WORDS.get(len(values), len(values))} {city} {noun}"
        return {v: snapshot.locations[v].name if v in snapshot.locations else str(v) for v in values}, None
    if field == "product":
        return {v: str(v) for v in values}, None
    return {v: f"{v} units" for v in values}, None


def build_clarification(branches: list[BranchSummary], snapshot: WorldSnapshot, drafts: int = 1) -> dict:
    if drafts > 1 and len({b.draft_index for b in branches}) > 1:
        return _role_clarification(branches, snapshot)

    scores = score_fields(branches)
    if not scores:
        return {"field": None, "question": "Which of these futures do you want?", "options": [], "scores": []}
    field = scores[0]["field"]
    values: list = []
    for b in branches:
        if b.value(field) not in values:
            values.append(b.value(field))
    labels, group = _option_labels(field, values, snapshot)

    options = []
    for v in values:
        members = [b for b in branches if b.value(field) == v]
        # Summarize the option by its best future: that is what choosing it makes possible.
        rank = {"VALID": 0, "WARNING": 1, "BLOCKED": 2}
        best = min(members, key=lambda b: (rank.get(b.outcome, 3), b.key))
        options.append(
            {
                "value": v,
                "label": labels[v],
                "outcome": best.outcome,
                "summary": outcome_phrase(best, snapshot),
                "branch_keys": [b.key for b in members],
            }
        )

    clauses = "; ".join(f"{o['label']} {o['summary']}" for o in options)
    if group:
        question = f"There are {group}. {clauses}. Which one do you mean?"
    else:
        question = f"That could mean {len(options)} different {FIELD_LABEL[field]}s. {clauses}. Which one do you mean?"
    question = question[0].upper() + question[1:]
    return {"field": field, "question": question, "options": options, "scores": scores}


def _role_clarification(branches: list[BranchSummary], snapshot: WorldSnapshot) -> dict:
    by_draft: dict[int, BranchSummary] = {}
    for b in branches:
        by_draft.setdefault(b.draft_index, b)
    options = []
    for idx, b in sorted(by_draft.items()):
        role = "source" if idx == 0 else "destination"
        options.append(
            {
                "value": role,
                "label": f"as the {role}",
                "outcome": b.outcome,
                "summary": outcome_phrase(b, snapshot),
                "branch_keys": [x.key for x in branches if x.draft_index == idx],
            }
        )
    clauses = "; ".join(f"{o['label']}, the transfer {o['summary']}" for o in options)
    return {
        "field": "role",
        "question": f"Did you mean that location as the source or the destination? {clauses[0].upper() + clauses[1:]}.",
        "options": options,
        "scores": [],
    }


def evidence_clarification(field: str, value, status: str, snapshot: WorldSnapshot) -> dict:
    """Single future, but a field is only inferred or unknown."""
    if status == "UNKNOWN":
        prompts = {
            "quantity": "How many units should move?",
            "product": "Which product?",
            "source": "Which location should the stock come from?",
            "destination": "Which location should receive the stock?",
        }
        return {"field": field, "kind": "MISSING", "question": prompts[field], "options": [], "scores": []}
    shown = snapshot.locations[value].name if field in ("source", "destination") and value in snapshot.locations else value
    noun = {"quantity": "units", "product": "", "source": "as the source", "destination": "as the destination"}[field]
    return {
        "field": field,
        "kind": "CONFIRM",
        "question": f"I didn't hear the {FIELD_LABEL[field]} myself. Do you mean {shown} {noun}?".replace("  ", " ").replace(" ?", "?"),
        "options": [{"value": value, "label": str(shown), "outcome": None, "summary": "confirm", "branch_keys": []}],
        "scores": [],
    }
