"""Turn a recorded live judge session into the web replay (apps/web/public/replay).

    python scripts/live_voice_test.py --only judge --out live-judge        # records it (real AssemblyAI session)
    python scripts/export_replay.py live-judge                              # writes manifest.json + mp3 clips

The replay keeps what was recorded (both voices, AssemblyAI's transcripts, the agent's words, the
tool calls, the measured reply latency) and lists the backend action for each step, which the page
re-runs against the server. Requires ffmpeg.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import wave
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "apps" / "web" / "public" / "replay"

# Must match the judge section in live_voice_test.py.
EXTERNAL = {"location_id": "MUMBAI-DC", "product_id": "XR-17", "delta": -300, "note": "another team shipped 300"}


def seconds(path: Path) -> float:
    with wave.open(str(path), "rb") as w:
        return w.getnframes() / w.getframerate()


def to_mp3(ffmpeg: str, src: Path, dst: Path) -> None:
    subprocess.run([ffmpeg, "-v", "error", "-y", "-i", str(src), "-ac", "1", "-ar", "24000", "-b:a", "48k", str(dst)], check=True)


def main() -> int:
    run = Path(sys.argv[1] if len(sys.argv) > 1 else "live-judge")
    report = json.loads((run / "report.json").read_text(encoding="utf-8"))
    turns = [t for t in report["turns"] if t["section"] == "judge"]
    if not turns:
        print(f"{run}/report.json has no judge turns")
        return 2
    failed = [k for t in turns for k, v in t["checks"].items() if not v and "(observed)" not in k]
    if failed:
        print(f"refusing to export a run with failed checks: {failed}")
        return 1
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        print("ffmpeg is required")
        return 2
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)

    steps, total = [], 0.0
    for i, t in enumerate(turns, 1):
        reality = t["said"].startswith("(")
        step = {
            "kind": "reality" if reality else "turn",
            "said": t["said"],
            "heard": t["heard"],
            "agent_said": t["agent_said"],
            "tool_calls": [c["name"] for c in t["tool_calls"]],
            "user_audio": None,
            "agent_audio": None,
            "agent_offset_s": t.get("speech_end_to_first_agent_audio_s"),
            "recorded_state": t["state"],
        }
        if reality:
            step["external_change"] = EXTERNAL
        if t["state"] == "DIVERGED" and any(c["name"] == "commit_transfer" for c in t["tool_calls"]):
            step["arm_fault"] = "verification_mismatch"
        for kind in ("user", "agent"):
            wav = t.get(f"{kind}_wav")
            if wav and (run / wav).exists():
                name = f"{i:02d}-{kind}.mp3"
                to_mp3(ffmpeg, run / wav, OUT / name)
                step[f"{kind}_audio"] = name
                total += seconds(run / wav)
        steps.append(step)

    spoken = [t.get("speech") or "" for t in turns if not t["said"].startswith("(")]
    human = sum(s.startswith("human:") for s in spoken)
    user_voice = (
        "a recorded human voice" if spoken and human == len(spoken)
        else f"a recorded human voice for {human} of {len(spoken)} lines, synthesized speech for the rest" if human
        else "synthesized speech (Windows SAPI) streamed as microphone audio"
    )
    manifest = {
        "recorded_at": datetime.fromtimestamp((run / "report.json").stat().st_mtime, timezone.utc).strftime("%Y-%m-%d"),
        "agent_voice": "AssemblyAI Voice Agent API (voice: alba)",
        "user_voice": user_voice,
        "checks": f"{report['checks_passed']}/{report['checks_total']}",
        "audio_seconds": round(total, 1),
        "steps": steps,
    }
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    size = sum(p.stat().st_size for p in OUT.iterdir())
    print(f"wrote {len(steps)} steps, {total:.0f} s of audio, {size / 1024:.0f} KB to {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
