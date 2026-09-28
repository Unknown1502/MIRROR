# Submission texts (lablab.ai, AssemblyAI Voice Agent Hackathon)

The demo and repository links have their own fields in the form, so they are not in these texts.
Limits from lablab's submission form (as it appears to participants): title 5 to 50 characters; short
description 50 to 255 characters; long description 600 to 2,000 characters and at least 100 words.
Paste the texts as plain text.

## Project title

MIRROR Voice Agent Safety Layer

(31 characters, letters and spaces only, which also satisfies the stricter rule lablab's site states. Alternatives: "MIRROR Counterfactual Runtime", "MIRROR See It Before It Happens".)

## Short description

A counterfactual runtime for voice agents. MIRROR forks what you said into simulated futures, asks the one question that changes the outcome, commits only the version you approved, and checks that reality agrees. Built on AssemblyAI's Voice Agent API.

## Long description

Voice agents jump from language to action. Ask one to "move 700 units of XR-17 from Mumbai to the Pune site" when there are two Pune sites, and it guesses, then writes to your inventory system. A wrong guess is only found when the stock is in the wrong place.

MIRROR puts a shadow world between the voice agent and the system of record. Every reading of a command is simulated on a frozen copy of the ERP: Pune Site 1 would go 400 over capacity, Site 2 is fine, so MIRROR asks only the question that changes the outcome. Operators explore by voice ("what if I move 900?", "compare 500 and 900") and nothing real moves. Only an explicit "commit" executes, bound by a signed token to the exact version they saw. MIRROR then reads the whole ERP back: if it short-ships, the screen says "Reality diverged" and a rollback restores and re-verifies. If the ERP changes under an open preview, the agent says so unprompted.

Built on AssemblyAI's Voice Agent API: recognition, turn-taking, barge-in, tool calls and speech over one server-side WebSocket. Tool calls are only proposals: held until the reply finishes, dropped if the user interrupts, and every value is checked against the user's own words. Free-form typing goes through the LLM Gateway behind the same checks.

Proof: 116 tests, live runs on AssemblyAI (53/53 and 28/28 checks), and on 26 scenarios a naive voice agent took 9 unsafe actions while MIRROR took none.

Who it is for: operations teams where hands are busy and mistakes are costly, starting with warehouses. AI agents are forecast to grow from $7.8B in 2025 to $52.6B in 2030 (MarketsandMarkets). Revenue from operator seats, verified transactions and ERP connectors.

Try it: watch a replay of a live session, talk to it, or type. Every visitor gets a private sandbox.

(1,788 characters, 308 words.)

## Technologies

"Categories" and "Technologies Used" are picked from lablab's own lists. Technologies: choose AssemblyAI first, then any of these that the list offers. Categories: pick the closest to voice AI, AI agents, and business or productivity.

AssemblyAI Voice Agent API, AssemblyAI LLM Gateway, FastAPI, Python, SQLite (SQLAlchemy), WebSockets, React, TypeScript, Vite, Tailwind CSS, Zustand, Web Audio (AudioWorklet), Playwright, Docker, Hugging Face Spaces

## Tags

voice agents, AssemblyAI, speech recognition, tool calling, agent safety, human-in-the-loop, simulation, counterfactual, inventory, ERP, operations, verification, audit trail

## Category

Voice agents for business operations

## Extended description (not for the form: over its 2,000-character limit)

Voice agents jump from language to action. When an operator says "move 700 units of XR-17 from Mumbai to the Pune site" and there are two Pune sites, a typical agent guesses, or asks a generic "which one?", and then writes to the inventory system. If it guessed wrong, nobody finds out until the stock is in the wrong place.

MIRROR sits between the voice agent and the system of record, and inserts a shadow world. Every reading of the command is simulated against a frozen copy of the ERP: here, Pune Site 1 would go 400 units over capacity and Pune Site 2 is fine. MIRROR asks only the question whose answer changes the outcome, and says why. The operator can explore by voice ("what if I move 900?", "compare 500 and 900", "as much as possible without breaking safety stock"), and nothing real moves.

Only an explicit "commit" executes, bound by an HMAC token to one version, one set of evidence and one simulation. A correction creates a new version and revokes the old authorization. After execution, MIRROR reads the whole world back: if the ERP reports success but short-ships 450 instead of 500, the screen says "Reality diverged" and a rollback, computed from what actually happened, restores and re-verifies. If reality changes under an open preview (another team ships stock), MIRROR re-simulates and the agent says so unprompted.

**How we use AssemblyAI.** The Voice Agent API does speech recognition, turn detection, barge-in, tool calling and the spoken replies, over one WebSocket relayed by our backend, so the key never reaches the browser. Tool calls are treated as proposals. They are held until `reply.done` and discarded if the user interrupts, so an interrupted commit cannot run. The tool list changes with the transaction state (commit only exists while a valid preview does). Keyterms narrow to the options being asked about. `reply.create` lets the agent announce reality changes. Every value the model passes is checked against the user's transcript: the model cannot invent a quantity, pick a Pune site for you, or commit because you said "sounds good". Typed commands the rule-based parser cannot read go to AssemblyAI's LLM Gateway, behind the same evidence gate.

**Evidence.** 116 backend tests. Live against AssemblyAI's service: 53/53 checks over 39 spoken turns and 31 tool calls, 28/28 after adding the reality watch, 8/8 for the recorded demo session, 13/13 through the real browser UI, and 6/6 for the LLM Gateway. On a 26-scenario benchmark against a naive voice-to-action agent, MIRROR ended in the correct state 26 times (baseline 10), with 0 unsafe actions (baseline 9) and 0 wrong interpretations executed (baseline 6).

**Try it.** The demo opens on a start page: watch a 2½-minute replay of a live session (the screen is recomputed by the server as you watch), talk to it, or type. Every visitor gets a private sandbox copy of the ERP.

**Who it is for, and the business.** Operations teams in warehouses and distribution, where voice is natural because hands are busy and a wrong transfer is expensive to undo. AI agents are forecast to grow from $7.8B in 2025 to $52.6B in 2030, and warehouse management software from $4.6B to $10.0B (MarketsandMarkets). MIRROR would earn from operator seats, usage per verified transaction, paid ERP and WMS connectors, and private deployments, sold through the integrators who already install those systems.

Inventory is the proof domain. The contract, action plus state plus simulator plus consequences plus commit plus verifier, fits procurement, IAM changes, cloud operations or scheduling: anywhere a voice agent can change something that is expensive to change back.

Demo: <DEMO_URL>
Code: <REPO_URL>
