"""Browser E2E for the landing page and the judge replay, plus sandbox isolation.

Runs the replay at 1.5x in a real Chrome against a running stack and checks that the screen state
(recomputed by the backend) matches what the recorded session produced at every beat. A second
visitor opened alongside must see an untouched world when the backend runs with
MIRROR_SANDBOX_PER_SESSION=true.

    python scripts/e2e_replay.py [--url http://127.0.0.1:5173] [--shots e2e-replay] [--sandbox]
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

from e2e_browser import launch


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:5173")
    ap.add_argument("--shots", default="e2e-replay")
    ap.add_argument("--headed", action="store_true")
    ap.add_argument("--sandbox", action="store_true", help="also check that a second visitor's world is untouched")
    ap.add_argument("--browser", default="chromium", choices=["chromium", "firefox", "webkit"])
    args = ap.parse_args()
    shots = Path(args.shots)
    shots.mkdir(parents=True, exist_ok=True)
    n = 0

    with sync_playwright() as p:
        browser = launch(p, args.headed, args.browser)
        ctx = browser.new_context(viewport={"width": 1440, "height": 900})
        page = ctx.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)

        def shot(name: str) -> None:
            nonlocal n
            n += 1
            page.screenshot(path=str(shots / f"{n:02d}-{name}.png"))

        page.goto(args.url)
        expect(page.get_by_role("heading", level=1)).to_contain_text("See what a voice command will do before it does it.")
        expect(page.get_by_role("button", name="Watch the 2½-minute replay")).to_be_visible()
        page.wait_for_timeout(900)  # entrance animation
        shot("landing")

        # Mobile layout of the landing page.
        mobile = ctx.new_page()
        mobile.set_viewport_size({"width": 390, "height": 844})
        mobile.goto(args.url)
        expect(mobile.get_by_role("heading", level=1)).to_be_visible()
        mobile.wait_for_timeout(900)
        assert mobile.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), "landing scrolls sideways on a phone"
        mobile.screenshot(path=str(shots / "landing-mobile.png"), full_page=True)
        mobile.close()

        started = time.perf_counter()
        page.get_by_role("button", name="Watch").first.click()
        bar = page.get_by_label("Replay", exact=True)
        expect(bar).to_be_visible()
        bar.get_by_role("button", name="1×").click()
        bar.get_by_role("button", name="1.25×").click()  # now 1.5x
        futures = page.get_by_label("Possible futures")
        status = page.get_by_role("status")
        expect(page.get_by_label("Command")).to_be_disabled()  # the replay owns the screen while it runs

        expect(futures).to_contain_text("Future B", timeout=30000)
        expect(futures).to_contain_text("Over capacity by 400")
        expect(bar).to_contain_text("AssemblyAI heard")
        shot("replay-fork")

        expect(page.get_by_role("button", name="Commit 700 XR-17 to Pune Site 2")).to_be_visible(timeout=40000)
        expect(futures).to_contain_text("Safety stock breached by 100", timeout=40000)
        shot("replay-what-if")
        expect(futures).to_contain_text("Side by side", timeout=40000)
        shot("replay-compare")

        banner = page.get_by_role("alert").filter(has_text="Reality changed under your preview")
        expect(banner).to_contain_text("went from 2000 to 1700", timeout=40000)
        expect(bar).to_contain_text("No one spoke")
        page.wait_for_timeout(700)  # let the column scroll to the alert
        shot("replay-reality-changed")

        expect(status).to_contain_text("Reality diverged: Yes", timeout=60000)
        reality = page.get_by_label("Real world")
        expect(reality).to_contain_text("1250")
        shot("replay-diverged")

        expect(status).to_contain_text("Reality restored: Yes", timeout=60000)
        expect(bar).to_contain_text("That was one live AssemblyAI session", timeout=60000)
        erp_table = reality.locator("table").first
        expect(erp_table.get_by_role("row", name="Mumbai")).to_contain_text("1700")
        conv = page.get_by_label("Conversation")
        expect(conv).to_contain_text("as AssemblyAI heard it (recording)")
        shot("replay-done")
        replay_s = time.perf_counter() - started

        if args.sandbox:
            other = browser.new_context(viewport={"width": 1440, "height": 900}).new_page()
            other.goto(args.url)
            other.get_by_role("button", name="Open the console").click()
            expect(other.get_by_text("Your sandbox")).to_be_visible(timeout=10000)
            other_table = other.get_by_label("Real world").locator("table").first
            expect(other_table.get_by_role("row", name="Mumbai")).to_contain_text("2000")
            expect(other_table.get_by_role("row", name="Pune Site 2")).to_contain_text("300")
            other.screenshot(path=str(shots / "second-visitor.png"))

        page.get_by_role("button", name="Try it yourself").click()
        expect(bar).to_have_count(0)
        expect(page.get_by_label("Command")).to_be_enabled()

        real = [e for e in errors if "favicon" not in e]
        if real:
            print("Browser errors:", *real, sep="\n  ")
            return 1
        print(f"REPLAY E2E PASS [{args.browser}]: landing, mobile landing, 8-step replay at 1.5x ({replay_s:.0f}s), reality alert, divergence, rollback"
              + (", second visitor isolated" if args.sandbox else ""))
        browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
