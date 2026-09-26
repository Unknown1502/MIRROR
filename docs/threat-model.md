# Threat model

MIRROR exists to stop language from turning directly into side effects. This document lists what could still go wrong and what is in place against it.

## Assets

1. **Reality**: the ERP's inventory. The only thing that must never change without an explicit, current, verified decision.
2. **Authorization**: commit tokens, idempotency keys, session tokens.
3. **Evidence and history**: transcripts, evidence, versions, audit ledger.
4. **Secrets**: `ASSEMBLYAI_API_KEY`, `MIRROR_COMMIT_SECRET`, `DEMO_ADMIN_TOKEN`.

## Trust boundaries

| Party | Trusted for | Not trusted for |
| --- | --- | --- |
| AssemblyAI transcripts | What the user said (evidence source) | Anything else |
| Language model (tool calls) | Pointing at a likely action | Values, choices between candidates, authorization, whether anything happened |
| Browser | Rendering, user clicks, microphone audio | Business state, authorization, transcripts used as evidence |
| ERP | Holding reality | Its own report of what it did |
| MIRROR runtime + policy | Decisions | — |

## Threats and mitigations

| # | Threat | Mitigation | Where | Tested |
| --- | --- | --- | --- | --- |
| T1 | Model executes on its own ("I'll go ahead and commit") | No execute tool exists. `commit_transfer` is only exposed at `PREVIEW_READY`, and the gate requires un-negated commit language in the user's latest final transcript | `voice/agent_config.py`, `voice/tools.py`, `intent/compiler.py` | `test_model_cannot_commit_without_user_saying_commit` |
| T2 | Model silently resolves ambiguity ("the Pune site" → Site 1) | Model values are pointers; candidates come from what the transcript says. Ambiguous speech stays ambiguous | `intent/evidence.py:check_model_value` | `test_model_value_cannot_resolve_ambiguity`, voice acceptance test |
| T3 | Model invents a value the user never said | Unsupported values are `INFERRED`; policy requires clarification | `intent/evidence.py`, `policy/engine.py` | `test_model_invented_quantity_requires_confirmation` |
| T3b | Model swaps or assigns roles ("from Mumbai to Delhi" sent as source Delhi) | A location said as the source is only evidence for the source; role-less corrections fork on role | `intent/evidence.py:_spoken_role`, `voice/tools.py` | `test_model_swapped_roles_are_not_evidence`, `test_correction_through_propose_tool_still_forks_on_role`, live adversarial run |
| T3c | Agent says something happened that did not | Tool results carry backend-written sentences; the prompt forbids claiming actions without a tool result in the same turn; the UI shows authoritative state | `voice/agent_config.py` | live honesty section (the defect was observed live, then fixed) |
| T4 | Model invents an entity | Resolution only against ERP data; unknown names yield zero candidates | `intent/entities.py` | `test_unknown_location_resolves_to_nothing` |
| T5 | Commit on a stale preview (old version, old token) | Token is HMAC over transaction, version, evidence hash, simulation hash; version must be current | `commit/tokens.py`, `Runtime.commit` | `test_stale_version_token_is_rejected`, `test_forged_token_is_rejected` |
| T6 | Reality changed after the preview | Re-simulation at commit must reproduce the simulation hash; ERP compare-and-swap preconditions | `Runtime.commit`, `MockERP._apply` | `test_world_changed_after_preview_makes_commit_stale` |
| T7 | Double commit (double click, network retry, two tabs) | Unique idempotency key per execution; committed transactions refuse new keys; runtime lock; ERP operation ids are idempotent | `Runtime.commit`, `MockERP` | `test_same_commit_twice_is_idempotent`, `test_concurrent_double_click_executes_once`, `test_duplicate_delivery_is_applied_once` |
| T8 | Interrupted commit still executes | Tool calls are held until their reply completes; interrupted replies drop them unexecuted | `voice/relay.py` | acceptance test 5 (protocol and browser) |
| T9 | Executor lies or partially applies | Full-world read-back; only equality is `VERIFIED`; mismatch is `DIVERGED` | `verification/verifier.py` | `test_fault_injection_diverges_and_is_never_verified` |
| T10 | Unknown execution outcome reported as success or failure | `UNKNOWN` status resolved only by reading reality | `Runtime._execute_commit` | `test_unknown_execution_result_is_resolved_by_reading_reality` |
| T11 | Rollback overwrites someone else's change | Rollback refuses if touched cells moved since the commit; compensation from actual post-state; CAS preconditions | `verification/rollback.py` | `test_rollback_refuses_to_overwrite_later_changes` |
| T12 | History rewritten | Append-only triggers on history tables; SHA-256 hash chain over every audit field; `verify_chain` | `core/db.py`, `audit/ledger.py` | `test_history_tables_are_append_only`, `test_audit_chain_verifies_and_detects_tampering` |
| T13 | API key exposed to the browser | Backend relay; key only in server env and the upstream `Authorization` header; settings `repr` hides it | `voice/relay.py`, `core/config.py` | code review |
| T14 | Another session reads or commits my transaction | Session token (SHA-256 stored) on every call; ownership check returns 404 | `api/routes.py` | `test_other_session_cannot_see_or_commit_transaction` |
| T15 | Session token leaked in logs | WebSocket authenticates with its first message, not a URL parameter | `api/ws.py`, `lib/socket.ts` | `test_websocket_rejects_bad_token` |
| T16 | Malformed or oversized input | Pydantic models with bounds, `extra="forbid"`; WS message and audio size caps | `api/schemas.py`, `api/ws.py`, `voice/relay.py` | `test_error_contract_and_validation` |
| T17 | Prompt injection via reflected model text | Model-supplied strings are truncated to printable text before storage or echo; tool results come from runtime templates | `intent/evidence.py:_bounded`, `voice/tools.py` | code review |
| T18 | SQL injection | SQLAlchemy parameterized queries only; no model text reaches SQL | all persistence | code review |
| T19 | Error detail leakage | Contract errors are deliberate messages; unexpected errors return `INTERNAL` without detail and are logged server-side | `main.py` | code review |
| T20 | XSS / clickjacking | React escaping; CSP (`default-src 'self'`, `frame-ancestors 'none'` unless `MIRROR_FRAME_ANCESTORS` names an embedder such as huggingface.co); `X-Frame-Options: DENY` when not embeddable; `nosniff` | `main.py` | `test_frame_ancestors_are_configurable` |
| T21 | Demo controls abused on a public deployment | Disabled unless `DEMO_MODE=true`. In sandbox mode they act only on the caller's own world; otherwise `DEMO_ADMIN_TOKEN` enforces `X-Demo-Token` | `api/routes.py:demo_guard` | `test_demo_controls_disabled_outside_demo_mode`, sandbox tests |
| T22 | One public visitor breaks another's demo (commit, fault, reset) | `MIRROR_SANDBOX_PER_SESSION`: a private ERP world per session, safe-id file names, LRU of open worlds, 24 h cleanup | `erp/worlds.py` | `test_sandbox_watch.py`, replay E2E second-visitor check |
| T23 | Someone burns the AssemblyAI credits | Voice time per session, concurrent voice sessions, and a daily voice budget, enforced server-side; the session is stopped at the cap | `voice/relay.py:VoiceRegistry` | `test_voice_concurrency_cap`, `test_voice_time_cap_stops_and_blocks_restart` |
| T24 | Request floods, or dodging per-IP limits with a forged `X-Forwarded-For` | Sliding-window limits per IP and per session, plus a global cap on session creation; the client IP is the entry the trusted proxy appended | `api/ratelimit.py`, `main.py` | `test_session_creation_is_rate_limited`, `test_client_ip_is_the_one_the_proxy_saw` |
| T25 | The LLM Gateway turns free text into an action | Its output is a proposal mapped onto the same proposal tools, so values need transcript evidence and commit needs commit words; output is schema-validated and bounded; failures fall back to help text | `intent/llm_interpreter.py`, `runtime/dispatcher.py` | `test_llm_gateway.py`; live: "yep sounds right to me" proposed as commit, refused |
| T26 | Reality moves under an open preview and nobody notices | The reality watch re-simulates dependent previews after every external change and every verified commit or rollback, revokes the old token, and the agent announces it | `Runtime._reality_watch`, `VoiceBridge.announce` | `test_external_change_re_simulates_the_open_preview_and_says_so`, live adversarial run |

## Residual risks (not mitigated)

- **WebSocket messages are not rate-limited** beyond size caps; a client can stream audio into its own voice session, bounded by the voice caps (AssemblyAI enforces real-time rate on its side).
- **Rate limits and voice usage live in one process.** Counters reset on restart (the daily voice total is persisted to `voice_usage.json`).
- **`/world`, `/audit/verify`, `/health`, `/demo/status` are unauthenticated reads.** Acceptable for demo data; a real deployment would scope them.
- **With `DEMO_ADMIN_TOKEN` set, the UI's demo controls have no way to send it** and will receive 401. Presenters would call the endpoints directly or leave the token unset on a private deployment.
- **The agent's speech is model output.** The prompt rule against unbacked claims fixed the case observed live, but a model can still misspeak; the screen, not the voice, is the authoritative record.
- **Transcription errors are evidence.** If AssemblyAI mishears "Site 1" as "Site 2", the evidence is wrong in a way MIRROR cannot see; the simulated consequences on screen are the user's chance to catch it.
- **Single process.** The runtime lock serializes mutations in one process. Several workers would need a database-level lock around commit (the ERP's compare-and-swap still prevents a double apply).
- **Ephemeral commit secret** if `MIRROR_COMMIT_SECRET` is unset: tokens stop validating after a restart (fails closed).
