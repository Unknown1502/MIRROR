import { useMirror } from '../store'

const STATUS_WORD: Record<string, string> = {
  VALID: 'valid',
  WARNING: 'valid with a warning',
  BLOCKED: 'blocked',
  NEEDS_CLARIFICATION: 'waiting on a question',
}

export function RealityBanner() {
  const alert = useMirror((s) => s.realityAlert)
  const dismiss = useMirror((s) => s.dismissReality)
  if (!alert) return null
  const was = STATUS_WORD[alert.previous_status ?? ''] ?? alert.previous_status?.toLowerCase()
  const now = STATUS_WORD[alert.new_status] ?? alert.new_status.toLowerCase()
  const changed = alert.previous_status && alert.previous_status !== alert.new_status
  return (
    <section
      role="alert"
      aria-label="Reality changed"
      className="flex items-start gap-4 rounded-lg border-[1.5px] border-warn bg-warn-soft px-4 py-3"
    >
      <div className="min-w-0 flex-1">
        <p className="text-[14px] font-semibold text-ink">Reality changed under your preview</p>
        <p className="mt-0.5 text-[13px] leading-snug text-ink-soft">
          {alert.cause}. MIRROR re-simulated it as v{alert.new_version}:{' '}
          <span className="font-semibold text-ink">{changed ? `was ${was}, now ${now}` : `still ${now}`}</span>. The old
          commit token no longer works.
        </p>
      </div>
      <button onClick={dismiss} className="shrink-0 text-[12px] font-semibold text-ink-soft hover:text-ink">
        Dismiss
      </button>
    </section>
  )
}
