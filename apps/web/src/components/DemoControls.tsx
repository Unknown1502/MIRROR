import { useEffect, useRef, useState } from 'react'
import { useMirror } from '../store'

const FAULTS: { id: string; label: string; detail: string }[] = [
  { id: 'verification_mismatch', label: 'Short shipment', detail: 'ERP moves 90% but reports 100%' },
  { id: 'temporary_erp_failure', label: 'ERP outage', detail: 'First call fails; retried idempotently' },
  { id: 'unknown_result', label: 'Lost response', detail: 'Applied, but the reply never arrives' },
  { id: 'duplicate_commit', label: 'Duplicate delivery', detail: 'Same operation delivered twice' },
]

export function DemoControls() {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  const demoMode = useMirror((s) => s.demoMode)
  const faultInjection = useMirror((s) => s.faultInjection)
  const armed = useMirror((s) => s.armedFault)
  const resetDemo = useMirror((s) => s.resetDemo)
  const armFault = useMirror((s) => s.armFault)
  const externalChange = useMirror((s) => s.externalChange)
  const replaying = useMirror((s) => s.replaying)

  useEffect(() => {
    if (!open) return
    const close = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    const esc = (e: KeyboardEvent) => e.key === 'Escape' && setOpen(false)
    window.addEventListener('mousedown', close)
    window.addEventListener('keydown', esc)
    return () => {
      window.removeEventListener('mousedown', close)
      window.removeEventListener('keydown', esc)
    }
  }, [open])

  if (!demoMode) return null
  return (
    <div className="relative" ref={ref}>
      <button
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        disabled={replaying}
        title={replaying ? 'Available when the replay ends' : undefined}
        className="rounded-full border border-rule px-3.5 py-1.5 text-[13px] font-medium text-ink-soft hover:border-ink hover:text-ink disabled:opacity-40 disabled:hover:border-rule"
      >
        Demo controls
        {armed !== 'none' && <span className="ml-2 inline-block h-2 w-2 rounded-full bg-blocked align-middle" title={`Armed: ${armed}`} />}
      </button>
      {open && (
        <div className="absolute right-0 top-full z-30 mt-2 w-[330px] rounded-lg border border-ink bg-glass p-4 shadow-[0_18px_40px_-18px_rgba(26,35,48,0.45)]">
          <button
            onClick={() => {
              void resetDemo()
              setOpen(false)
            }}
            className="w-full rounded-md bg-ink px-3 py-2 text-left text-[13px] font-semibold text-glass hover:opacity-90"
          >
            Reset to baseline world
            <span className="block text-[12px] font-normal opacity-75">Mumbai 2000, Pune Site 1 1200, Pune Site 2 300</span>
          </button>

          <p className="label mt-4 mb-2">Break the next ERP call on purpose</p>
          {!faultInjection && <p className="text-[12px] text-ink-faint">Disabled: DEMO_FAULT_INJECTION is off.</p>}
          <div className="flex flex-col gap-1.5">
            {FAULTS.map((f) => (
              <button
                key={f.id}
                disabled={!faultInjection}
                onClick={() => void armFault(armed === f.id ? 'none' : f.id)}
                aria-pressed={armed === f.id}
                className={`rounded-md border px-3 py-1.5 text-left text-[13px] ${
                  armed === f.id ? 'border-blocked bg-blocked-soft text-blocked' : 'border-rule hover:border-ink'
                }`}
              >
                <span className="font-semibold">{f.label}</span>
                <span className="block text-[12px] text-ink-soft">{armed === f.id ? 'Armed. Click again to disarm.' : f.detail}</span>
              </button>
            ))}
          </div>

          <p className="label mt-4 mb-2">Change reality underneath a preview</p>
          <button
            onClick={() => void externalChange()}
            className="w-full rounded-md border border-rule px-3 py-1.5 text-left text-[13px] hover:border-ink"
          >
            <span className="font-semibold">Ship 300 XR-17 out of Mumbai</span>
            <span className="block text-[12px] text-ink-soft">Another system writes to the ERP; stale previews can't commit</span>
          </button>
        </div>
      )}
    </div>
  )
}
