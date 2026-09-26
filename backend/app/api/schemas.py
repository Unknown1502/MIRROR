"""Request bodies. Every field is bounded; unknown fields are rejected."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TurnRequest(_Strict):
    text: str = Field(min_length=1, max_length=500)
    context_version: int | None = Field(default=None, ge=1)


class TransferAction(_Strict):
    product: str | None = Field(default=None, max_length=64)
    quantity: int | None = Field(default=None, ge=1, le=1_000_000)
    source: str | None = Field(default=None, max_length=64)
    destination: str | None = Field(default=None, max_length=64)


class CreateTransactionRequest(_Strict):
    action: TransferAction
    utterance: str | None = Field(default=None, max_length=500)


class WhatIfRequest(TransferAction):
    pass


class ClarifyRequest(_Strict):
    branch_key: str | None = Field(default=None, pattern=r"^[a-h]$")
    field: str | None = Field(default=None, pattern=r"^(product|quantity|source|destination)$")
    value: str | int | None = None


class CompareRequest(_Strict):
    quantities: list[int] = Field(default_factory=list, max_length=6)
    versions: list[int] = Field(default_factory=list, max_length=6)


class CommitRequest(_Strict):
    version: int = Field(ge=1)
    commit_token: str = Field(min_length=10, max_length=2000)
    idempotency_key: str = Field(min_length=8, max_length=200)


class RollbackRequest(_Strict):
    idempotency_key: str = Field(min_length=8, max_length=200)


class FaultRequest(_Strict):
    fault: str = Field(pattern=r"^(none|verification_mismatch|temporary_erp_failure|unknown_result|duplicate_commit)$")


class ExternalChangeRequest(_Strict):
    location_id: str = Field(max_length=64)
    product_id: str = Field(max_length=64)
    delta: int = Field(ge=-100_000, le=100_000)
    note: str = Field(default="external system adjustment", max_length=200)
