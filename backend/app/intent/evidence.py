"""Evidence engine: every intent field carries provenance (build plan parts 35-36).

    EXPLICIT  - the user said the value itself                     -> may execute
    RESOLVED  - deterministic resolution of what the user said     -> may execute
    INFERRED  - a guess (ambiguous reference, or model-only value) -> clarification required
    UNKNOWN   - nothing supports a value                           -> clarification required
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum

from .entities import MATCH_ALIAS, MATCH_EXACT, MATCH_GROUP, MATCH_NUMBER, EntityResolver, Mention, in_quantity_position

FIELDS = ("product", "quantity", "source", "destination")


class EvidenceStatus(str, Enum):
    EXPLICIT = "EXPLICIT"
    RESOLVED = "RESOLVED"
    INFERRED = "INFERRED"
    UNKNOWN = "UNKNOWN"


class SourceType(str, Enum):
    USER_SPEECH = "USER_SPEECH"
    USER_TEXT = "USER_TEXT"
    USER_SELECTION = "USER_SELECTION"
    STRUCTURED_INPUT = "STRUCTURED_INPUT"
    MODEL_PROPOSAL = "MODEL_PROPOSAL"
    RUNTIME_OPTIMIZER = "RUNTIME_OPTIMIZER"
    NONE = "NONE"


@dataclass(frozen=True)
class Utterance:
    item_id: str
    text: str
    source: str  # VOICE | TEXT

    @property
    def source_type(self) -> SourceType:
        return SourceType.USER_SPEECH if self.source == "VOICE" else SourceType.USER_TEXT


@dataclass(frozen=True)
class FieldEvidence:
    field: str
    candidates: tuple
    status: EvidenceStatus
    source_type: SourceType
    transcript_item_id: str | None = None
    text_span: str | None = None
    note: str = ""
    evidence_id: str | None = None  # assigned when persisted; reused when carried forward

    @property
    def value(self):
        return self.candidates[0] if len(self.candidates) == 1 else None

    @property
    def ambiguous(self) -> bool:
        return len(self.candidates) > 1

    def with_id(self, evidence_id: str) -> "FieldEvidence":
        return replace(self, evidence_id=evidence_id)

    def as_dict(self) -> dict:
        return {
            "evidence_id": self.evidence_id,
            "field": self.field,
            "value": self.value,
            "candidates": list(self.candidates),
            "status": self.status.value,
            "ambiguous": self.ambiguous,
            "source": {
                "type": self.source_type.value,
                "transcript_item_id": self.transcript_item_id,
                "text_span": self.text_span,
            },
            "note": self.note,
        }


def unknown(field: str, note: str = "not stated") -> FieldEvidence:
    return FieldEvidence(field, (), EvidenceStatus.UNKNOWN, SourceType.NONE, note=note)


def from_mention(field: str, mention: Mention, utterance: Utterance | None, source_type: SourceType) -> FieldEvidence:
    if mention.match == MATCH_NUMBER:
        status, note = EvidenceStatus.EXPLICIT, "number stated"
        candidates = (mention.value,)
    elif mention.match == MATCH_EXACT:
        status, note = EvidenceStatus.EXPLICIT, "registered name stated"
        candidates = mention.candidates
    elif mention.match == MATCH_ALIAS:
        status, note = EvidenceStatus.RESOLVED, "registered alias resolves to exactly one entity"
        candidates = mention.candidates
    elif mention.match == MATCH_GROUP and mention.unique:
        status, note = EvidenceStatus.RESOLVED, "only one entity matches"
        candidates = mention.candidates
    else:
        status = EvidenceStatus.INFERRED
        note = f"'{mention.text}' matches {len(mention.candidates)} entities"
        candidates = mention.candidates
    return FieldEvidence(
        field,
        tuple(candidates),
        status,
        source_type,
        transcript_item_id=utterance.item_id if utterance else None,
        text_span=mention.text,
        note=note,
    )


def _mentions(field: str, text: str, resolver: EntityResolver) -> list[Mention]:
    products = resolver.find_products(text)
    locations = resolver.find_locations(text)
    if field == "product":
        return products
    if field in ("source", "destination"):
        return locations
    return resolver.find_quantities(text, exclude=products + locations)


def check_model_value(field: str, model_value, utterances: list[Utterance], resolver: EntityResolver) -> FieldEvidence:
    """Validate a value proposed by the language model against what the user actually said.

    The model's value is only a pointer. Evidence always comes from the transcript:
    if the user's words are ambiguous, the field stays ambiguous even when the model
    picked one interpretation; if the user never said it, the value is INFERRED.
    """
    if model_value in (None, ""):
        return unknown(field)

    if field == "quantity":
        try:
            q = int(model_value)
        except (TypeError, ValueError):
            return unknown(field, f"model supplied non-numeric quantity {model_value!r}")
        # Witness-first: collect the amounts the user said *as amounts* before looking at the model's
        # number, so an unrelated number ("it's been 2 weeks") can never be picked up as evidence.
        witnesses = []
        said_elsewhere = None
        for utt in utterances:
            for m in _mentions(field, utt.text, resolver):
                if in_quantity_position(utt.text, m):
                    witnesses.append((m, utt))
                elif m.value == q and said_elsewhere is None:
                    said_elsewhere = m
        for m, utt in witnesses:
            if m.value == q:
                return from_mention(field, m, utt, utt.source_type)
        if said_elsewhere is not None:
            return FieldEvidence(
                field, (q,), EvidenceStatus.INFERRED, SourceType.MODEL_PROPOSAL, text_span=said_elsewhere.text,
                note=f"{q} was said, but not as the amount to move",
            )
        return FieldEvidence(
            field, (q,), EvidenceStatus.INFERRED, SourceType.MODEL_PROPOSAL,
            note=f"model proposed {q} but no recent utterance contains it",
        )

    model_value = _bounded(model_value)
    model_mention = resolver.resolve_product(model_value) if field == "product" else resolver.resolve_location(model_value)
    model_ids = set(model_mention.candidates) if model_mention else set()
    for utt in utterances:
        for m in _mentions(field, utt.text, resolver):
            if model_ids & set(m.candidates):
                ev = from_mention(field, m, utt, utt.source_type)
                if field in ("source", "destination"):
                    said_as = _spoken_role(utt.text, m, resolver)
                    if said_as is not None and said_as != field and not ev.ambiguous:
                        # "from Mumbai to Delhi" with the model's roles swapped: the place was said, the role was not.
                        return replace(ev, status=EvidenceStatus.INFERRED,
                                       note=f"'{m.text}' was said as the {said_as}, not the {field}")
                return ev
    if not model_ids:
        return unknown(field, f"'{model_value}' does not match any {('product' if field == 'product' else 'location')} in the ERP")
    return FieldEvidence(
        field, tuple(sorted(model_ids)), EvidenceStatus.INFERRED, SourceType.MODEL_PROPOSAL,
        text_span=str(model_value), note="model proposed a value the user did not say",
    )


def _spoken_role(text: str, mention: Mention, resolver: EntityResolver) -> str | None:
    """The role the user's own grammar gave this location ('from X' / 'to X'), if any."""
    from .compiler import compile_utterance  # local import: the compiler is a sibling consumer of entities

    cmd = compile_utterance(text, resolver)
    for role, m in (("source", cmd.source), ("destination", cmd.destination)):
        if m is not None and (m.start, m.end) == (mention.start, mention.end):
            return role
    return None


def _bounded(value) -> str:
    """Model output is untrusted: keep it short and printable before it is stored or echoed."""
    text = "".join(ch for ch in str(value) if ch.isprintable())
    return text[:60]


def evidence_hash_input(fields: dict[str, FieldEvidence]) -> dict:
    return {
        name: {
            "evidence_id": ev.evidence_id,
            "candidates": list(ev.candidates),
            "status": ev.status.value,
            "source_type": ev.source_type.value,
            "transcript_item_id": ev.transcript_item_id,
            "text_span": ev.text_span,
        }
        for name, ev in sorted(fields.items())
    }
