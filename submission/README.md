# Submission kit

Deadline: **30 September 2026, 15:00 UTC (20:30 IST)**. Judging: application of technology, presentation, business value, originality.

| lablab field | Where it is | Status |
| --- | --- | --- |
| Title, short and long description, technologies, tags | [descriptions.md](descriptions.md) | Ready. Fill in `<DEMO_URL>` and `<REPO_URL>` |
| Cover image (16:9) | [cover.png](cover.png) | Ready |
| Slide presentation (PDF) | Slides artifact "MIRROR pitch deck" (private until you share it): open it, fill in `[DEMO_URL]` and `[REPO_URL]` on the last slide, then Share › Export › PDF | Ready |
| Video presentation | [video-script.md](video-script.md) | Script ready; you record it |
| Public GitHub repository | local repo, branch `main`, first commit made | You create the GitHub repo and push |
| Demo application URL | `python scripts/deploy_hf_space.py --space <hf-user>/mirror`, see [../docs/deploy.md](../docs/deploy.md) | You run it (needs your Hugging Face login) |

## Your steps, in order

1. **Push the code.** Create an empty public repository on GitHub (no README), then:
   ```bash
   git remote add origin https://github.com/<you>/mirror.git
   git push -u origin main
   ```
2. **Deploy the demo.**
   ```bash
   pip install huggingface_hub
   huggingface-cli login
   python scripts/deploy_hf_space.py --space <hf-user>/mirror --repo-url https://github.com/<you>/mirror
   ```
   Wait for the Space to build (a few minutes), then open `https://<hf-user>-mirror.hf.space` and watch the replay once.
   Then check live voice there: `python scripts/e2e_live_browser.py --url https://<hf-user>-mirror.hf.space --out live-browser-hf`.
3. **Keep it awake.** In the GitHub repository: Settings › Secrets and variables › Actions › Variables › New: `MIRROR_URL` = the `hf.space` URL.
4. **Record the video** with [video-script.md](video-script.md) against the deployed app (or locally), and upload it (YouTube unlisted is fine).
5. **Fill in the links** in [descriptions.md](descriptions.md) and on the deck's last slide, export the deck as PDF, and submit.

Optional, if time allows: record the eight replay lines with your own voice ([../docs/human-voice.md](../docs/human-voice.md)) and re-run `live_voice_test.py --only judge` and `export_replay.py`, so the replay's user side is a real person.
