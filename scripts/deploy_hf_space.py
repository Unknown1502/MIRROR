"""Deploy MIRROR as a public Hugging Face Space (Docker SDK) with public-demo guardrails.

    pip install huggingface_hub
    huggingface-cli login                      # or set HF_TOKEN (a token with write access)
    python scripts/deploy_hf_space.py --space <your-hf-username>/mirror --voice-daily-minutes 90

What it does:
  * creates the Space (Docker SDK) if it does not exist
  * stores ASSEMBLYAI_API_KEY and MIRROR_COMMIT_SECRET from .env as Space *secrets* (never printed)
  * sets the public-demo configuration as Space variables: a private ERP sandbox per visitor,
    voice time caps, rate limits, iframe embedding for huggingface.co
  * uploads only what the image needs (Dockerfile, backend/app, apps/web sources incl. the replay)

The app is then served at https://<user>-<space>.hf.space and embedded at huggingface.co/spaces/<user>/<space>.
Free Spaces sleep after 48 h without traffic; .github/workflows/keep-awake.yml pings it.
"""

from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

SPACE_README = """---
title: MIRROR
emoji: 🪞
colorFrom: indigo
colorTo: gray
sdk: docker
app_port: 8000
pinned: false
license: mit
short_description: See what a voice command will do before it does it
---

# MIRROR: the counterfactual runtime for voice agents

Voice agents jump from language to action. MIRROR forks what you said into simulated futures, asks
the one question that changes the outcome, commits only the version you approved, and then checks
that reality agrees. Built on AssemblyAI's Voice Agent API and LLM Gateway.

Every visitor gets a private sandbox copy of the ERP, so faults, resets and commits only touch
your own world. Voice sessions are time-limited.

Source, tests and live results: {repo}
"""


def read_env() -> dict[str, str]:
    env = {}
    path = ROOT / ".env"
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    return env


def stage(dst: Path, repo_url: str) -> None:
    shutil.copy(ROOT / "Dockerfile", dst / "Dockerfile")
    shutil.copy(ROOT / "LICENSE", dst / "LICENSE")
    (dst / ".dockerignore").write_text("**/__pycache__\n**/node_modules\n**/dist\n", encoding="utf-8")
    (dst / "backend").mkdir()
    shutil.copy(ROOT / "backend" / "requirements.txt", dst / "backend" / "requirements.txt")
    shutil.copytree(ROOT / "backend" / "app", dst / "backend" / "app", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    shutil.copytree(ROOT / "apps" / "web", dst / "apps" / "web", ignore=shutil.ignore_patterns("node_modules", "dist", "*.tsbuildinfo"))
    (dst / "README.md").write_text(SPACE_README.format(repo=repo_url or "see the project page"), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--space", required=True, help="<hf-username>/<space-name>")
    ap.add_argument("--repo-url", default="", help="public source repository shown on the Space page")
    ap.add_argument("--voice-daily-minutes", type=int, default=90, help="AssemblyAI voice minutes per day across all visitors (0 = no cap)")
    ap.add_argument("--voice-session-seconds", type=int, default=300)
    ap.add_argument("--voice-concurrent", type=int, default=3)
    ap.add_argument("--private", action="store_true")
    ap.add_argument("--dry-run", action="store_true", help="stage the files and print the plan without uploading")
    ap.add_argument("--stage", metavar="DIR", help="only copy the Space's files into DIR (e.g. to docker build it locally)")
    a = ap.parse_args()

    if a.stage:
        out = Path(a.stage)
        if out.exists() and any(out.iterdir()):
            print(f"{out} is not empty")
            return 2
        out.mkdir(parents=True, exist_ok=True)
        stage(out, a.repo_url)
        print(f"staged into {out}")
        return 0

    env = read_env()
    for key in ("ASSEMBLYAI_API_KEY", "MIRROR_COMMIT_SECRET"):
        if not env.get(key):
            print(f"{key} is missing from .env")
            return 2
    if not (ROOT / "apps" / "web" / "public" / "replay" / "manifest.json").exists():
        print("apps/web/public/replay is missing: run scripts/export_replay.py first")
        return 2

    variables = {
        "MIRROR_SANDBOX_PER_SESSION": "true",
        "MIRROR_FRAME_ANCESTORS": "https://huggingface.co",
        "MIRROR_PROXY_HOPS": "1",
        "MIRROR_RATE_LIMIT": "true",
        "DEMO_MODE": "true",
        "DEMO_FAULT_INJECTION": "true",
        "VOICE_MAX_SESSION_SECONDS": str(a.voice_session_seconds),
        "VOICE_MAX_CONCURRENT": str(a.voice_concurrent),
        "VOICE_DAILY_BUDGET_SECONDS": str(a.voice_daily_minutes * 60),
        "ASSEMBLYAI_VOICE": env.get("ASSEMBLYAI_VOICE", "anna"),
        "LLM_GATEWAY_ENABLED": env.get("LLM_GATEWAY_ENABLED", "true"),
        "LLM_GATEWAY_MODEL": env.get("LLM_GATEWAY_MODEL", "qwen3.5-4b-32k-fast"),
        "MIRROR_CORS_ORIGINS": "",
    }

    with tempfile.TemporaryDirectory(prefix="mirror-space-") as tmp:
        stage(Path(tmp), a.repo_url)
        files = sorted(str(p.relative_to(tmp)).replace("\\", "/") for p in Path(tmp).rglob("*") if p.is_file())
        print(f"Space {a.space}: {len(files)} files, variables:")
        for k, v in variables.items():
            print(f"  {k}={v}")
        print("  secrets: ASSEMBLYAI_API_KEY, MIRROR_COMMIT_SECRET (values not shown)")
        if a.dry_run:
            print("dry run: nothing uploaded")
            return 0

        from huggingface_hub import HfApi

        api = HfApi()
        user = api.whoami()["name"]
        print(f"logged in to Hugging Face as {user}")
        api.create_repo(a.space, repo_type="space", space_sdk="docker", private=a.private, exist_ok=True)
        for key in ("ASSEMBLYAI_API_KEY", "MIRROR_COMMIT_SECRET"):
            api.add_space_secret(a.space, key, env[key])
        for k, v in variables.items():
            api.add_space_variable(a.space, k, v)
        api.upload_folder(repo_id=a.space, repo_type="space", folder_path=tmp,
                          commit_message="Deploy MIRROR", delete_patterns=["*"])
    name = a.space.replace("/", "-").replace("_", "-").lower()
    print(f"uploaded. Building now; the app will be at https://{name}.hf.space (page: https://huggingface.co/spaces/{a.space})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
