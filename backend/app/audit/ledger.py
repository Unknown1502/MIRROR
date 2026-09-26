"""Append-only, hash-chained audit ledger (build plan part 38).

    hash = SHA256(previous_hash + canonical_payload)

The canonical payload covers every column of the event, so changing any stored
field - payload, type, timestamp, ordering - breaks the chain at that event.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..core.db import AuditEventRow
from ..core.hashing import GENESIS_HASH, canonical_json, sha256_hex
from ..core.ids import iso, new_id, utcnow


def _canonical(row_fields: dict) -> str:
    return canonical_json(
        {
            "sequence": row_fields["sequence"],
            "event_id": row_fields["event_id"],
            "session_id": row_fields["session_id"],
            "transaction_id": row_fields["transaction_id"],
            "transaction_version": row_fields["transaction_version"],
            "event_type": row_fields["event_type"],
            "timestamp": row_fields["timestamp"],
            "payload": row_fields["payload"],
        }
    )


def compute_hash(previous_hash: str, row_fields: dict) -> str:
    return sha256_hex(previous_hash + _canonical(row_fields))


@dataclass(frozen=True)
class ChainReport:
    valid: bool
    length: int
    head_hash: str
    first_invalid_sequence: int | None = None
    reason: str | None = None

    def as_dict(self) -> dict:
        return {
            "valid": self.valid,
            "length": self.length,
            "head_hash": self.head_hash,
            "first_invalid_sequence": self.first_invalid_sequence,
            "reason": self.reason,
        }


def event_to_dict(row: AuditEventRow) -> dict:
    return {
        "sequence": row.sequence,
        "event_id": row.event_id,
        "session_id": row.session_id,
        "transaction_id": row.transaction_id,
        "transaction_version": row.transaction_version,
        "event_type": row.event_type,
        "timestamp": row.timestamp,
        "payload": json.loads(row.payload_json),
        "previous_hash": row.previous_hash,
        "hash": row.hash,
    }


class AuditLedger:
    """Callers must serialize appends (the runtime holds its lock while appending)."""

    def append(
        self,
        db: Session,
        event_type: str,
        payload: dict,
        *,
        session_id: str | None = None,
        transaction_id: str | None = None,
        transaction_version: int | None = None,
    ) -> AuditEventRow:
        last = db.execute(select(AuditEventRow).order_by(AuditEventRow.sequence.desc()).limit(1)).scalar_one_or_none()
        previous_hash = last.hash if last else GENESIS_HASH
        fields = {
            "sequence": (last.sequence + 1) if last else 1,
            "event_id": new_id("evt"),
            "session_id": session_id,
            "transaction_id": transaction_id,
            "transaction_version": transaction_version,
            "event_type": event_type,
            "timestamp": iso(utcnow()),
            "payload": payload,
        }
        row = AuditEventRow(
            sequence=fields["sequence"],
            event_id=fields["event_id"],
            session_id=session_id,
            transaction_id=transaction_id,
            transaction_version=transaction_version,
            event_type=event_type,
            timestamp=fields["timestamp"],
            payload_json=canonical_json(payload),
            previous_hash=previous_hash,
            hash=compute_hash(previous_hash, fields),
        )
        db.add(row)
        db.flush()
        return row

    def verify_chain(self, db: Session) -> ChainReport:
        previous_hash = GENESIS_HASH
        expected_seq = 1
        count = 0
        for row in db.execute(select(AuditEventRow).order_by(AuditEventRow.sequence)).scalars():
            count += 1
            if row.sequence != expected_seq:
                return ChainReport(False, count, previous_hash, row.sequence, f"gap: expected sequence {expected_seq}")
            if row.previous_hash != previous_hash:
                return ChainReport(False, count, previous_hash, row.sequence, "previous_hash does not link to prior event")
            try:
                payload = json.loads(row.payload_json)
            except ValueError:
                return ChainReport(False, count, previous_hash, row.sequence, "payload is not valid JSON")
            fields = {
                "sequence": row.sequence,
                "event_id": row.event_id,
                "session_id": row.session_id,
                "transaction_id": row.transaction_id,
                "transaction_version": row.transaction_version,
                "event_type": row.event_type,
                "timestamp": row.timestamp,
                "payload": payload,
            }
            if compute_hash(previous_hash, fields) != row.hash:
                return ChainReport(False, count, previous_hash, row.sequence, "hash mismatch: event content was altered")
            previous_hash = row.hash
            expected_seq += 1
        return ChainReport(True, count, previous_hash)

    def events_for_transaction(self, db: Session, transaction_id: str) -> list[dict]:
        rows = db.execute(
            select(AuditEventRow).where(AuditEventRow.transaction_id == transaction_id).order_by(AuditEventRow.sequence)
        ).scalars()
        return [event_to_dict(r) for r in rows]

    def length(self, db: Session) -> int:
        return db.execute(select(func.count(AuditEventRow.sequence))).scalar_one()
