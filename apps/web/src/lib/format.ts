import type { Branch, Outcome, TxState, Version, World } from './types'

export function locationName(world: World | null, id: string, short = true): string {
  const loc = world?.locations.find((l) => l.id === id)
  if (!loc) return id
  if (!short) return loc.name
  const sameCity = world!.locations.filter((l) => l.city === loc.city)
  return sameCity.length === 1 ? loc.city : loc.name
}

export const OUTCOME_WORD: Record<Outcome, string> = {
  VALID: 'Valid',
  WARNING: 'Valid, with a warning',
  BLOCKED: 'Blocked',
}

export const OUTCOME_COLOR: Record<Outcome, string> = {
  VALID: 'text-valid',
  WARNING: 'text-warn',
  BLOCKED: 'text-blocked',
}

export const STATE_WORD: Record<TxState, string> = {
  IDLE: 'Idle',
  LISTENING: 'Listening',
  INTERPRETING: 'Interpreting',
  FORKING: 'Forking',
  SIMULATING: 'Simulating',
  CONSEQUENCE_REVIEW: 'Consequence review',
  CLARIFYING: 'Clarifying',
  WHAT_IF: 'What-if',
  PREVIEW_READY: 'Preview ready',
  COMMIT_ARMED: 'Commit armed',
  EXECUTING: 'Executing',
  VERIFYING: 'Verifying',
  VERIFIED: 'Verified',
  DIVERGED: 'Diverged',
  ROLLBACK: 'Rolling back',
  ABORTED: 'Aborted',
  FAILED: 'Failed',
}

export const KIND_WORD: Record<Version['kind'], string> = {
  PROPOSAL: 'Request',
  AMENDMENT: 'Correction',
  CLARIFICATION: 'Clarified',
  WHAT_IF: 'What-if',
  OPTIMIZATION: 'Max safe',
  RESIMULATION: 'Re-simulated',
}

const REASON: Record<string, (c: { delta: number | null; threshold: number | null }) => string> = {
  DESTINATION_CAPACITY_EXCEEDED: (c) => `Over capacity by ${c.delta}`,
  SOURCE_SAFETY_STOCK_BREACH: (c) => `Safety stock breached by ${Math.abs(c.delta ?? 0)}`,
  SOURCE_INSUFFICIENT_INVENTORY: (c) => `Short by ${Math.abs(c.delta ?? 0)} units`,
  ROUTE_UNAVAILABLE: () => 'Route unavailable',
  NO_ROUTE: () => 'No route',
  SAME_LOCATION: () => 'Same location',
  ABOVE_MAXIMUM_TRANSFER: (c) => `Above max transfer ${c.threshold}`,
  BELOW_MINIMUM_TRANSFER: (c) => `Below min transfer ${c.threshold}`,
  UNKNOWN_PRODUCT: () => 'Unknown product',
  UNKNOWN_LOCATION: () => 'Unknown location',
  SOURCE_AT_SAFETY_FLOOR: () => 'Source left at safety floor',
  DESTINATION_NEAR_CAPACITY: () => 'Destination nearly full',
}

export function primaryReason(branch: Branch): string | null {
  const cons = branch.simulation?.consequences ?? []
  const c = cons.find((x) => x.severity === 'CRITICAL' || x.severity === 'HIGH') ?? cons.find((x) => x.severity === 'MEDIUM')
  if (!c) return null
  return REASON[c.code]?.(c) ?? c.message
}

export function currentVersion(versions: Version[], current: number): Version | undefined {
  return versions.find((v) => v.version === current)
}

export function shortHash(h: string | null | undefined): string {
  return h ? h.slice(0, 8) : '—'
}

export function sentenceCase(code: string): string {
  const s = code.toLowerCase().replaceAll('_', ' ')
  return s.charAt(0).toUpperCase() + s.slice(1)
}
