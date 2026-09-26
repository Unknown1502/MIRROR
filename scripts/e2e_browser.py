"""Browser-level end-to-end run of the MIRROR demo (typed path), with screenshots.

Drives the real UI in a local Chrome/Edge via Playwright against a running stack:
    backend  : uvicorn app.main:create_app --factory --port 8000
    frontend : vite (5173) or the backend serving apps/web/dist (8000)

Usage:
    python scripts/e2e_browser.py [--url http://127.0.0.1:5173] [--shots DIR] [--headed]
Exits non-zero on the first failed expectation.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from playwright.sync_api import Page, expect, sync_playwright


def launch(p, headed: bool):
    for channel in ("chrome", "msedge", None):
        try:
            return p.chromium.launch(channel=channel, headless=not headed) if channel else p.chromium.launch(headless=not headed)
        except Exception:  # noqa: BLE001 - try the next available browser
            continue
    raise SystemExit("No Chromium-based browser available for Playwright")


class Run:
    def __init__(self, page: Page, shots: Path):
        self.page = page
        self.shots = shots
        self.n = 0

    def shot(self, name: str) -> None:
        self.n += 1
        self.page.screenshot(path=str(self.shots / f"{self.n:02d}-{name}.png"), full_page=False)

    def say(self, text: str) -> None:
        box = self.page.get_by_label("Command")
        box.fill(text)
        box.press("Enter")
        # the reply for this turn is appended once the backend answers
        self.page.wait_for_function(
            "(t) => [...document.querySelectorAll('[aria-label=Conversation] p')].some(p => p.textContent.includes(t))",
            arg=text,
        )
        self.page.wait_for_timeout(450)

    def status(self) -> str:
        return self.page.get_by_role("status").inner_text()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:5173")
    ap.add_argument("--shots", default="e2e-shots")
    ap.add_argument("--headed", action="store_true")
    args = ap.parse_args()
    shots = Path(args.shots)
    shots.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = launch(p, args.headed)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.request.post(args.url + "/api/v1/demo/reset")
        page.goto(args.url)
        r = Run(page, shots)
        page.get_by_role("button", name="Open the console").click()
        expect(page.get_by_text("Runtime connected")).to_be_visible(timeout=10000)
        expect(page.get_by_role("status")).to_contain_text("Real world changed: No")
        r.shot("empty")

        started = time.perf_counter()
        r.say("Move 700 units of XR-17 from Mumbai to the Pune site.")
        futures = page.get_by_label("Possible futures")
        expect(futures).to_contain_text("There are two Pune sites. Site 1 would exceed capacity by 400 units; Site 2 remains valid.")
        expect(futures).to_contain_text("Future A")
        expect(futures).to_contain_text("Future B")
        expect(futures).to_contain_text("Over capacity by 400")
        assert "Real world changed: No" in r.status()
        r.shot("forked-futures")

        r.say("Site 2.")
        expect(page.get_by_role("status")).to_contain_text("Real world changed: No")
        expect(page.get_by_role("button", name="Commit 700 XR-17 to Pune Site 2")).to_be_visible()
        r.shot("clarified-preview")

        r.say("What if I move 900?")
        expect(futures).to_contain_text("Safety stock breached by 100")
        expect(page.get_by_role("button", name="Commit 900 XR-17 to Pune Site 2")).to_have_count(0)
        r.shot("what-if-900")

        r.say("What about 500?")
        expect(page.get_by_role("button", name="Commit 500 XR-17 to Pune Site 2")).to_be_visible()

        r.say("Compare 500 and 900.")
        expect(futures).to_contain_text("Side by side")
        r.shot("compare")

        r.say("Commit 500.")
        expect(page.get_by_role("status")).to_contain_text("Reality verified: Yes", timeout=8000)
        reality = page.get_by_label("Real world")
        expect(reality).to_contain_text("Expected = actual")
        r.shot("verified")
        loop_s = time.perf_counter() - started

        # Controlled divergence: reset, arm the fault through the UI, run the commit again.
        page.get_by_role("button", name="Demo controls").click()
        page.get_by_role("button", name="Reset to baseline world").click()
        expect(page.get_by_text("Demo reset.")).to_be_visible()
        page.get_by_role("button", name="Demo controls").click()
        page.get_by_role("button", name="Short shipment").click()
        page.keyboard.press("Escape")
        r.say("Move 500 units of XR-17 from Mumbai to Pune Site 2")
        page.get_by_role("button", name="Commit 500 XR-17 to Pune Site 2").click()
        expect(page.get_by_role("status")).to_contain_text("Reality diverged: Yes", timeout=8000)
        expect(reality).to_contain_text("Expected ≠ actual")
        expect(reality).to_contain_text("1550")
        expect(reality).to_contain_text("750")
        erp_table = reality.locator("table").first
        expect(erp_table.get_by_role("row", name="Mumbai")).to_contain_text("1550")
        expect(erp_table.get_by_role("row", name="Pune Site 2")).to_contain_text("750")
        r.shot("diverged")

        page.get_by_role("button", name="Roll back and verify").click()
        expect(page.get_by_role("status")).to_contain_text("Reality restored: Yes", timeout=8000)
        expect(erp_table.get_by_role("row", name="Mumbai")).to_contain_text("2000")
        expect(erp_table.get_by_role("row", name="Pune Site 2")).to_contain_text("300")
        r.shot("restored")

        page.get_by_role("button", name="Verify hash chain").click()
        expect(page.get_by_label("Audit timeline")).to_contain_text("Intact")
        r.shot("audit")

        real_errors = [e for e in errors if "favicon" not in e]
        if real_errors:
            print("Browser errors:", *real_errors, sep="\n  ")
            return 1
        print(f"E2E PASS: fork, clarify, what-if, compare, commit, verify, divergence, rollback, audit ({loop_s:.1f}s for the main loop)")
        print(f"Screenshots: {shots.resolve()}")
        browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
