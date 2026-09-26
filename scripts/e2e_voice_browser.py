"""Browser voice-path E2E: Chrome fake microphone -> AudioWorklet -> backend relay -> scripted
AssemblyAI stand-in (scripts/fake_assemblyai.py) -> tool calls -> runtime -> UI.

Requires a backend started with ASSEMBLYAI_API_KEY set and ASSEMBLYAI_AGENT_URL pointing at
the fake server, serving the built frontend. Proves the plumbing, not AssemblyAI itself.

    python scripts/e2e_voice_browser.py --url http://127.0.0.1:8001 --report report.json
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from playwright.sync_api import expect, sync_playwright


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8001")
    ap.add_argument("--report", type=Path, required=True)
    ap.add_argument("--shots", type=Path, default=Path("e2e-voice-shots"))
    a = ap.parse_args()
    a.shots.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(
            channel="chrome",
            args=["--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream", "--autoplay-policy=no-user-gesture-required"],
        )
        ctx = browser.new_context(viewport={"width": 1440, "height": 900}, permissions=["microphone"])
        page = ctx.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.request.post(a.url + "/api/v1/demo/reset")
        page.goto(a.url)
        expect(page.get_by_text("Runtime connected")).to_be_visible(timeout=10000)

        page.get_by_role("button", name="Start voice").click()
        expect(page.get_by_text("Voice connected.")).to_be_visible(timeout=10000)
        conv = page.get_by_label("Conversation")

        # Live partial transcript appears (overwritten, not concatenated), then the final turn.
        expect(conv).to_contain_text("Move 700 units of XR-17 from Mumbai to the Pune site.", timeout=20000)
        futures = page.get_by_label("Possible futures")
        expect(futures).to_contain_text("There are two Pune sites.", timeout=10000)
        page.screenshot(path=str(a.shots / "01-voice-fork.png"))

        # Barge-in during a commit reply: visible in the UI, and nothing executed.
        expect(conv).to_contain_text("You interrupted. Discarded before execution: commit_transfer.", timeout=20000)
        page.screenshot(path=str(a.shots / "02-voice-interrupt.png"))

        expect(page.get_by_role("status")).to_contain_text("Reality verified: Yes", timeout=20000)
        expect(page.get_by_label("Real world").locator("table").first.get_by_role("row", name="Mumbai")).to_contain_text("1500")
        page.screenshot(path=str(a.shots / "03-voice-verified.png"))
        page.get_by_role("button", name="Stop voice").click()
        time.sleep(1.0)
        browser.close()

    deadline = time.time() + 10
    while not a.report.exists() and time.time() < deadline:
        time.sleep(0.2)
    report = json.loads(a.report.read_text(encoding="utf-8"))
    print(json.dumps(report, indent=2))
    checks = {
        "browser audio reached the agent": report.get("audio_chunks", 0) >= 20,
        "PCM16 24 kHz chunks (~50 ms)": report.get("audio_seconds", 0) > 0 and abs(report["audio_seconds"] / report["audio_chunks"] - 0.05) < 0.001,
        "input format audio/pcm": report.get("session_update", {}).get("input_format") == {"encoding": "audio/pcm"},
        "commit hidden until preview": "commit_transfer" not in report.get("session_update", {}).get("tools", []),
        "interrupted tool result discarded": report.get("interrupted_result_received") is False,
        "script completed": report.get("script_complete") is True,
        "final commit verified": (report.get("tool_results") or [{}])[-1].get("state") == "VERIFIED",
        "no page errors": not errors,
    }
    for name, ok in checks.items():
        print(f"{'PASS' if ok else 'FAIL'}  {name}")
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
