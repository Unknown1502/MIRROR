"""Compensation planning for rollback (build plan part 25).

The plan is computed from what *actually* happened (the verified post-state), not
from what was intended. After a divergent short shipment of 450, rollback moves 450
back, not 500.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..core.errors import ErrorCode, MirrorError


@dataclass(frozen=True)
class CompensationPlan:
    kind: str  # TRANSFER | ADJUST
    transfer: dict | None
    adjustments: tuple[dict, ...]
    affected: tuple[str, ...]
    expected_after: dict[str, int]

    def as_dict(self) -> dict:
        return {
            "kind": self.kind,
            "transfer": self.transfer,
            "adjustments": list(self.adjustments),
            "affected": list(self.affected),
        }


def plan_compensation(pre: dict[str, int], post: dict[str, int], current: dict[str, int]) -> CompensationPlan:
    affected = tuple(sorted(k for k in pre if post.get(k) != pre[k]))
    if not affected:
        raise MirrorError(ErrorCode.ROLLBACK_FAILED, "Nothing to roll back: the commit did not change reality")

    moved_since = [k for k in affected if current.get(k) != post.get(k)]
    if moved_since:
        raise MirrorError(
            ErrorCode.ROLLBACK_FAILED,
            "Reality changed after the commit; automatic compensation would overwrite someone else's change",
            cells=moved_since,
        )

    expected_after = dict(current)
    for k in affected:
        expected_after[k] = pre[k]

    deltas = {k: pre[k] - post[k] for k in affected}  # what compensation must apply
    products = {k.split("|", 1)[1] for k in affected}
    if len(affected) == 2 and len(products) == 1 and sum(deltas.values()) == 0:
        giver = next(k for k, d in deltas.items() if d < 0)
        taker = next(k for k, d in deltas.items() if d > 0)
        transfer = {
            "product_id": giver.split("|", 1)[1],
            "quantity": deltas[taker],
            "source_id": giver.split("|", 1)[0],
            "destination_id": taker.split("|", 1)[0],
        }
        return CompensationPlan("TRANSFER", transfer, (), affected, expected_after)

    adjustments = tuple(
        {"location_id": k.split("|", 1)[0], "product_id": k.split("|", 1)[1], "delta": d} for k, d in deltas.items()
    )
    return CompensationPlan("ADJUST", None, adjustments, affected, expected_after)
