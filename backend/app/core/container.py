"""Composition root: builds every component once and wires them together."""

from __future__ import annotations

from dataclasses import dataclass

from ..audit.ledger import AuditLedger
from ..erp.service import MockERP
from ..erp.worlds import WorldRegistry
from ..intent.llm_interpreter import LLMInterpreter
from ..runtime.dispatcher import TurnDispatcher
from ..runtime.transaction_runtime import Runtime
from .config import Settings
from .db import make_engine, make_session_factory
from .events import EventBus


@dataclass
class Container:
    settings: Settings
    erp: MockERP
    runtime: Runtime
    dispatcher: TurnDispatcher
    bus: EventBus
    ledger: AuditLedger
    engine: object
    worlds: WorldRegistry

    def dispose(self) -> None:
        self.engine.dispose()
        self.erp.engine.dispose()
        self.worlds.dispose()


def build_container(settings: Settings) -> Container:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    engine = make_engine(settings.runtime_db_url)
    session_factory = make_session_factory(engine)
    erp = MockERP(settings.erp_db_url)
    worlds = WorldRegistry(erp, settings.data_dir, per_session=settings.sandbox_per_session)
    ledger = AuditLedger()
    bus = EventBus()
    runtime = Runtime(settings, session_factory, erp, ledger, bus, worlds)
    dispatcher = TurnDispatcher(runtime, LLMInterpreter(settings))
    return Container(settings, erp, runtime, dispatcher, bus, ledger, engine, worlds)
