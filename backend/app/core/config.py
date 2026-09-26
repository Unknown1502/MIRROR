"""Runtime configuration, loaded once from the environment (and an optional .env file)."""

from __future__ import annotations

import logging
import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

log = logging.getLogger("mirror.config")

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_AGENT_URL = "wss://agents.assemblyai.com/v1/ws"


def _bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    try:
        return int(raw) if raw else default
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    demo_mode: bool
    demo_fault_injection: bool
    demo_admin_token: str | None
    commit_secret: bytes = field(repr=False)
    assemblyai_api_key: str | None = field(repr=False)
    assemblyai_agent_url: str
    assemblyai_voice: str
    cors_origins: tuple[str, ...]
    static_dir: Path | None
    # Public-demo protections. Every visitor gets a private ERP sandbox; voice time is capped.
    sandbox_per_session: bool = False
    voice_max_session_seconds: int = 600
    voice_max_concurrent: int = 4
    voice_daily_budget_seconds: int = 0  # 0 = unlimited
    rate_limit: bool = True
    # Reverse proxies in front of the app that append to X-Forwarded-For (1 on Hugging Face Spaces,
    # 0 when clients connect directly). The client IP is the entry the nearest trusted proxy added.
    proxy_hops: int = 1
    # Who may embed the UI in an iframe (CSP frame-ancestors). Hugging Face Spaces embeds apps.
    frame_ancestors: str = "'none'"
    # AssemblyAI LLM Gateway: interprets free-form typed commands (proposals only; same evidence gate).
    llm_gateway_enabled: bool = True
    llm_gateway_url: str = "https://llm-gateway.assemblyai.com/v1/chat/completions"
    llm_gateway_model: str = "qwen3.5-4b-32k-fast"

    @property
    def runtime_db_url(self) -> str:
        return f"sqlite:///{(self.data_dir / 'mirror.db').as_posix()}"

    @property
    def erp_db_url(self) -> str:
        return f"sqlite:///{(self.data_dir / 'erp.db').as_posix()}"

    @property
    def voice_available(self) -> bool:
        return bool(self.assemblyai_api_key)


def load_settings(**overrides) -> Settings:
    load_dotenv(REPO_ROOT / ".env", override=False)

    data_dir = Path(os.environ.get("MIRROR_DATA_DIR", REPO_ROOT / "data"))
    secret = os.environ.get("MIRROR_COMMIT_SECRET", "")
    if not secret:
        # Tokens signed with an ephemeral key die with the process: safe, just not durable.
        log.warning("MIRROR_COMMIT_SECRET not set; using an ephemeral commit-signing key")
        secret = secrets.token_hex(32)

    static_env = os.environ.get("MIRROR_STATIC_DIR")
    static_dir = Path(static_env) if static_env else REPO_ROOT / "apps" / "web" / "dist"

    values = dict(
        data_dir=data_dir,
        demo_mode=_bool("DEMO_MODE", True),
        demo_fault_injection=_bool("DEMO_FAULT_INJECTION", True),
        demo_admin_token=os.environ.get("DEMO_ADMIN_TOKEN") or None,
        commit_secret=secret.encode(),
        assemblyai_api_key=os.environ.get("ASSEMBLYAI_API_KEY") or None,
        assemblyai_agent_url=os.environ.get("ASSEMBLYAI_AGENT_URL", DEFAULT_AGENT_URL),
        assemblyai_voice=os.environ.get("ASSEMBLYAI_VOICE", "alba"),
        cors_origins=tuple(
            o.strip()
            for o in os.environ.get(
                "MIRROR_CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173"
            ).split(",")
            if o.strip()
        ),
        static_dir=static_dir if static_dir.exists() else None,
        sandbox_per_session=_bool("MIRROR_SANDBOX_PER_SESSION", False),
        voice_max_session_seconds=_int("VOICE_MAX_SESSION_SECONDS", 600),
        voice_max_concurrent=_int("VOICE_MAX_CONCURRENT", 4),
        voice_daily_budget_seconds=_int("VOICE_DAILY_BUDGET_SECONDS", 0),
        rate_limit=_bool("MIRROR_RATE_LIMIT", True),
        proxy_hops=_int("MIRROR_PROXY_HOPS", 1),
        frame_ancestors=os.environ.get("MIRROR_FRAME_ANCESTORS", "'none'").strip() or "'none'",
        llm_gateway_enabled=_bool("LLM_GATEWAY_ENABLED", True),
        llm_gateway_url=os.environ.get("LLM_GATEWAY_URL", "https://llm-gateway.assemblyai.com/v1/chat/completions"),
        llm_gateway_model=os.environ.get("LLM_GATEWAY_MODEL", "qwen3.5-4b-32k-fast"),
    )
    values.update(overrides)
    return Settings(**values)
