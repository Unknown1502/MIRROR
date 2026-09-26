"""Canonical serialization and hashing. Every hash in MIRROR goes through here."""

from __future__ import annotations

import hashlib
import json
from typing import Any

GENESIS_HASH = "0" * 64


def canonical_json(value: Any) -> str:
    """Deterministic JSON: sorted keys, no whitespace, no lossy fallbacks."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def hash_value(value: Any) -> str:
    return sha256_hex(canonical_json(value))
