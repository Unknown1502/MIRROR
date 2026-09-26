"""LIVE browser end-to-end: real UI in Chrome, real speech, real AssemblyAI.

Chrome's microphone is fed from a WAV timeline of synthesized utterances (Windows SAPI)
separated by long silences for the agent's replies. Audio flows through the real
AudioWorklet, the backend relay and the AssemblyAI Voice Agent API; the test watches the
UI and the ERP. Requires ASSEMBLYAI_API_KEY in .env and a backend serving the built UI.

    python scripts/e2e_live_browser.py --url http://127.0.0.1:8000 --out live-browser
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
import wave
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent))
from live_voice_test import synthesize  # noqa: E402

RATE = 24000
LEAD_SILENCE_S = 12.0  # connection + greeting
GAP_S = 24.0  # room for the agent's answer before the next utterance

SCRIPT = [
    ("fork", "Move 700 units of X R 17 from Mumbai to the Pune site."),
    ("clarify", "Site 2."),
    ("what-if", "What if I move 900?"),
    ("what-about", "What about 500?"),
    ("compare", "Compare 500 and 900."),
    ("commit", "Commit 500."),
]


def build_timeline(out: Path, voice: str) -> tuple[Path, list[tuple[str, float]]]:
    frames = bytearray(b"\x00\x00" * int(RATE * LEAD_SILENCE_S))
    marks = []
    for name, text in SCRIPT:
        marks.append((name, len(frames) / 2 / RATE))
        frames += synthesize(text, out / f"utt-{name}.wav", voice)
        frames += b"\x00\x00" * int(RATE * GAP_S)
    path = out / "timeline.wav"
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(bytes(frames))
    return path, marks


CREDS: dict[str, str] = {}  # the page's own session, so sandboxed deployments return the visitor's world


def world(url: str) -> dict:
    headers = {"X-Session-Id": CREDS["id"], "Authorization": f"Bearer {CREDS['token']}"} if CREDS else {}
    with urllib.request.urlopen(urllib.request.Request(url + "/api/v1/world", headers=headers), timeout=10) as r:
        data = json.loads(r.read())
    return {(c["location_id"], c["product_id"]): c["on_hand"] for c in data["cells"]}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8000")
    ap.add_argument("--out", type=Path, default=Path("live-browser"))
    ap.add_argument("--voice", default="Microsoft Zira Desktop")
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    timeline, marks = build_timeline(a.out, a.voice)
    total = LEAD_SILENCE_S + len(SCRIPT) * GAP_S
    print(f"timeline {timeline} ({total:.0f} s); utterances at " + ", ".join(f"{n}@{t:.0f}s" for n, t in marks), flush=True)

    try:
        urllib.request.urlopen(urllib.request.Request(a.url + "/api/v1/demo/reset", method="POST", data=b""), timeout=10)
    except urllib.error.HTTPError as exc:
        if exc.code != 401:  # 401: sandbox mode, where every visitor starts from the baseline world anyway
            raise
    results: dict[str, bool] = {}
    errors: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(
            channel="chrome",
            args=[
                "--use-fake-device-for-media-stream",
                "--use-fake-ui-for-media-stream",
                f"--use-file-for-fake-audio-capture={timeline.resolve()}%noloop",
                "--autoplay-policy=no-user-gesture-required",
            ],
        )
        ctx = browser.new_context(viewport={"width": 1440, "height": 900}, permissions=["microphone"])
        page = ctx.new_page()
        page.on("pageerror", lambda e: errors.append(str(e)))

        def remember_session(resp):
            if resp.url.endswith("/api/v1/sessions") and resp.request.method == "POST" and resp.ok:
                body = resp.json()
                CREDS.update(id=body["session_id"], token=body["session_token"])

        page.on("response", remember_session)
        page.goto(a.url)
        # The judge path: the landing page's "Talk to it" opens the console and starts voice.
        expect(page.get_by_role("button", name="Talk to it")).to_be_enabled(timeout=15000)
        page.get_by_role("button", name="Talk to it").click()
        expect(page.get_by_text("Runtime connected")).to_be_visible(timeout=15000)
        t_start = time.monotonic()
        conv = page.get_by_label("Conversation")
        futures = page.get_by_label("Possible futures")
        status = page.get_by_role("status")

        def check(name: str, fn, timeout_s: float, shot: str | None = None):
            deadline = time.monotonic() + timeout_s
            ok = False
            while time.monotonic() < deadline:
                try:
                    if fn():
                        ok = True
                        break
                except Exception:  # noqa: BLE001 - element not there yet
                    pass
                time.sleep(0.5)
            results[name] = ok
            print(f"{'PASS' if ok else 'FAIL'}  {name}  (t={time.monotonic() - t_start:.0f}s)", flush=True)
            if shot:
                page.screenshot(path=str(a.out / f"{shot}.png"))

        check("voice connected to AssemblyAI", lambda: "Voice connected." in conv.inner_text(), 25)
        check("greeting spoken (agent transcript shown)", lambda: "MIRROR is live" in conv.inner_text(), 25)
        check("utterance 1 transcribed: XR-17, 700, Pune",  # only the transcript, not the demo-script chips below it
              lambda: "Move 700 units of XR-17" in conv.inner_text().split("Demo script")[0], 45)
        check("forked into two futures + consequence-aware question",
              lambda: "Future A" in futures.inner_text() and "Future B" in futures.inner_text() and "two Pune sites" in futures.inner_text(), 30, "live-01-fork")
        check("agent asked the clarification aloud", lambda: "Which one do you mean" in conv.inner_text(), 30)
        check("real world unchanged during exploration", lambda: world(a.url)[("MUMBAI-DC", "XR-17")] == 2000, 5)
        check("clarified: preview for Pune Site 2", lambda: page.get_by_role("button", name="Commit 700 XR-17 to Pune Site 2").is_visible(), 45, "live-02-clarified")
        check("what-if 900 blocked by safety stock", lambda: "Safety stock breached by 100" in futures.inner_text(), 45, "live-03-what-if")
        check("what about 500: valid preview", lambda: page.get_by_role("button", name="Commit 500 XR-17 to Pune Site 2").is_visible(), 45)
        check("compare 500 vs 900 table", lambda: "Side by side" in futures.inner_text(), 45, "live-04-compare")
        check("commit by voice: reality verified", lambda: "Reality verified: Yes" in status.inner_text(), 60, "live-05-verified")
        w = world(a.url)
        results["ERP really changed: Mumbai 1500, Pune Site 2 800"] = w[("MUMBAI-DC", "XR-17")] == 1500 and w[("PUNE-SITE-2", "XR-17")] == 800
        print(f"{'PASS' if results['ERP really changed: Mumbai 1500, Pune Site 2 800'] else 'FAIL'}  ERP really changed: {w[('MUMBAI-DC', 'XR-17')]}/{w[('PUNE-SITE-2', 'XR-17')]}")
        (a.out / "conversation.txt").write_text(conv.inner_text(), encoding="utf-8")
        stop = page.get_by_role("button", name="Stop voice")
        if stop.count():
            stop.click()
        time.sleep(2)
        browser.close()

    results["no page errors"] = not errors
    for e in errors:
        print("page error:", e)
    passed = sum(results.values())
    print(f"LIVE BROWSER: {passed}/{len(results)} checks passed")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
