import { currentVersion, locationName, shortHash } from '../lib/format'
import type { Transaction } from '../lib/types'
import { useMirror } from '../store'

interface Status {
  headline: string
  detail: string
  tone: 'neutral' | 'active' | 'good' | 'bad'
}

function statusOf(txn: Transaction | null): Status {
  if (!txn) return { headline: 'Real world changed: No', detail: 'No transaction open. The ERP has not been touched.', tone: 'neutral' }
  const last = txn.executions[txn.executions.length - 1]
  switch (txn.state) {
    case 'COMMIT_ARMED':
    case 'EXECUTING':
      return { headline: 'Real world changed: Yes', detail: 'Executing in the ERP now.', tone: 'active' }
    case 'VERIFYING':
      return txn.reality === 'UNKNOWN'
        ? { headline: 'Reality unknown', detail: 'The ERP could not be read. Nothing is claimed until it can.', tone: 'bad' }
        : { headline: 'Real world changed: Yes', detail: 'Reading the ERP back to compare with the prediction.', tone: 'active' }
    case 'DIVERGED':
      return { headline: 'Reality diverged: Yes', detail: 'Expected ≠ actual. Not marked verified. Rollback available.', tone: 'bad' }
    case 'ROLLBACK':
      return { headline: 'Rolling back', detail: 'Compensating from what actually happened, then verifying.', tone: 'active' }
    case 'VERIFIED':
      return txn.reality === 'RESTORED'
        ? { headline: 'Reality restored: Yes', detail: 'Rollback executed, then verified against the ERP.', tone: 'good' }
        : { headline: 'Reality verified: Yes', detail: 'The ERP now matches the prediction, cell for cell.', tone: 'good' }
    case 'FAILED':
      return last?.verification === 'NOT_APPLIED'
        ? { headline: 'Real world changed: No', detail: 'The ERP did not execute. Confirmed by reading it back.', tone: 'neutral' }
        : { headline: 'Reality diverged: Yes', detail: 'The operation failed and reality does not match. Manual review required.', tone: 'bad' }
    case 'ABORTED':
      return { headline: 'Real world changed: No', detail: 'Cancelled before commit.', tone: 'neutral' }
    default:
      return {
        headline: 'Real world changed: No',
        detail: `Everything above is simulation. Writes to the ERP by this transaction: ${txn.live_writes}.`,
        tone: 'neutral',
      }
  }
}

const TONE = {
  neutral: { line: 'bg-ink', text: 'text-ink' },
  active: { line: 'bg-warn', text: 'text-warn' },
  good: { line: 'bg-valid', text: 'text-valid' },
  bad: { line: 'bg-blocked', text: 'text-blocked' },
}

/** The mirror: shadow futures above, reality below. Status of reality sits on the boundary. */
export function MirrorLine() {
  const txn = useMirror((s) => s.txn)
  const world = useMirror((s) => s.world)
  const busy = useMirror((s) => s.busy)
  const commit = useMirror((s) => s.commit)
  const rollback = useMirror((s) => s.rollback)
  const abort = useMirror((s) => s.abort)
  const reverify = useMirror((s) => s.reverify)
  const st = statusOf(txn)
  const tone = TONE[st.tone]

  const ver = txn ? currentVersion(txn.versions, txn.current_version) : undefined
  const intent = ver?.branches.length === 1 ? ver.branches[0].intent : undefined
  const rolledBack = txn?.executions.some((e) => e.kind === 'ROLLBACK' && e.verification === 'VERIFIED')
  const canRollback = txn && (txn.state === 'DIVERGED' || (txn.state === 'VERIFIED' && !rolledBack))
  const canCancel = txn && ['CLARIFYING', 'CONSEQUENCE_REVIEW', 'PREVIEW_READY'].includes(txn.state)

  return (
    <div className="relative py-3" role="status" aria-live="polite">
      <div className={`absolute inset-x-0 top-1/2 h-[3px] -translate-y-1/2 ${tone.line}`} aria-hidden />
      <div className="relative mx-6 flex flex-wrap items-center gap-x-5 gap-y-2 rounded-md border border-ink bg-glass px-4 py-2.5">
        <div key={st.headline} className={`line-pulse min-w-76 flex-1 rounded ${tone.text}`}>
          <p className="num whitespace-nowrap text-[21px] font-bold leading-none">{st.headline}</p>
          <p className="mt-1 text-[13px] text-ink-soft">{st.detail}</p>
        </div>

        <div className="ml-auto flex shrink-0 flex-wrap items-center gap-2">
          {txn?.commit && intent && (
            <div className="text-right">
              <button
                onClick={() => void commit()}
                disabled={busy}
                className="rounded-md bg-ink px-4 py-2 text-[14px] font-semibold text-glass hover:opacity-90 disabled:opacity-40"
              >
                Commit {intent.quantity} {intent.product_id} to {locationName(world, intent.destination_id, false)}
              </button>
              <p className="tabular mt-1 text-[11px] text-ink-faint" title="The commit token is an HMAC over these values">
                Bound to v{txn.commit.version}, evidence {shortHash(txn.commit.evidence_hash)}, simulation {shortHash(txn.commit.simulation_hash)}
              </p>
            </div>
          )}
          {canRollback && (
            <button
              onClick={() => void rollback()}
              disabled={busy}
              className="rounded-md bg-blocked px-4 py-2 text-[14px] font-semibold text-glass hover:opacity-90 disabled:opacity-40"
            >
              Roll back and verify
            </button>
          )}
          {txn?.state === 'VERIFYING' && (
            <button onClick={() => void reverify()} className="rounded-md border border-ink px-3 py-2 text-[13px] font-semibold">
              Read reality again
            </button>
          )}
          {canCancel && (
            <button onClick={() => void abort()} disabled={busy} className="rounded-md px-3 py-2 text-[13px] text-ink-soft hover:text-ink">
              Cancel request
            </button>
          )}
        </div>
      </div>
    </div>
  )
}
