# Submission kit

**Deadline: 30 September 2026, 15:00 UTC (20:30 IST)**, the event's official end time. A manual submission is
possible for 6 hours afterwards only with a valid reason and prior approval from the organizers.

Sources: the [event page](https://lablab.ai/ai-hackathons/assemblyai-voice-agent-hackathon), lablab's
[submission guide](https://lablab.ai/delivering-your-hackathon-solution) and
[rule book](https://lablab.ai/hackathon-rules) (checked 28 September 2026).

## Before you can submit

1. Enroll in the hackathon on lablab.ai (the Enroll button on the event page).
2. Connect Discord in your lablab profile **and join the lablab.ai Discord server**: lablab requires both before you can create a team.
3. Create a team. A solo team is fine (teams are 1 to 6 people). Only the team admin, or up to 3 members given permission, can submit.
4. Optional: lablab asks you to use a company email if you have one.

## Every field of the form

| Field | Official requirement | Ours | Status |
| --- | --- | --- | --- |
| Submission title | 5–50 characters (form); lablab's site also states letters and spaces only | "MIRROR Voice Agent Safety Layer" (31) | Ready |
| Short description | 50–255 characters | 251 characters, [descriptions.md](descriptions.md) | Ready |
| Long description | 600–2,000 characters (form) and at least 100 words: problem, solution, target audience, unique features | 1,788 characters, 308 words, including market and business model | Ready |
| Categories, Technologies Used | picked from lablab's lists | AssemblyAI first, then the closest matches from [descriptions.md](descriptions.md) | Pick in the form |
| Cover image | PNG or JPG, 16:9 | [cover.png](cover.png), 1920×1080 PNG, 139 KB | Ready |
| Video presentation | MP4, at most 5 minutes, under 300 MB; intro, then slides, then the product | `MIRROR-demo.mp4` (4:23, 14 MB, 1080p, -16 LUFS), built by `scripts/make_video.py`: AI narration (AssemblyAI "charles", labelled on the first frame) over the slides, then the real app replaying the recorded live session with its own voices. To record yourself instead, use [video-script.md](video-script.md) | Ready |
| Slide presentation | PDF | [MIRROR-deck.pdf](MIRROR-deck.pdf), 15 pages, demo link https://prajwalsutar899-mirror.hf.space; rebuild with `python scripts/export_deck_pdf.py --demo-url <url>` if you deploy under another name | Ready |
| Public GitHub repository | public (a private repo lowers the score) | local repo, commits ready on `main`, MIT licence | **Push it** |
| Demo application platform | the guide suggests Streamlit, Replit or Vercel | Hugging Face Spaces (Docker). MIRROR needs a long-running server with WebSockets for live voice, which Vercel's serverless functions and Streamlit do not provide; pick "Other" or the closest option if the form asks | Deploy it |
| Application URL | a link judges can interact with | `https://<hf-user>-mirror.hf.space` after deploying | **Deploy it** |

Other rules that apply: submissions must be original and MIT-compliant (the repo is MIT-licensed); every
project must be built on AssemblyAI (MIRROR uses the Voice Agent API and the LLM Gateway); plagiarism leads
to disqualification.

## How the rubric scores us

The rule book scores each criterion from 1 to 5.

| Criterion | What earns 5 | Where we answer it |
| --- | --- | --- |
| Application of technology | demo video with all features, working demo link, GitHub code available | full demo in the video; the Space; public repo with tests and live results |
| Presentation | problem, solution and value proposition in under 5 minutes, with market analysis, revenue, future plans and competitive analysis | deck slides 2–4 (problem, solution), 11 (market), 12 (revenue), 13 (competition), 14 (roadmap); the video covers each |
| Business value | clear market potential, scalability, sustainable revenue | slides 11 and 12; the long description's business paragraph |
| Originality | unconventional approach, new perspective | counterfactual simulation of every reading before acting, with verification and rollback (slides 3–5, 13) |

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
   Wait for the Space to build (a few minutes), open `https://<hf-user>-mirror.hf.space` and watch the replay once.
   Then have Claude run `scripts/e2e_live_browser.py` against that URL to confirm live voice there.
3. **Keep it awake.** GitHub repository › Settings › Secrets and variables › Actions › Variables › New: `MIRROR_URL` = the `hf.space` URL.
4. **Record the video** with [video-script.md](video-script.md) and export it as MP4 (under 5 minutes, under 300 MB).
5. **Fill in the links** in [descriptions.md](descriptions.md) and on the deck's last slide, export the deck as PDF.
6. **Submit on lablab** before 30 September, 15:00 UTC. Leave a margin: uploads can be slow near the deadline.

Optional: record the replay's user lines in your own voice (`python scripts/record_lines.py --who <name>`, see [../docs/human-voice.md](../docs/human-voice.md)); today they are AssemblyAI's `george` voice, labelled as an AI voice.
