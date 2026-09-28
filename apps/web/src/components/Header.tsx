import { useEffect, useState } from 'react'
import { useMirror } from '../store'
import { DemoControls } from './DemoControls'

export function Header({ onReplay }: { onReplay: () => void }) {
  const socketStatus = useMirror((s) => s.socketStatus)
  const voice = useMirror((s) => s.voice)
  const voiceAvailable = useMirror((s) => s.voiceAvailable)
  const toggleVoice = useMirror((s) => s.toggleVoice)
  const userSpeaking = useMirror((s) => s.userSpeaking)
  const agentSpeaking = useMirror((s) => s.agentSpeaking)
  const micLevel = useMirror((s) => s.micLevel)
  const sandbox = useMirror((s) => s.sandbox)
  const replaying = useMirror((s) => s.replaying)
  const setScreen = useMirror((s) => s.setScreen)

  const live = voice === 'live'
  const voiceLabel =
    voice === 'starting' ? 'Connecting voice' : live ? 'Stop voice' : voiceAvailable ? 'Start voice' : 'Voice unavailable'

  return (
    <header className="flex items-center gap-6 border-b border-rule bg-glass px-5 py-2.5">
      <div className="flex items-baseline gap-4">
        <button className="relative leading-none" aria-label="MIRROR start page" title="Start page" onClick={() => setScreen('landing')}>
          <span className="num block text-[26px] font-800 tracking-tight" style={{ fontWeight: 800 }}>
            MIRROR
          </span>
          <span
            aria-hidden
            className="num pointer-events-none absolute left-0 top-full block origin-top text-[26px] tracking-tight text-shadow"
            style={{ fontWeight: 800, transform: 'scaleY(-0.45)', opacity: 0.35, maskImage: 'linear-gradient(to bottom, black, transparent)' }}
          >
            MIRROR
          </span>
        </button>
        <p className="hidden text-[13px] text-ink-soft xl:block">Counterfactual runtime for voice agents</p>
      </div>

      <div className="ml-auto flex flex-wrap items-center justify-end gap-x-4 gap-y-2">
        {sandbox && (
          <span
            className="hidden rounded-full border border-shadow/50 px-2.5 py-0.5 text-[12px] text-shadow sm:inline"
            title="You have your own copy of the ERP world. Commits, faults and resets here never touch other visitors."
          >
            Your sandbox
          </span>
        )}
        <span className="flex items-center gap-2 text-[12px] text-ink-soft" title="Backend event stream">
          <span
            className={`inline-block h-2 w-2 rounded-full ${socketStatus === 'open' ? 'bg-valid' : socketStatus === 'connecting' ? 'bg-warn' : 'bg-blocked'}`}
          />
          {socketStatus === 'open' ? 'Runtime connected' : socketStatus === 'connecting' ? 'Connecting' : 'Runtime offline'}
        </span>

        <button
          onClick={() => void toggleVoice()}
          disabled={(!voiceAvailable && !live) || replaying}
          title={voiceAvailable ? 'Stream your microphone to the AssemblyAI Voice Agent through the MIRROR backend' : 'Set ASSEMBLYAI_API_KEY on the server to enable voice'}
          className={`flex items-center gap-2.5 rounded-full border px-4 py-1.5 text-[13px] font-semibold transition-colors ${
            live ? 'border-ink bg-ink text-glass' : 'border-ink text-ink hover:bg-ink hover:text-glass disabled:border-rule disabled:text-ink-faint disabled:hover:bg-transparent'
          }`}
        >
          <MicGlyph />
          {voiceLabel}
          {live && (
            <span className="flex h-3 items-end gap-[2px]" aria-hidden>
              {[0.35, 0.7, 1].map((k) => (
                <span key={k} className="w-[3px] rounded-sm bg-glass" style={{ height: `${Math.max(3, Math.min(12, micLevel * 40 * k))}px` }} />
              ))}
            </span>
          )}
        </button>
        {live && (
          <span className="text-[12px] text-ink-soft" aria-live="polite">
            {userSpeaking ? 'Hearing you' : agentSpeaking ? 'Agent speaking' : 'Listening'}
            <VoiceClock />
          </span>
        )}
        {!replaying && (
          <button
            onClick={onReplay}
            className="rounded-full border border-rule px-3.5 py-1.5 text-[13px] font-medium text-ink-soft hover:border-ink hover:text-ink"
          >
            Watch replay
          </button>
        )}
        <DemoControls />
      </div>
    </header>
  )
}

function VoiceClock() {
  const endsAt = useMirror((s) => s.voiceEndsAt)
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    if (!endsAt) return
    const t = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(t)
  }, [endsAt])
  if (!endsAt) return null
  const left = Math.max(0, Math.round((endsAt - now) / 1000))
  return (
    <span className={`tabular ml-2 ${left < 60 ? 'text-warn' : 'text-ink-faint'}`} title="Voice time left in this session">
      {Math.floor(left / 60)}:{String(left % 60).padStart(2, '0')} left
    </span>
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
