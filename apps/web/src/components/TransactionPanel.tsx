import { KIND_WORD, OUTCOME_COLOR, STATE_WORD, locationName, primaryReason, sentenceCase } from '../lib/format'
import type { TxState, Version } from '../lib/types'
import { useMirror } from '../store'

const LIFECYCLE: { label: string; states: TxState[] }[] = [
  { label: 'Interpret', states: ['INTERPRETING'] },
  { label: 'Fork', states: ['FORKING'] },
  { label: 'Simulate', states: ['SIMULATING', 'WHAT_IF', 'CONSEQUENCE_REVIEW'] },
  { label: 'Clarify', states: ['CLARIFYING'] },
  { label: 'Preview', states: ['PREVIEW_READY'] },
  { label: 'Commit', states: ['COMMIT_ARMED', 'EXECUTING'] },
  { label: 'Verify', states: ['VERIFYING', 'VERIFIED', 'DIVERGED'] },
  { label: 'Recover', states: ['ROLLBACK'] },
]

export function TransactionPanel() {
  const txn = useMirror((s) => s.txn)
  const world = useMirror((s) => s.world)
  const voiceTools = useMirror((s) => s.voiceTools)
  const voice = useMirror((s) => s.voice)

  if (!txn) {
    return (
      <section className="px-4 pt-3" aria-label="Transaction">
        <h2 className="text-[15px] font-semibold">Transaction</h2>
        <p className="mt-2 text-[13px] text-ink-soft">A spoken or typed instruction becomes a transaction here: versioned, simulated, and only then committable.</p>
      </section>
    )
  }

  const reached = new Set<TxState>(txn.versions.flatMap((v) => v.state_trail))
  reached.add(txn.state)
  for (const e of txn.executions) {
    reached.add('EXECUTING')
    if (e.kind === 'ROLLBACK') reached.add('ROLLBACK')
  }
  const bad = txn.state === 'DIVERGED' || txn.state === 'FAILED'

  return (
    <section className="px-4 pt-3" aria-label="Transaction">
      <div className="flex items-baseline justify-between">
        <h2 className="text-[15px] font-semibold">Transaction</h2>
        <span className="tabular text-[11px] text-ink-faint">{txn.id}</span>
      </div>
      <p className={`num mt-1 text-[22px] font-bold leading-none ${bad ? 'text-blocked' : txn.state === 'VERIFIED' ? 'text-valid' : 'text-ink'}`}>
        {STATE_WORD[txn.state]}
      </p>
      <ol className="mt-2.5 flex flex-wrap gap-1" aria-label="Lifecycle">
        {LIFECYCLE.map((step) => {
          const on = step.states.includes(txn.state)
          const done = step.states.some((s) => reached.has(s))
          return (
            <li
              key={step.label}
              className={`rounded px-1.5 py-0.5 text-[11px] font-medium ${
                on ? 'bg-ink text-glass' : done ? 'bg-silver text-ink' : 'text-ink-faint'
              }`}
            >
              {step.label}
            </li>
          )
        })}
      </ol>

      <h3 className="mt-4 text-[13px] font-semibold">Versions</h3>
      <ol className="mt-1.5 border-l border-ink pl-3">
        {txn.versions.map((v) => (
          <VersionRow key={v.version} v={v} world={world} current={v.version === txn.current_version} />
        ))}
      </ol>

      <Evidence />

      {voice === 'live' && voiceTools.length > 0 && (
        <p className="mt-3 text-[11px] leading-snug text-ink-faint">
          Tools the voice agent can call now: {voiceTools.join(', ')}.
        </p>
      )}
    </section>
  )
}

function VersionRow({ v, world, current }: { v: Version; world: ReturnType<typeof useMirror.getState>['world']; current: boolean }) {
  const single = v.branches.length === 1 ? v.branches[0] : undefined
  const reason = single ? primaryReason(single) : null
  return (
    <li className={`relative py-1 ${v.superseded ? 'opacity-60' : ''}`}>
      <span className={`absolute -left-[17px] top-[11px] h-2 w-2 rounded-full border border-ink ${current ? 'bg-ink' : 'bg-glass'}`} aria-hidden />
      <p className="text-[13px] leading-snug">
        <span className="num font-semibold">v{v.version}</span>{' '}
        <span className="text-ink-soft">{KIND_WORD[v.kind]}</span>
        {v.committed && <span className="ml-1.5 rounded bg-ink px-1 text-[10px] font-semibold text-glass">committed</span>}
      </p>
      {v.branches.length === 0 ? (
        <p className="text-[12px] text-ink-faint">Waiting for a missing field</p>
      ) : (
        v.branches.map((b) => (
          <p key={b.key} className="tabular text-[12px] leading-snug">
            {b.intent.quantity} to {locationName(world, b.intent.destination_id, false)}
            {b.intent.source_id !== 'MUMBAI-DC' && ` from ${locationName(world, b.intent.source_id)}`}{' '}
            <span className={OUTCOME_COLOR[b.outcome]}>
              {b.outcome === 'BLOCKED' ? '✕' : '✓'}
              {v.branches.length === 1 && reason ? ` ${reason.toLowerCase()}` : ''}
            </span>
          </p>
        ))
      )}
    </li>
  )
}

function Evidence() {
  const txn = useMirror((s) => s.txn)
  const world = useMirror((s) => s.world)
  if (!txn || txn.evidence.length === 0) return null
  const rows = txn.evidence.filter((e) => e.source.type !== 'NONE')
  return (
    <>
      <h3 className="mt-4 text-[13px] font-semibold">Evidence</h3>
      <ul className="mt-1 space-y-0.5">
        {rows.slice(-8).map((e) => (
          <li key={e.evidence_id} className={`text-[12px] leading-snug ${e.lifecycle === 'SUPERSEDED' ? 'text-ink-faint line-through' : ''}`}>
            <span className="text-ink-soft">{e.field}</span>{' '}
            <span className="font-medium">
              {e.candidates.map((c) => (e.field === 'source' || e.field === 'destination' ? locationName(world, String(c)) : c)).join(' or ')}
            </span>{' '}
            <span className="text-ink-faint">
              {sentenceCase(e.status)}, {sentenceCase(e.source.type)}
              {e.source.text_span ? ` “${e.source.text_span}”` : ''}
            </span>
          </li>
        ))}
      </ul>
    </>
  )
}
