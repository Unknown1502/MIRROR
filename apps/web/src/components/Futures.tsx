import { KIND_WORD, OUTCOME_COLOR, OUTCOME_WORD, currentVersion, locationName, primaryReason, shortHash } from '../lib/format'
import type { Branch, Comparison, Version, World } from '../lib/types'
import { useMirror } from '../store'
import { Gauge } from './Gauge'

export function Futures() {
  const txn = useMirror((s) => s.txn)
  const world = useMirror((s) => s.world)
  const busy = useMirror((s) => s.busy || s.replaying) // the replay owns the screen while it runs
  const selectFuture = useMirror((s) => s.selectFuture)

  const ver = txn ? currentVersion(txn.versions, txn.current_version) : undefined
  const clarifying = txn?.state === 'CLARIFYING'
  const showCompare = Boolean(
    txn?.comparison && txn.comparison.version === txn.current_version && ['CONSEQUENCE_REVIEW', 'PREVIEW_READY'].includes(txn.state),
  )

  return (
    <section className="shadow-pane flex flex-col rounded-lg px-5 pt-4 pb-5" aria-label="Possible futures">
      <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1">
        <h2 className="text-[17px] font-semibold text-shadow">Possible futures</h2>
        <p className="text-[12px] text-ink-soft">
          {!ver
            ? 'Simulations appear here. Nothing in this area ever touches the ERP.'
            : txn!.committed_version === ver.version
              ? `v${ver.version} was committed. This is what MIRROR predicted; the real world below is what the ERP holds.`
              : `v${ver.version} ${KIND_WORD[ver.kind].toLowerCase()}, simulated on ERP revision ${ver.world_revision}. Nothing here has happened.`}
        </p>
      </div>

      {!ver && <EmptyFutures />}

      {ver && clarifying && ver.clarification && <Question version={ver} />}

      {ver && ver.branches.length === 0 && !clarifying && (
        <p className="mt-4 text-[14px] text-ink-soft">This version could not be simulated yet.</p>
      )}

      {ver && ver.branches.length > 0 && (
        <div
          key={`${txn!.id}-${ver.version}`}
          className="mt-4 grid gap-4"
          style={{
            gridTemplateColumns:
              ver.branches.length === 1
                ? showCompare
                  ? 'minmax(0, 1fr) minmax(0, 1fr)'
                  : 'minmax(0, 620px)'
                : `repeat(${Math.min(ver.branches.length, 3)}, minmax(0, 1fr))`,
          }}
        >
          {ver.branches.map((b) => (
            <FutureCard
              key={b.branch_id}
              branch={b}
              world={world}
              version={ver}
              multi={ver.branches.length > 1}
              onChoose={clarifying ? () => void selectFuture(b.key) : undefined}
              disabled={busy}
            />
          ))}
          {showCompare && ver.branches.length === 1 && <ComparisonTable cmp={txn!.comparison!} world={world} />}
        </div>
      )}

      {ver?.extra?.optimization && <OptimizationStrip opt={ver.extra.optimization} />}
      {showCompare && ver && ver.branches.length > 1 && <ComparisonTable cmp={txn!.comparison!} world={world} />}
    </section>
  )
}

function EmptyFutures() {
  return (
    <div className="mt-6 grid grid-cols-3 gap-4" aria-hidden>
      {['A', 'B', 'C'].map((k) => (
        <div key={k} className="h-40 rounded-md border border-dashed border-shadow/30" />
      ))}
    </div>
  )
}

function Question({ version }: { version: Version }) {
  const c = version.clarification!
  const top = c.scores[0]
  return (
    <div className="mt-3 max-w-[70ch]">
      <p className="text-[18px] font-medium leading-snug text-ink">{c.question}</p>
      {top && (
        <p className="mt-1 text-[12px] text-ink-soft">
          Asked because {top.field} has the highest decision impact: ambiguity {top.ambiguity} × consequence divergence{' '}
          {top.consequence_divergence} × risk {top.risk_weight} = {top.score}
          {c.scores.length > 1 && `, ahead of ${c.scores.slice(1).map((s) => `${s.field} ${s.score}`).join(', ')}`}.
        </p>
      )}
    </div>
  )
}

function FutureCard({
  branch,
  world,
  version,
  multi,
  onChoose,
  disabled,
}: {
  branch: Branch
  world: World | null
  version: Version
  multi: boolean
  onChoose?: () => void
  disabled: boolean
}) {
  const i = branch.intent
  const sim = branch.simulation
  const reason = primaryReason(branch)
  const title = multi ? distinguishing(branch, version, world) : `${locationName(world, i.source_id)} to ${locationName(world, i.destination_id)}`
  const scaleMax = Math.max(1, ...(sim?.effects ?? []).map((e) => Math.max(e.capacity, e.after, e.before)))
  const edge = branch.outcome === 'BLOCKED' ? 'border-blocked' : branch.outcome === 'WARNING' ? 'border-warn' : 'border-valid'

  return (
    <article className={`future-enter flex flex-col rounded-md border-t-4 bg-glass/90 px-4 pt-3 pb-4 ${edge}`}>
      <div className="flex items-baseline justify-between gap-2">
        <p className="text-[12px] font-semibold text-shadow">
          {multi ? `Future ${branch.key.toUpperCase()}` : `${KIND_WORD[version.kind]}, v${version.version}`}
        </p>
        <p className="tabular text-[11px] text-ink-faint" title="Simulation result hash">
          sim {shortHash(sim?.result_hash)}
        </p>
      </div>
      <h3 className="mt-0.5 text-[19px] font-semibold leading-tight">{title}</h3>
      <p className="text-[13px] text-ink-soft">
        {i.quantity} {i.product_id} from {locationName(world, i.source_id)}
      </p>

      <div className="mt-3 space-y-3">
        {(sim?.effects ?? []).map((e) => (
            <div key={e.location_id}>
              <div className="flex items-baseline justify-between">
                <span className="text-[12px] text-ink-soft">{locationName(world, e.location_id)}</span>
                <span className="text-[11px] text-ink-faint tabular">
                  {e.location_id === i.destination_id ? `capacity ${e.capacity}` : `safety ${e.safety_stock}`}
                </span>
              </div>
              <p className="num text-[24px] font-semibold leading-none">
                <span className="text-ink-faint">{e.before}</span>
                <span className="mx-1.5 text-[18px] text-ink-faint">→</span>
                <span className={e.after > e.capacity || e.after < e.safety_stock ? 'text-blocked' : 'text-ink'}>{e.after}</span>
              </p>
              <div className="mt-1.5">
                <Gauge before={e.before} value={e.after} capacity={e.capacity} safety={e.safety_stock} tone="shadow" scaleMax={scaleMax} />
              </div>
            </div>
          ))}
      </div>

      <div className="mt-4 flex items-end justify-between gap-3">
        <div>
          <p className={`num text-[22px] font-bold leading-none ${OUTCOME_COLOR[branch.outcome]}`}>
            {branch.outcome === 'BLOCKED' ? '✕ ' : '✓ '}
            {OUTCOME_WORD[branch.outcome]}
          </p>
          {reason && <p className={`mt-1 text-[13px] ${OUTCOME_COLOR[branch.outcome]}`}>{reason}</p>}
          {sim?.transport && (
            <p className="mt-1 text-[12px] text-ink-faint">
              {sim.transport.mode.toLowerCase()}, {sim.transport.lead_time_hours} h, est. cost {sim.transport.estimated_cost.toLocaleString()}
            </p>
          )}
        </div>
        {onChoose && (
          <button
            onClick={onChoose}
            disabled={disabled}
            className="shrink-0 rounded-md border border-shadow px-3 py-1.5 text-[13px] font-semibold text-shadow hover:bg-shadow hover:text-glass disabled:opacity-40"
          >
            Choose this future
          </button>
        )}
      </div>
    </article>
  )
}

function distinguishing(branch: Branch, version: Version, world: World | null): string {
  const others = version.branches.filter((b) => b.key !== branch.key)
  const i = branch.intent
  if (others.some((o) => o.intent.destination_id !== i.destination_id) && others.every((o) => o.intent.source_id === i.source_id)) {
    return locationName(world, i.destination_id, false)
  }
  if (others.some((o) => o.intent.product_id !== i.product_id)) return i.product_id
  return `${locationName(world, i.source_id)} to ${locationName(world, i.destination_id)}`
}

function OptimizationStrip({ opt }: { opt: NonNullable<NonNullable<Version['extra']>['optimization']> }) {
  return (
    <div className="mt-4 flex flex-wrap items-center gap-x-5 gap-y-2 border-t border-dashed border-shadow/40 pt-3">
      <p className="text-[13px] text-ink-soft">
        Searched {opt.evaluated} quantities in {opt.latency_ms} ms. Largest safe transfer:{' '}
        <span className="num font-semibold text-ink">{opt.best_quantity}</span>
      </p>
      <ol className="flex gap-1.5">
        {opt.probes.map((p) => (
          <li
            key={p.quantity}
            className={`num rounded px-2 py-0.5 text-[13px] ${p.outcome === 'BLOCKED' ? 'bg-blocked-soft text-blocked' : 'bg-valid-soft text-valid'}`}
          >
            {p.quantity} {p.outcome === 'BLOCKED' ? '✕' : '✓'}
          </li>
        ))}
      </ol>
      {opt.binding_constraint && <p className="text-[12px] text-ink-faint">Limit: {opt.binding_constraint.toLowerCase().replaceAll('_', ' ')}</p>}
    </div>
  )
}

function ComparisonTable({ cmp, world }: { cmp: Comparison; world: World | null }) {
  const src = locationName(world, cmp.source_id)
  const dst = locationName(world, cmp.destination_id)
  const rows: { label: string; cell: (c: Comparison['columns'][number]) => React.ReactNode }[] = [
    { label: `${src} after`, cell: (c) => c.source_after },
    { label: 'Safety stock', cell: (c) => <Pass ok={c.safety_stock_ok} /> },
    { label: `${dst} after`, cell: (c) => c.destination_after },
    { label: 'Capacity', cell: (c) => <Pass ok={c.capacity_ok} /> },
    { label: 'Overall', cell: (c) => <span className={OUTCOME_COLOR[c.outcome]}>{OUTCOME_WORD[c.outcome]}</span> },
  ]
  return (
    <div className="rounded-md border border-dashed border-shadow/50 bg-glass/90 px-4 pt-3 pb-3">
      <p className="text-[13px] font-semibold text-shadow">Side by side, same snapshot (revision {cmp.snapshot_revision})</p>
      <table className="mt-2 w-full text-[14px]">
        <thead>
          <tr className="text-left text-[12px] text-ink-soft">
            <th className="py-1 pr-4 font-medium">Consequence</th>
            {cmp.columns.map((c) => (
              <th key={c.name} className="num py-1 pr-4 text-right font-semibold text-ink">
                {c.name}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.label} className="border-t border-rule">
              <td className="py-1.5 pr-4 text-ink-soft">{r.label}</td>
              {cmp.columns.map((c) => (
                <td key={c.name} className="num py-1.5 pr-4 text-right font-medium">
                  {r.cell(c)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function Pass({ ok }: { ok: boolean }) {
  return <span className={ok ? 'text-valid' : 'text-blocked'}>{ok ? 'Pass' : 'Fail'}</span>
}
