# Submission texts (lablab.ai, AssemblyAI Voice Agent Hackathon)

Replace `<DEMO_URL>` and `<REPO_URL>` after deploying and pushing.

## Project title

MIRROR: see what a voice command will do before it does it

## Short description

A counterfactual runtime for voice agents. MIRROR forks what you said into simulated futures, asks the one question that changes the outcome, commits only the version you approved, and checks that reality agrees. Built on AssemblyAI's Voice Agent API.

## Long description

Voice agents jump from language to action. When an operator says "move 700 units of XR-17 from Mumbai to the Pune site" and there are two Pune sites, a typical agent guesses, or asks a generic "which one?", and then writes to the inventory system. If it guessed wrong, nobody finds out until the stock is in the wrong place.

MIRROR sits between the voice agent and the system of record, and inserts a shadow world. Every reading of the command is simulated against a frozen copy of the ERP: here, Pune Site 1 would go 400 units over capacity and Pune Site 2 is fine. MIRROR asks only the question whose answer changes the outcome, and says why. The operator can explore by voice ("what if I move 900?", "compare 500 and 900", "as much as possible without breaking safety stock"), and nothing real moves.

Only an explicit "commit" executes, bound by an HMAC token to one version, one set of evidence and one simulation. A correction creates a new version and revokes the old authorization. After execution, MIRROR reads the whole world back: if the ERP reports success but short-ships 450 instead of 500, the screen says "Reality diverged" and a rollback, computed from what actually happened, restores and re-verifies. If reality changes under an open preview (another team ships stock), MIRROR re-simulates and the agent says so unprompted.

**How we use AssemblyAI.** The Voice Agent API does speech recognition, turn detection, barge-in, tool calling and the spoken replies, over one WebSocket relayed by our backend, so the key never reaches the browser. Tool calls are treated as proposals. They are held until `reply.done` and discarded if the user interrupts, so an interrupted commit cannot run. The tool list changes with the transaction state (commit only exists while a valid preview does). Keyterms narrow to the options being asked about. `reply.create` lets the agent announce reality changes. Every value the model passes is checked against the user's transcript: the model cannot invent a quantity, pick a Pune site for you, or commit because you said "sounds good". Typed commands the rule-based parser cannot read go to AssemblyAI's LLM Gateway, behind the same evidence gate.

**Evidence.** 116 backend tests. Live against AssemblyAI's service: 53/53 checks over 39 spoken turns and 31 tool calls, 28/28 after adding the reality watch, 8/8 for the recorded demo session, 13/13 through the real browser UI, and 6/6 for the LLM Gateway. On a 26-scenario benchmark against a naive voice-to-action agent, MIRROR ended in the correct state 26 times (baseline 10), with 0 unsafe actions (baseline 9) and 0 wrong interpretations executed (baseline 6).

**Try it.** The demo opens on a start page: watch a 2½-minute replay of a live session (the screen is recomputed by the server as you watch), talk to it, or type. Every visitor gets a private sandbox copy of the ERP.

Inventory is the proof domain. The contract, action plus state plus simulator plus consequences plus commit plus verifier, fits procurement, IAM changes, cloud operations or scheduling: anywhere a voice agent can change something that is expensive to change back.

Demo: <DEMO_URL>
Code: <REPO_URL>

## Technologies

AssemblyAI Voice Agent API, AssemblyAI LLM Gateway, FastAPI, Python, SQLite (SQLAlchemy), WebSockets, React, TypeScript, Vite, Tailwind CSS, Zustand, Web Audio (AudioWorklet), Playwright, Docker, Hugging Face Spaces

## Tags

voice agents, AssemblyAI, speech recognition, tool calling, agent safety, human-in-the-loop, simulation, counterfactual, inventory, ERP, operations, verification, audit trail

## Category

Voice agents for business operations
