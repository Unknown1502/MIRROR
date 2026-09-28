# Testing

Every claim in the README is backed by one of the runs below. Commands assume the repository root on Windows; on macOS/Linux use `backend/.venv/bin/python`.

## 1. Backend unit and integration tests (115)

```bash
cd backend && .venv/Scripts/python -m pytest -q
```

| File | Tests | Covers |
| --- | ---: | --- |
| `test_simulator.py` | 11 | valid transfer, capacity breach (1200 → 1900 vs 1500), safety-stock breach (2000 → 1100 vs 1200), insufficient inventory, unknown product/location, route unavailable, determinism (same hash), **zero ERP writes across 60 simulations**, snapshot immutability, optimizer finds 800 with 801 blocked, simulation latency < 100 ms |
| `test_intent.py` | 12 | candidate generation for "the Pune site", product codes and site numbers not read as quantities, spoken numbers, XR-17 vs XR-17B vs "XR", unknown locations never invented, command classification, commit language (negation, questions, what-ifs), model values cannot resolve ambiguity, model-invented quantity is `INFERRED`, speech-supported values are `EXPLICIT`/`RESOLVED` |
| `test_transactions.py` | 27 | the full demo sequence with exact numbers and the exact clarification sentence; optimizer; commit without a preview; commit of a blocked future; quantity mismatch; "wait, make that 500" supersedes evidence; "actually I meant Mumbai" forks on role; stale version token; forged token; commit without explicit user evidence; idempotent replay; duplicate commit; six concurrent clicks execute once; world changed after preview; amend after commit; fault divergence never verified (1550/750); rollback after divergence compensates 450 and verifies; rollback of a verified commit, never twice; rollback refuses to overwrite later changes; temporary ERP failure retried idempotently; unknown result resolved by reading reality; duplicate delivery applied once; abort after commit refused; audit chain verifies and detects tampering; history tables append-only; every lifecycle step audited |
| `test_api.py` | 12 | health; auth required; cross-session isolation; **acceptance test 1** (structured, no voice) through REST; error contract and validation; typed turns + WebSocket event envelope; WebSocket rejects bad and URL tokens; voice without a key reports `ASSEMBLYAI_UNAVAILABLE`; fault divergence and rollback over HTTP; demo reset repeatable 3×; demo controls disabled outside demo mode; the built UI is served with SPA fallback, `HEAD`, replay assets, JSON 404 for unknown API routes and no path traversal |
| `test_sandbox_watch.py` | 23 | per-visitor sandboxes: isolation of worlds, resets and faults; unsafe session ids rejected; sandbox API requires a session. **Reality watch**: an external change re-simulates an open preview and produces the spoken alert; unrelated changes are ignored; one visitor's commit alerts another visitor's preview in the shared world. Quantity position: "it's been 2 weeks" or "at 3" is not an amount, while a spoken list is ("compare 500, 900", as the live transcriber punctuates it); idle event buffers are freed; stale sandbox files are deleted while the server runs |
| `test_guardrails.py` | 10 | session-creation rate limit (429 with `Retry-After`); client IP from the proxy's `X-Forwarded-For` entry, not the client's; CSP `frame-ancestors` configurable; voice concurrency cap, including **simultaneous starts** from many sessions and a double start of one session (one upstream connection); voice time cap stops the session; the daily voice budget counts sessions that are still live and stops them; reality alert spoken with `reply.create`, and held until `reply.done` when the agent is mid-reply |
| `test_llm_gateway.py` | 11 | LLM Gateway fallback (gateway mocked): a free-form request becomes a checked preview and the `request_id` is audited; a model-invented number is `INFERRED`; the model cannot commit without the user's words; network errors, prose and unknown actions fall back to help; the gateway is not called when the compiler understands; strict JSON schema for models that support it; output validation and bounds; **429 honoured**: one call, then paused with a message instead of retry storms; "with 900" counts as an amount |
| `test_voice_protocol.py` | 9 | against a local fake of the AssemblyAI WebSocket: **acceptance test 2** (voice flow) and **acceptance test 5** (barge-in discards a pending commit, new version invalidates the old authorization); model cannot commit without the user saying commit; model-invented quantity requires confirmation; `session.resume` accepted; resume refused leads to one fresh session, no second greeting, context restored; a tool call from a dropped reply never runs; upstream item ids do not collide across sessions; model-swapped roles are not evidence; a correction sent through `propose_transfer` still forks on role |

Last run: 115 passed.

## 2. Browser end-to-end, typed path

Real Chrome driven by Playwright against a running stack. Fails on any page or console error.

```bash
backend/.venv/Scripts/python scripts/e2e_browser.py --url http://127.0.0.1:8000   # production build served by the backend
backend/.venv/Scripts/python scripts/e2e_browser.py --url http://127.0.0.1:5173   # Vite dev server
backend/.venv/Scripts/python scripts/e2e_browser.py --url http://127.0.0.1:8000 --browser firefox   # or webkit
```

Checks: fork with both futures and the clarification sentence; preview after "Site 2"; what-if 900 blocked with no commit button; compare; commit verified; Demo controls → reset → arm short shipment through the UI → commit → "Reality diverged", reality table shows 1550/750; rollback → "Reality restored", table back to 2000/300; hash chain intact. Last run: PASS on both URLs, and in Chromium, Firefox and WebKit (Safari's engine).

## 2b. Landing page and judge replay

```bash
backend/.venv/Scripts/python scripts/e2e_replay.py --url http://127.0.0.1:8000 --sandbox
```

Checks the landing page (desktop and 390 px phone width, no sideways scroll), then plays the recorded replay at 1.5× and asserts the backend-recomputed screen at every beat: fork with "Over capacity by 400", preview, what-if 900 blocked, comparison, the "Reality changed under your preview" alert with "2000 to 1700", divergence at Mumbai 1250, restoration at 1700. With `--sandbox`, a second visitor opened afterwards must still see Mumbai 2000 / Pune Site 2 300. It also checks that the command box is locked while the replay runs and unlocked after. Last runs: PASS in Chromium, Firefox and WebKit against the production build, and against the Docker image built from the exact Hugging Face Space file set.

## 3. Browser end-to-end, voice path

Chrome with a fake microphone device streams through the real AudioWorklet and the backend relay to `scripts/fake_assemblyai.py`, a scripted stand-in that speaks the documented AssemblyAI event protocol and only starts its conversation after receiving real audio.

```bash
backend/.venv/Scripts/python scripts/fake_assemblyai.py --port 8765 --report report.json
# second backend wired to it, serving the built UI:
MIRROR_DATA_DIR=/tmp/voice ASSEMBLYAI_API_KEY=fake ASSEMBLYAI_AGENT_URL=ws://127.0.0.1:8765 \
  backend/.venv/Scripts/python -m uvicorn app.main:create_app --factory --app-dir backend --port 8001
backend/.venv/Scripts/python scripts/e2e_voice_browser.py --url http://127.0.0.1:8001 --report report.json
```

Last run (under the production Content-Security-Policy): PASS on all 8 checks: browser audio reached the agent (322 chunks, 16.1 s); every chunk exactly 50 ms of PCM16 at 24 kHz; `audio/pcm` input format; commit tool hidden until preview; interrupted tool result discarded; scripted conversation completed; final commit verified; no page errors.

This proves MIRROR's plumbing. It does not prove AssemblyAI's recognition or model behavior: that needs a real key (see status).

## 4. Smoke test and ledger check

```bash
backend/.venv/Scripts/python scripts/smoke_test.py --url http://127.0.0.1:8000
backend/.venv/Scripts/python scripts/verify_ledger.py
```

Last smoke run: all 12 checks PASS. Measured on a Windows laptop: fork round trip 27 ms; commit including ERP execution and full-world verification 44 ms (execution 2.4 ms, verification 1.2 ms). Simulation per branch: 0.1–0.3 ms.

## 5. Benchmark

```bash
backend/.venv/Scripts/python scripts/benchmark.py
```

26 deterministic scenarios, baseline vs MIRROR. See [benchmark.md](benchmark.md).

## 6. Live AssemblyAI (real service, real key)

Speech is synthesized with the Windows voices (24 kHz, 16-bit mono) and streamed in real time through MIRROR's actual relay to `wss://agents.assemblyai.com/v1/ws`. Needs `ASSEMBLYAI_API_KEY` in `.env`; uses its own temporary data directory; the full run costs about 11 minutes of session time.

```bash
backend/.venv/Scripts/python scripts/live_voice_test.py --out live-run            # all sections
backend/.venv/Scripts/python scripts/live_voice_test.py --only demo honesty       # a subset
backend/.venv/Scripts/python scripts/e2e_live_browser.py --url http://127.0.0.1:8000 --out live-browser
```

Last full run (2026-09-26): **53/53 checks**, 39 spoken turns, 31 tool calls, ledger intact. Report: [live-results.json](live-results.json). The agent's audio for each turn is saved as WAV in the output folder.

After the reality watch was added, the honesty and adversarial sections were re-run live: **28/28** ([live-results-reality-watch.json](live-results-reality-watch.json)). With nobody speaking, another system shipped 300 out of Mumbai; MIRROR re-simulated the open preview and the agent said, unprompted: "Heads up: reality changed under your preview. Mumbai Distribution Center XR-17 went from 2000 to 1700 (another team shipped 300). I re-simulated it…". "Commit 500" then executed against the new world and verified at 1200.

The `judge` section is the landing-page replay, recorded as one continuous live session: **8/8** ([live-results-judge.json](live-results-judge.json)). The user's lines in it are spoken by AssemblyAI's `george` voice (`scripts/ai_voice_lines.py`, labelled as an AI voice everywhere) and the agent answers as `anna`; AssemblyAI transcribed them and the agent called the tools live. `scripts/export_replay.py` turns it into `apps/web/public/replay`. Human recordings can replace the synthesized speech in every section: see [human-voice.md](human-voice.md).

| Section | What was verified live |
| --- | --- |
| demo | fork on "the Pune site" with the exact consequence-aware question; clarify; what-if 900 blocked; 500 valid; compare; spoken commit verified, ERP 1500/800 |
| honesty | "don't commit yet", "hold on", "wait": no tool call and no false claim; stock answered from a tool; cancel through the tool |
| divergence | armed short shipment detected as divergence (1550/750) and never verified; spoken rollback restored and verified |
| restraint | "that sounds good to me" did not commit; the model asked for an explicit commit |
| optimizer and drop | max safe 800; socket dropped; resume refused by the service; one fresh session with context; "what about 600" applied to the same transaction |
| barge-in | talking over the agent interrupted the reply (`reply.done` interrupted), flushed audio, and the new turn cancelled the request |
| adversarial | role-ambiguous correction forked; "make that 500" revoked the old authorization; "commit 900" against a 500 preview refused; "don't commit yet" did nothing; reality changed underneath while nobody spoke, and MIRROR re-simulated and announced it; the commit then verified against the new world; second commit did not run; rollback restored the post-change world; unknown city never invented; product family forked and resolved |
| judge | the replay: fork, clarify, what-if 900, what about 500, compare, spoken reality alert, short-shipped commit detected as divergence (1250), spoken rollback restored and verified (1700) |
| commit barge-in | the commit completed before the barge-in registered and verified; the late correction was refused (a safe outcome) |

Measured on this machine and network:

| Latency | Median | Range |
| --- | ---: | ---: |
| End of user speech to final transcript | 1.17 s | 0.0 to 1.25 s |
| End of user speech to first agent audio, with a tool call | 3.35 s | 2.0 to 4.3 s |
| End of user speech to first agent audio, no tool call | 1.9 s | 1.7 to 2.4 s |

Recognition: 34/34 scripted utterances were transcribed exactly (spoken "X R 17" came back as "XR-17", "X R 17 B" as "XR-17B"). Two short phrases spoken over the agent were misheard ("Stop" as "job", "Wait" as "Weight"); the agent asked what was meant and took no action.

The live browser run (real UI, WAV-fed microphone, real service) passed 13/13, now starting from the landing page's "Talk to it" button against a sandboxed server: voice connected, greeting, transcription, fork with the question spoken aloud, reality unchanged, clarification, what-if, 500 preview, comparison, spoken commit verified, the visitor's ERP changed to 1500/800, no page errors. Through Docker Desktop on the development machine the same run failed because the container's upload path stalls; see [deploy.md](deploy.md#verified-here-and-one-caveat).

### LLM Gateway (real service)

```bash
backend/.venv/Scripts/python scripts/live_llm_gateway.py       # 31 s between calls: this account allows 2 requests per minute
```

Six free-form phrasings the deterministic compiler does not classify, through the real dispatcher and AssemblyAI's LLM Gateway (`qwen3.5-4b-32k-fast`, the model this account can access): **6/6** ([llm-gateway-live.txt](llm-gateway-live.txt)). "I'd like to get 500 of the XR-17 relays over to Pune Site 2 from Mumbai" became a valid preview; "put 500 next to 900 so I can see both" a comparison; "how's our XR-17 stock looking in Delhi" was answered from the ERP; "let's scrap this whole thing" cancelled. For "yep sounds right to me" the model proposed `commit`; the backend refused, and nothing executed. Gateway latency 1.0–1.7 s.

## 7. Frontend

```bash
cd apps/web && npx tsc -b && npm run build
```

Type-checks and builds. There are no frontend unit tests; the UI is covered by the two browser suites above.
