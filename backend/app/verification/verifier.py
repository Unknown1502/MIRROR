"""Post-state verification (build plan parts 23-24).

The verifier compares the full predicted world with the full world read back from
the ERP. Only exact equality is VERIFIED; the executor's own report is ignored.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class VerificationReport:
    match: bool
    checked_cells: int
    focus: tuple[dict, ...]  # cells the operation was expected to touch
    diffs: tuple[dict, ...]  # every cell where actual != expected

    def as_dict(self) -> dict:
        return {
            "match": self.match,
            "checked_cells": self.checked_cells,
            "focus": list(self.focus),
            "diffs": list(self.diffs),
        }


def _split(key: str) -> tuple[str, str]:
    location_id, product_id = key.split("|", 1)
    return location_id, product_id


def compare_worlds(pre: dict[str, int], expected: dict[str, int], actual: dict[str, int]) -> VerificationReport:
    keys = sorted(set(expected) | set(actual))
    diffs = []
    for key in keys:
        exp, act = expected.get(key), actual.get(key)
        if exp != act:
            loc, prod = _split(key)
            diffs.append(
                {
                    "cell": key,
                    "location_id": loc,
                    "product_id": prod,
                    "before": pre.get(key),
                    "expected": exp,
                    "actual": act,
                    "difference": None if exp is None or act is None else act - exp,
                }
            )
    focus_keys = sorted(k for k in keys if pre.get(k) != expected.get(k) or pre.get(k) != actual.get(k))
    focus = tuple(
        {
            "cell": k,
            "location_id": _split(k)[0],
            "product_id": _split(k)[1],
            "before": pre.get(k),
            "expected": expected.get(k),
            "actual": actual.get(k),
            "match": expected.get(k) == actual.get(k),
        }
        for k in focus_keys
    )
    return VerificationReport(not diffs, len(keys), focus, tuple(diffs))
