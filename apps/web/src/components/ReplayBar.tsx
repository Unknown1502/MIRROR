import { useEffect, useRef, useState } from 'react'
import { replayManifest } from '../lib/api'
import type { ReplayManifest, ReplayStep } from '../lib/types'
import { useMirror } from '../store'

// What to notice at each step of the recorded judge session (scripts/live_voice_test.py, section "judge").
const CAPTIONS = [
  '“The Pune site” could mean two places. MIRROR simulates both instead of guessing, and asks. Nothing real has moved.',
  'The answer picks one future. It becomes a preview, and a commit token is signed for exactly this version.',
  'A what-if runs in the shadow world only. 900 would push Mumbai below its safety stock, so that future is blocked.',
  'Back to 500: valid. Every change is a new immutable version, and the old commit token stops working.',
  'Both quantities side by side, computed from the same ERP snapshot.',
  'Another team ships 300 out of Mumbai, straight in the ERP. MIRROR notices, re-simulates the open preview and says so without being asked.',
  'Commit. We rigged the ERP to short-ship: it reports success but moves only 450. MIRROR checks the real world and refuses to call it verified.',
  'The rollback is computed from what actually happened, not from the plan. Reality is restored and verified again.',
]

type Phase = 'loading' | 'user' | 'runtime' | 'world' | 'agent' | 'done'

const PHASE_WORD: Record<Phase, string> = {
  loading: 'Resetting the world',
  user: 'User speaking (recording)',
  runtime: 'Runtime simulating (live)',
  world: 'Reality changes (live)',
  agent: 'Agent speaking (recording)',
  done: 'Replay finished',
}

class Cancelled extends Error {}

interface Run {
  cancelled: boolean
}

export function ReplayBar({ onExit }: { onExit: () => void }) {
  const [manifest, setManifest] = useState<ReplayManifest | null>(null)
  const [step, setStep] = useState(0)
  const [phase, setPhase] = useState<Phase>('loading')
  const [paused, setPaused] = useState(false)
  const [speed, setSpeed] = useState(1)
  const [error, setError] = useState<string | null>(null)
  const [runId, setRunId] = useState(0)
  const voiceAvailable = useMirror((s) => s.voiceAvailable)
  const ctl = useRef({ paused: false, skip: false, speed: 1, audio: null as HTMLAudioElement | null })

  useEffect(() => {
    const run: Run = { cancelled: false }
    const c = ctl.current
    const store = useMirror.getState
    store().setReplaying(true)

    const check = () => {
      if (run.cancelled) throw new Cancelled()
    }

    const wait = (ms: number) =>
      new Promise<void>((resolve, reject) => {
        let left = ms
        const t = window.setInterval(() => {
          if (run.cancelled) {
            window.clearInterval(t)
            reject(new Cancelled())
          } else if (c.skip || left <= 0) {
            window.clearInterval(t)
            resolve()
          } else if (!c.paused) left -= 50 * c.speed
        }, 50)
      })

    const clips = new Map<string, HTMLAudioElement>()
    const clip = (name: string) => {
      let a = clips.get(name)
      if (!a) {
        a = new Audio(`/replay/${name}`)
        a.preload = 'auto'
        clips.set(name, a)
      }
      return a
    }

    // `seconds` is the clip's recorded length: if a browser never reports the end of playback
    // (or cannot decode the file), the replay moves on instead of waiting forever.
    const play = (name: string | null, seconds?: number | null) =>
      new Promise<void>((resolve, reject) => {
        if (!name || c.skip) return resolve()
        const a = clip(name)
        a.currentTime = 0
        a.playbackRate = c.speed
        c.audio = a
        const done = () => {
          window.clearInterval(t)
          a.onended = null
          c.audio = null
          resolve()
        }
        const limit = ((seconds ?? 30) + 2.5) * 1000
        let played = 0
        const t = window.setInterval(() => {
          if (!c.paused) played += 50 * c.speed
          if (run.cancelled) {
            a.pause()
            window.clearInterval(t)
            c.audio = null
            reject(new Cancelled())
          } else if (c.skip || played > limit) {
            a.pause()
            done()
          }
        }, 50)
        a.onended = done
        a.onerror = done
        if (!c.paused) a.play().catch(() => wait(2500).then(done, done))
      })

    // Keep the part of the screen that changed in view: futures while deciding, the real world once it moved.
    const behavior = (): ScrollBehavior => (window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth')
    const reveal = (label: string) => document.querySelector(`[aria-label="${label}"]`)?.scrollIntoView({ behavior: behavior(), block: 'start' })
    // The top of the centre column holds the reality alert, then the intent and the futures.
    const revealTop = () => document.querySelector('[aria-label="Possible futures"]')?.closest('.overflow-y-auto')?.scrollTo({ top: 0, behavior: behavior() })

    const doStep = async (s: ReplayStep) => {
      const st = store()
      if (s.arm_fault) await st.armFault(s.arm_fault)
      check()
      if (s.kind === 'turn') {
        setPhase('user')
        st.replayLine('user', s.heard ?? s.said)
        await play(s.user_audio, s.user_s)
        check()
        setPhase('runtime')
        await Promise.all([store().replayTurn(s.heard ?? s.said), wait(900)])
        const txn = store().txn
        if (txn && txn.committed_version !== null) reveal('Real world')
        else revealTop()
      } else {
        setPhase('world')
        await store().externalChange()
        revealTop()
        await wait(1400)
      }
      check()
      setPhase('agent')
      for (const line of s.agent_said) store().replayLine('agent', line)
      await play(s.agent_audio, s.agent_s)
      await wait(800)
    }

    ;(async () => {
      try {
        setPhase('loading')
        setError(null)
        setStep(0)
        const m = await replayManifest()
        check()
        setManifest(m)
        m.steps.forEach((s) => [s.user_audio, s.agent_audio].forEach((n) => n && clip(n)))
        const st = store()
        if (!st.demoMode) throw new Error('The replay resets the demo world, and demo mode is off on this server.')
        if (st.voice === 'live' || st.voice === 'starting') await st.toggleVoice()
        await st.resetDemo()
        // The reset clears the conversation when its event arrives; start after that.
        for (let i = 0; i < 30 && !useMirror.getState().lines.some((l) => l.text.startsWith('Demo reset')); i++) await wait(50)
        check()
        for (let i = 0; i < m.steps.length; i++) {
          c.skip = false
          setStep(i)
          await doStep(m.steps[i])
          check()
        }
        setPhase('done')
      } catch (err) {
        if (!(err instanceof Cancelled)) setError(err instanceof Error ? err.message : String(err))
      }
    })()

    return () => {
      run.cancelled = true
      c.audio?.pause()
      clips.forEach((a) => a.pause())
      useMirror.getState().setReplaying(false)
    }
  }, [runId])

  const togglePause = () => {
    const c = ctl.current
    c.paused = !c.paused
    setPaused(c.paused)
    if (c.audio) void (c.paused ? c.audio.pause() : c.audio.play().catch(() => undefined))
  }
  const next = () => {
    ctl.current.skip = true
  }
  const cycleSpeed = () => {
    const s = ctl.current.speed === 1 ? 1.25 : ctl.current.speed === 1.25 ? 1.5 : 1
    ctl.current.speed = s
    setSpeed(s)
    if (ctl.current.audio) ctl.current.audio.playbackRate = s
  }
  const again = () => {
    ctl.current.paused = false
    setPaused(false)
    setRunId((n) => n + 1)
  }

  const s = manifest?.steps[step]
  const total = manifest?.steps.length ?? 8
  const done = phase === 'done'

  return (
    <div className="pointer-events-none fixed inset-x-0 bottom-0 z-40 flex justify-center px-3 pb-3 sm:px-4 sm:pb-4">
      <section
        aria-label="Replay"
        className="pointer-events-auto w-full max-w-215 rounded-xl border-[1.5px] border-ink bg-glass px-4 py-3 shadow-[0_24px_60px_-24px_rgba(26,35,48,0.55)] sm:px-5"
      >
        <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
          <ol className="flex flex-1 gap-1" aria-label={`Step ${step + 1} of ${total}`}>
            {Array.from({ length: total }, (_, i) => (
              <li
                key={i}
                className={`h-1.5 flex-1 rounded-full ${done || i < step ? 'bg-ink' : i === step ? 'bg-shadow' : 'bg-rule'}`}
              />
            ))}
          </ol>
          <span className="tabular text-[12px] text-ink-soft" aria-live="polite">
            {error ? 'Replay stopped' : done ? PHASE_WORD.done : `Step ${step + 1} of ${total}: ${PHASE_WORD[phase]}`}
          </span>
        </div>

        {error ? (
          <p className="mt-2 text-[14px] text-blocked" role="alert">
            {error}
          </p>
        ) : done ? (
          <p className="mt-2 text-[15px] leading-snug text-ink">
            That was one live AssemblyAI session. Every future, check and number you saw was recomputed by this server while you
            watched.
          </p>
        ) : (
          <p className="mt-2 min-h-[2.6em] text-[15px] leading-snug text-ink">{CAPTIONS[step] ?? ''}</p>
        )}

        {s && !done && !error && (
          <p className="mt-1.5 flex flex-wrap gap-x-4 gap-y-0.5 text-[12px] text-ink-soft">
            {s.kind === 'turn' ? (
              <span>
                AssemblyAI heard <span className="font-semibold text-ink">“{s.heard}”</span>
              </span>
            ) : (
              <span>No one spoke. The agent was told by the runtime.</span>
            )}
            {s.tool_calls.length > 0 && <span>Agent called {s.tool_calls.join(', ')}</span>}
            {s.agent_offset_s != null && <span className="tabular">Reply began {s.agent_offset_s.toFixed(1)} s after the user stopped</span>}
          </p>
        )}

        <div className="mt-3 flex flex-wrap items-center gap-2">
          {done || error ? (
            <>
              <button onClick={onExit} className="rounded-full bg-ink px-4 py-1.5 text-[13px] font-semibold text-glass hover:opacity-90">
                Try it yourself
              </button>
              {voiceAvailable && (
                <button
                  onClick={() => {
                    onExit()
                    void useMirror.getState().toggleVoice()
                  }}
                  className="rounded-full border border-ink px-4 py-1.5 text-[13px] font-semibold text-ink hover:bg-ink hover:text-glass"
                >
                  Talk to it
                </button>
              )}
              <button onClick={again} className="rounded-full px-3 py-1.5 text-[13px] font-semibold text-ink-soft hover:text-ink">
                Watch again
              </button>
            </>
          ) : (
            <>
              <button
                onClick={togglePause}
                disabled={phase === 'loading'}
                className="rounded-full border border-ink px-4 py-1.5 text-[13px] font-semibold text-ink hover:bg-ink hover:text-glass disabled:opacity-40"
              >
                {paused ? 'Resume' : 'Pause'}
              </button>
              <button
                onClick={next}
                disabled={phase === 'loading'}
                className="rounded-full border border-rule px-4 py-1.5 text-[13px] font-semibold text-ink-soft hover:border-ink hover:text-ink disabled:opacity-40"
              >
                Next step
              </button>
              <button
                onClick={cycleSpeed}
                title="Playback speed"
                className="tabular rounded-full border border-rule px-3 py-1.5 text-[13px] font-semibold text-ink-soft hover:border-ink hover:text-ink"
              >
                {speed}×
              </button>
              <button onClick={onExit} className="ml-auto rounded-full px-3 py-1.5 text-[13px] font-semibold text-ink-soft hover:text-ink">
                Exit replay
              </button>
            </>
          )}
        </div>
        {manifest && (
          <p className="mt-2 border-t border-rule pt-2 text-[11px] leading-snug text-ink-faint">
            Recorded {manifest.recorded_at} with the {manifest.agent_voice}; the user’s side is {manifest.user_voice}. Pauses
            between turns are shortened.
          </p>
        )}
      </section>
    </div>
  )
}
