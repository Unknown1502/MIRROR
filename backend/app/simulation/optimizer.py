"""Deterministic quantity search ("move as much as possible without breaking safety stock").

No language model is involved: every candidate quantity is run through the same
side-effect-free simulator and policy used for ordinary branches.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from ..core.world import WorldSnapshot
from ..policy.engine import Outcome, outcome_of
from .simulator import TransferIntent, simulate


@dataclass(frozen=True)
class OptimizationResult:
    best_quantity: int | None
    binding_constraint: str | None
    probes: tuple[dict, ...]
    evaluated: int
    search_range: tuple[int, int]
    latency_ms: float

    def as_dict(self) -> dict:
        return {
            "best_quantity": self.best_quantity,
            "binding_constraint": self.binding_constraint,
            "probes": list(self.probes),
            "evaluated": self.evaluated,
            "search_range": list(self.search_range),
            "latency_ms": round(self.latency_ms, 2),
        }


def max_safe_quantity(snapshot: WorldSnapshot, product_id: str, source_id: str, destination_id: str) -> OptimizationResult:
    """Largest quantity whose future is not BLOCKED. Exhaustive over the product's transfer range."""
    started = time.perf_counter()
    product = snapshot.products.get(product_id)
    if product is None:
        return OptimizationResult(None, "UNKNOWN_PRODUCT", (), 0, (0, 0), 0.0)

    lo, hi = product.min_transfer, product.max_transfer
    outcomes: dict[int, Outcome] = {}
    best = None
    for q in range(lo, hi + 1):
        sim = simulate(snapshot, TransferIntent(product_id, q, source_id, destination_id))
        outcomes[q] = outcome_of(sim)
        if outcomes[q] is not Outcome.BLOCKED:
            best = q

    binding = None
    probe_qs: list[int] = []
    if best is not None:
        probe_qs = [q for q in (best - 300, best - 200, best - 100, best) if q >= lo]
        if best < hi:
            probe_qs.append(best + 1)
            over = simulate(snapshot, TransferIntent(product_id, best + 1, source_id, destination_id))
            blocking = sorted(c.code for c in over.consequences if c.severity.value in {"HIGH", "CRITICAL"})
            binding = blocking[0] if blocking else None
        else:
            binding = "ABOVE_MAXIMUM_TRANSFER"
    probes = tuple({"quantity": q, "outcome": outcomes[q].value} for q in probe_qs)
    return OptimizationResult(best, binding, probes, len(outcomes), (lo, hi), (time.perf_counter() - started) * 1000)
