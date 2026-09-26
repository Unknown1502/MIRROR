"""Runtime persistence (MIRROR's own database, separate from the ERP's).

History tables are append-only and enforced as such by SQLite triggers: versions,
evidence, branches, simulations, consequences, transcripts and the audit ledger can
be inserted but never updated or deleted through the database.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    event,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker


class Base(DeclarativeBase):
    pass


class SessionRow(Base):
    __tablename__ = "sessions"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    token_hash: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    voice_state: Mapped[str] = mapped_column(String, default="IDLE")
    assemblyai_session_id: Mapped[str | None] = mapped_column(String, nullable=True)
    assemblyai_disconnected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    active_transaction_id: Mapped[str | None] = mapped_column(String, nullable=True)


class TranscriptItem(Base):
    __tablename__ = "transcript_items"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id"), index=True)
    item_id: Mapped[str] = mapped_column(String, index=True)
    source: Mapped[str] = mapped_column(String)  # VOICE | TEXT
    role: Mapped[str] = mapped_column(String, default="user")  # user | agent
    text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class TransactionRow(Base):
    __tablename__ = "transactions"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id"), index=True)
    current_version: Mapped[int] = mapped_column(Integer, default=0)
    state: Mapped[str] = mapped_column(String)
    reality: Mapped[str] = mapped_column(String, default="UNCHANGED")
    committed_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class TransactionVersionRow(Base):
    __tablename__ = "transaction_versions"
    __table_args__ = (UniqueConstraint("transaction_id", "version"),)
    id: Mapped[str] = mapped_column(String, primary_key=True)
    transaction_id: Mapped[str] = mapped_column(ForeignKey("transactions.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    parent_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    kind: Mapped[str] = mapped_column(String)
    label: Mapped[str] = mapped_column(Text)
    intent_json: Mapped[str] = mapped_column(Text)
    evidence_hash: Mapped[str] = mapped_column(String)
    simulation_hash: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String)
    state_trail_json: Mapped[str] = mapped_column(Text)
    clarification_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    extra_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    world_revision: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class EvidenceRow(Base):
    __tablename__ = "evidence"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    transaction_id: Mapped[str] = mapped_column(ForeignKey("transactions.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    field: Mapped[str] = mapped_column(String)
    candidates_json: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String)
    source_type: Mapped[str] = mapped_column(String)
    transcript_item_id: Mapped[str | None] = mapped_column(String, nullable=True)
    text_span: Mapped[str | None] = mapped_column(Text, nullable=True)
    note: Mapped[str] = mapped_column(Text, default="")
    supersedes_id: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class BranchRow(Base):
    __tablename__ = "branches"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    transaction_id: Mapped[str] = mapped_column(ForeignKey("transactions.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    branch_key: Mapped[str] = mapped_column(String)
    intent_json: Mapped[str] = mapped_column(Text)
    evidence_status_json: Mapped[str] = mapped_column(Text)
    outcome: Mapped[str] = mapped_column(String)
    policy_json: Mapped[str] = mapped_column(Text)
    simulation_run_id: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class SimulationRunRow(Base):
    __tablename__ = "simulation_runs"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    transaction_id: Mapped[str | None] = mapped_column(String, index=True, nullable=True)
    version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    branch_key: Mapped[str | None] = mapped_column(String, nullable=True)
    world_revision: Mapped[int] = mapped_column(Integer)
    result_json: Mapped[str] = mapped_column(Text)
    result_hash: Mapped[str] = mapped_column(String)
    latency_ms: Mapped[float] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ConsequenceRow(Base):
    __tablename__ = "consequences"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    simulation_run_id: Mapped[str] = mapped_column(ForeignKey("simulation_runs.id"), index=True)
    code: Mapped[str] = mapped_column(String)
    severity: Mapped[str] = mapped_column(String)
    blocking: Mapped[bool] = mapped_column(Boolean)
    message: Mapped[str] = mapped_column(Text)
    subject: Mapped[str] = mapped_column(String)
    before: Mapped[float | None] = mapped_column(Float, nullable=True)
    after: Mapped[float | None] = mapped_column(Float, nullable=True)
    threshold: Mapped[float | None] = mapped_column(Float, nullable=True)
    delta: Mapped[float | None] = mapped_column(Float, nullable=True)


class ExecutionRow(Base):
    __tablename__ = "executions"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    transaction_id: Mapped[str] = mapped_column(ForeignKey("transactions.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String)  # COMMIT | ROLLBACK
    idempotency_key: Mapped[str] = mapped_column(String, unique=True)
    status: Mapped[str] = mapped_column(String)  # PENDING | APPLIED | NOT_APPLIED | UNKNOWN | REJECTED
    verification: Mapped[str | None] = mapped_column(String, nullable=True)
    operations_json: Mapped[str] = mapped_column(Text, default="[]")
    reported_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    pre_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    predicted_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    actual_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    diff_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    exec_latency_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    verify_latency_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AuditEventRow(Base):
    __tablename__ = "audit_events"
    __table_args__ = {"sqlite_autoincrement": True}
    sequence: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[str] = mapped_column(String, unique=True)
    session_id: Mapped[str | None] = mapped_column(String, index=True, nullable=True)
    transaction_id: Mapped[str | None] = mapped_column(String, index=True, nullable=True)
    transaction_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    event_type: Mapped[str] = mapped_column(String)
    timestamp: Mapped[str] = mapped_column(String)
    payload_json: Mapped[str] = mapped_column(Text)
    previous_hash: Mapped[str] = mapped_column(String)
    hash: Mapped[str] = mapped_column(String)


APPEND_ONLY_TABLES = (
    "transaction_versions",
    "evidence",
    "branches",
    "simulation_runs",
    "consequences",
    "transcript_items",
    "audit_events",
)


def make_engine(url: str):
    engine = create_engine(url, future=True, connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def _pragmas(dbapi_conn, _):  # pragma: no cover - trivial
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA busy_timeout=5000")
        cur.close()

    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        for table in APPEND_ONLY_TABLES:
            for op in ("UPDATE", "DELETE"):
                conn.execute(
                    text(
                        f"CREATE TRIGGER IF NOT EXISTS {table}_no_{op.lower()} BEFORE {op} ON {table} "
                        f"BEGIN SELECT RAISE(ABORT, '{table} is append-only'); END;"
                    )
                )
    return engine


def make_session_factory(engine):
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)
