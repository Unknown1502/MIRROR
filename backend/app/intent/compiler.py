"""Deterministic intent compiler.

Turns one user utterance into a typed Command with evidence spans. It is used for
typed commands, and to extract evidence from final voice transcripts so that tool
calls proposed by the language model can be checked against what was actually said.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum

from .entities import EntityResolver, Mention, find_ordinal, in_quantity_position


class CommandKind(str, Enum):
    PROPOSE = "PROPOSE"
    AMEND = "AMEND"
    WHAT_IF = "WHAT_IF"
    COMPARE = "COMPARE"
    OPTIMIZE = "OPTIMIZE"
    COMMIT = "COMMIT"
    ROLLBACK = "ROLLBACK"
    ABORT = "ABORT"
    QUERY = "QUERY"
    UNKNOWN = "UNKNOWN"  # may still be a clarification answer, depending on context


_ROLLBACK = re.compile(r"\b(roll\s?back|undo|revert|reverse (it|that|the transfer))\b", re.I)
_ABORT = re.compile(r"\b(cancel|abort|never\s?mind|forget (it|that)|scrap (it|that))\b", re.I)
_COMPARE = re.compile(r"\b(compare|versus|vs\.?)\b", re.I)
_OPTIMIZE = re.compile(
    r"\b(as (much|many) as (possible|we can|i can)|max(imum)? (safe|possible)|largest safe|most (we|i) can)\b", re.I
)
_WHAT_IF = re.compile(r"\b(what if|what about|how about|suppose|simulate|try)\b", re.I)
_COMMIT = re.compile(r"\b(commit|confirm|execute|approve|go ahead|do it|proceed|make it so)\b", re.I)
_NEGATION = re.compile(
    r"\b(don'?t|do not|not yet|never|cancel|stop|hold on|wait|what if|what about|how about|should i|can i|could i|would it)\b",
    re.I,
)
_AMEND = re.compile(
    r"\b(actually|instead|make (it|that)|change (it|that)|i meant|i mean|rather|correction|scratch that)\b", re.I
)
_QUERY = re.compile(r"\b(how (much|many)|what('?s| is) the (stock|inventory|level)|show me|stock level)\b", re.I)
_PROPOSE = re.compile(r"\b(move|transfer|send|ship|relocate|rebalance|shift)\b", re.I)
_SOURCE_PREP = re.compile(r"\b(from|out of)\s+(the\s+)?$", re.I)
_DEST_PREP = re.compile(r"\b(to|into|towards|over to)\s+(the\s+)?$", re.I)
_ROLE_WORD = re.compile(r"\b(as (the )?)?(source|origin|destination|target)\b", re.I)


@dataclass
class Command:
    kind: CommandKind
    text: str
    product: Mention | None = None
    quantity: Mention | None = None
    source: Mention | None = None
    destination: Mention | None = None
    bare_locations: list[Mention] = field(default_factory=list)
    quantities: list[Mention] = field(default_factory=list)
    ordinal: tuple[int, str] | None = None
    role_word: str | None = None

    def summary(self) -> dict:
        def m(x: Mention | None):
            return None if x is None else {"text": x.text, "candidates": list(x.candidates), "match": x.match}

        return {
            "kind": self.kind.value,
            "product": m(self.product),
            "quantity": m(self.quantity),
            "source": m(self.source),
            "destination": m(self.destination),
            "bare_locations": [m(b) for b in self.bare_locations],
            "quantities": [q.value for q in self.quantities],
            "ordinal": self.ordinal[0] if self.ordinal else None,
        }


def has_commit_language(text: str) -> bool:
    """An explicit, un-negated instruction to commit. Questions and what-ifs never count."""
    return bool(_COMMIT.search(text)) and not _NEGATION.search(text) and "?" not in text


def compile_utterance(text: str, resolver: EntityResolver) -> Command:
    products = resolver.find_products(text)
    locations = resolver.find_locations(text)
    all_numbers = resolver.find_quantities(text, exclude=products + locations)
    # Only numbers said as an amount count ("move 700", "make that 500"); "it's been 2 weeks" does not.
    quantities = [q for q in all_numbers if in_quantity_position(text, q)]

    cmd = Command(kind=_classify(text, products, locations, quantities), text=text)
    cmd.product = products[0] if products else None
    cmd.quantities = quantities
    cmd.quantity = quantities[0] if quantities else None
    cmd.ordinal = find_ordinal(text)
    role = _ROLE_WORD.search(text)
    cmd.role_word = role.group(3).lower() if role else None

    unassigned: list[Mention] = []
    for loc in locations:
        before = text[: loc.start]
        if _SOURCE_PREP.search(before) and cmd.source is None:
            cmd.source = loc
        elif _DEST_PREP.search(before) and cmd.destination is None:
            cmd.destination = loc
        else:
            unassigned.append(loc)
    # "Mumbai to Pune": a location immediately followed by "to <destination>" is the source.
    if cmd.source is None and cmd.destination is not None:
        for loc in list(unassigned):
            between = text[loc.end : cmd.destination.start]
            if loc.end <= cmd.destination.start and re.fullmatch(r"\s*(to|into)\s+(the\s+)?", between, re.I):
                cmd.source = loc
                unassigned.remove(loc)
                break
    cmd.bare_locations = unassigned
    return cmd


def _classify(text, products, locations, quantities) -> CommandKind:
    if _ROLLBACK.search(text):
        return CommandKind.ROLLBACK
    if _COMPARE.search(text):
        return CommandKind.COMPARE
    if _OPTIMIZE.search(text):
        return CommandKind.OPTIMIZE
    if _WHAT_IF.search(text):
        return CommandKind.WHAT_IF
    if has_commit_language(text):
        return CommandKind.COMMIT
    if _ABORT.search(text):
        return CommandKind.ABORT
    if _AMEND.search(text):
        return CommandKind.AMEND
    if _QUERY.search(text):
        return CommandKind.QUERY
    if _PROPOSE.search(text) and (products or locations or quantities):
        return CommandKind.PROPOSE
    return CommandKind.UNKNOWN
