# API

Base path `/api/v1`. JSON in, JSON out. Unknown request fields are rejected.

## Authentication

`POST /sessions` returns `session_id` and `session_token`. Every session-scoped call sends:

```http
X-Session-Id: sess_…
Authorization: Bearer <session_token>
```

Transactions are visible only to the session that created them (another session gets `404`). Sessions expire after 12 hours. Demo endpoints require `DEMO_MODE=true`, and `X-Demo-Token` when `DEMO_ADMIN_TOKEN` is set.

**Sandbox mode** (`MIRROR_SANDBOX_PER_SESSION=true`, used for the public demo): every session gets its own copy of the ERP world. `/world`, `/demo/status` and all transaction endpoints then act on the caller's sandbox, demo endpoints require the session headers (no admin token), and resets, faults and external changes never reach another visitor. Sandboxes are deleted after 24 hours.

`POST /sessions` returns, besides the credentials: `voice_available`, `demo_mode`, `fault_injection`, `sandbox`, `voice_max_session_seconds`, and `llm_gateway` (`{enabled, model, rate_limited_for_seconds}` or null).

## Endpoints

| Method | Path | Auth | Purpose |
| --- | --- | --- | --- |
| GET | `/health` | none | Liveness, voice availability, world revision, ledger check |
| POST | `/sessions` | none | Create a session |
| GET | `/sessions/{id}` | session | Session info and transcript |
| POST | `/sessions/{id}/turns` | session | Typed utterance through the deterministic compiler (extension) |
| POST | `/transactions` | session | Structured proposal: `{action: {product, quantity, source, destination}, utterance?}` |
| GET | `/transactions/{id}` | session | Full transaction view |
| POST | `/transactions/{id}/what-if` | session | Counterfactual version: any subset of `{product, quantity, source, destination}` |
| POST | `/transactions/{id}/clarify` | session | `{branch_key}` or `{field, value}` |
| POST | `/transactions/{id}/compare` | session | `{quantities: [..]}` and/or `{versions: [..]}`; read-only (extension) |
| POST | `/transactions/{id}/optimize` | session | Maximum safe quantity (extension) |
| POST | `/transactions/{id}/commit` | session | `{version, commit_token, idempotency_key}` |
| POST | `/transactions/{id}/rollback` | session | `{idempotency_key}` |
| POST | `/transactions/{id}/abort` | session | Abandon an uncommitted transaction |
| POST | `/transactions/{id}/verify` | session | Re-read reality when the post-state is unknown (extension) |
| GET | `/transactions/{id}/audit` | session | The transaction's audit events plus a chain check |
| GET | `/world` | optional session | Live ERP snapshot (the caller's sandbox in sandbox mode), armed fault, recent ERP operations |
| GET | `/audit/verify` | none | `verify_chain()` over the whole ledger |
| GET | `/metrics` | session | Measured runtime and voice metrics for this session, plus `voice_remaining_seconds` |
| GET | `/demo/status` | optional session | Demo flags, sandbox flag and armed fault |
| POST | `/demo/reset` | demo | Restore the baseline world; abort open proposals |
| POST | `/demo/faults` | demo | `{fault: none/verification_mismatch/temporary_erp_failure/unknown_result/duplicate_commit}` (one-shot) |
| POST | `/demo/external-change` | demo | `{location_id, product_id, delta, note}`: another system writes to the ERP |

"Extension" marks endpoints beyond the build plan's contract that the UI and tests need.

`POST /sessions/{id}/turns` returns `{reply, command, transaction, comparison, error, interpreted_by}`. `interpreted_by` is null when the deterministic compiler understood the text; when it did not, the text went to AssemblyAI's LLM Gateway and `interpreted_by` is `{model, request_id, latency_ms, action}`. The model's answer is mapped onto the same proposal tools the voice agent uses, so every value still has to be found in the user's own words (an invented number stays `INFERRED`, and "commit" without the word commit is refused).

### Rate limits

With `MIRROR_RATE_LIMIT=true` (default): session creation 120 per hour per client IP and 1500 per hour in total, turns 40 per minute per session, transaction actions 60 per minute per session, demo controls 30 per minute per IP. The client IP is the address the nearest trusted proxy saw (`MIRROR_PROXY_HOPS`, default 1), so a client cannot pick its own via `X-Forwarded-For`. Over the limit: `429` with `Retry-After` and code `RATE_LIMITED`.

Voice has its own caps: `VOICE_MAX_SESSION_SECONDS` per session (default 600), `VOICE_MAX_CONCURRENT` live voice sessions (default 4), and `VOICE_DAILY_BUDGET_SECONDS` across all sessions (default 0 = unlimited). A refused or ended voice session reports `ASSEMBLYAI_UNAVAILABLE` with `details.reason` `voice_time_limit`, `voice_concurrency_limit` or `voice_daily_budget`; typed commands keep working.

## Transaction view (abridged)

```json
{
  "id": "txn_d7391cadfeda0edc",
  "state": "CLARIFYING",
  "reality": "UNCHANGED",
  "real_world_changed": false,
  "live_writes": 0,
  "current_version": 1,
  "committed_version": null,
  "versions": [{
    "version": 1, "kind": "PROPOSAL", "status": "AMBIGUOUS",
    "state_trail": ["INTERPRETING", "FORKING", "SIMULATING", "CONSEQUENCE_REVIEW", "CLARIFYING"],
    "evidence_hash": "…", "simulation_hash": "…",
    "clarification": {
      "field": "destination",
      "question": "There are two Pune sites. Site 1 would exceed capacity by 400 units; Site 2 remains valid. Which one do you mean?",
      "scores": [{"field": "destination", "ambiguity": 0.5, "consequence_divergence": 1.0, "risk_weight": 1.0, "score": 0.5}]
    },
    "branches": [{
      "key": "a",
      "intent": {"product_id": "XR-17", "quantity": 700, "source_id": "MUMBAI-DC", "destination_id": "PUNE-SITE-1"},
      "outcome": "BLOCKED",
      "policy": {"decision": "BLOCK", "blocking_codes": ["DESTINATION_CAPACITY_EXCEEDED"]},
      "simulation": {
        "effects": [{"location_id": "PUNE-SITE-1", "before": 1200, "after": 1900, "capacity": 1500}],
        "consequences": [{"code": "DESTINATION_CAPACITY_EXCEEDED", "severity": "HIGH",
                          "before": 1200, "after": 1900, "threshold": 1500, "delta": 400}]
      }
    }]
  }],
  "commit": null,
  "executions": [],
  "evidence": [{"field": "destination", "candidates": ["PUNE-SITE-1", "PUNE-SITE-2"], "status": "INFERRED",
                "source": {"type": "USER_TEXT", "text_span": "Pune"}, "lifecycle": "ACTIVE"}]
}
```

When `state` is `PREVIEW_READY`, `commit` carries `{version, token, evidence_hash, simulation_hash}`.

## Commit example

```http
POST /api/v1/transactions/txn_…/commit
{"version": 4, "commit_token": "eyJ…", "idempotency_key": "ui:txn_…:v4:6f1c…"}
```

Same key again returns the stored result with `"idempotent_replay": true`. A different key on a committed transaction returns `409 DUPLICATE_COMMIT`.

## Error contract

```json
{"error": {"code": "STALE_TRANSACTION", "message": "v2 is no longer current; the transaction is at v3", "details": {"requested_version": 2, "current_version": 3}}}
```

| Code | HTTP | Meaning |
| --- | --- | --- |
| `AMBIGUOUS_INTENT` | 409 | An answer or action still matches several futures |
| `INSUFFICIENT_EVIDENCE` | 422 | The user never explicitly said it (commit, rollback, a value) |
| `INVALID_TRANSACTION` | 409 | Not allowed in the current state (e.g. commit without a valid preview) |
| `SIMULATION_FAILED` | 500 | Reserved |
| `POLICY_VIOLATION` | 409 | No safe option exists (e.g. optimizer finds none) |
| `STALE_TRANSACTION` | 409 | Version, simulation or quantity no longer matches |
| `INVALID_COMMIT_TOKEN` | 403 | Token malformed, forged, or for another transaction or evidence |
| `DUPLICATE_COMMIT` | 409 | Already committed; will not execute twice |
| `EXECUTION_FAILED` | 502 | Reported in events when the ERP did not execute (verified) |
| `VERIFICATION_FAILED` | 502 | Reality could not be read; state stays unknown |
| `ROLLBACK_FAILED` | 409 | Nothing to roll back, already rolled back, or reality moved since the commit |
| `ASSEMBLYAI_UNAVAILABLE` | 503 | No key configured, AssemblyAI unreachable, or a voice cap reached (`details.reason`) |
| `RATE_LIMITED` | 429 | Too many requests; see `Retry-After` |
| `SESSION_EXPIRED` | 410 | Unknown or expired session |
| `NOT_FOUND` / `UNAUTHORIZED` / `INVALID_REQUEST` / `DEMO_DISABLED` | 404/401/400/403 | Transport-level |

## WebSocket

```text
ws(s)://host/ws/v1/sessions/{session_id}?after=<last sequence seen>
client -> {"type": "auth", "token": "<session_token>"}      (first message, within 5 s)
server -> {"type": "auth.ok"}, then replayed + live events
client -> {"type": "voice.start"} | {"type": "voice.audio", "audio": "<base64 PCM16 24 kHz mono>"} | {"type": "voice.stop"} | {"type": "ping"}
```

The token is sent in the first message rather than the URL so it never appears in access logs.

Event envelope:

```json
{"version": "1", "event_id": "evt_…", "sequence": 42, "timestamp": "2026-09-26T…Z",
 "session_id": "sess_…", "transaction_id": "txn_…", "transaction_version": 3,
 "type": "SIMULATION_COMPLETED", "data": {"branch_key": "a", "outcome": "BLOCKED", "live_writes": 0, "audit_sequence": 118, "audit_hash": "…"}}
```

Event types include every type in the build plan's list (`SESSION_READY`, `VOICE_STARTED`, `VOICE_STOPPED`, `TRANSCRIPT_DELTA`, `USER_TURN`, `INTENT_CREATED`, `BRANCH_CREATED`, `SIMULATION_STARTED`, `SIMULATION_COMPLETED`, `CONSEQUENCES_UPDATED`, `CLARIFICATION_REQUIRED`, `WHAT_IF_STARTED`, `PREVIEW_READY`, `COMMIT_REQUESTED`, `COMMIT_ACCEPTED`, `EXECUTION_STARTED`, `EXECUTION_COMPLETED`, `VERIFICATION_STARTED`, `VERIFIED`, `REALITY_DIVERGED`, `ROLLBACK_STARTED`, `ROLLBACK_COMPLETED`, `ERROR`) plus `STATE_CHANGED`, `COMMIT_TOKEN_INVALIDATED`, `STALE_TRANSACTION`, `COMPARISON_READY`, `EXECUTION_RETRY`, `DUPLICATE_DELIVERY_SUPPRESSED`, `TOOL_CALL_PROPOSED`, `TOOL_CALL_DISCARDED`, `TOOL_RESULT_SENT`, `TOOL_RESULT_WITHHELD`, `REPLY_INTERRUPTED`, `VOICE_RESUME_REFUSED`, `AUDIO_FLUSH`, `AGENT_AUDIO`, `AGENT_TRANSCRIPT`, `VOICE_ACTIVITY`, `VOICE_TOOLS_UPDATED`, `VOICE_METRICS`, `DEMO_RESET`, `FAULT_ARMED`, `WORLD_CHANGED`, `EXTERNAL_WORLD_CHANGE`, and:

- `REALITY_CHANGED`: the ERP changed under an open preview (another system, or another session's commit). MIRROR re-simulated it as a new `RESIMULATION` version. Payload: `{cells, cause, previous_status, new_status, new_version, state, say}`; `say` is what the voice agent announces.
- `VOICE_ANNOUNCEMENT`: the agent was asked to speak without a user turn (`reply.create`), e.g. the reality alert.
- `VOICE_CONNECTION_LOST`: the AssemblyAI socket closed without `session.ended`: `{close_code, close_reason, last_event}`. Pending tool calls are dropped, then the relay reconnects.
- `LLM_INTERPRETATION`: a typed turn was interpreted by the LLM Gateway: Payload: the model's proposal (action and values), `model`, `request_id`, `latency_ms`, the user's `text`, the proposal `tool` it was mapped to, and whether the backend `accepted` it. High-frequency voice events (`TRANSCRIPT_DELTA`, `AGENT_AUDIO`, …) are not written to the audit ledger; everything consequential is.
