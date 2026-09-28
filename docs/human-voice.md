# Validating with human voices

The live suites speak with synthesized voices (Windows SAPI). To show MIRROR works with real people,
record the same lines with your own voices and run the identical checks against the real AssemblyAI
service.

## 1. Get the list of lines

```bash
python scripts/live_voice_test.py --list-lines
```

It prints every distinct line (28 of them) with the file name to use, for example:

```text
move-700-units-of-x-r-17-from-mumbai-to-the-pune-site.m4a   Move 700 units of X R 17 from Mumbai to the Pune site.
site-2.m4a                                                  Site 2.
what-if-i-move-900.m4a                                      What if I move 900?
```

Say "X R 17" however you naturally would ("ex-are seventeen" is fine): that is the point.

## 2. Record

One folder per speaker, e.g. `recordings/alice/`. Any format ffmpeg reads works: a phone's voice
recorder (`.m4a`), `.wav`, `.webm`, `.mp3`. Keep each clip to the line itself, with a short pause at
the start and end. A normal room is fine; background noise is part of the test.

`recordings/` is gitignored: the clips stay on your machine.

For the judge replay only, the eight lines of the `judge` section are enough.

## 3. Run

```bash
python scripts/live_voice_test.py --recordings recordings/alice --out live-run-alice
python scripts/live_voice_test.py --recordings recordings/alice --only judge --out live-judge   # re-record the replay with a human voice
python scripts/export_replay.py live-judge                                                       # then publish it to the web replay
```

Lines without a recording fall back to the synthesized voice. Each turn in `report.json` records its
source (`"speech": "human:site-2.m4a"` or `"sapi:Microsoft Zira Desktop"`), and the run prints how many
turns were human. The replay's footer names the user voice as a recorded human voice when it was one.

## Without a person: a natural AI voice

The replay currently uses AssemblyAI's own `george` voice for the user's lines instead of the robotic Windows voice:

```bash
python scripts/ai_voice_lines.py --samples anna,alba,jane,vera,george,michael,charles,paul   # hear the options in voice-samples/
python scripts/ai_voice_lines.py --voice george                                              # the judge lines -> recordings/ai-george/
python scripts/live_voice_test.py --recordings recordings/ai-george --only judge --out live-judge
python scripts/export_replay.py live-judge
```

Each clip is kept only if the agent's transcript matches the line word for word. The folder's `SPEAKER.txt` makes every report and the replay footer say it is an AI voice, not a person. The agent's own voice is `ASSEMBLYAI_VOICE` in `.env` (now `anna`).

## Recording yourself, quickly

`python scripts/record_lines.py --who <name>` shows each judge line, records it from your microphone, plays it back, and saves it where `--recordings recordings/<name>` expects (`--all` for every section). Requires `pip install sounddevice`.

## What to look at

- `heard`: AssemblyAI's transcript of the speaker. Numbers and "XR-17" should survive accents.
- The checks: same pass criteria as the synthesized runs (fork, block, compare, commit, divergence, rollback, honesty).
- Misheard lines are useful findings: MIRROR should ask or refuse, never act on a guess.
