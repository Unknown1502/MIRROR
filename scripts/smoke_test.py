"""Smoke test against a running MIRROR backend. Uses only the public HTTP API.

Runs the demo loop (fork, clarify, what-if, compare, commit, verify), then a controlled
divergence and rollback, and prints measured latencies. Resets the demo world first.

    python scripts/smoke_test.py [--url http://127.0.0.1:8000]
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request


class Client:
    def __init__(self, url: str):
        self.url = url.rstrip("/")
        self.headers: dict[str, str] = {}

    def call(self, method: str, path: str, body=None):
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(self.url + "/api/v1" + path, method=method, data=data)
        req.add_header("Content-Type", "application/json")
        for k, v in self.headers.items():
            req.add_header(k, v)
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            return json.loads(e.read())


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"{'PASS' if ok else 'FAIL'}  {label}{('  ' + detail) if detail else ''}")
    if not ok:
        raise SystemExit(1)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8000")
    a = ap.parse_args()
    c = Client(a.url)

    health = c.call("GET", "/health")
    check("backend healthy", health.get("status") == "ok", f"voice_available={health.get('voice_available')}")
    c.call("POST", "/demo/reset")
    s = c.call("POST", "/sessions")
    c.headers = {"X-Session-Id": s["session_id"], "Authorization": f"Bearer {s['session_token']}"}
    sid = s["session_id"]

    def say(text):
        t0 = time.perf_counter()
        r = c.call("POST", f"/sessions/{sid}/turns", {"text": text})
        return r, (time.perf_counter() - t0) * 1000

    r, ms = say("Move 700 units of XR-17 from Mumbai to the Pune site.")
    outcomes = {b["intent"]["destination_id"]: b["outcome"] for b in r["transaction"]["versions"][-1]["branches"]}
    check("one command forks into two simulated futures", outcomes == {"PUNE-SITE-1": "BLOCKED", "PUNE-SITE-2": "VALID"}, f"{ms:.0f} ms round trip")
    check("consequence-aware clarification", r["reply"].startswith("There are two Pune sites. Site 1 would exceed capacity by 400 units"))
    check("real world unchanged", r["transaction"]["real_world_changed"] is False)

    r, _ = say("Site 2.")
    check("clarified to a preview", r["transaction"]["state"] == "PREVIEW_READY")
    r, _ = say("What if I move 900?")
    check("what-if 900 blocked by safety stock", "100 units below its safety-stock threshold" in r["reply"])
    r, _ = say("What about 500?")
    r, _ = say("Compare 500 and 900.")
    cols = {col["name"]: col["outcome"] for col in r["comparison"]["columns"]}
    check("comparison 500 vs 900", cols == {"500": "VALID", "900": "BLOCKED"})
    r, ms = say("Commit 500.")
    txn = r["transaction"]
    check("commit verified against the ERP", txn["state"] == "VERIFIED" and txn["reality"] == "VERIFIED", f"{ms:.0f} ms incl. execute+verify")
    ex = txn["executions"][0]
    print(f"      execution {ex['exec_latency_ms']} ms, verification {ex['verify_latency_ms']} ms")

    c.call("POST", "/demo/reset")
    say("Move 500 units of XR-17 from Mumbai to Pune Site 2")
    c.call("POST", "/demo/faults", {"fault": "verification_mismatch"})
    r, _ = say("Commit 500.")
    focus = {f["location_id"]: (f["expected"], f["actual"]) for f in r["transaction"]["executions"][0]["report"]["focus"]}
    check("controlled divergence detected, never VERIFIED", r["transaction"]["state"] == "DIVERGED", str(focus))
    r, _ = say("Rollback.")
    check("rollback restored and verified", r["transaction"]["state"] == "VERIFIED" and r["transaction"]["reality"] == "RESTORED")

    chain = c.call("GET", "/audit/verify")
    check("audit hash chain intact", chain["valid"], f"{chain['length']} events")
    return 0


if __name__ == "__main__":
    sys.exit(main())
