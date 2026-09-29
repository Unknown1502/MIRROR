"""Have an AssemblyAI voice read the user's lines: a natural AI voice instead of Windows' SAPI voice.

    python scripts/ai_voice_lines.py --voice george                 # the judge replay's lines -> recordings/ai-george/
    python scripts/ai_voice_lines.py --voice george --all           # every live section
    python scripts/ai_voice_lines.py --samples anna,alba,jane,vera,george,michael,charles,paul

The lines are spoken by the Voice Agent API itself (reply.create with the exact sentence); each clip is
kept only if the agent's transcript matches the line word for word. The folder gets a SPEAKER.txt,
so live_voice_test.py and export_replay.py label these clips as an AI voice, never as a person.
Uses real AssemblyAI session time (about 2 s of audio per line).
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import re
import sys
import wave
from pathlib import Path

import websockets

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(ROOT / "backend"))

from app.core.config import load_settings  # noqa: E402
from live_voice_test import SECTIONS, slug, spoken_lines  # noqa: E402

PROMPT = (
    "You are a voice actor recording lines for a product demo. You play a warehouse operations manager "
    "talking to a voice assistant. When you are given a line, say exactly that line, word for word, in a "
    "natural, relaxed, conversational tone. Never add, drop or change a word, never answer it, never say "
    "anything else. Say product codes the way people do: XR-17 is 'X R seventeen'."
)
SAMPLE = "There are two Pune sites. Site 1 would exceed capacity by 400 units; Site 2 remains valid. Which one do you mean?"


def readable(line: str) -> str:
    line = re.sub(r"\bX R 17 B\b", "XR-17B", line)
    line = re.sub(r"\bX R 17\b", "XR-17", line)
    return re.sub(r"\bX R\b", "XR", line)


def words(s: str) -> list[str]:
    s = s.lower().replace("xr-17", "xr17").replace("x r 17", "xr17").replace("x r seventeen", "xr17")
    return re.findall(r"[a-z0-9]+", s)


def save_wav(path: Path, pcm: bytes) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(24000)
        w.writeframes(pcm)


def exact(text: str, line: str) -> bool:
    return words(text) == words(line)


async def say_all(settings, voice: str, lines: list[str], prompt: str = PROMPT, accept=exact) -> dict[str, bytes]:
    out: dict[str, bytes] = {}
    async with websockets.connect(settings.assemblyai_agent_url, max_size=2**23, open_timeout=10,
                                  additional_headers={"Authorization": f"Bearer {settings.assemblyai_api_key}"}) as ws:
        await ws.send(json.dumps({"type": "session.update", "session": {
            "system_prompt": prompt,
            "input": {"format": {"encoding": "audio/pcm"}},
            "output": {"voice": voice, "format": {"encoding": "audio/pcm"}},
        }}))
        while json.loads(await asyncio.wait_for(ws.recv(), 20)).get("type") != "session.ready":
            pass
        for line in lines:
            for attempt in range(3):
                await ws.send(json.dumps({"type": "reply.create",
                                          "instructions": f'Say exactly this line, word for word, and nothing else: "{line}"'}))
                pcm, text = bytearray(), ""
                while True:
                    e = json.loads(await asyncio.wait_for(ws.recv(), 30))
                    t = e.get("type")
                    if t == "reply.audio":
                        pcm += base64.b64decode(e.get("data", ""))
                    elif t == "transcript.agent":
                        text = e.get("text") or ""
                    elif t == "reply.done":
                        break
                    elif t in ("session.error", "error"):
                        raise RuntimeError(f"AssemblyAI: {e}")
                ok = accept(text, line) and len(pcm) > 4800
                print(f"  {voice:8} {'ok ' if ok else 'retry'} {len(pcm) / 48000:4.1f}s  said: {text!r}", flush=True)
                if ok:
                    out[line] = bytes(pcm)
                    break
        await ws.send(json.dumps({"type": "session.end"}))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--voice", default="george")
    ap.add_argument("--all", action="store_true", help="every live section, not only the judge replay")
    ap.add_argument("--samples", help="comma-separated voices: one sample sentence each, into voice-samples/")
    a = ap.parse_args()
    settings = load_settings()
    if not settings.assemblyai_api_key:
        print("ASSEMBLYAI_API_KEY is not set")
        return 2

    if a.samples:
        folder = ROOT / "voice-samples"
        folder.mkdir(exist_ok=True)
        for voice in [v.strip() for v in a.samples.split(",") if v.strip()]:
            got = asyncio.run(say_all(settings, voice, [SAMPLE]))
            if SAMPLE in got:
                save_wav(folder / f"{voice}.wav", got[SAMPLE])
        print(f"samples in {folder}")
        return 0

    lines = spoken_lines(list(SECTIONS) if a.all else ["judge"])
    folder = ROOT / "recordings" / f"ai-{a.voice}"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "SPEAKER.txt").write_text(f"assemblyai:{a.voice}\n", encoding="utf-8")
    got = asyncio.run(say_all(settings, a.voice, [readable(t) for t in lines]))
    for t in lines:
        pcm = got.get(readable(t))
        if pcm:
            save_wav(folder / f"{slug(t)}.wav", pcm)
    missing = [t for t in lines if readable(t) not in got]
    print(f"{len(lines) - len(missing)} of {len(lines)} lines in {folder}" + (f"; not matched: {missing}" if missing else ""))
    return 0 if not missing else 1


if __name__ == "__main__":
    sys.exit(main())
