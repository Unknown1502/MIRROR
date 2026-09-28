import { currentVersion, locationName } from '../lib/format'
import type { EvidenceField, World } from '../lib/types'
import { useMirror } from '../store'

const FIELD_LABEL: Record<string, string> = { product: 'Product', quantity: 'Quantity', source: 'From', destination: 'To' }
const STATUS_STYLE: Record<EvidenceField['status'], string> = {
  EXPLICIT: 'text-ink',
  RESOLVED: 'text-ink',
  INFERRED: 'text-shadow',
  UNKNOWN: 'text-ink-faint',
}
const STATUS_WORD: Record<EvidenceField['status'], string> = {
  EXPLICIT: 'said',
  RESOLVED: 'resolved',
  INFERRED: 'guess',
  UNKNOWN: 'missing',
}

function display(f: EvidenceField, world: World | null): string {
  if (f.candidates.length === 0) return 'not stated'
  if (f.field === 'source' || f.field === 'destination') {
    return f.candidates.map((c) => locationName(world, String(c), false)).join(' or ')
  }
  return f.candidates.join(' or ')
}

/** Spoken world -> intent world: what was said, and what the runtime can prove about each field. */
export function IntentStrip() {
  const txn = useMirror((s) => s.txn)
  const world = useMirror((s) => s.world)
  const ver = txn ? currentVersion(txn.versions, txn.current_version) : undefined
  if (!ver) return null
  const drafts = ver.intent.drafts
  const fields = ['product', 'quantity', 'source', 'destination'] as const

  return (
    <section className="flex flex-wrap items-start gap-x-8 gap-y-3 px-1" aria-label="Spoken and interpreted intent">
      <div className="min-w-[220px] max-w-[46ch] flex-1">
        {ver.kind === 'RESIMULATION' ? (
          <>
            <p className="label">Re-simulated after the ERP changed</p>
            <p className="text-[15px] leading-snug first-letter:uppercase">{ver.label.replace(/^Re-simulated:\s*/, '')}</p>
          </>
        ) : (
          <>
            <p className="label">Spoken</p>
            <p className="text-[15px] leading-snug">“{ver.label}”</p>
          </>
        )}
      </div>
      <div className="min-w-[260px] flex-[2]">
        <p className="label">
          Interpreted{drafts.length > 1 ? `, ${drafts.length} readings of which field changed` : ''}
        </p>
        <dl className="mt-0.5 flex flex-wrap gap-x-6 gap-y-1">
          {fields.map((name) => {
            const f = drafts[0][name]
            const values = drafts.length > 1 ? drafts.map((d) => display(d[name], world)) : [display(f, world)]
            const same = values.every((v) => v === values[0])
            const status = f.ambiguous ? 'INFERRED' : f.status
            return (
              <div key={name}>
                <dt className="text-[12px] text-ink-soft">{FIELD_LABEL[name]}</dt>
                <dd className={`text-[15px] font-semibold ${STATUS_STYLE[status]}`}>
                  {same ? values[0] : values.join(' | ')}
                  <span
                    className="ml-1.5 text-[11px] font-medium text-ink-faint"
                    title={`${f.status}: ${f.note}${f.source.text_span ? ` (from “${f.source.text_span}”)` : ''}`}
                  >
                    {f.ambiguous ? `${f.candidates.length} candidates` : STATUS_WORD[f.status]}
                  </span>
                </dd>
              </div>
            )
          })}
        </dl>
      </div>
    </section>
  )
}
