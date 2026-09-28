# Project status against the build plan

Status as of 2026-09-26 (updated after the public-demo work). Categories: **LIVE** (works against the real thing), **MOCKED** (a deliberate stand-in named by the plan), **LOCAL FALLBACK** (works without the external dependency), **NOT VERIFIED** (built but not exercised against the real dependency), **NOT DONE**.

## Integration status

| Area | Status | Notes |
| --- | --- | --- |
| Transaction runtime, simulation, policy, clarification, commit gate, verification, rollback, audit | LIVE | Real code paths, real SQLite persistence, 106 backend tests + browser E2E |
| Reality watch (re-simulate open previews when the ERP changes, spoken alert) | LIVE | Unit tests; live AssemblyAI run: the agent announced the change unprompted via `reply.create` (28/28 adversarial and honesty checks) |
| Per-visitor sandbox worlds | LIVE | Unit tests; replay E2E opens a second visitor after a full replay and finds the baseline world |
| Public-demo guardrails (rate limits, voice time, concurrency and daily caps) | LIVE | Unit tests; limits are in-process (single worker) |
| AssemblyAI LLM Gateway (free-form typed commands) | LIVE | 6/6 live phrasings ([llm-gateway-live.txt](llm-gateway-live.txt)); this account can use only `qwen3.5-4b-32k-fast` and 2 requests per minute, which MIRROR honours |
| Landing page and judge replay | LIVE | Replay recorded from a live AssemblyAI session (8/8 checks); the page re-runs every step against the backend; replay E2E passes on dev, production build and the Docker image |
| Inventory ERP | MOCKED (per plan part 6) | Separate database with real writes, idempotency, CAS preconditions; not a real ERP |
| Fault injection | MOCKED (per plan part 58) | Deterministic, one-shot, armed explicitly |
| Typed command path | LOCAL FALLBACK | Deterministic compiler; drives the identical runtime when no AssemblyAI key is set |
| Browser audio capture/playback + backend voice relay | LIVE | Chrome with a WAV-fed microphone, through the real UI, to the real AssemblyAI service: 13/13 checks, ending in a spoken commit that changed the ERP and was verified |
| AssemblyAI Voice Agent API (real service) | LIVE | 53/53 checks over 39 spoken turns and 31 tool calls (`scripts/live_voice_test.py`, results in [live-results.json](live-results.json)); re-run after the reality watch: 28/28; judge session 8/8 |
| AssemblyAI `session.resume` | LIVE, with fallback | The live service answered every `session.resume` with `session_not_found` (tested 0.2 s, 1.5 s and 6 s after a drop, with and without `resume_token`). MIRROR tries it, then opens one fresh session and restores the open transaction as context; verified live |
| Docker image | LIVE (typed and replay paths) | Built from the exact Hugging Face Space file set; typed and replay browser suites pass against it; runs as uid 1000. Live voice through Docker Desktop on the dev machine failed because the VM's upload path stalls ([deploy.md](deploy.md#verified-here-and-one-caveat)) |
| Public deployment | READY, NOT DEPLOYED | `scripts/deploy_hf_space.py` (dry run verified) needs the owner's Hugging Face login; keep-awake workflow included |
| Human-voice validation | TOOLING READY | `record_lines.py` + `--recordings` mode ([human-voice.md](human-voice.md)); no human recordings made yet. The replay uses AssemblyAI's `george` voice for the user's lines |
| Cross-browser | LIVE | Typed and replay browser suites pass in Chromium, Firefox and WebKit |
| Competitor research (plan parts 0–2) | DONE | 144 hackathon submissions reviewed; positioning in the submission materials |
| Demo video, pitch deck | SCRIPT READY | [submission/](../submission/) has the video script, slides and texts; recording the video is the owner's step |

## Requirement → implementation → test

| Plan part | Requirement | Implementation | Test | Status |
| --- | --- | --- | --- | --- |
| 4, 11, 40 | Simulation on a shadow world, never mutating live state | `simulate()` takes a frozen `WorldSnapshot`; no ERP handle | `test_simulation_never_writes_reality`, `test_snapshot_is_immutable` | PASS |
| 5 | Spoken / intent / shadow / real made visible | IntentStrip, Possible futures, mirror line, Real world panel | browser E2E | PASS |
| 7–8 | 5 locations, 5 products, attributes, signature state | `erp/seed.py` | `test_valid_transfer_to_pune_site_2` et al. | PASS |
| 9–10 | Fork without asking first; candidates from data; no fake probabilities | `EntityResolver`, `Runtime._expand`; no probability field shown | `test_demo_command_forks_pune`, `test_ambiguous_command_forks_into_two_simulated_futures` | PASS |
| 12 | Consequence records with before/after/threshold | `Consequence` dataclass | `test_capacity_breach_at_pune_site_1` | PASS |
| 13 | Branch A blocked 1200→1900, Branch B valid 300→1000 | simulator + policy | transactions test, E2E | PASS |
| 14 | Consequence-aware clarification sentence | `clarification/optimizer.py` | exact-string assertion | PASS |
| 15 | `ambiguity × divergence × risk` ranking | `score_fields` | clarification scores in view; role-fork test | PASS |
| 16 | What-if 900: breach by 100, nothing changed | `Runtime.what_if` | exact reply assertion | PASS |
| 17 | Compare 500 vs 900 table | `Runtime.compare` | API and transactions tests, E2E | PASS |
| 18 | Max safe quantity by deterministic search | `max_safe_quantity` (exhaustive) | `test_optimizer_finds_800` | PASS |
| 19 | Real world changes only after explicit commit | commit gate + evidence | `test_commit_requires_explicit_user_evidence`, voice tests | PASS |
| 20 | Immutable versions; change invalidates token | append-only `transaction_versions`; token includes version | `test_stale_version_token_is_rejected` | PASS |
| 21 | Token bound to txn, version, evidence hash, simulation hash | `commit/tokens.py` | forged/stale token tests | PASS |
| 22 | `TRANSFER_INVENTORY` really modifies persistent state | `MockERP.transfer_inventory` | world read-back assertions | PASS |
| 23 | Verified only if actual == predicted | `compare_worlds` over all 25 cells | fault tests | PASS |
| 24 | Controlled divergence 1550/750 | `Fault.VERIFICATION_MISMATCH` | `test_fault_injection_diverges_and_is_never_verified`, E2E | PASS |
| 25 | Rollback, then verify again | `plan_compensation` + verification | rollback tests, E2E | PASS |
| 26, 28 | AssemblyAI protocol events; delta overwrite; interruption handling | `voice/relay.py` | protocol tests, live suite (barge-in interrupted a reply, audio flushed) | PASS (live) |
| 27 | PCM16 24 kHz mono via AudioWorklet, echo cancellation | `lib/audio.ts` | voice E2E (50 ms chunks); live browser run | PASS (live) |
| 29–31 | Proposal tools only; tool call is never permission | `voice/agent_config.py`, `voice/tools.py` | protocol tests; live: model did not commit on "that sounds good", wrong-quantity commit refused | PASS (live) |
| 32 | Session resume | `VoiceBridge._supervise` | resume accepted and resume refused (stand-in); live drop recovered with context kept | PASS (live, via fallback) |
| 33 | Keyterms, dynamic when context narrows | `keyterms_for` | live: spoken "X R 17" transcribed as "XR-17" in every turn; mid-session `session.update` accepted | PASS (live) |
| 34 | System prompt | `SYSTEM_PROMPT` (plan text + tool guidance) | — | DONE |
| 35–36 | Evidence statuses and immutability | `intent/evidence.py`, `evidence` table | supersession test | PASS |
| 37 | Data model tables | `core/db.py`, `erp/service.py` | all tests | PASS |
| 38 | Hash-chained ledger + `verify_chain()` | `audit/ledger.py`, `scripts/verify_ledger.py` | tamper test | PASS |
| 39 | State machine with deterministic transitions | `core/state_machine.py` | state trail assertions | PASS |
| 41–47 | React/TS/Vite/Tailwind/Zustand command-control UI, futures, reality status, branch tree | `apps/web` | browser E2E | PASS |
| 48–49 | Demo script incl. rollback | [demo.md](demo.md) | E2E follows it | PASS |
| 50 | UI shows only real backend results | store renders API/WS data only | E2E | PASS |
| 51–52 | Unit and adversarial tests | `backend/tests` | 66 passing | PASS |
| 53 | Interruption visible in the UI | REPLY_INTERRUPTED / TOOL_CALL_DISCARDED lines | voice E2E screenshot; live barge-in | PASS |
| 54–55 | API and event contract | `api/routes.py`, `core/events.py` | API tests | PASS |
| 56 | Idempotency | `executions.idempotency_key` unique | idempotency tests | PASS |
| 57 | Error contract | `core/errors.py` | validation tests | PASS (`SIMULATION_FAILED` reserved, never emitted) |
| 58 | Deterministic faults: mismatch, ERP failure, duplicate commit | `Fault` enum (+ unknown result) | fault tests | PASS |
| 59, 61 | Measured metrics only | `Runtime.metrics`, voice metrics, smoke test | smoke test output | PASS |
| 60 | 20–30 scenario benchmark, actually run | `scripts/benchmark.py` | [benchmark.md](benchmark.md) | PASS |
| 62 | LLM never the authority | see [threat-model.md](threat-model.md) | adversarial tests | PASS |
| 63–64 | Repository structure and stack | as specified, plus `backend/app/runtime/` | — | DONE |
| 67–71 | Acceptance tests 1–5 | see [testing.md](testing.md) | 1–4 PASS live; 5 PASS live for reply interruption, and for a pending commit only against the stand-in (see limitations) | PASS |
| 83 | Public deployment, video, pitch | deploy script, replay, submission kit | dry run, Docker E2E | READY (deploy and recording are the owner's steps) |

## Found by live testing, and fixed

- **`session.updated` arrives before `session.ready`** on the live service. The relay no longer treats early traffic as readiness except while resuming.
- **Resume refused → reconnect cascade.** Reconnecting from inside the old socket's read loop spawned duplicate sessions and repeated greetings. Rewritten around one supervisor task; covered by `test_resume_refused_falls_back_to_fresh_session_with_context`.
- **Voice idempotency keys collided across sessions** (upstream item ids are per upstream session). Keys are now scoped to the MIRROR session.
- **The agent claimed an action it had not taken** ("I have cancelled the pending request" after "don't commit yet", with no tool call). The system prompt now forbids claiming actions without a tool result in the same turn; the live honesty checks pass.
- **Model-chosen location roles were trusted.** A location the user said "from" is now only evidence for the source; a correction routed through `propose_transfer` is handled as an amendment.
- **Spelled-out numbers were garbled** ("one thousand five00"); the agent now keeps the backend's digits.
- **Misleading latency metrics** (server events arrive back to back) were removed; user-perceived latency is measured end to end.

- **Docker Desktop hid a silent voice failure.** Through the container, AssemblyAI heard nothing and the socket dropped every ~50 s; the relay now records each drop (`VOICE_CONNECTION_LOST` with the close code) and the UI says "reconnected" instead of a second "connected". The cause was the VM's upload throughput, not MIRROR ([deploy.md](deploy.md#verified-here-and-one-caveat)).
- **A client could choose its own rate-limit bucket** by sending `X-Forwarded-For`; the IP is now the entry the trusted proxy appended, and session creation has a global cap.
- **The Hugging Face Space page could not have shown the app** (`frame-ancestors 'none'`); embedding is now allowed for a configured origin only.
- **A re-simulation was labelled "Spoken"** in the intent strip; it now says it was re-simulated after the ERP changed.
- **Simultaneous voice starts slipped past the caps.** Many sessions (or two sockets of one session) starting at once could each open an AssemblyAI session before any was registered, and the daily budget only counted finished sessions. Slots are now reserved before the first await, the budget includes live time, and a watchdog stops sessions at either limit.
- **A long-running demo grew without bound**: sandbox files were only cleaned at startup and event buffers of old sessions were never freed. Both are now bounded while the server runs.
- **Clicking during the replay desynchronized it** from the recording. The console's actions are locked while the replay runs.
- **Typed commands crashed in a browser without Web Audio** (found with WebKit): audio is now optional, and voice explains why it cannot start.
- **The replay could stall forever** where a browser never reports the end of a clip; each clip now has a time limit from its recorded length.
- **"Compare 500, 900."** (how the live transcriber punctuated "compare 500 and 900") was not read as a comparison by the typed path; spoken lists now count as amounts when the first number does.

## Known limitations

- **Pending-commit barge-in could not be forced live.** In both attempts the live service completed the commit reply before the interruption registered, so the commit executed (and verified) and the late correction was refused. Discarding an interrupted commit is proven against the protocol stand-in and by live interruption of an ordinary reply.
- **Short phrases spoken over the agent are sometimes misheard** ("Stop" as "job", "Wait" as "Weight"). The agent asked what was meant instead of acting, so nothing unsafe followed.
- **Live test speech is synthesized** (Windows SAPI voices), not human speech with accents or background noise.
- The deterministic compiler covers the demo grammar and common variations. Other typed phrasing goes to the LLM Gateway, which on this account allows 2 requests per minute; beyond that MIRROR says free-form understanding is paused and standard phrasings still work.
- The replay's user side is AssemblyAI's `george` voice, not a person (labelled as such on the page); `scripts/record_lines.py` replaces it with a real recording ([human-voice.md](human-voice.md)).
- Sandboxes, rate-limit counters and voice usage live in one process; the data directory is not persistent on a free Hugging Face Space.
- Single-process concurrency model (see threat model).
- The mock ERP enforces non-negative stock and capacity itself; route availability, safety stock and transfer limits are MIRROR policy.
- No frontend unit tests; the UI is tested through the browser suites.
