"""LIVE check of the LLM Gateway fallback for free-form typed commands.

Runs phrasings the deterministic compiler cannot classify through the real dispatcher with
the real AssemblyAI LLM Gateway. Uses a temporary data directory. Requires ASSEMBLYAI_API_KEY.

    python scripts/live_llm_gateway.py [--model qwen3.5-4b-32k-fast]
"""

from __future__ import annotations

import argparse
import sys
import tempfile
import time
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.core.config import load_settings  # noqa: E402
from app.core.container import build_container  # noqa: E402

STEPS = [
    # (utterance, check(result, container) -> bool, description)
    ("I'd like to get 500 of the XR-17 relays over to Pune Site 2 from Mumbai",
     lambda r, c: r.transaction and r.transaction["state"] == "PREVIEW_READY", "free-form request -> valid preview"),
    ("could you see what happens with 900 instead",
     lambda r, c: r.transaction and r.transaction["state"] == "CONSEQUENCE_REVIEW", "free-form what-if 900 -> blocked future"),
    ("put 500 next to 900 so I can see both",
     lambda r, c: r.error is None and "900" in r.reply, "free-form comparison"),
    ("yep sounds right to me",
     lambda r, c: not [o for o in c.erp.operations() if o["actor"] == "MIRROR"], "agreement without 'commit' executes nothing"),
    ("how's our XR-17 stock looking in Delhi",
     lambda r, c: "1500" in r.reply, "free-form stock question answered from the ERP"),
    ("let's scrap this whole thing",
     lambda r, c: r.transaction and r.transaction["state"] == "ABORTED", "free-form cancel"),
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=None)
    ap.add_argument("--spacing", type=float, default=31.0, help="seconds between turns (account limit: 2 requests/min)")
    a = ap.parse_args()
    s = load_settings(data_dir=Path(tempfile.mkdtemp(prefix="mirror-llm-")), static_dir=None, rate_limit=False)
    if a.model:
        s = replace(s, llm_gateway_model=a.model)
    if not s.assemblyai_api_key:
        print("ASSEMBLYAI_API_KEY is not set")
        return 2
    c = build_container(s)
    sid, _ = c.runtime.create_session()
    passed = 0
    for i, (text, check, what) in enumerate(STEPS):
        if i:
            time.sleep(a.spacing)
        r = c.dispatcher.handle(sid, text)
        ok = bool(check(r, c))
        passed += ok
        llm = r.interpreted_by or {}
        print(f"{'PASS' if ok else 'FAIL'}  {what}")
        print(f"      said   : {text}")
        print(f"      model  : {llm.get('model', 'deterministic compiler')} action={llm.get('action')} "
              f"{llm.get('latency_ms', '')}{' ms' if llm else ''} request_id={llm.get('request_id')}")
        print(f"      reply  : {r.reply[:160]}")
        interp = c.dispatcher.interpreter
        if not llm and interp is not None and interp.last_outcome not in (None, "ok"):
            print(f"      gateway: {interp.last_outcome}")
    print(f"LLM GATEWAY LIVE: {passed}/{len(STEPS)}")
    c.dispose()
    return 0 if passed == len(STEPS) else 1


if __name__ == "__main__":
    sys.exit(main())
