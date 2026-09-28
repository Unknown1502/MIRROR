"""Record the live-test lines in your own voice, one by one.

    python scripts/record_lines.py --who prajwal            # the 8 lines of the judge replay
    python scripts/record_lines.py --who prajwal --all      # every line of every live section

Each line is shown on screen: press Enter, say it naturally, press Enter again. You hear it back
and can keep it or record it again. Files land in recordings/<who>/ under the names
live_voice_test.py --recordings expects. recordings/ is gitignored: your voice stays on this machine.
Requires: pip install sounddevice
"""

from __future__ import annotations

import argparse
import re
import sys
import threading
import wave
from array import array
from pathlib import Path

import sounddevice as sd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from live_voice_test import SECTIONS, slug, spoken_lines  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def readable(line: str) -> str:
    """The lines spell product codes for the synthetic voice; show them the way people say them."""
    line = re.sub(r"\bX R 17 B\b", "XR-17B", line)
    line = re.sub(r"\bX R 17\b", "XR-17", line)
    return re.sub(r"\bX R\b", "XR (just the family, no number)", line)


def record(rate: int) -> bytes:
    chunks: list[bytes] = []
    stop = threading.Event()

    def cb(indata, frames, t, status):  # noqa: ARG001
        chunks.append(bytes(indata))

    with sd.RawInputStream(samplerate=rate, channels=1, dtype="int16", callback=cb):
        threading.Thread(target=lambda: (input(), stop.set()), daemon=True).start()
        stop.wait()
    return b"".join(chunks)


def trim(pcm: bytes, rate: int) -> bytes:
    """Cut leading and trailing silence, keeping 0.3 s around the speech."""
    s = array("h", pcm)
    frame = max(1, rate // 50)  # 20 ms
    loud = [i for i in range(0, len(s), frame) if max((abs(x) for x in s[i:i + frame]), default=0) > 900]
    if not loud:
        return pcm
    pad = int(rate * 0.3)
    return s[max(0, loud[0] - pad):min(len(s), loud[-1] + frame + pad)].tobytes()


def peak(pcm: bytes) -> int:
    s = array("h", pcm)
    return max((abs(x) for x in s), default=0)


def save(path: Path, pcm: bytes, rate: int) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--who", required=True, help="your name: recordings are saved in recordings/<who>/")
    ap.add_argument("--all", action="store_true", help="every live section, not only the judge replay")
    ap.add_argument("--redo", action="store_true", help="record lines again even if a file exists")
    a = ap.parse_args()

    out = ROOT / "recordings" / a.who
    out.mkdir(parents=True, exist_ok=True)
    sections = list(SECTIONS) if a.all else ["judge"]
    lines = spoken_lines(sections)
    rate = int(sd.query_devices(kind="input")["default_samplerate"])
    print(f"Microphone: {sd.query_devices(kind='input')['name']} ({rate} Hz). Saving to {out}\n")
    print("Speak normally, as you would to a colleague. Headphones help.\n")

    done = 0
    for n, text in enumerate(lines, 1):
        target = out / f"{slug(text)}.wav"
        if target.exists() and not a.redo:
            print(f"[{n}/{len(lines)}] already recorded: {readable(text)}")
            done += 1
            continue
        while True:
            print(f"\n[{n}/{len(lines)}]  Say:  \"{readable(text)}\"")
            input("        Press Enter, then speak. ")
            print("        Recording... press Enter when you have finished.")
            pcm = trim(record(rate), rate)
            seconds = len(pcm) / 2 / rate
            if peak(pcm) < 1500:
                print("        That was very quiet. Check the microphone, or speak closer to it.")
            print(f"        Playing back ({seconds:.1f} s)...")
            _play(pcm, rate)
            choice = input("        Enter = keep,  r = record again,  s = skip,  q = quit: ").strip().lower()
            if choice == "q":
                print(f"\nStopped. {done} of {len(lines)} lines recorded in {out}")
                return 0
            if choice == "s":
                break
            if choice == "r":
                continue
            save(target, pcm, rate)
            done += 1
            break

    print(f"\nDone: {done} of {len(lines)} lines in {out}")
    if done:
        section = "" if a.all else " --only judge"
        print("Next (real AssemblyAI session with your voice):")
        print(f"  python scripts/live_voice_test.py --recordings recordings/{a.who}{section} --out live-{'run-' + a.who if a.all else 'judge'}")
        if not a.all:
            print("  python scripts/export_replay.py live-judge")
    return 0


def _play(pcm: bytes, rate: int) -> None:
    with sd.RawOutputStream(samplerate=rate, channels=1, dtype="int16") as stream:
        stream.write(pcm)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nStopped.")
        sys.exit(0)
