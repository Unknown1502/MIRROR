"""AssemblyAI Voice Agent session configuration: prompt, tools, keyterms.

Protocol reference (verified against AssemblyAI docs, Sept 2026):
  wss://agents.assemblyai.com/v1/ws, Authorization: Bearer <key>
  session.update {session: {system_prompt, greeting, tools, input: {format, keyterms, turn_detection}, output: {voice, format}}}
Tools use {"type": "function", "name", "description", "parameters": <JSON Schema>}.
`session.tools` updates replace the whole array, which is how commit/rollback are
revealed only when the runtime state allows them (progressive tool reveal).
"""

from __future__ import annotations

SYSTEM_PROMPT = """You are MIRROR.

You are a counterfactual voice execution agent.

Your job is to help users explore consequential inventory operations.

You must:

- interpret user speech
- avoid inventing entities
- use registered tools for business facts
- distinguish simulation from execution
- explain important consequences
- ask clarifying questions when ambiguity changes the decision
- support what-if exploration
- never claim the real world changed during simulation
- never claim execution succeeded without backend verification
- defer policy and authorization decisions to the TrustVoice backend

The backend is authoritative.

Your job is to interpret, explain, and interact.

The backend decides whether anything can execute.

How to use the tools:
- A new request to move inventory -> propose_transfer. Pass product, quantity and locations exactly as the user said them (for example "the Pune site"). Never pick between sites yourself; the backend forks every interpretation.
- The user answers a question you relayed from the backend ("site 2", "the second one", "as the source") -> clarify_transfer.
- "What if ...", "what about 500", "try 900" -> simulate_transfer. This only simulates.
- A correction ("wait, make that 500", "actually I meant Mumbai") -> amend_transfer.
- "Compare 500 and 900" -> compare_branches.
- "As much as possible", "the maximum safe amount" -> find_max_safe_quantity.
- Only when the user explicitly says commit, confirm or execute in their latest turn -> commit_transfer. Never commit on your own initiative.
- "Roll back", "undo" -> rollback_transfer. "Cancel" -> cancel_transfer.
- Stock questions -> get_inventory.

When a tool result has a "say" field, say that to the user. You may shorten it, but never change a number, never add consequences the backend did not report, and never say anything happened in the real world unless the result says real_world_changed is true and the state is VERIFIED.
If a tool result is an error, explain it briefly and follow its guidance.
Never say you committed, cancelled, rolled back, moved, changed or saved anything unless a tool result you received in this turn says so. Without a tool call, nothing happened, and you must say so plainly.
If the user says "don't commit yet", "hold on" or "wait" without a new instruction, call no tool: say that the preview stays open and nothing has changed.
Keep every reply to one or two short sentences. Keep numbers as digits exactly as they appear in the tool result (write 1500, not words); the voice reads digits correctly.

Examples:
User: "Move 700 units of XR-17 from Mumbai to the Pune site."
You: [call propose_transfer with product "XR-17", quantity 700, source_location "Mumbai", destination_location "the Pune site"] then say the backend's question.
User: "Don't commit yet."
You: [no tool call] "Okay. Nothing has changed; the preview is still open whenever you're ready."
User: "Cancel it."
You: [call cancel_transfer] then say the result.
"""

GREETING = "MIRROR is live. Tell me what to move, and I'll show you what it would do first."

BASE_KEYTERMS = [
    "XR-17", "XR-17B", "AX-77148", "CN-220", "PS-410",
    "Mumbai Distribution Center", "Mumbai", "Pune Site 1", "Pune Site 2", "Pune",
    "Bengaluru Hub", "Bengaluru", "Delhi Warehouse", "Delhi",
    "safety stock", "capacity", "inventory", "commit", "rollback", "what if",
]

_LOC = {"type": "string", "description": "Location exactly as the user said it, e.g. 'Mumbai', 'the Pune site', 'Pune Site 2'.",
        "examples": ["Mumbai", "the Pune site", "Pune Site 2", "Delhi Warehouse"]}
_PRODUCT = {"type": "string", "description": "Product code as spoken, uppercase with hyphen.", "examples": ["XR-17", "XR-17B", "AX-77148"]}
_QTY = {"type": "integer", "minimum": 1, "description": "Number of units the user said.", "examples": [700, 500]}
_TXN = {"type": "string", "description": "Transaction id from the last tool result. Optional; defaults to the open transaction."}


def _fn(name: str, description: str, properties: dict, required: list[str] | None = None) -> dict:
    return {
        "type": "function",
        "name": name,
        "description": description,
        "parameters": {"type": "object", "properties": properties, "required": required or []},
    }


PROPOSE = _fn(
    "propose_transfer",
    "Call when the user asks to move, transfer or send inventory. Creates a new MIRROR transaction and simulates every "
    "possible interpretation in a shadow world. Never changes real inventory.",
    {"product": _PRODUCT, "quantity": _QTY, "source_location": _LOC, "destination_location": _LOC},
)
CLARIFY = _fn(
    "clarify_transfer",
    "Call when the user answers the clarification question from the last tool result, e.g. 'site 2', 'the second one', "
    "'as the source', 'yes'. Pass the user's words.",
    {"answer": {"type": "string", "description": "The user's answer, verbatim.", "examples": ["site 2", "the second one"]}, "transaction_id": _TXN},
    ["answer"],
)
SIMULATE = _fn(
    "simulate_transfer",
    "Run a read-only counterfactual simulation of the open transaction with a change, for 'what if' or 'what about' "
    "questions. Never changes real inventory.",
    {"transaction_id": _TXN, "product": _PRODUCT, "quantity": _QTY, "source_location": _LOC, "destination_location": _LOC},
)
AMEND = _fn(
    "amend_transfer",
    "Call when the user corrects the open request ('wait, make that 500', 'actually I meant Mumbai'). Creates a new "
    "version; the previous preview and any commit authorization become invalid.",
    {"transaction_id": _TXN, "product": _PRODUCT, "quantity": _QTY, "source_location": _LOC, "destination_location": _LOC},
)
COMPARE = _fn(
    "compare_branches",
    "Compare alternative quantities for the open transaction side by side, e.g. 'compare 500 and 900'. Read-only.",
    {"transaction_id": _TXN, "quantities": {"type": "array", "items": {"type": "integer", "minimum": 1}, "minItems": 2, "maxItems": 4, "examples": [[500, 900]]}},
    ["quantities"],
)
OPTIMIZE = _fn(
    "find_max_safe_quantity",
    "Call when the user asks to move as much as possible without breaking safety stock or capacity. The backend searches "
    "every quantity deterministically. Read-only.",
    {"transaction_id": _TXN},
)
COMMIT = _fn(
    "commit_transfer",
    "Execute the currently previewed transfer in the real ERP. Call ONLY when the user's latest words explicitly say "
    "commit, confirm or execute. The backend verifies the user's words, the preview version and the simulation before "
    "anything happens.",
    {"transaction_id": _TXN, "quantity": {**_QTY, "description": "The quantity the user said when committing, if any."}},
)
ROLLBACK = _fn(
    "rollback_transfer",
    "Reverse the last committed transfer and verify that reality was restored. Call only when the user explicitly asks "
    "to roll back or undo.",
    {"transaction_id": _TXN},
)
CANCEL = _fn("cancel_transfer", "Abandon the open, uncommitted request. Nothing changes in the real world.", {"transaction_id": _TXN})
INVENTORY = _fn(
    "get_inventory",
    "Look up current stock, capacity and safety stock. Use for any question about inventory levels.",
    {"product": _PRODUCT, "location": _LOC},
    ["product"],
)

ALWAYS = [PROPOSE, CLARIFY, SIMULATE, AMEND, COMPARE, OPTIMIZE, CANCEL, INVENTORY]


def tools_for_state(state: str | None, can_rollback: bool) -> list[dict]:
    """Progressive tool reveal: execution tools exist only when the runtime would allow them."""
    tools = list(ALWAYS)
    if state == "PREVIEW_READY":
        tools.append(COMMIT)
    if can_rollback:
        tools.append(ROLLBACK)
    return tools


def keyterms_for(clarification: dict | None, state: str | None) -> list[str]:
    terms = list(BASE_KEYTERMS)
    if clarification:
        for opt in clarification.get("options", []):
            terms.append(str(opt.get("label")))
        terms += ["the first one", "the second one", "as the source", "as the destination"]
    if state == "PREVIEW_READY":
        terms += ["commit", "confirm"]
    seen, out = set(), []
    for t in terms:
        if t and t not in seen:
            seen.add(t)
            out.append(t)
    return out[:100]


def initial_session(voice: str, greeting: bool = True) -> dict:
    session = {
        "system_prompt": SYSTEM_PROMPT,
        "tools": tools_for_state(None, False),
        "input": {
            "format": {"encoding": "audio/pcm"},
            "keyterms": BASE_KEYTERMS,
            "transcription_prompt": "Inventory operations: product codes like XR-17 and AX-77148, warehouse names in Indian cities, quantities in units.",
        },
        "output": {"voice": voice, "format": {"encoding": "audio/pcm"}},
    }
    if greeting:  # omitted on reconnects so the agent does not greet the user twice
        session["greeting"] = GREETING
    return session
