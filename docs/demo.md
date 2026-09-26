# Demo

The demo is deterministic. Reset puts the ERP back at the same baseline every time, and failures are armed on purpose, never random.

## Setup (once)

```bash
# backend
cd backend
python -m venv .venv
.venv/Scripts/pip install -r requirements-dev.txt        # Windows; use .venv/bin/pip on macOS/Linux
cd ../apps/web && npm install && npm run build && cd ../..

# configure
cp .env.example .env    # set ASSEMBLYAI_API_KEY for voice, MIRROR_COMMIT_SECRET for durable tokens

# run (serves the UI and the API on one origin)
cd backend && .venv/Scripts/python -m uvicorn app.main:create_app --factory --port 8000
```

Open <http://127.0.0.1:8000>. Before each take: **Demo controls → Reset to baseline world** (or `python scripts/seed_demo.py`).

Baseline, XR-17: Mumbai 2000 (capacity 3000, safety 1200), Pune Site 1 1200 (capacity 1500), Pune Site 2 300 (capacity 2000).

## Script (about 90 seconds)

Speak with **Start voice**, or type the same lines into the command box. Both drive the same runtime.

| Time | Say | What the screen shows |
| --- | --- | --- |
| 0–5 s | (nothing) | "MIRROR, counterfactual runtime for voice agents". Presenter: "Voice agents can act. MIRROR lets them explore the consequences first." |
| 5–20 s | "Move 700 units of XR-17 from Mumbai to the Pune site." | Two futures appear above the mirror line. Future A, Pune Site 1: 1200 → 1900, capacity 1500, ✕ Blocked, over capacity by 400. Future B, Pune Site 2: 300 → 1000, ✓ Valid. **Real world changed: No.** |
| 20–30 s | (agent) | "There are two Pune sites. Site 1 would exceed capacity by 400 units; Site 2 remains valid. Which one do you mean?" The line under the question shows why it asked: ambiguity × consequence divergence × risk. |
| 30–38 s | "Site 2." | v2, one valid future, commit button appears, bound to v2's evidence and simulation hashes. |
| 38–50 s | "What if I move 900?" | v3, what-if: Mumbai 2000 → 1100, safety 1200, ✕ safety stock breached by 100. "Moving 900 would leave Mumbai 100 units below its safety-stock threshold. Nothing has changed." v2's commit authorization is revoked in the conversation and the ledger. |
| 50–60 s | "What about 500?" | v4: Mumbai 2000 → 1500, Pune Site 2 300 → 800, ✓ Valid. |
| 60–68 s | "Compare 500 and 900." | Side-by-side table on one snapshot: Mumbai after 1500 / 1100, safety stock Pass / Fail, Pune Site 2 after 800 / 1200, overall Valid / Blocked. |
| 68–76 s | "Commit 500." | Real world changed: Yes, then **Reality verified: Yes**. Verification table: expected 1500 / 800, actual 1500 / 800. |
| 76–84 s | Demo controls → Reset, then Demo controls → **Short shipment**; "Move 500 units of XR-17 from Mumbai to Pune Site 2", "Commit 500." | **Reality diverged: Yes.** Expected Mumbai 1500, Pune Site 2 800; actual 1550 (+50), 750 (−50). "ERP said: SUCCESS, 500 applied" next to "MIRROR read: 2 cells differ". Never marked verified. |
| 84–90 s | "Rollback." | Rollback moves the 450 that actually moved, then verifies: **Reality restored: Yes**, expected = actual 2000 / 300. |

Closing line: "We don't ask the agent to be careful. We give it a world where it can make mistakes before reality pays for them."

### Optional beats

- **Maximum safe quantity**: "Move as much as possible without breaking safety stock." The runtime simulates every quantity from 50 to 1000 and answers 800, with probes 500 ✓ … 800 ✓, 801 ✕.
- **Interruption (voice)**: say "Commit 500" and interrupt the agent while it is still speaking ("wait, make that 450"). The conversation shows "You interrupted. Discarded before execution: commit_transfer." and nothing executes; v5 supersedes v4.
- **Reality moves underneath a preview**: after a preview of 700, Demo controls → "Ship 300 XR-17 out of Mumbai", then "Commit 700". MIRROR re-simulates before executing, finds that 700 would now breach safety stock, and refuses.
- **Duplicate click**: click the commit button twice quickly. One ERP operation; the second request replays the stored result.
- **Audit**: "Verify hash chain" recomputes every hash in the ledger.

## If something goes wrong on stage

- Voice will not connect: the header says "Voice unavailable" when the server has no key; type the script instead, the runtime is identical.
- A fault stays armed: the Demo controls button shows a red dot. Click the armed fault again to disarm it, or reset.
- Anything else: Reset to baseline world, then start from the first line.

## Pitch

Voice agents have learned how to act. They haven't learned how to show us the consequences before they act. MIRROR inserts a shadow world between speech and reality. It forks ambiguous intent, simulates each possible future, explains the consequences, lets the user explore alternatives by voice, and only commits the selected future to reality. Then it verifies that reality actually matches the plan.

**Isn't this just a confirmation system?** Confirmation asks whether you want to do an action. MIRROR shows you what the action would do before you decide, and checks afterwards that it did exactly that.

**Isn't this just a simulator?** The simulator is one component. MIRROR combines voice-driven intent branching, counterfactual execution, consequence-aware clarification, explicit version-bound commit and post-state verification into one loop.

**Why voice?** People revise spoken commands, ask what-if questions and navigate alternatives out loud. MIRROR treats those turns as new versions of one live transaction instead of separate chatbot messages.
