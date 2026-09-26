"""Mock ERP: the authoritative "real world" for the inventory adapter.

It lives in its own SQLite database and is reachable only through this API. MIRROR's
runtime never writes inventory rows directly; it asks the ERP to execute operations,
then reads the world back to find out what actually happened.

Operations are idempotent by operation_id and support compare-and-swap preconditions.
Demo faults are deterministic and one-shot: they are armed explicitly and consumed by
the next operation they apply to.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from enum import Enum
from typing import Mapping

from sqlalchemy import (
    Boolean,
    Column,
    Float,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    create_engine,
    delete,
    event,
    insert,
    select,
    update,
)

from ..core.ids import iso, utcnow
from ..core.world import Cell, Location, Product, Route, WorldSnapshot
from . import seed as seed_data

metadata = MetaData()

products_t = Table(
    "products",
    metadata,
    Column("id", String, primary_key=True),
    Column("name", String, nullable=False),
    Column("family", String, nullable=False),
    Column("unit", String, nullable=False),
    Column("min_transfer", Integer, nullable=False),
    Column("max_transfer", Integer, nullable=False),
)

locations_t = Table(
    "locations",
    metadata,
    Column("id", String, primary_key=True),
    Column("name", String, nullable=False),
    Column("city", String, nullable=False),
    Column("kind", String, nullable=False),
    Column("aliases_json", Text, nullable=False),
)

inventory_t = Table(
    "inventory",
    metadata,
    Column("location_id", String, primary_key=True),
    Column("product_id", String, primary_key=True),
    Column("on_hand", Integer, nullable=False),
    Column("capacity", Integer, nullable=False),
    Column("safety_stock", Integer, nullable=False),
)

routes_t = Table(
    "routes",
    metadata,
    Column("source_id", String, primary_key=True),
    Column("destination_id", String, primary_key=True),
    Column("available", Boolean, nullable=False),
    Column("mode", String, nullable=False),
    Column("cost_per_unit", Float, nullable=False),
    Column("lead_time_hours", Integer, nullable=False),
)

operations_t = Table(
    "erp_operations",
    metadata,
    Column("operation_id", String, primary_key=True),
    Column("type", String, nullable=False),
    Column("actor", String, nullable=False),
    Column("request_json", Text, nullable=False),
    Column("applied_json", Text, nullable=False),
    Column("reported_json", Text, nullable=False),
    Column("revision", Integer, nullable=False),
    Column("created_at", String, nullable=False),
)

meta_t = Table(
    "erp_meta",
    metadata,
    Column("key", String, primary_key=True),
    Column("value", Text, nullable=False),
)


class Fault(str, Enum):
    NONE = "none"
    # ERP applies 90% of the requested quantity but reports the full quantity.
    VERIFICATION_MISMATCH = "verification_mismatch"
    # ERP is unavailable for one call; nothing is applied. Retryable.
    TEMPORARY_ERP_FAILURE = "temporary_erp_failure"
    # ERP applies the operation, then the response is lost. Outcome unknown to the caller.
    UNKNOWN_RESULT = "unknown_result"
    # The transport delivers the same operation twice (at-least-once delivery).
    DUPLICATE_COMMIT = "duplicate_commit"


class ErpError(Exception):
    applied: bool | None = None


class ErpUnavailable(ErpError):
    """Definitely not applied; safe to retry with the same operation_id."""

    applied = False


class ErpTimeout(ErpError):
    """The call may or may not have been applied. Only a read can tell."""

    applied = None


class ErpRejected(ErpError):
    """The ERP refused the operation (validation or precondition). Not applied."""

    applied = False

    def __init__(self, reason: str, message: str):
        super().__init__(message)
        self.reason = reason


@dataclass(frozen=True)
class CellDelta:
    location_id: str
    product_id: str
    delta: int


@dataclass(frozen=True)
class ErpReceipt:
    operation_id: str
    duplicate: bool
    revision: int
    # What the ERP *claims* it did. MIRROR never treats this as ground truth.
    reported: dict = field(default_factory=dict)


def _key(location_id: str, product_id: str) -> str:
    return f"{location_id}|{product_id}"


class MockERP:
    def __init__(self, db_url: str):
        self.engine = create_engine(db_url, future=True, connect_args={"check_same_thread": False})

        @event.listens_for(self.engine, "connect")
        def _pragmas(dbapi_conn, _):  # pragma: no cover - trivial
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA busy_timeout=5000")
            cur.close()

        metadata.create_all(self.engine)
        self._lock = threading.RLock()
        with self.engine.begin() as conn:
            empty = conn.execute(select(products_t.c.id).limit(1)).first() is None
        if empty:
            self.reset_world()

    # ------------------------------------------------------------------ reads

    def read_snapshot(self) -> WorldSnapshot:
        with self.engine.connect() as conn:
            products = [
                Product(r.id, r.name, r.family, r.unit, r.min_transfer, r.max_transfer)
                for r in conn.execute(select(products_t))
            ]
            locations = [
                Location(r.id, r.name, r.city, r.kind, tuple(json.loads(r.aliases_json)))
                for r in conn.execute(select(locations_t))
            ]
            cells = [
                Cell(r.location_id, r.product_id, r.on_hand, r.capacity, r.safety_stock)
                for r in conn.execute(select(inventory_t))
            ]
            routes = [
                Route(r.source_id, r.destination_id, bool(r.available), r.mode, r.cost_per_unit, r.lead_time_hours)
                for r in conn.execute(select(routes_t))
            ]
            revision = self._get_meta(conn, "revision", 0)
        return WorldSnapshot.build(revision, products, locations, cells, routes)

    def revision(self) -> int:
        with self.engine.connect() as conn:
            return self._get_meta(conn, "revision", 0)

    def get_operation(self, operation_id: str) -> dict | None:
        with self.engine.connect() as conn:
            row = conn.execute(select(operations_t).where(operations_t.c.operation_id == operation_id)).first()
        return self._op_row(row) if row else None

    def operations_for_transaction(self, transaction_id: str) -> list[dict]:
        with self.engine.connect() as conn:
            rows = conn.execute(
                select(operations_t)
                .where(operations_t.c.operation_id.like(f"{transaction_id}:%"))
                .order_by(operations_t.c.revision)
            ).all()
        return [self._op_row(r) for r in rows]

    def operations(self, limit: int = 50) -> list[dict]:
        with self.engine.connect() as conn:
            rows = conn.execute(select(operations_t).order_by(operations_t.c.revision.desc()).limit(limit)).all()
        return [self._op_row(r) for r in rows]

    # ------------------------------------------------------------ fault control

    def arm_fault(self, fault: Fault) -> None:
        with self._lock, self.engine.begin() as conn:
            self._set_meta(conn, "armed_fault", fault.value)

    def armed_fault(self) -> Fault:
        with self.engine.connect() as conn:
            return Fault(self._get_meta(conn, "armed_fault", Fault.NONE.value))

    def consume_fault(self, fault: Fault) -> bool:
        """Atomically consume the armed fault if it is `fault`. One-shot and deterministic."""
        with self._lock, self.engine.begin() as conn:
            if self._get_meta(conn, "armed_fault", Fault.NONE.value) != fault.value:
                return False
            self._set_meta(conn, "armed_fault", Fault.NONE.value)
            return True

    # ----------------------------------------------------------------- writes

    def transfer_inventory(
        self,
        *,
        operation_id: str,
        transaction_id: str,
        version: int,
        product_id: str,
        quantity: int,
        source_id: str,
        destination_id: str,
        preconditions: Mapping[str, int] | None = None,
        actor: str = "MIRROR",
    ) -> ErpReceipt:
        request = {
            "operation": "TRANSFER_INVENTORY",
            "transaction_id": transaction_id,
            "version": version,
            "product_id": product_id,
            "quantity": quantity,
            "source": source_id,
            "destination": destination_id,
        }
        if quantity <= 0:
            raise ErpRejected("INVALID_QUANTITY", "quantity must be positive")
        if source_id == destination_id:
            raise ErpRejected("SAME_LOCATION", "source and destination are the same")

        with self._lock:
            duplicate = self._existing_receipt(operation_id)
            if duplicate:
                return duplicate

            armed = self.armed_fault()
            if armed is Fault.TEMPORARY_ERP_FAILURE and self.consume_fault(armed):
                raise ErpUnavailable("ERP temporarily unavailable (injected fault); nothing was applied")

            applied_qty = quantity
            consumed: Fault | None = None
            if armed is Fault.VERIFICATION_MISMATCH:
                applied_qty = (quantity * 9) // 10  # short shipment, silently
                consumed = armed
            elif armed is Fault.UNKNOWN_RESULT:
                consumed = armed

            deltas = [
                CellDelta(source_id, product_id, -applied_qty),
                CellDelta(destination_id, product_id, applied_qty),
            ]
            reported = {**request, "status": "SUCCESS", "applied_quantity": quantity}
            receipt = self._apply(
                operation_id, "TRANSFER_INVENTORY", actor, request, deltas, reported, preconditions, consumed
            )

            if consumed is Fault.UNKNOWN_RESULT:
                raise ErpTimeout("ERP response lost after submission (injected fault); outcome unknown")
            return receipt

    def adjust_inventory(
        self,
        *,
        operation_id: str,
        deltas: list[CellDelta],
        reason: str,
        preconditions: Mapping[str, int] | None = None,
        actor: str = "MIRROR",
    ) -> ErpReceipt:
        request = {
            "operation": "ADJUST_INVENTORY",
            "reason": reason,
            "deltas": [{"location_id": d.location_id, "product_id": d.product_id, "delta": d.delta} for d in deltas],
        }
        with self._lock:
            duplicate = self._existing_receipt(operation_id)
            if duplicate:
                return duplicate
            reported = {**request, "status": "SUCCESS"}
            return self._apply(operation_id, "ADJUST_INVENTORY", actor, request, deltas, reported, preconditions)

    def reset_world(self) -> None:
        with self._lock, self.engine.begin() as conn:
            for table in (inventory_t, routes_t, locations_t, products_t, operations_t):
                conn.execute(delete(table))
            conn.execute(
                insert(products_t),
                [
                    dict(id=p[0], name=p[1], family=p[2], unit=p[3], min_transfer=p[4], max_transfer=p[5])
                    for p in seed_data.PRODUCTS
                ],
            )
            conn.execute(
                insert(locations_t),
                [
                    dict(id=loc[0], name=loc[1], city=loc[2], kind=loc[3], aliases_json=json.dumps(loc[4]))
                    for loc in seed_data.LOCATIONS
                ],
            )
            conn.execute(
                insert(inventory_t),
                [
                    dict(location_id=k[0], product_id=k[1], on_hand=v[0], capacity=v[1], safety_stock=v[2])
                    for k, v in seed_data.INVENTORY.items()
                ],
            )
            conn.execute(
                insert(routes_t),
                [
                    dict(
                        source_id=r[0],
                        destination_id=r[1],
                        mode=r[2],
                        cost_per_unit=r[3],
                        lead_time_hours=r[4],
                        available=r[5],
                    )
                    for r in seed_data.ROUTES
                ],
            )
            revision = self._get_meta(conn, "revision", 0) + 1
            self._set_meta(conn, "revision", revision)
            self._set_meta(conn, "armed_fault", Fault.NONE.value)

    # --------------------------------------------------------------- internals

    def _existing_receipt(self, operation_id: str) -> ErpReceipt | None:
        existing = self.get_operation(operation_id)
        if existing is None:
            return None
        return ErpReceipt(operation_id, True, existing["revision"], existing["reported"])

    def _apply(
        self, operation_id, op_type, actor, request, deltas, reported, preconditions, consumed_fault=None
    ) -> ErpReceipt:
        with self.engine.begin() as conn:
            current: dict[str, tuple[int, int]] = {}
            for d in deltas:
                row = conn.execute(
                    select(inventory_t.c.on_hand, inventory_t.c.capacity).where(
                        (inventory_t.c.location_id == d.location_id) & (inventory_t.c.product_id == d.product_id)
                    )
                ).first()
                if row is None:
                    raise ErpRejected("UNKNOWN_CELL", f"{d.product_id} is not stocked at {d.location_id}")
                current[_key(d.location_id, d.product_id)] = (row.on_hand, row.capacity)

            for key, expected in (preconditions or {}).items():
                if key not in current:
                    row = conn.execute(
                        select(inventory_t.c.on_hand).where(
                            (inventory_t.c.location_id == key.split("|")[0])
                            & (inventory_t.c.product_id == key.split("|")[1])
                        )
                    ).first()
                    actual = row.on_hand if row else None
                else:
                    actual = current[key][0]
                if actual != expected:
                    raise ErpRejected(
                        "PRECONDITION_FAILED",
                        f"{key} is {actual}, expected {expected}; the world changed underneath this operation",
                    )

            for d in deltas:
                on_hand, capacity = current[_key(d.location_id, d.product_id)]
                after = on_hand + d.delta
                if after < 0:
                    raise ErpRejected("NEGATIVE_INVENTORY", f"{d.location_id} would go negative ({after})")
                if after > capacity:
                    raise ErpRejected("OVER_CAPACITY", f"{d.location_id} would exceed capacity ({after}>{capacity})")

            for d in deltas:
                conn.execute(
                    update(inventory_t)
                    .where((inventory_t.c.location_id == d.location_id) & (inventory_t.c.product_id == d.product_id))
                    .values(on_hand=inventory_t.c.on_hand + d.delta)
                )
            revision = self._get_meta(conn, "revision", 0) + 1
            self._set_meta(conn, "revision", revision)
            if consumed_fault is not None:
                # The fault is spent in the same database transaction that applied it.
                self._set_meta(conn, "armed_fault", Fault.NONE.value)
            applied = [{"location_id": d.location_id, "product_id": d.product_id, "delta": d.delta} for d in deltas]
            conn.execute(
                insert(operations_t).values(
                    operation_id=operation_id,
                    type=op_type,
                    actor=actor,
                    request_json=json.dumps(request, sort_keys=True),
                    applied_json=json.dumps(applied, sort_keys=True),
                    reported_json=json.dumps(reported, sort_keys=True),
                    revision=revision,
                    created_at=iso(utcnow()),
                )
            )
        return ErpReceipt(operation_id, False, revision, reported)

    @staticmethod
    def _op_row(row) -> dict:
        return {
            "operation_id": row.operation_id,
            "type": row.type,
            "actor": row.actor,
            "request": json.loads(row.request_json),
            "applied": json.loads(row.applied_json),
            "reported": json.loads(row.reported_json),
            "revision": row.revision,
            "created_at": row.created_at,
        }

    @staticmethod
    def _get_meta(conn, key: str, default):
        row = conn.execute(select(meta_t.c.value).where(meta_t.c.key == key)).first()
        if row is None:
            return default
        return type(default)(row.value) if not isinstance(default, str) else row.value

    @staticmethod
    def _set_meta(conn, key: str, value) -> None:
        exists = conn.execute(select(meta_t.c.key).where(meta_t.c.key == key)).first()
        if exists:
            conn.execute(update(meta_t).where(meta_t.c.key == key).values(value=str(value)))
        else:
            conn.execute(insert(meta_t).values(key=key, value=str(value)))
