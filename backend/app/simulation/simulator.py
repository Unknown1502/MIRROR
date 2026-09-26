"""Shadow simulation of inventory transfers.

`simulate` is a pure function of (snapshot, intent). It receives an immutable
WorldSnapshot and has no reference to the ERP, so it cannot change reality.
Consequences are facts about the predicted world; whether a consequence blocks
execution is decided by the policy engine, not here.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum

from ..core.hashing import hash_value
from ..core.world import CellKey, WorldSnapshot

# Source stock within this fraction above safety stock is flagged as a warning.
SAFETY_FLOOR_MARGIN = 0.05
# Destination fill above this fraction of capacity is flagged as a warning.
NEAR_CAPACITY_RATIO = 0.90


class Severity(str, Enum):
    INFO = "INFO"
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


@dataclass(frozen=True)
class TransferIntent:
    product_id: str
    quantity: int
    source_id: str
    destination_id: str

    def as_dict(self) -> dict:
        return {
            "product_id": self.product_id,
            "quantity": self.quantity,
            "source_id": self.source_id,
            "destination_id": self.destination_id,
        }


@dataclass(frozen=True)
class Consequence:
    code: str
    severity: Severity
    message: str
    subject: str  # location or route the consequence is about
    before: float | None = None
    after: float | None = None
    threshold: float | None = None
    delta: float | None = None

    def as_dict(self) -> dict:
        return {
            "code": self.code,
            "severity": self.severity.value,
            "message": self.message,
            "subject": self.subject,
            "before": self.before,
            "after": self.after,
            "threshold": self.threshold,
            "delta": self.delta,
        }


@dataclass(frozen=True)
class CellEffect:
    location_id: str
    product_id: str
    before: int
    after: int
    capacity: int
    safety_stock: int

    def as_dict(self) -> dict:
        return {
            "location_id": self.location_id,
            "product_id": self.product_id,
            "before": self.before,
            "after": self.after,
            "capacity": self.capacity,
            "safety_stock": self.safety_stock,
        }


@dataclass(frozen=True)
class SimulationResult:
    intent: TransferIntent
    snapshot_revision: int
    inputs: dict
    effects: tuple[CellEffect, ...]
    consequences: tuple[Consequence, ...]
    transport: dict | None
    result_hash: str
    latency_ms: float = field(compare=False, default=0.0)

    def predicted_on_hand(self) -> dict[CellKey, int]:
        return {(e.location_id, e.product_id): e.after for e in self.effects}

    def codes(self) -> set[str]:
        return {c.code for c in self.consequences}

    def as_dict(self) -> dict:
        return {
            "intent": self.intent.as_dict(),
            "snapshot_revision": self.snapshot_revision,
            "inputs": self.inputs,
            "effects": [e.as_dict() for e in self.effects],
            "consequences": [c.as_dict() for c in self.consequences],
            "transport": self.transport,
            "result_hash": self.result_hash,
            "latency_ms": round(self.latency_ms, 3),
        }


def simulate(snapshot: WorldSnapshot, intent: TransferIntent) -> SimulationResult:
    started = time.perf_counter()
    consequences: list[Consequence] = []
    effects: list[CellEffect] = []
    transport: dict | None = None
    q = intent.quantity

    product = snapshot.products.get(intent.product_id)
    src_loc = snapshot.locations.get(intent.source_id)
    dst_loc = snapshot.locations.get(intent.destination_id)
    src = snapshot.cell(intent.source_id, intent.product_id)
    dst = snapshot.cell(intent.destination_id, intent.product_id)
    route = snapshot.route(intent.source_id, intent.destination_id)

    # Inputs are exactly the parts of the world this prediction depends on. The
    # simulation hash covers them, so any relevant change in reality invalidates it.
    inputs = {
        "product": None
        if product is None
        else {"id": product.id, "min_transfer": product.min_transfer, "max_transfer": product.max_transfer},
        "source": src.as_dict() if src else None,
        "destination": dst.as_dict() if dst else None,
        "route": route.as_dict() if route else None,
    }

    if product is None:
        consequences.append(
            Consequence("UNKNOWN_PRODUCT", Severity.CRITICAL, f"{intent.product_id} is not a known product", intent.product_id)
        )
    for role, loc_id, loc in (("source", intent.source_id, src_loc), ("destination", intent.destination_id, dst_loc)):
        if loc is None:
            consequences.append(
                Consequence("UNKNOWN_LOCATION", Severity.CRITICAL, f"{role} {loc_id} is not a known location", loc_id)
            )

    if q <= 0:
        consequences.append(Consequence("INVALID_QUANTITY", Severity.CRITICAL, "Quantity must be positive", "quantity", after=q))

    if product and src_loc and dst_loc and q > 0:
        if intent.source_id == intent.destination_id:
            consequences.append(
                Consequence("SAME_LOCATION", Severity.CRITICAL, "Source and destination are the same location", intent.source_id)
            )
        if q < product.min_transfer:
            consequences.append(
                Consequence(
                    "BELOW_MINIMUM_TRANSFER",
                    Severity.HIGH,
                    f"{q} is below the minimum transfer of {product.min_transfer} for {product.id}",
                    product.id,
                    after=q,
                    threshold=product.min_transfer,
                    delta=q - product.min_transfer,
                )
            )
        if q > product.max_transfer:
            consequences.append(
                Consequence(
                    "ABOVE_MAXIMUM_TRANSFER",
                    Severity.HIGH,
                    f"{q} exceeds the maximum single transfer of {product.max_transfer} for {product.id}",
                    product.id,
                    after=q,
                    threshold=product.max_transfer,
                    delta=q - product.max_transfer,
                )
            )

        if route is None:
            if intent.source_id != intent.destination_id:
                consequences.append(
                    Consequence("NO_ROUTE", Severity.HIGH, f"No route from {src_loc.name} to {dst_loc.name}", f"{intent.source_id}->{intent.destination_id}")
                )
        else:
            if not route.available:
                consequences.append(
                    Consequence(
                        "ROUTE_UNAVAILABLE",
                        Severity.HIGH,
                        f"The {route.mode.lower()} route {src_loc.name} -> {dst_loc.name} is currently unavailable",
                        f"{intent.source_id}->{intent.destination_id}",
                    )
                )
            transport = {
                "mode": route.mode,
                "available": route.available,
                "cost_per_unit": route.cost_per_unit,
                "estimated_cost": round(route.cost_per_unit * q, 2),
                "lead_time_hours": route.lead_time_hours,
            }

        if src is None:
            consequences.append(
                Consequence("SOURCE_NOT_STOCKED", Severity.CRITICAL, f"{product.id} is not stocked at {src_loc.name}", intent.source_id)
            )
        if dst is None:
            consequences.append(
                Consequence("DESTINATION_NOT_STOCKED", Severity.CRITICAL, f"{product.id} is not carried at {dst_loc.name}", intent.destination_id)
            )

        if src is not None and dst is not None and intent.source_id != intent.destination_id:
            src_after = src.on_hand - q
            dst_after = dst.on_hand + q
            effects = [
                CellEffect(src.location_id, src.product_id, src.on_hand, src_after, src.capacity, src.safety_stock),
                CellEffect(dst.location_id, dst.product_id, dst.on_hand, dst_after, dst.capacity, dst.safety_stock),
            ]

            if src_after < 0:
                consequences.append(
                    Consequence(
                        "SOURCE_INSUFFICIENT_INVENTORY",
                        Severity.CRITICAL,
                        f"{src_loc.name} has only {src.on_hand} {product.id}; short by {-src_after}",
                        intent.source_id,
                        before=src.on_hand,
                        after=src_after,
                        threshold=0,
                        delta=src_after,
                    )
                )
            elif src_after < src.safety_stock:
                consequences.append(
                    Consequence(
                        "SOURCE_SAFETY_STOCK_BREACH",
                        Severity.HIGH,
                        f"{src_loc.name} would fall to {src_after}, {src.safety_stock - src_after} below its safety stock of {src.safety_stock}",
                        intent.source_id,
                        before=src.on_hand,
                        after=src_after,
                        threshold=src.safety_stock,
                        delta=src_after - src.safety_stock,
                    )
                )
            elif src_after < src.safety_stock * (1 + SAFETY_FLOOR_MARGIN):
                consequences.append(
                    Consequence(
                        "SOURCE_AT_SAFETY_FLOOR",
                        Severity.MEDIUM,
                        f"{src_loc.name} would sit at {src_after}, only {src_after - src.safety_stock} above safety stock",
                        intent.source_id,
                        before=src.on_hand,
                        after=src_after,
                        threshold=src.safety_stock,
                        delta=src_after - src.safety_stock,
                    )
                )

            if dst_after > dst.capacity:
                consequences.append(
                    Consequence(
                        "DESTINATION_CAPACITY_EXCEEDED",
                        Severity.HIGH,
                        f"{dst_loc.name} would hold {dst_after}, exceeding capacity {dst.capacity} by {dst_after - dst.capacity}",
                        intent.destination_id,
                        before=dst.on_hand,
                        after=dst_after,
                        threshold=dst.capacity,
                        delta=dst_after - dst.capacity,
                    )
                )
            elif dst_after > dst.capacity * NEAR_CAPACITY_RATIO:
                consequences.append(
                    Consequence(
                        "DESTINATION_NEAR_CAPACITY",
                        Severity.MEDIUM,
                        f"{dst_loc.name} would be {round(100 * dst_after / dst.capacity)}% full",
                        intent.destination_id,
                        before=dst.on_hand,
                        after=dst_after,
                        threshold=dst.capacity,
                        delta=dst_after - dst.capacity,
                    )
                )

            if dst.on_hand < dst.safety_stock <= dst_after:
                consequences.append(
                    Consequence(
                        "DESTINATION_SAFETY_STOCK_RESTORED",
                        Severity.INFO,
                        f"{dst_loc.name} rises from below safety stock ({dst.on_hand}<{dst.safety_stock}) to {dst_after}",
                        intent.destination_id,
                        before=dst.on_hand,
                        after=dst_after,
                        threshold=dst.safety_stock,
                    )
                )

        if transport is not None:
            consequences.append(
                Consequence(
                    "TRANSPORT_COST",
                    Severity.INFO,
                    f"{route.mode.title()} transfer, est. cost {transport['estimated_cost']:.2f}, lead time {route.lead_time_hours}h",
                    f"{intent.source_id}->{intent.destination_id}",
                    after=transport["estimated_cost"],
                )
            )

    consequences.sort(key=lambda c: (_SEVERITY_ORDER[c.severity], c.code))
    body = {
        "intent": intent.as_dict(),
        "inputs": inputs,
        "effects": [e.as_dict() for e in effects],
        "consequences": [c.as_dict() for c in consequences],
        "transport": transport,
    }
    return SimulationResult(
        intent=intent,
        snapshot_revision=snapshot.revision,
        inputs=inputs,
        effects=tuple(effects),
        consequences=tuple(consequences),
        transport=transport,
        result_hash=hash_value(body),
        latency_ms=(time.perf_counter() - started) * 1000,
    )


_SEVERITY_ORDER = {
    Severity.CRITICAL: 0,
    Severity.HIGH: 1,
    Severity.MEDIUM: 2,
    Severity.LOW: 3,
    Severity.INFO: 4,
}


def predicted_world(snapshot: WorldSnapshot, result: SimulationResult) -> dict[str, int]:
    """Full predicted on-hand map: every cell of the world after the transfer."""
    world = snapshot.on_hand_map()
    for e in result.effects:
        world[f"{e.location_id}|{e.product_id}"] = e.after
    return world
