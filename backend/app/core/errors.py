"""The MIRROR error contract. Every failure the API reports uses one of these codes."""

from __future__ import annotations

from enum import Enum
from typing import Any


class ErrorCode(str, Enum):
    AMBIGUOUS_INTENT = "AMBIGUOUS_INTENT"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    INVALID_TRANSACTION = "INVALID_TRANSACTION"
    SIMULATION_FAILED = "SIMULATION_FAILED"
    POLICY_VIOLATION = "POLICY_VIOLATION"
    STALE_TRANSACTION = "STALE_TRANSACTION"
    INVALID_COMMIT_TOKEN = "INVALID_COMMIT_TOKEN"
    DUPLICATE_COMMIT = "DUPLICATE_COMMIT"
    EXECUTION_FAILED = "EXECUTION_FAILED"
    VERIFICATION_FAILED = "VERIFICATION_FAILED"
    ROLLBACK_FAILED = "ROLLBACK_FAILED"
    ASSEMBLYAI_UNAVAILABLE = "ASSEMBLYAI_UNAVAILABLE"
    SESSION_EXPIRED = "SESSION_EXPIRED"
    # Transport-level codes (not domain decisions).
    NOT_FOUND = "NOT_FOUND"
    UNAUTHORIZED = "UNAUTHORIZED"
    INVALID_REQUEST = "INVALID_REQUEST"
    DEMO_DISABLED = "DEMO_DISABLED"


_HTTP_STATUS = {
    ErrorCode.AMBIGUOUS_INTENT: 409,
    ErrorCode.INSUFFICIENT_EVIDENCE: 422,
    ErrorCode.INVALID_TRANSACTION: 409,
    ErrorCode.SIMULATION_FAILED: 500,
    ErrorCode.POLICY_VIOLATION: 409,
    ErrorCode.STALE_TRANSACTION: 409,
    ErrorCode.INVALID_COMMIT_TOKEN: 403,
    ErrorCode.DUPLICATE_COMMIT: 409,
    ErrorCode.EXECUTION_FAILED: 502,
    ErrorCode.VERIFICATION_FAILED: 502,
    ErrorCode.ROLLBACK_FAILED: 409,
    ErrorCode.ASSEMBLYAI_UNAVAILABLE: 503,
    ErrorCode.SESSION_EXPIRED: 410,
    ErrorCode.NOT_FOUND: 404,
    ErrorCode.UNAUTHORIZED: 401,
    ErrorCode.INVALID_REQUEST: 400,
    ErrorCode.DEMO_DISABLED: 403,
}


class MirrorError(Exception):
    def __init__(self, code: ErrorCode, message: str, **details: Any):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details

    @property
    def http_status(self) -> int:
        return _HTTP_STATUS[self.code]

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code.value, "message": self.message, "details": self.details}
