import { useState } from 'react'
import { currentVersion, locationName } from '../lib/format'
import type { Execution } from '../lib/types'
import { useMirror } from '../store'
import { Gauge } from './Gauge'

/** Real world: what the ERP says right now, and how it compares with what was predicted. */
export function Reality() {
  const world = useMirror((s) => s.world)
  const txn = useMirror((s) => s.txn)
  const [picked, setPicked] = useState<string | null>(null)

  const ver = txn ? currentVersion(txn.versions, txn.current_version) : undefined
  const focusProduct = picked ?? ver?.branches[0]?.intent.product_id ?? 'XR-17'
  const cells = (world?.cells ?? []).filter((c) => c.product_id === focusProduct)
  const scaleMax = Math.max(1, ...cells.map((c) => c.capacity))
  const last = txn?.executions[txn.executions.length - 1]
  const touched = new Set(last?.report?.focus.map((f) => f.location_id) ?? [])

  return (
    <section className="real-pane grid gap-6 rounded-lg px-5 pt-4 pb-5 lg:grid-cols-[minmax(0,1.1fr)_minmax(0,1fr)]" aria-label="Real world">
      <div className="min-w-0">
        <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1">
          <h2 className="text-[17px] font-semibold">Real world</h2>
          <p className="text-[12px] text-ink-soft">ERP revision {world?.revision ?? '—'}, read live</p>
          <label className="ml-auto text-[12px] text-ink-soft">
            <span className="sr-only">Product</span>
            <select
              value={focusProduct}
              onChange={(e) => setPicked(e.target.value)}
              className="rounded border border-rule bg-glass px-1.5 py-0.5 text-[12px] text-ink"
            >
              {(world?.products ?? []).map((p) => (
                <option key={p.id} value={p.id}>
                  {p.id}
                </option>
              ))}
            </select>
          </label>
        </div>
        <table className="mt-2 w-full text-[13px]">
          <thead>
            <tr className="text-left text-[12px] text-ink-soft">
              <th className="py-1 font-medium">Location</th>
              <th className="py-1 pr-3 text-right font-medium">On hand</th>
              <th className="w-[42%] py-1 font-medium">
                <span className="sr-only">Level</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {cells.map((c) => (
              <tr key={c.location_id} className={`border-t border-rule ${touched.has(c.location_id) ? 'bg-silver/60' : ''}`}>
                <td className="py-1.5 pr-2">{locationName(world, c.location_id)}</td>
                <td className="num py-1.5 pr-3 text-right text-[17px] font-semibold">{c.on_hand}</td>
                <td className="py-1.5">
                  <Gauge value={c.on_hand} capacity={c.capacity} safety={c.safety_stock} tone="real" scaleMax={scaleMax} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        <p className="mt-1.5 text-[11px] text-ink-faint">Solid tick: capacity. Dashed tick: safety stock.</p>
      </div>

      <Verification execution={last} />
    </section>
  )
}

function Verification({ execution }: { execution: Execution | undefined }) {
  const world = useMirror((s) => s.world)
  if (!execution) {
    return (
      <div className="min-w-0">
        <h3 className="text-[15px] font-semibold">Verification</h3>
        <p className="mt-2 max-w-[42ch] text-[13px] leading-relaxed text-ink-soft">
          After a commit, MIRROR ignores what the ERP says it did. It reads every cell back and compares the whole world with the
          prediction. Only an exact match is verified.
        </p>
      </div>
    )
  }
  const report = execution.report
  const verdict =
    execution.verification === 'VERIFIED'
      ? { text: 'Expected = actual', cls: 'text-valid' }
      : execution.verification === 'DIVERGED'
        ? { text: 'Expected ≠ actual', cls: 'text-blocked' }
        : execution.verification === 'NOT_APPLIED'
          ? { text: 'Not executed', cls: 'text-ink' }
          : { text: 'Unverified', cls: 'text-warn' }
  const reported = execution.erp_reported as { applied_quantity?: number; status?: string } | null

  return (
    <div className="min-w-0">
      <h3 className="text-[15px] font-semibold">{execution.kind === 'ROLLBACK' ? 'Rollback verification' : 'Commit verification'}</h3>
      <p className={`num mt-0.5 text-[22px] font-bold leading-tight ${verdict.cls}`}>{verdict.text}</p>
      {report && (
        <table className="mt-2 w-full text-[13px]">
          <thead>
            <tr className="text-left text-[12px] text-ink-soft">
              <th className="py-1 font-medium">Location</th>
              <th className="py-1 text-right font-medium">Before</th>
              <th className="py-1 text-right font-medium">Expected</th>
              <th className="py-1 text-right font-medium">Actual</th>
            </tr>
          </thead>
          <tbody>
            {report.focus.map((f) => (
              <tr key={f.cell} className="border-t border-rule">
                <td className="py-1.5">{locationName(world, f.location_id)}</td>
                <td className="num py-1.5 text-right text-ink-faint">{f.before}</td>
                <td className="num py-1.5 text-right text-[16px] font-semibold">{f.expected}</td>
                <td className={`num py-1.5 text-right text-[16px] font-semibold ${f.match ? 'text-valid' : 'text-blocked'}`}>
                  {f.actual}
                  {!f.match && <span className="ml-1 text-[12px]">({(f.actual - f.expected > 0 ? '+' : '') + (f.actual - f.expected)})</span>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <dl className="mt-3 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-[12px]">
        <dt className="text-ink-soft">ERP said</dt>
        <dd>
          {reported
            ? `${reported.status ?? 'SUCCESS'}${reported.applied_quantity !== undefined ? `, ${reported.applied_quantity} applied` : ''}`
            : execution.error
              ? execution.error.message
              : 'no response'}
        </dd>
        <dt className="text-ink-soft">MIRROR read</dt>
        <dd>
          {report ? `${report.checked_cells} cells, ${report.diffs.length} differ from prediction` : 'pending'}
          {execution.verify_latency_ms !== null && `, in ${execution.verify_latency_ms} ms`}
        </dd>
        {execution.attempts > 1 && (
          <>
            <dt className="text-ink-soft">Attempts</dt>
            <dd>{execution.attempts}, same operation id (idempotent)</dd>
          </>
        )}
      </dl>
    </div>
  )
}
