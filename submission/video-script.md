# Demo video: script and shot list (4 min 45 s)

lablab's rules for the video: **MP4, at most 5 minutes, under 300 MB**. Their guide asks for this order: an
introduction, then the PDF slides, then the product working. The judging rubric's top Presentation score
also wants the problem, solution, value proposition, market, revenue, competition and future plans, all in
under 5 minutes. This script does that in 4:45.

**Setup.** Record the real app with your own voice. OBS (or Win+Alt+R) at 1920×1080, 30 fps, video bitrate
about 5,000 kbps (about 190 MB for 5 minutes), with desktop audio and microphone both captured so the
agent's voice is on the recording. Use headphones so the agent does not hear itself. Before recording:
open the app, **Demo controls → Reset to baseline world**, close other tabs, open the deck PDF in a
second window.

If a file comes out over 300 MB: `ffmpeg -i video.mp4 -c:v libx264 -crf 24 -preset medium -c:a aac -b:a 128k video-small.mp4`.

If a take goes wrong, keep going: MIRROR's recovery is part of the story. If voice misbehaves on recording
day, record the start page's replay instead and say it is a recording of a live session.

## Part 1: introduction (0:00–0:15), deck slide 1

"Hi, I'm [your name]. This is MIRROR: it shows you what a voice command will do before it does it. Built on AssemblyAI's Voice Agent API."

## Part 2: the slides (0:15–1:30)

| Time | Slide | Say |
| --- | --- | --- |
| 0:15–0:30 | 2, the problem | "Voice agents turn what they *think* you said into actions. Say 'the Pune site' when there are two, and a typical agent guesses, then writes to the inventory system." |
| 0:30–0:45 | 3 and 4, the idea | "MIRROR simulates every reading first. Site 1 would overflow by 400, Site 2 is fine, so that is the one question it asks. Nothing real moves until you say commit, and afterwards it checks that reality agrees." |
| 0:45–1:00 | 7 and 8, AssemblyAI | "AssemblyAI does the conversation: recognition, turn-taking, barge-in, tool calls and the voice. Every tool call is only a proposal. It waits for the reply to finish, is dropped if I interrupt, and every value is checked against my own words." |
| 1:00–1:15 | 11 and 12, market and revenue | "AI agents are forecast to grow from 7.8 to 52.6 billion dollars by 2030; warehouse software, our first market, to 10 billion. We earn from operator seats, per verified transaction, and paid ERP connectors." |
| 1:15–1:30 | 13, competition | "Confirmation prompts show no consequences, guardrails never see real data, and voice agents act on the model's reading. MIRROR previews every reading, commits exactly one, and verifies it." |

## Part 3: the product (1:30–4:10)

| Time | On screen | Say |
| --- | --- | --- |
| 1:30–1:40 | Start page → **Talk to it** | "Here's the real thing, live." |
| 1:40–2:00 | Two futures appear; the agent asks | To the agent: **"Move 700 units of XR-17 from Mumbai to the Pune site."** |
| 2:00–2:10 | Preview for Site 2 | **"Site 2."** |
| 2:10–2:30 | What-if 900 in red; "Real world changed: No" | **"What if I move 900?"** Voice-over: "A what-if never touches the real world." |
| 2:30–2:50 | 500 valid, then the side-by-side table | **"What about 500?"**, then **"Compare 500 and 900."** |
| 2:50–3:15 | Demo controls → **Ship 300 XR-17 out of Mumbai**; orange alert; the agent speaks | "Another team ships stock behind my back." Let the alert play. |
| 3:15–3:40 | Demo controls → **Short shipment**; red "Reality diverged" | **"Commit 500."** "The ERP claims success but moves 450. MIRROR reads everything back and refuses to call it verified." |
| 3:40–3:55 | "Reality restored: Yes" | **"Roll back."** "The rollback is computed from what actually happened, then verified." |
| 3:55–4:10 | Audit panel → **Verify hash chain** | "Every step is in a hash-chained ledger." |

## Part 4: proof and what's next (4:10–4:45), deck slides 9, 14, 15

"116 tests and live runs against AssemblyAI's real service. On 26 scenarios, a naive agent took 9 unsafe actions; MIRROR took none. Next: NetSuite and SAP connectors, approval routing, more languages. Try it yourself at the link below. Thanks!"

## Lines to say to the agent, in order

1. Move 700 units of XR-17 from Mumbai to the Pune site.
2. Site 2.
3. What if I move 900?
4. What about 500?
5. Compare 500 and 900.
6. *(click: Ship 300 XR-17 out of Mumbai, then wait for the alert)*
7. *(click: Short shipment)* Commit 500.
8. Roll back.

Wait for the agent to finish each answer before the next line.
