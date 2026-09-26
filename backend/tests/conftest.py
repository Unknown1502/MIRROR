from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import load_settings  # noqa: E402
from app.core.container import build_container  # noqa: E402


@pytest.fixture()
def settings(tmp_path):
    return load_settings(
        data_dir=tmp_path,
        demo_mode=True,
        demo_fault_injection=True,
        demo_admin_token=None,
        commit_secret=b"test-secret",
        assemblyai_api_key=None,
        static_dir=None,
        rate_limit=False,  # exercised explicitly in test_guardrails.py
    )


@pytest.fixture()
def container(settings):
    c = build_container(settings)
    yield c
    c.engine.dispose()
    c.erp.engine.dispose()


@pytest.fixture()
def rt(container):
    return container.runtime


@pytest.fixture()
def session(rt):
    session_id, token = rt.create_session()
    return session_id


@pytest.fixture()
def say(container, session):
    """Send a typed turn through the deterministic path and return the TurnResult."""

    def _say(text: str, context_version: int | None = None):
        return container.dispatcher.handle(session, text, context_version)

    return _say
