"""Immutable domain types for the inventory world.

A WorldSnapshot is a frozen read of the ERP at one revision. The simulator only ever
receives a snapshot, never an ERP handle, so it has no way to write to reality.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Mapping

from .hashing import hash_value

CellKey = tuple[str, str]  # (location_id, product_id)


@dataclass(frozen=True)
class Product:
    id: str
    name: str
    family: str
    unit: str
    min_transfer: int
    max_transfer: int


@dataclass(frozen=True)
class Location:
    id: str
    name: str
    city: str
    kind: str
    aliases: tuple[str, ...]


@dataclass(frozen=True)
class Cell:
    location_id: str
    product_id: str
    on_hand: int
    capacity: int
    safety_stock: int

    def as_dict(self) -> dict:
        return {
            "location_id": self.location_id,
            "product_id": self.product_id,
            "on_hand": self.on_hand,
            "capacity": self.capacity,
            "safety_stock": self.safety_stock,
        }


@dataclass(frozen=True)
class Route:
    source_id: str
    destination_id: str
    available: bool
    mode: str
    cost_per_unit: float
    lead_time_hours: int

    def as_dict(self) -> dict:
        return {
            "source_id": self.source_id,
            "destination_id": self.destination_id,
            "available": self.available,
            "mode": self.mode,
            "cost_per_unit": self.cost_per_unit,
            "lead_time_hours": self.lead_time_hours,
        }


@dataclass(frozen=True)
class WorldSnapshot:
    revision: int
    products: Mapping[str, Product]
    locations: Mapping[str, Location]
    cells: Mapping[CellKey, Cell]
    routes: Mapping[tuple[str, str], Route]
    _hash: str = field(default="", compare=False)

    @staticmethod
    def build(revision: int, products, locations, cells, routes) -> "WorldSnapshot":
        snap = WorldSnapshot(
            revision=revision,
            products=MappingProxyType({p.id: p for p in products}),
            locations=MappingProxyType({loc.id: loc for loc in locations}),
            cells=MappingProxyType({(c.location_id, c.product_id): c for c in cells}),
            routes=MappingProxyType({(r.source_id, r.destination_id): r for r in routes}),
        )
        object.__setattr__(snap, "_hash", hash_value(snap.state_dict()))
        return snap

    def cell(self, location_id: str, product_id: str) -> Cell | None:
        return self.cells.get((location_id, product_id))

    def route(self, source_id: str, destination_id: str) -> Route | None:
        return self.routes.get((source_id, destination_id))

    def on_hand_map(self) -> dict[str, int]:
        return {f"{k[0]}|{k[1]}": c.on_hand for k, c in sorted(self.cells.items())}

    def state_dict(self) -> dict:
        return {
            "cells": [c.as_dict() for _, c in sorted(self.cells.items())],
            "routes": [r.as_dict() for _, r in sorted(self.routes.items())],
        }

    @property
    def hash(self) -> str:
        return self._hash

    def with_on_hand(self, updates: Mapping[CellKey, int]) -> "WorldSnapshot":
        """Return a new snapshot with some on-hand values replaced (used for predictions)."""
        cells = dict(self.cells)
        for key, value in updates.items():
            base = cells[key]
            cells[key] = Cell(base.location_id, base.product_id, value, base.capacity, base.safety_stock)
        return WorldSnapshot.build(
            self.revision, self.products.values(), self.locations.values(), cells.values(), self.routes.values()
        )

    def to_public(self) -> dict:
        return {
            "revision": self.revision,
            "hash": self.hash,
            "products": [
                {
                    "id": p.id,
                    "name": p.name,
                    "family": p.family,
                    "unit": p.unit,
                    "min_transfer": p.min_transfer,
                    "max_transfer": p.max_transfer,
                }
                for p in sorted(self.products.values(), key=lambda p: p.id)
            ],
            "locations": [
                {"id": loc.id, "name": loc.name, "city": loc.city, "kind": loc.kind}
                for loc in sorted(self.locations.values(), key=lambda loc: loc.id)
            ],
            "cells": [c.as_dict() for _, c in sorted(self.cells.items())],
            "routes": [r.as_dict() for _, r in sorted(self.routes.items())],
        }
