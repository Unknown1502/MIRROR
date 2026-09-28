"""Free-form typed commands through AssemblyAI's LLM Gateway.

Used only when the deterministic compiler cannot classify an utterance. The model's
answer is a *proposal*: it is mapped onto the same proposal tools the voice agent uses,
so every value passes the same transcript-evidence check, and commit/rollback still need
explicit user words. The model can suggest; it cannot decide or execute.

Model support differs: models that accept `response_format` get a strict JSON schema;
others (e.g. AssemblyAI-hosted qwen3.5-4b-32k-fast) get a JSON-only prompt and the answer is
validated here. Invalid output is discarded, never guessed at.
"""

from __future__ import annotations

import json
import logging
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

from ..core.config import Settings

log = logging.getLogger("mirror.llm")

UNREACHABLE_BACKOFF_S = 30.0
ACTIONS = ("propose", "what_if", "amend", "clarify", "compare", "optimize", "commit", "rollback", "cancel", "query", "other")
# Models whose catalog entry lists `response_format` (verified against the live LLM Gateway catalog, 2026-09-26).
STRUCTURED_OUTPUT_MODELS = {
    "gemini-2.5-flash-lite", "gemini-2.5-flash", "gemini-2.5-pro", "gemini-3.1-flash-lite", "gemini-3.5-flash-lite",
    "claude-haiku-4-5-20251001", "claude-sonnet-4-6", "claude-sonnet-4-5-20250929", "gpt-5-nano", "gpt-5-mini",
    "gpt-5.1", "gpt-5.2", "gpt-oss-120b", "qwen3-32B", "qwen3-next-80b-a3b", "deepseek-v4.1-flash",
}

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["action", "product", "quantity", "source", "destination", "quantities", "answer"],
    "properties": {
        "action": {"type": "string", "enum": list(ACTIONS)},
        "product": {"type": ["string", "null"]},
        "quantity": {"type": ["integer", "null"]},
        "source": {"type": ["string", "null"]},
        "destination": {"type": ["string", "null"]},
        "quantities": {"type": "array", "items": {"type": "integer"}},
        "answer": {"type": ["string", "null"]},
    },
}

SYSTEM = """You convert ONE message from a warehouse operator into JSON for an inventory-transfer system.
Output only a JSON object with exactly these keys:
{"action": one of propose|what_if|amend|clarify|compare|optimize|commit|rollback|cancel|query|other,
 "product": product code as said or null, "quantity": integer as said or null,
 "source": location words as said or null, "destination": location words as said or null,
 "quantities": integers to compare (else []), "answer": the operator's words if answering a question, else null}

Rules: copy names and numbers exactly as the operator said them. If something was not said, use null.
Never invent a product, place or number. "commit" only if the operator explicitly tells you to execute.

Examples:
Message: I'd like to get 500 of the XR-17 relays over to Pune Site 2 from Mumbai
{"action":"propose","product":"XR-17","quantity":500,"source":"Mumbai","destination":"Pune Site 2","quantities":[],"answer":null}
Message: hmm, could you see what happens with 900 instead
{"action":"what_if","product":null,"quantity":900,"source":null,"destination":null,"quantities":[],"answer":null}
Message: put 500 next to 900 so I can see both
{"action":"compare","product":null,"quantity":null,"source":null,"destination":null,"quantities":[500,900],"answer":null}
Message: grab the biggest amount that keeps us above safety stock
{"action":"optimize","product":null,"quantity":null,"source":null,"destination":null,"quantities":[],"answer":null}
Message: the second site please
{"action":"clarify","product":null,"quantity":null,"source":null,"destination":null,"quantities":[],"answer":"the second site"}
Message: what's the weather like
{"action":"other","product":null,"quantity":null,"source":null,"destination":null,"quantities":[],"answer":null}"""


@dataclass(frozen=True)
class LLMProposal:
    action: str
    product: str | None
    quantity: int | None
    source: str | None
    destination: str | None
    quantities: tuple[int, ...]
    answer: str | None
    model: str
    request_id: str | None
    latency_ms: float

    def as_dict(self) -> dict:
        return {
            "action": self.action, "product": self.product, "quantity": self.quantity, "source": self.source,
            "destination": self.destination, "quantities": list(self.quantities), "answer": self.answer,
            "model": self.model, "request_id": self.request_id, "latency_ms": round(self.latency_ms, 1),
        }


def _clean_str(v) -> str | None:
    if v is None:
        return None
    s = "".join(ch for ch in str(v) if ch.isprintable()).strip()
    return s[:60] or None


def _clean_int(v) -> int | None:
    if isinstance(v, bool) or v is None:
        return None
    try:
        n = int(v)
    except (TypeError, ValueError):
        return None
    return n if 0 < n <= 1_000_000 else None


def parse_proposal(content: str) -> dict | None:
    """Extract and validate the first JSON object in the model's reply. Invalid means None."""
    m = re.search(r"\{.*\}", content or "", re.S)
    if not m:
        return None
    try:
        raw = json.loads(m.group(0))
    except ValueError:
        return None
    if not isinstance(raw, dict) or raw.get("action") not in ACTIONS:
        return None
    qs = raw.get("quantities") or []
    return {
        "action": raw["action"],
        "product": _clean_str(raw.get("product")),
        "quantity": _clean_int(raw.get("quantity")),
        "source": _clean_str(raw.get("source")),
        "destination": _clean_str(raw.get("destination")),
        "quantities": tuple(q for q in (_clean_int(x) for x in (qs if isinstance(qs, list) else [])) if q)[:4],
        "answer": _clean_str(raw.get("answer")),
    }


class LLMInterpreter:
    """Rate-limit aware: the gateway's X-RateLimit headers are honoured, so once the quota is spent
    MIRROR stops calling (and says so) instead of making the user wait on a 429."""

    def __init__(self, settings: Settings, post=None):
        self.settings = settings
        self._post = post or self._http_post
        self._blocked_until = 0.0
        self._down_until = 0.0  # after a network failure, skip the gateway briefly instead of waiting on it per turn
        self.last_outcome: str | None = None  # "ok" | "rate_limited" | "unreachable" | "error" | "unusable" | "disabled"

    def rate_limited_for(self) -> float:
        return max(0.0, self._blocked_until - time.monotonic())

    def status(self) -> dict:
        return {"enabled": self.enabled, "model": self.model, "rate_limited_for_seconds": round(self.rate_limited_for())}

    def _note_limits(self, headers) -> None:
        try:
            remaining = int(headers.get("X-RateLimit-Remaining", "1"))
            reset = float(headers.get("X-RateLimit-Reset", "60"))
        except (TypeError, ValueError):
            return
        if remaining <= 0:
            self._blocked_until = time.monotonic() + reset

    @property
    def enabled(self) -> bool:
        return bool(self.settings.llm_gateway_enabled and self.settings.assemblyai_api_key)

    @property
    def model(self) -> str:
        return self.settings.llm_gateway_model

    def _http_post(self, body: dict) -> dict:
        req = urllib.request.Request(
            self.settings.llm_gateway_url,
            data=json.dumps(body).encode(),
            method="POST",
            # Server-side only; LLM Gateway accepts the raw AssemblyAI key.
            headers={"Authorization": self.settings.assemblyai_api_key or "", "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            self._note_limits(r.headers)
            return json.loads(r.read())

    def interpret(self, text: str, context: str = "") -> LLMProposal | None:
        if not self.enabled:
            self.last_outcome = "disabled"
            return None
        if self.rate_limited_for() > 0:
            self.last_outcome = "rate_limited"
            return None
        if time.monotonic() < self._down_until:
            self.last_outcome = "unreachable"
            return None
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content": (f"Context: {context}\n" if context else "") + f"Message: {text[:500]}"},
            ],
            "max_tokens": 160,
            "temperature": 0,
            "post_processing_steps": [{"type": "json-repair"}],
        }
        if self.model in STRUCTURED_OUTPUT_MODELS:
            body["response_format"] = {"type": "json_schema", "json_schema": {"name": "mirror_intent", "schema": SCHEMA, "strict": True}}
        started = time.perf_counter()
        try:
            data = self._post(body)
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                wait = exc.headers.get("Retry-After") or exc.headers.get("X-RateLimit-Reset") or "60"
                self._blocked_until = time.monotonic() + float(wait)
                self.last_outcome = "rate_limited"
            else:
                self.last_outcome = "error"
            log.warning("LLM Gateway HTTP %s", exc.code)
            return None
        except Exception as exc:  # noqa: BLE001 - network or timeout: fall back to the deterministic path
            self.last_outcome = "unreachable"
            self._down_until = time.monotonic() + UNREACHABLE_BACKOFF_S
            log.warning("LLM Gateway unavailable: %s", exc)
            return None
        latency = (time.perf_counter() - started) * 1000
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            return None
        parsed = parse_proposal(content)
        if parsed is None:
            self.last_outcome = "unusable"
            log.info("LLM Gateway returned unusable output (request_id=%s)", data.get("request_id"))
            return None
        self.last_outcome = "ok"
        return LLMProposal(**parsed, model=self.model, request_id=data.get("request_id"), latency_ms=latency)
