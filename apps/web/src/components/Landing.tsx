import { useMirror } from '../store'

// The two futures shown here are what the runtime computes for this sentence on the baseline world
// (Pune Site 1: 1200 on hand, capacity 1500; Pune Site 2: 300, capacity 2000; Mumbai 2000, safety stock 1200).
const FUTURES = [
  { site: 'Pune Site 1', before: 1200, after: 1900, note: 'Blocked: 400 over the site’s capacity of 1500', outcome: 'blocked' as const },
  { site: 'Pune Site 2', before: 300, after: 1000, note: 'Valid: Mumbai stays above safety stock at 1300', outcome: 'valid' as const },
]

export function Landing({ onWatch }: { onWatch: () => void }) {
  const voiceAvailable = useMirror((s) => s.voiceAvailable)
  const sandbox = useMirror((s) => s.sandbox)
  const sessionId = useMirror((s) => s.sessionId)
  const bootError = useMirror((s) => s.bootError)
  const setScreen = useMirror((s) => s.setScreen)
  const toggleVoice = useMirror((s) => s.toggleVoice)

  const talk = () => {
    setScreen('console')
    void toggleVoice()
  }

  return (
    <div className="min-h-full overflow-y-auto">
      <div className="mx-auto flex max-w-[1080px] flex-col px-4 pt-6 pb-12 sm:px-8 lg:pt-10">
        <Wordmark />

        <h1 className="landing-title mt-10 max-w-[17ch] text-[clamp(32px,5.4vw,60px)] leading-[1.02] text-ink lg:mt-14">
          See what a voice command will do before it does it.
        </h1>
        <p className="mt-5 max-w-[60ch] text-[16px] leading-relaxed text-ink-soft sm:text-[17px]">
          MIRROR sits between a voice agent and your inventory system. It forks what you said into simulated futures, asks the
          one question that changes the outcome, commits only the version you approved, and then checks that reality agrees.
        </p>

        <figure className="mt-10 lg:mt-12" aria-label="Example: one sentence, two possible futures">
          <blockquote className="text-[clamp(19px,2.4vw,26px)] font-medium leading-snug text-ink">
            “Move 700 XR-17 from Mumbai to the Pune site.”
          </blockquote>
          <div className="mirror-rule mt-4" aria-hidden />
          <div className="mt-4 grid gap-3 sm:grid-cols-2 sm:gap-4">
            {FUTURES.map((f) => (
              <div key={f.site} className="future-enter shadow-pane rounded-lg px-4 py-3.5">
                <p className="text-[13px] font-semibold text-shadow">If you meant {f.site}</p>
                <p className="num mt-1 text-[26px] font-bold text-ink">
                  {f.before} <span className="text-ink-faint">to</span> <span className={f.outcome === 'blocked' ? 'text-blocked' : 'text-valid'}>{f.after}</span>
                </p>
                <p className={`mt-0.5 text-[13px] ${f.outcome === 'blocked' ? 'text-blocked' : 'text-valid'}`}>{f.note}</p>
              </div>
            ))}
          </div>
          <figcaption className="mt-3 text-[13px] text-ink-soft">
            Both futures are simulated against a frozen copy of the ERP. Nothing has moved, so MIRROR asks: which Pune site?
          </figcaption>
        </figure>

        <div className="mt-10 flex flex-wrap items-center gap-3">
          <button
            onClick={onWatch}
            disabled={!sessionId}
            className="flex items-center gap-2.5 rounded-full bg-ink px-5 py-3 text-[15px] font-semibold text-glass hover:opacity-90 disabled:opacity-40"
          >
            <PlayGlyph />
            Watch the 2½-minute replay
          </button>
          <button
            onClick={talk}
            disabled={!voiceAvailable || !sessionId}
            title={voiceAvailable ? 'Uses your microphone. Audio goes to AssemblyAI through this server.' : 'Voice is off on this server (no AssemblyAI key)'}
            className="flex items-center gap-2.5 rounded-full border-[1.5px] border-ink px-5 py-3 text-[15px] font-semibold text-ink hover:bg-ink hover:text-glass disabled:border-rule disabled:text-ink-faint disabled:hover:bg-transparent"
          >
            <MicGlyph />
            Talk to it
          </button>
          <button
            onClick={() => setScreen('console')}
            className="rounded-full px-4 py-3 text-[15px] font-semibold text-ink-soft underline decoration-rule underline-offset-4 hover:text-ink hover:decoration-ink"
          >
            Open the console
          </button>
        </div>

        {bootError && (
          <p className="mt-4 text-[13px] text-blocked" role="alert">
            The backend did not answer: {bootError}
          </p>
        )}

        <div className="mt-8 grid max-w-[900px] gap-x-10 gap-y-3 border-t border-rule pt-5 text-[13px] leading-relaxed text-ink-soft sm:grid-cols-2">
          <p>
            The replay is a live session with AssemblyAI’s Voice Agent API. Its voices and transcripts are the recording; every
            future, check and ERP number on screen is recomputed by this server while you watch.
          </p>
          <p>
            {sandbox
              ? 'You get your own sandbox copy of the ERP, so try anything: faults, resets and commits only touch your world.'
              : 'Typed commands and voice drive the same runtime. The model proposes; the backend decides what executes.'}
            {voiceAvailable && ' Talking to it uses your microphone; voice sessions are time-limited.'}
          </p>
        </div>
      </div>
    </div>
  )
}

function Wordmark() {
  return (
    <div className="relative w-fit leading-none" aria-label="MIRROR">
      <span className="num block text-[30px] tracking-tight" style={{ fontWeight: 800 }}>
        MIRROR
      </span>
      <span
        aria-hidden
        className="num pointer-events-none absolute left-0 top-full block origin-top text-[30px] tracking-tight text-shadow"
        style={{ fontWeight: 800, transform: 'scaleY(-0.45)', opacity: 0.35, maskImage: 'linear-gradient(to bottom, black, transparent)' }}
      >
        MIRROR
      </span>
    </div>
  )
}

function PlayGlyph() {
  return (
    <svg width="12" height="14" viewBox="0 0 12 14" aria-hidden fill="currentColor">
      <path d="M1 1.2v11.6a.6.6 0 0 0 .9.5l9.5-5.8a.6.6 0 0 0 0-1L1.9.7a.6.6 0 0 0-.9.5Z" />
    </svg>
  )
}

function MicGlyph() {
  return (
    <svg width="12" height="14" viewBox="0 0 12 14" aria-hidden fill="none" stroke="currentColor" strokeWidth="1.6">
      <rect x="3.5" y="1" width="5" height="8" rx="2.5" />
      <path d="M1 7a5 5 0 0 0 10 0M6 12v1.5" strokeLinecap="round" />
    </svg>
  )
}
