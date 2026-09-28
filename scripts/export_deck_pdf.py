"""Render the pitch deck (submission/deck) to a PDF for the lablab submission.

    python scripts/export_deck_pdf.py --demo-url https://<hf-user>-mirror.hf.space [--repo-url https://github.com/<you>/mirror]

Each slide is a 1920x1080 page, rendered in Chrome with the deck's own fonts; speaker notes are left out.
Without --repo-url the last slide points to the GitHub link in the submission instead.
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
DECK = ROOT / "submission" / "deck"
# Uploaded artifact assets -> the same images in the repository.
ASSETS = {"/_blob/2b6b1c1d5672bbfc6c04d2b6e42f4334": ROOT / "docs" / "images" / "reality-changed.png"}

BASE_CSS = """
@page { size: 1920px 1080px; margin: 0 }
* { margin: 0; box-sizing: border-box }
html, body { background: #1A2330 }
.page { width: 1920px; height: 1080px; position: relative; overflow: hidden; page-break-after: always; break-after: page }
.page > section { width: 1920px; height: 1080px; position: relative }
aside { display: none }
h1, h2, h3 { font-weight: 600 }
ul, ol { padding-left: 1.2em }
li { margin-bottom: 0.25em }
table { border-collapse: collapse; width: 100% }
th, td { padding: 0.35em 0.6em; border-bottom: 1px solid #CDD3D9; text-align: left; vertical-align: top }
th { font-weight: 700 }
"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo-url", required=True)
    ap.add_argument("--repo-url", default="")
    ap.add_argument("--out", default=str(ROOT / "submission" / "MIRROR-deck.pdf"))
    a = ap.parse_args()

    deck = json.loads((DECK / "deck.json").read_text(encoding="utf-8"))
    fonts = "".join(f'<link rel="stylesheet" href="{f["href"]}">' for f in deck["faces"].values() if f.get("href"))
    pages = []
    for sid in deck["order"]:
        html = (DECK / "slides" / f"{sid}.html").read_text(encoding="utf-8")
        for blob, path in ASSETS.items():
            html = html.replace(blob, "data:image/png;base64," + base64.b64encode(path.read_bytes()).decode())
        html = html.replace("[DEMO_URL]", a.demo_url.removeprefix("https://"))
        if a.repo_url:
            html = html.replace("[REPO_URL]", a.repo_url.removeprefix("https://"))
        else:
            html = html.replace("Code, tests and live results: [REPO_URL]", "Code, tests and live results: the GitHub link in this submission")
        if "[" in html and "_URL]" in html:
            print(f"slide {sid} still has a placeholder")
            return 1
        pages.append(f'<div class="page">{html}</div>')
    doc = f'<!doctype html><html><head><meta charset="utf-8">{fonts}<style>{BASE_CSS}</style></head><body>{"".join(pages)}</body></html>'

    out = Path(a.out)
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome")
        page = browser.new_page(viewport={"width": 1920, "height": 1080})
        page.set_content(doc, wait_until="networkidle")
        page.evaluate("document.fonts.ready")
        page.pdf(path=str(out), width="1920px", height="1080px", print_background=True, margin={"top": "0", "right": "0", "bottom": "0", "left": "0"})
        # A PNG of each page, to check the layout by eye.
        shots = out.parent / "deck-pages"
        shots.mkdir(exist_ok=True)
        for i, sid in enumerate(deck["order"], 1):
            page.locator(".page").nth(i - 1).screenshot(path=str(shots / f"{i:02d}-{sid}.png"))
        browser.close()
    print(f"{out} ({out.stat().st_size / 1024:.0f} KB, {len(pages)} pages); page images in {shots}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
