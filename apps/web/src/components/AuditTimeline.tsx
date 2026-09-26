import { sentenceCase, shortHash } from '../lib/format'
import { useMirror } from '../store'

const QUIET = new Set(['STATE_CHANGED', 'SIMULATION_STARTED', 'CONSEQUENCES_UPDATED', 'TOOL_RESULT_SENT'])

export function AuditTimeline() {
  const audit = useMirror((s) => s.audit)
  const chain = useMirror((s) => s.chain)
  const verifyChain = useMirror((s) => s.verifyChain)
  const events = audit.filter((e) => !QUIET.has(e.event_type)).slice(-14).reverse()

  return (
    <section className="px-4 pt-3 pb-2" aria-label="Audit timeline">
      <div className="flex items-baseline justify-between">
        <h2 className="text-[15px] font-semibold">Audit ledger</h2>
        <button onClick={() => void verifyChain()} className="text-[12px] font-semibold text-ink underline decoration-rule underline-offset-2 hover:decoration-ink">
          Verify hash chain
        </button>
      </div>
      {chain && (
        <p className={`mt-0.5 text-[12px] ${chain.valid ? 'text-valid' : 'text-blocked'}`}>
          {chain.valid ? `Intact: ${chain.length} events, head ${shortHash(chain.head_hash)}` : `Broken at event ${chain.first_invalid_sequence}: ${chain.reason}`}
        </p>
      )}
      {events.length === 0 ? (
        <p className="mt-2 text-[12px] text-ink-soft">Every interpretation, simulation, commit and verification is appended here, hash-chained.</p>
      ) : (
        <ol className="mt-2 space-y-1">
          {events.map((e) => (
            <li key={e.sequence} className="grid grid-cols-[auto_1fr_auto] items-baseline gap-x-2 text-[12px]">
              <span className="tabular text-ink-faint">{e.sequence}</span>
              <span className={eventTone(e.event_type)}>
                {sentenceCase(e.event_type)}
                {e.transaction_version ? <span className="text-ink-faint"> v{e.transaction_version}</span> : null}
              </span>
              <span className="tabular text-[11px] text-ink-faint" title={`hash ${e.hash}\nprevious ${e.previous_hash}`}>
                {shortHash(e.hash)}
              </span>
            </li>
          ))}
        </ol>
      )}
    </section>
  )
}

function eventTone(type: string): string {
  if (['REALITY_DIVERGED', 'ERROR', 'TOOL_CALL_DISCARDED', 'STALE_TRANSACTION'].includes(type)) return 'text-blocked'
  if (['VERIFIED', 'ROLLBACK_COMPLETED'].includes(type)) return 'text-valid'
  if (['COMMIT_TOKEN_INVALIDATED', 'REPLY_INTERRUPTED', 'EXECUTION_RETRY'].includes(type)) return 'text-warn'
  return 'text-ink'
}
