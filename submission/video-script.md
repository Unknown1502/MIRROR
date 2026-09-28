# Demo video: script and shot list (about 3 minutes)

Record the real app with your own voice. Screen recorder: OBS or the Windows Game Bar (Win+Alt+R), 1920×1080, browser at 100% zoom, with the desktop audio and microphone both captured so the agent's voice is on the recording. Before you start: open the app, **Demo controls → Reset to baseline world**, close other tabs, and use headphones so the agent does not hear itself.

If a take goes wrong, keep going: MIRROR's recovery is part of the story. Fallback if voice misbehaves on recording day: record the start page's replay (a real live session) and narrate over it, saying it is a recording.

| Time | On screen | Say (voice-over or to the agent) |
| --- | --- | --- |
| 0:00–0:12 | Start page, headline and the two futures | "Voice agents turn what they *think* you said into actions. This is MIRROR. It shows you what a voice command will do before it does it." |
| 0:12–0:30 | Click **Talk to it**; the console opens, voice connects | "It sits between an AssemblyAI voice agent and an inventory system. Nothing I explore touches the real system until I say commit." |
| 0:30–0:50 | Say it. Two futures appear; the agent asks | To the agent: **"Move 700 units of XR-17 from Mumbai to the Pune site."** Voice-over after it answers: "There are two Pune sites. Instead of guessing, it simulated both. Site 1 would overflow by 400, so that is the question it asks." |
| 0:50–1:00 | Preview for Site 2; a commit button appears | To the agent: **"Site 2."** |
| 1:00–1:20 | What-if 900 goes red; the mirror line still says "Real world changed: No" | **"What if I move 900?"**, then voice-over: "What-ifs run in the shadow world. 900 would break Mumbai's safety stock. The real world hasn't moved." |
| 1:20–1:35 | 500 valid; then the side-by-side table | **"What about 500?"**, then **"Compare 500 and 900."** |
| 1:35–1:55 | Demo controls → **Ship 300 XR-17 out of Mumbai**. Orange alert; the agent speaks | Voice-over: "Now another team ships stock out of Mumbai behind my back." Let the agent's alert play: it re-simulated and tells me unprompted. |
| 1:55–2:15 | Demo controls → **Short shipment**. Say commit. Red "Reality diverged" | **"Commit 500."** Voice-over: "I rigged the ERP to report success but move only 450. MIRROR reads the whole world back and refuses to call it verified." |
| 2:15–2:30 | "Reality restored: Yes" | **"Roll back."** Voice-over: "The rollback is computed from what actually happened, then verified again." |
| 2:30–2:50 | Audit panel: **Verify hash chain** → Intact; then briefly the Transaction versions list | "Every step is in a hash-chained ledger. Under the hood the Voice Agent API's tool calls are only proposals. They're held until the reply finishes, and dropped if I interrupt. Every value is checked against my own transcript." |
| 2:50–3:05 | README evidence table, or the start page | "115 tests, and live runs against AssemblyAI's real service. On 26 scenarios a naive agent took 9 unsafe actions; MIRROR took none. Try it yourself: link below." |

## Lines to say to the agent, in order

1. Move 700 units of XR-17 from Mumbai to the Pune site.
2. Site 2.
3. What if I move 900?
4. What about 500?
5. Compare 500 and 900.
6. *(click: Ship 300 XR-17 out of Mumbai, then wait for the alert)*
7. *(click: Short shipment)* Commit 500.
8. Roll back.

Wait for the agent to finish each answer before the next line. Interrupting is fine and safe, but it cuts the answer short on camera.

## Optional 15-second beats (if time allows)

- **Restraint:** after a preview, say "That sounds good to me." Nothing executes; the agent asks for an explicit commit.
- **Barge-in:** talk over the agent. Its audio stops at once, and any pending tool call is discarded (shown in the conversation).
- **Free-form typing:** type "put 500 next to 900 so I can see both". The reply notes it was read by the AssemblyAI LLM Gateway and checked against your words.
