"""Commit tokens (build plan part 21).

A token is an HMAC over (transaction_id, version, evidence_hash, simulation_hash).
It authorizes exactly one preview: any new version, any change in the evidence, or
any change in the simulated consequences makes it invalid.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json

from ..core.errors import ErrorCode, MirrorError


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _sign(secret: bytes, claims: dict) -> str:
    message = f"{claims['t']}|{claims['v']}|{claims['e']}|{claims['s']}".encode()
    return hmac.new(secret, message, hashlib.sha256).hexdigest()


def mint(secret: bytes, transaction_id: str, version: int, evidence_hash: str, simulation_hash: str) -> str:
    claims = {"t": transaction_id, "v": version, "e": evidence_hash, "s": simulation_hash}
    body = _b64(json.dumps(claims, sort_keys=True, separators=(",", ":")).encode())
    return f"{body}.{_sign(secret, claims)}"


def verify(secret: bytes, token: str, transaction_id: str, version: int, evidence_hash: str, simulation_hash: str) -> None:
    """Raise a specific error for every way a token can be wrong or stale."""
    try:
        body, signature = token.split(".", 1)
        claims = json.loads(_unb64(body))
        if not isinstance(claims, dict) or set(claims) != {"t", "v", "e", "s"}:
            raise ValueError("claims")
    except (ValueError, TypeError, AttributeError):
        raise MirrorError(ErrorCode.INVALID_COMMIT_TOKEN, "Commit token is malformed")

    if not hmac.compare_digest(_sign(secret, claims), signature):
        raise MirrorError(ErrorCode.INVALID_COMMIT_TOKEN, "Commit token signature is invalid")
    if claims["t"] != transaction_id:
        raise MirrorError(ErrorCode.INVALID_COMMIT_TOKEN, "Commit token belongs to a different transaction")
    if claims["v"] != version:
        raise MirrorError(
            ErrorCode.STALE_TRANSACTION,
            f"Commit token is for version {claims['v']}, but the transaction is at version {version}",
            token_version=claims["v"],
            current_version=version,
        )
    if claims["e"] != evidence_hash:
        raise MirrorError(ErrorCode.INVALID_COMMIT_TOKEN, "Evidence changed since this token was issued")
    if claims["s"] != simulation_hash:
        raise MirrorError(ErrorCode.STALE_TRANSACTION, "Simulation changed since this token was issued")
