import { useMirror } from '../store'

const ms = (v: number | null | undefined) => (v === null || v === undefined ? '—' : `${v < 10 ? v.toFixed(2) : Math.round(v)} ms`)

/** Only values the backend measured. Nothing here is estimated. */
export function MetricsPanel() {
  const m = useMirror((s) => s.metrics)
  if (!m) return null
  const rows: [string, string | number][] = [
    ['Futures simulated', m.branches_explored],
    ['Clarifications', m.clarifications],
    ['What-if runs', m.what_if_simulations],
    ['Versions', m.commit_versions],
    ['Commits', m.commits],
    ['Rollbacks', m.rollbacks],
    ['Simulation', ms(m.simulation_latency_ms)],
    ['Execution', ms(m.execution_latency_ms)],
    ['Verification', ms(m.verification_latency_ms)],
  ]
  if (m.voice) {
    rows.push(['Transcript to reply audio', ms(m.voice.time_to_first_audio_ms)])
    rows.push(['Barge-ins', m.voice.interruptions])
  }
  return (
    <section className="px-4 pt-3 pb-4" aria-label="Runtime metrics">
      <h2 className="text-[15px] font-semibold">This session, measured</h2>
      <dl className="mt-1.5 grid grid-cols-2 gap-x-4 gap-y-0.5">
        {rows.map(([k, v]) => (
          <div key={k} className="flex items-baseline justify-between gap-2 border-b border-rule/70 py-0.5 text-[12px]">
            <dt className="text-ink-soft">{k}</dt>
            <dd className="tabular font-semibold">{v}</dd>
          </div>
        ))}
      </dl>
    </section>
  )
}
