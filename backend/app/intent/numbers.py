"""Spoken and written quantity parsing ("700", "1,200", "seven hundred", "twelve hundred")."""

from __future__ import annotations

import re

_UNITS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
    "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19,
}
_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90}
_SCALES = {"hundred": 100, "thousand": 1000}
_NUMBER_WORDS = set(_UNITS) | set(_TENS) | set(_SCALES)

WORD_SEQUENCE = re.compile(
    r"\b(?:a\s+)?(?:(?:%s)(?:[\s-]+(?:and[\s-]+)?)?)+\b" % "|".join(sorted(_NUMBER_WORDS, key=len, reverse=True)),
    re.IGNORECASE,
)
DIGITS = re.compile(r"(?<![\w.-])\d{1,3}(?:,\d{3})+(?![\w-])|(?<![\w.-])\d+(?![\w-])")


def words_to_int(phrase: str) -> int | None:
    tokens = [t for t in re.split(r"[\s-]+", phrase.lower().strip()) if t and t != "and"]
    if tokens and tokens[0] == "a":
        tokens[0] = "one"
    if not tokens or any(t not in _NUMBER_WORDS for t in tokens):
        return None
    total, current = 0, 0
    for t in tokens:
        if t in _UNITS:
            current += _UNITS[t]
        elif t in _TENS:
            current += _TENS[t]
        elif t == "hundred":
            current = (current or 1) * 100
        elif t == "thousand":
            total += (current or 1) * 1000
            current = 0
    return total + current


def find_numbers(text: str) -> list[tuple[int, int, int, str]]:
    """Return (value, start, end, span_text) for every quantity-like number in text."""
    found: list[tuple[int, int, int, str]] = []
    for m in DIGITS.finditer(text):
        found.append((int(m.group().replace(",", "")), m.start(), m.end(), m.group()))
    for m in WORD_SEQUENCE.finditer(text):
        span = m.group().strip()
        if span.lower() == "one":  # "the second one", "that one": a pronoun, not a quantity
            continue
        value = words_to_int(span)
        if value is None or value == 0:
            continue
        start = m.start() + (len(m.group()) - len(m.group().lstrip()))
        found.append((value, start, start + len(span), span))
    found.sort(key=lambda f: f[1])
    return found
