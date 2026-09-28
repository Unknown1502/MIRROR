"""Entity resolution against authoritative ERP data.

Every candidate location or product comes from the world snapshot. Nothing is
invented: an unrecognized name resolves to zero candidates, not a guess.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..core.world import WorldSnapshot
from .numbers import find_numbers

# Alternate names for cities that appear in the ERP's location records.
CITY_SYNONYMS = {"bombay": "Mumbai", "bangalore": "Bengaluru", "new delhi": "Delhi"}

ORDINALS = {
    "first": 0, "1st": 0, "option a": 0, "future a": 0, "branch a": 0,
    "second": 1, "2nd": 1, "option b": 1, "future b": 1, "branch b": 1,
    "third": 2, "3rd": 2, "option c": 2, "future c": 2, "branch c": 2,
    "fourth": 3, "4th": 3, "option d": 3, "future d": 3, "branch d": 3,
}

MATCH_EXACT = "EXACT"  # the id or full registered name was said
MATCH_ALIAS = "ALIAS"  # a registered alias that names exactly one entity
MATCH_GROUP = "GROUP"  # a city or product family: may name several entities
MATCH_NUMBER = "NUMBER"


@dataclass(frozen=True)
class Mention:
    kind: str  # location | product | quantity
    text: str
    start: int
    end: int
    match: str
    candidates: tuple = field(default_factory=tuple)
    value: int | None = None

    @property
    def unique(self) -> bool:
        return len(self.candidates) == 1


def _phrase_regex(phrase: str) -> str:
    parts = [re.escape(p) for p in re.split(r"[\s\-]+", phrase.strip()) if p]
    return r"(?<![\w-])" + r"[\s\-]+".join(parts) + r"(?![\w-])"


class EntityResolver:
    def __init__(self, snapshot: WorldSnapshot):
        self.snapshot = snapshot
        patterns: list[tuple[re.Pattern, str, tuple[str, ...]]] = []
        by_city: dict[str, list[str]] = {}
        for loc in snapshot.locations.values():
            by_city.setdefault(loc.city.lower(), []).append(loc.id)
            patterns.append((re.compile(_phrase_regex(loc.id), re.I), MATCH_EXACT, (loc.id,)))
            patterns.append((re.compile(_phrase_regex(loc.name), re.I), MATCH_EXACT, (loc.id,)))
            for alias in loc.aliases:
                patterns.append((re.compile(_phrase_regex(alias), re.I), MATCH_ALIAS, (loc.id,)))
        for city, ids in by_city.items():
            patterns.append((re.compile(_phrase_regex(city), re.I), MATCH_GROUP, tuple(sorted(ids))))
        for synonym, city in CITY_SYNONYMS.items():
            ids = by_city.get(city.lower())
            if ids:
                patterns.append((re.compile(_phrase_regex(synonym), re.I), MATCH_GROUP, tuple(sorted(ids))))
        self._location_patterns = patterns

        self._product_ids = {p.id.replace("-", "").upper(): p.id for p in snapshot.products.values()}
        families: dict[str, list[str]] = {}
        for p in snapshot.products.values():
            families.setdefault(p.family.upper(), []).append(p.id)
        self._families = {k: tuple(sorted(v)) for k, v in families.items()}

    # ------------------------------------------------------------- locations

    def find_locations(self, text: str) -> list[Mention]:
        raw: list[Mention] = []
        for pattern, match, ids in self._location_patterns:
            for m in pattern.finditer(text):
                raw.append(Mention("location", m.group(), m.start(), m.end(), match, ids))
        return _non_overlapping(raw)

    def resolve_location(self, phrase: str) -> Mention | None:
        """Resolve a free-text location reference (e.g. from a tool call argument)."""
        mentions = self.find_locations(phrase)
        if not mentions:
            return None
        return max(mentions, key=lambda m: m.end - m.start)

    # --------------------------------------------------------------- products

    _PRODUCT_RE = re.compile(r"(?<![\w-])([A-Za-z])\s?\.?\s?([A-Za-z])\s*-?\s*(\d{2,6})(?:\s*-?\s*([A-Za-z])(?![\w-]))?")

    def find_products(self, text: str) -> list[Mention]:
        found: list[Mention] = []
        for m in self._PRODUCT_RE.finditer(text):
            letters = (m.group(1) + m.group(2)).upper()
            digits = m.group(3)
            suffix = (m.group(4) or "").upper()
            end = m.end()
            pid = self._product_ids.get(f"{letters}{digits}{suffix}")
            if pid is None and suffix:
                pid = self._product_ids.get(f"{letters}{digits}")
                end = m.end(3)
            if pid is not None:
                found.append(Mention("product", text[m.start():end], m.start(), end, MATCH_EXACT, (pid,)))
        taken = [(f.start, f.end) for f in found]
        for family, ids in self._families.items():
            for m in re.finditer(rf"(?<![\w-]){family}(?![\w-]*\d)(?:\s+(?:series|family|line))?(?![\w-])", text, re.I):
                if not any(s <= m.start() < e for s, e in taken):
                    found.append(Mention("product", m.group(), m.start(), m.end(), MATCH_GROUP, ids))
        return _non_overlapping(found)

    def resolve_product(self, phrase: str) -> Mention | None:
        mentions = self.find_products(phrase)
        return max(mentions, key=lambda m: m.end - m.start) if mentions else None

    # ------------------------------------------------------------- quantities

    def find_quantities(self, text: str, exclude: list[Mention] | None = None) -> list[Mention]:
        masked = [(m.start, m.end) for m in (exclude or [])]
        masked += [(m.start(), m.end()) for m in re.finditer(r"(?<![\w-])v(?:ersion)?\s?\d+(?![\w-])", text, re.I)]
        out = []
        for value, start, end, span in find_numbers(text):
            if any(s <= start < e or s < end <= e for s, e in masked):
                continue
            out.append(Mention("quantity", span, start, end, MATCH_NUMBER, (value,), value))
        return out

    def find_action_quantities(self, text: str, exclude: list[Mention] | None = None) -> list[Mention]:
        """Numbers the user said *as a quantity*: the only numbers that can be quantity evidence."""
        return [q for q in self.find_quantities(text, exclude) if in_quantity_position(text, q)]

    # ------------------------------------------------------------------ misc

    def location_name(self, location_id: str) -> str:
        loc = self.snapshot.locations.get(location_id)
        return loc.name if loc else location_id


_UNIT_AFTER = re.compile(r"^\s*(units?|pcs|pieces?|items?|boxes|cases|cartons|pallets|of)\b", re.I)
_ACTION_BEFORE = re.compile(
    r"\b(move|moving|transfer|send|ship|shift|commit|confirm|execute|make (?:it|that)|(?:change|set) (?:it|that)(?: to)?|"
    r"what if(?: (?:i|we))?(?: (?:move|send|transfer|ship|did|do))?|what about|how about|try|simulate|compare|and|or|"
    r"versus|vs\.?|instead|rather|only|just|up to|with)\s+(?:(?:only|just|about|like|maybe)\s+)?$",
    re.I,
)
# The number just before, in a spoken list: "500, 900", "500 900", "400, 500 and 900".
_LIST_BEFORE = re.compile(r"(\d+(?:,\d{3})*)\s*(?:,|&|and|or)?\s*$", re.I)
_BARE_NUMBER = re.compile(r"^\W*(?:(?:uh|um|ok|okay|yes|yeah|so|make it)\W+)?%s\W*(?:units?|please)?\W*$")


def in_quantity_position(text: str, q: Mention, _depth: int = 0) -> bool:
    """True when the grammar around a number says it is the amount to act on.

    "move 700", "700 units", "make that 500", "what about 500", "compare 500 and 900", or a bare
    "500" answering a quantity question qualify. "It's been 2 weeks" and "at 3" do not.
    """
    if _UNIT_AFTER.match(text[q.end:]):
        return True
    if _ACTION_BEFORE.search(text[max(0, q.start - 40):q.start]):
        return True
    if re.match(_BARE_NUMBER.pattern % re.escape(q.text), text.strip(), re.I):
        return True
    # A list continues an amount: in "compare 500, 900" the 900 counts because the 500 does.
    # "Site 2, 900" does not, because the 2 is not an amount.
    m = _LIST_BEFORE.search(text[max(0, q.start - 24):q.start]) if _depth < 6 else None
    if m:
        offset = max(0, q.start - 24)
        prev = Mention("quantity", m.group(1), offset + m.start(1), offset + m.end(1), MATCH_NUMBER)
        return in_quantity_position(text, prev, _depth + 1)
    return False


def find_ordinal(text: str) -> tuple[int, str] | None:
    lowered = text.lower()
    best = None
    for phrase, index in ORDINALS.items():
        m = re.search(rf"(?<![\w-]){re.escape(phrase)}(?![\w-])", lowered)
        if m and (best is None or m.start() < best[2]):
            best = (index, text[m.start():m.end()], m.start())
    return (best[0], best[1]) if best else None


def _non_overlapping(mentions: list[Mention]) -> list[Mention]:
    """Longest match wins; ties prefer the more specific match type."""
    rank = {MATCH_EXACT: 0, MATCH_ALIAS: 1, MATCH_GROUP: 2, MATCH_NUMBER: 3}
    ordered = sorted(mentions, key=lambda m: (-(m.end - m.start), rank[m.match], m.start))
    chosen: list[Mention] = []
    for m in ordered:
        if all(m.end <= c.start or m.start >= c.end for c in chosen):
            chosen.append(m)
    return sorted(chosen, key=lambda m: m.start)
