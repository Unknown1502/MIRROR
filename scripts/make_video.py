"""Build the submission video: AI-narrated slides + a capture of the real app playing the recorded live session.

    python scripts/make_video.py [--url http://127.0.0.1:8000] [--voice charles]

Needs: a running MIRROR server (sandbox mode is fine), ffmpeg, Chrome, ASSEMBLYAI_API_KEY (narration is
spoken by an AssemblyAI voice through the Voice Agent API), and submission/deck-pages from
scripts/export_deck_pdf.py. Output: submission/MIRROR-demo.mp4 (MP4, under 5 minutes, well under 300 MB).

The narration is labelled as an AI voice on the first frame. The demo part is the page's replay of a live
session: the voices and transcripts are that recording, and the app recomputes everything on screen.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import difflib
import hashlib
import json
import re
import shutil
import subprocess
import sys
import time
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(ROOT / "backend"))

from ai_voice_lines import save_wav, say_all, words  # noqa: E402
from app.core.config import load_settings  # noqa: E402

BUILD = ROOT / "video-build"
PAGES = ROOT / "submission" / "deck-pages"
OUT = ROOT / "submission" / "MIRROR-demo.mp4"

NARRATOR_PROMPT = (
    "You are the narrator of a short product demo video. When given a line, say exactly that line, word for "
    "word, in a calm, clear, confident tone, at a natural pace. Never add, drop or change a word, and never say "
    "anything else."
)

# (id, what is on screen, narration). "DEMO" = the live app; everything else is a deck page.
# Numbers are written as words: the voice reads digits one by one ("116" came out as "one one six").
SCENES = [
    ("n01", "01-cover", "This is MIRROR. See what a voice command will do, before it does it. Built on AssemblyAI's Voice Agent API."),
    ("n02", "02-problem", "Voice agents act on what they think you said. Say the Pune site when there are two, and a typical agent guesses, then writes to your inventory."),
    ("n03", "03-fork", "MIRROR simulates every reading first. Site one would overflow by four hundred units, Site two is fine, so it asks the one question that changes the outcome."),
    ("n04", "04-loop", "You can explore freely. Only your own word, commit, executes the exact version you saw, and then MIRROR checks that reality agrees."),
    ("n05", "07-assemblyai", "AssemblyAI runs the conversation. Every tool call is only a proposal: held until the reply ends, dropped if you interrupt, and checked against your own words."),
    ("n06", "11-market", "AI agents are forecast to grow from seven point eight billion dollars in twenty twenty-five, to fifty-two point six billion in twenty thirty. We start in warehouses, where mistakes are expensive."),
    ("n07", "12-business", "Revenue comes from operator seats, verified transactions, and connectors to the systems customers already run."),
    ("n08", "13-competition", "Confirmations show no consequences, and guardrails never see real data. MIRROR previews every reading, and verifies the result."),
    ("n09", "DEMO", "Now a real session, recorded live with AssemblyAI. The voices are the recording. Every number on screen is recomputed live."),
    ("n10", "09-evidence", "This is measured, not claimed. A hundred and sixteen tests, and live runs on AssemblyAI. On twenty-six scenarios, a naive agent took nine unsafe actions. MIRROR took none."),
    ("n11", "14-roadmap", "Next: connectors for NetSuite and SAP, approval routing, and more languages."),
    ("n12", "15-try", "Try it yourself at the link on screen. Thank you."),
]
LEAD, TAIL = 0.35, 0.6  # silence around each narration

# Logs when the page starts each audio clip, so the recording's voices can be placed exactly.
AUDIO_HOOK = """
(() => {
  const play = HTMLMediaElement.prototype.play;
  HTMLMediaElement.prototype.play = function (...args) {
    const el = this;
    const r = play.apply(this, args);
    Promise.resolve(r).then(
      () => console.log('[mirror-video] play ' + (performance.timeOrigin + performance.now()) + ' ' + el.currentSrc + ' ' + el.playbackRate),
      () => console.log('[mirror-video] playfail ' + Date.now() + ' ' + el.currentSrc));
    if (!el.__mirrorEnded) {
      el.__mirrorEnded = true;
      el.addEventListener('ended', () => console.log('[mirror-video] ended ' + (performance.timeOrigin + performance.now()) + ' ' + el.currentSrc));
    }
    return r;
  };
})();
"""


def ffmpeg() -> str:
    exe = shutil.which("ffmpeg")
    if not exe:
        raise SystemExit("ffmpeg is required")
    return exe


def run(args: list[str]) -> None:
    r = subprocess.run(args, capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit("ffmpeg failed:\n" + r.stderr[-2000:])


def seconds(path: Path) -> float:
    with wave.open(str(path), "rb") as w:
        return w.getnframes() / w.getframerate()


def similar(text: str, line: str) -> bool:
    a, b = words(text), words(line)
    return difflib.SequenceMatcher(None, a, b).ratio() >= 0.9 and len(a) <= len(b) + 2


# ------------------------------------------------------------------ narration

def narration(voice: str) -> dict[str, Path]:
    folder = BUILD / "narration"
    folder.mkdir(parents=True, exist_ok=True)
    paths = {sid: folder / f"{sid}-{hashlib.sha1((voice + text).encode()).hexdigest()[:8]}.wav" for sid, _, text in SCENES}
    todo = [(sid, text) for sid, _, text in SCENES if not paths[sid].exists()]
    if todo:
        settings = load_settings()
        got = asyncio.run(say_all(settings, voice, [t for _, t in todo], prompt=NARRATOR_PROMPT, accept=similar))
        for sid, text in todo:  # keep every line that worked, even if another did not
            if text in got:
                save_wav(paths[sid], got[text])
        missing = [sid for sid, text in todo if text not in got]
        if missing:
            raise SystemExit(f"narration {', '.join(missing)} did not come back word for word; run again")
    return paths


# ---------------------------------------------------------------- slide parts

def cover_with_label(voice: str) -> Path:
    """The first frame says the narration is an AI voice."""
    from playwright.sync_api import sync_playwright

    out = BUILD / "cover-labelled.png"
    img = base64.b64encode((PAGES / "01-cover.png").read_bytes()).decode()
    html = f"""<html><head><link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Archivo:wght@400..700&display=swap"></head>
<body style="margin:0"><div style="position:relative;width:1920px;height:1080px;background:url(data:image/png;base64,{img})">
<p style="position:absolute;left:128px;bottom:64px;margin:0;font:24px Archivo, Arial, sans-serif;color:#BFC6D0">
Narration: an AI voice (AssemblyAI “{voice}”). The demo is a recorded live AssemblyAI session, replayed in the real app.</p></div></body></html>"""
    with sync_playwright() as p:
        b = p.chromium.launch(channel="chrome")
        pg = b.new_page(viewport={"width": 1920, "height": 1080})
        pg.set_content(html, wait_until="networkidle")
        pg.evaluate("document.fonts.ready")
        pg.screenshot(path=str(out))
        b.close()
    return out


def slide_part(image: Path, voice_wav: Path, out: Path) -> float:
    dur = LEAD + seconds(voice_wav) + TAIL
    run([ffmpeg(), "-y", "-loop", "1", "-framerate", "30", "-i", str(image), "-i", str(voice_wav),
         "-filter_complex", f"[0:v]scale=1920:1080,format=yuv420p[v];[1:a]adelay={int(LEAD * 1000)}:all=1,apad,"
                            f"aformat=sample_rates=48000:channel_layouts=stereo[a]",
         "-map", "[v]", "-map", "[a]", "-t", f"{dur:.3f}", "-r", "30",
         "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-tune", "stillimage", "-c:a", "aac", "-b:a", "160k", str(out)])
    return dur


# ------------------------------------------------------------------ demo part

async def capture_demo(url: str, hold: float, frames_dir: Path) -> tuple[list[tuple[float, Path]], list[str], float, float]:
    from playwright.async_api import async_playwright

    frames_dir.mkdir(parents=True, exist_ok=True)
    for f in frames_dir.glob("*.jpg"):
        f.unlink()
    frames: list[tuple[float, Path]] = []
    logs: list[str] = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(channel="chrome", args=["--autoplay-policy=no-user-gesture-required"])
        ctx = await browser.new_context(viewport={"width": 1920, "height": 1080}, device_scale_factor=1)
        page = await ctx.new_page()
        await page.add_init_script(AUDIO_HOOK)
        page.on("console", lambda m: logs.append(m.text) if m.text.startswith("[mirror-video]") else None)
        await page.goto(url)
        await page.wait_for_load_state("networkidle")
        await page.evaluate("document.fonts.ready")
        await page.wait_for_timeout(1500)  # the landing page's entrance animation, before recording starts

        cdp = await ctx.new_cdp_session(page)

        async def on_frame(params):
            path = frames_dir / f"{len(frames):05d}.jpg"
            path.write_bytes(base64.b64decode(params["data"]))
            frames.append((params["metadata"]["timestamp"], path))
            try:
                await cdp.send("Page.screencastFrameAck", {"sessionId": params["sessionId"]})
            except Exception:  # noqa: BLE001 - screencast already stopped
                pass

        cdp.on("Page.screencastFrame", lambda params: asyncio.ensure_future(on_frame(params)))
        t0 = time.time()
        await cdp.send("Page.startScreencast", {"format": "jpeg", "quality": 92, "maxWidth": 1920, "maxHeight": 1080, "everyNthFrame": 1})
        await page.wait_for_timeout(hold * 1000)  # the narrator introduces the demo over the start page
        await page.get_by_role("button", name=re.compile(r"^Watch the")).click()
        await page.get_by_text("That was one live AssemblyAI session").wait_for(timeout=420_000)
        await page.wait_for_timeout(2500)
        await cdp.send("Page.stopScreencast")
        t1 = time.time()
        await asyncio.sleep(0.3)
        await browser.close()
    frames.sort()
    return frames, logs, t0, t1


def demo_part(url: str, intro_wav: Path, out: Path) -> float:
    hold = LEAD + seconds(intro_wav) + 0.5
    frames, logs, t0, t1 = asyncio.run(capture_demo(url, hold, BUILD / "demo-frames"))
    if not frames:
        raise SystemExit("no frames captured")
    plays = []
    for line in logs:
        parts = line.split()
        if parts[1] == "playfail":
            raise SystemExit(f"the browser refused to play a clip: {line}")
        if parts[1] == "play":
            name = parts[3].rsplit("/", 1)[-1]
            plays.append((float(parts[2]) / 1000 - t0, name, float(parts[4])))
    if any(rate != 1.0 for *_, rate in plays):
        raise SystemExit("the replay ran at a speed other than 1x; audio would not line up")
    total = t1 - t0

    # Video: every captured frame, held until the next one, resampled to 30 fps.
    lst = BUILD / "demo-frames.txt"
    lines = ["ffconcat version 1.0"]
    for i, (ts, path) in enumerate(frames):
        start = max(ts, t0) if i else t0
        end = frames[i + 1][0] if i + 1 < len(frames) else t1
        lines += [f"file '{path.as_posix()}'", f"duration {max(0.001, end - start):.4f}"]
    lines.append(f"file '{frames[-1][1].as_posix()}'")
    lst.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # Audio: the narrator's intro, then each recorded clip at the moment the page played it.
    replay_dir = ROOT / "apps" / "web" / "public" / "replay"
    inputs = ["-i", str(intro_wav)]
    chains = [f"[1:a]adelay={int(LEAD * 1000)}:all=1,aformat=sample_rates=48000:channel_layouts=stereo[a0]"]
    for k, (offset, name, _) in enumerate(plays, start=1):
        inputs += ["-i", str(replay_dir / name)]
        chains.append(f"[{k + 1}:a]adelay={max(0, int(offset * 1000))}:all=1,aformat=sample_rates=48000:channel_layouts=stereo[a{k}]")
    mix = "".join(f"[a{k}]" for k in range(len(plays) + 1))
    chains.append(f"{mix}amix=inputs={len(plays) + 1}:normalize=0:duration=longest,apad[a]")
    run([ffmpeg(), "-y", "-f", "concat", "-safe", "0", "-i", str(lst), *inputs,
         "-filter_complex", ";".join(chains) + ";[0:v]fps=30,scale=1920:1080,format=yuv420p[v]",
         "-map", "[v]", "-map", "[a]", "-t", f"{total:.3f}",
         "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-c:a", "aac", "-b:a", "160k", str(out)])
    print(f"demo: {total:.1f} s, {len(frames)} frames, {len(plays)} recorded clips placed")
    return total


# ---------------------------------------------------------------------- main

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8000")
    ap.add_argument("--voice", default="charles")
    a = ap.parse_args()
    ffmpeg()
    if not (PAGES / "01-cover.png").exists():
        raise SystemExit("run scripts/export_deck_pdf.py first (it renders submission/deck-pages)")
    BUILD.mkdir(exist_ok=True)

    voices = narration(a.voice)
    print("narration ready:", ", ".join(f"{sid} {seconds(p):.1f}s" for sid, p in voices.items()))
    cover = cover_with_label(a.voice)

    parts, total = [], 0.0
    for i, (sid, scene, _) in enumerate(SCENES):
        part = BUILD / f"part{i:02d}.mp4"
        if scene == "DEMO":
            total += demo_part(a.url, voices[sid], part)
        else:
            image = cover if scene == "01-cover" else PAGES / f"{scene}.png"
            total += slide_part(image, voices[sid], part)
        parts.append(part)

    inputs = [x for p in parts for x in ("-i", str(p))]
    streams = "".join(f"[{i}:v][{i}:a]" for i in range(len(parts)))
    # Loudness normalized to -16 LUFS, the usual target for web video.
    run([ffmpeg(), "-y", *inputs, "-filter_complex", f"{streams}concat=n={len(parts)}:v=1:a=1[v][a0];[a0]loudnorm=I=-16:TP=-1.5:LRA=11[a]",
         "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-preset", "medium", "-crf", "21",
         "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(OUT)])
    size = OUT.stat().st_size / 1024 / 1024
    print(f"{OUT}: {total:.1f} s ({total / 60:.2f} min), {size:.1f} MB")
    if total > 300 or size > 300:
        print("WARNING: over lablab's limit of 5 minutes or 300 MB")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
