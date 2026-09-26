interface GaugeProps {
  before?: number
  value: number
  capacity: number
  safety: number
  tone: 'shadow' | 'real'
  scaleMax: number
}

/** Stock against capacity: fill = level, dashed tick = safety stock, hollow tick = where it started. */
export function Gauge({ before, value, capacity, safety, tone, scaleMax }: GaugeProps) {
  const w = (n: number) => `${Math.max(0, Math.min(100, (n / scaleMax) * 100))}%`
  const over = value > capacity
  const under = value < safety
  const fill = over || value < 0 ? 'var(--color-blocked)' : under ? 'var(--color-blocked)' : tone === 'shadow' ? 'var(--color-shadow)' : 'var(--color-ink)'
  return (
    <div className="relative h-2.5 w-full" role="img" aria-label={`${value} of capacity ${capacity}, safety stock ${safety}`}>
      <div className="absolute inset-y-0 left-0 rounded-sm bg-silver" style={{ width: w(capacity) }} />
      <div className="absolute inset-y-0 left-0 rounded-sm" style={{ width: w(Math.min(Math.max(value, 0), capacity)), background: fill, opacity: tone === 'shadow' ? 0.85 : 1 }} />
      {over && (
        <div
          className="absolute inset-y-0 rounded-r-sm"
          style={{
            left: w(capacity),
            width: `calc(${w(value)} - ${w(capacity)})`,
            background: 'repeating-linear-gradient(135deg, var(--color-blocked) 0 3px, transparent 3px 6px)',
          }}
        />
      )}
      <div className="absolute -inset-y-1 w-px bg-ink" style={{ left: w(capacity) }} title={`Capacity ${capacity}`} />
      <div className="absolute -inset-y-1 border-l border-dashed border-ink-soft" style={{ left: w(safety) }} title={`Safety stock ${safety}`} />
      {before !== undefined && before !== value && (
        <div className="absolute -inset-y-0.5 w-[3px] rounded-sm border border-ink bg-glass" style={{ left: `calc(${w(before)} - 1px)` }} title={`Before ${before}`} />
      )}
    </div>
  )
}
