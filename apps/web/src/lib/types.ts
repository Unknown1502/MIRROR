// Shapes returned by the MIRROR backend. The UI renders these; it never computes them.

export type TxState =
  | 'IDLE' | 'LISTENING' | 'INTERPRETING' | 'FORKING' | 'SIMULATING' | 'CONSEQUENCE_REVIEW'
  | 'CLARIFYING' | 'WHAT_IF' | 'PREVIEW_READY' | 'COMMIT_ARMED' | 'EXECUTING' | 'VERIFYING'
  | 'VERIFIED' | 'DIVERGED' | 'ROLLBACK' | 'ABORTED' | 'FAILED'

export type Reality = 'UNCHANGED' | 'CHANGED' | 'VERIFIED' | 'DIVERGED' | 'RESTORED' | 'UNKNOWN'
export type Outcome = 'VALID' | 'WARNING' | 'BLOCKED'

export interface Consequence {
  code: string
  severity: 'INFO' | 'LOW' | 'MEDIUM' | 'HIGH' | 'CRITICAL'
  message: string
  subject: string
  before: number | null
  after: number | null
  threshold: number | null
  delta: number | null
}

export interface Effect {
  location_id: string
  product_id: string
  before: number
  after: number
  capacity: number
  safety_stock: number
}

export interface Simulation {
  intent: BranchIntent
  snapshot_revision: number
  effects: Effect[]
  consequences: Consequence[]
  transport: { mode: string; estimated_cost: number; lead_time_hours: number; available: boolean } | null
  result_hash: string
  latency_ms: number
}

export interface BranchIntent {
  product_id: string
  quantity: number
  source_id: string
  destination_id: string
  draft_index?: number
}

export interface Branch {
  branch_id: string
  key: string
  intent: BranchIntent
  evidence_status: Record<string, string>
  outcome: Outcome
  policy: {
    decision: 'ALLOW' | 'REQUIRE_CLARIFICATION' | 'BLOCK'
    outcome: Outcome
    rules: { rule: string; passed: boolean; detail: string }[]
    blocking_codes: string[]
  }
  simulation: Simulation | null
}

export interface EvidenceField {
  evidence_id: string | null
  field: string
  value: string | number | null
  candidates: (string | number)[]
  status: 'EXPLICIT' | 'RESOLVED' | 'INFERRED' | 'UNKNOWN'
  ambiguous: boolean
  source: { type: string; transcript_item_id: string | null; text_span: string | null }
  note: string
}

export interface Clarification {
  field: string | null
  kind?: string
  question: string
  options: { value: string | number; label: string; outcome: Outcome | null; summary: string; branch_keys: string[] }[]
  scores: { field: string; ambiguity: number; consequence_divergence: number; risk_weight: number; score: number }[]
}

export interface Version {
  version: number
  parent_version: number | null
  kind: 'PROPOSAL' | 'AMENDMENT' | 'CLARIFICATION' | 'WHAT_IF' | 'OPTIMIZATION' | 'RESIMULATION'
  label: string
  status: string
  state_trail: TxState[]
  intent: { drafts: Record<string, EvidenceField>[] }
  evidence_hash: string
  simulation_hash: string | null
  clarification: Clarification | null
  extra: { optimization?: { best_quantity: number; binding_constraint: string | null; probes: { quantity: number; outcome: Outcome }[]; evaluated: number; latency_ms: number } } | null
  world_revision: number
  created_at: string
  superseded: boolean
  committed: boolean
  branches: Branch[]
}

export interface VerificationFocus {
  cell: string
  location_id: string
  product_id: string
  before: number
  expected: number
  actual: number
  match: boolean
}

export interface Execution {
  id: string
  kind: 'COMMIT' | 'ROLLBACK'
  version: number
  status: string
  verification: 'VERIFIED' | 'DIVERGED' | 'NOT_APPLIED' | null
  attempts: number
  erp_reported: Record<string, unknown> | null
  error: { reason: string; message: string } | null
  report: { match: boolean; checked_cells: number; focus: VerificationFocus[]; diffs: unknown[] } | null
  exec_latency_ms: number | null
  verify_latency_ms: number | null
}

export interface Comparison {
  version?: number
  snapshot_revision: number
  source_id: string
  destination_id: string
  product_id: string
  columns: {
    name: string
    outcome: Outcome
    source_before: number
    source_after: number
    safety_stock: number
    safety_stock_ok: boolean
    destination_before: number
    destination_after: number
    capacity: number
    capacity_ok: boolean
    estimated_cost: number | null
  }[]
}

export interface Transaction {
  id: string
  session_id: string
  state: TxState
  reality: Reality
  real_world_changed: boolean
  live_writes: number
  current_version: number
  committed_version: number | null
  versions: Version[]
  commit: { version: number; token: string; evidence_hash: string; simulation_hash: string } | null
  executions: Execution[]
  comparison: Comparison | null
  evidence: {
    evidence_id: string
    version: number
    field: string
    candidates: (string | number)[]
    status: string
    source: { type: string; text_span: string | null }
    lifecycle: 'ACTIVE' | 'SUPERSEDED'
    supersedes: string | null
  }[]
  erp_operations: { operation_id: string; type: string; request: Record<string, unknown>; revision: number }[]
  idempotent_replay?: boolean
}

export interface World {
  revision: number
  hash: string
  products: { id: string; name: string; min_transfer: number; max_transfer: number }[]
  locations: { id: string; name: string; city: string; kind: string }[]
  cells: { location_id: string; product_id: string; on_hand: number; capacity: number; safety_stock: number }[]
  armed_fault: string
  recent_operations: { operation_id: string; type: string; actor: string; revision: number; created_at: string }[]
}

export interface AuditEvent {
  sequence: number
  event_id: string
  event_type: string
  timestamp: string
  transaction_version: number | null
  payload: Record<string, unknown>
  previous_hash: string
  hash: string
}

export interface ChainReport {
  valid: boolean
  length: number
  head_hash: string
  first_invalid_sequence: number | null
  reason: string | null
}

export interface MirrorEvent {
  version: string
  event_id: string
  sequence: number
  timestamp: string
  session_id: string
  transaction_id: string | null
  transaction_version: number | null
  type: string
  data: Record<string, unknown>
}

export interface ApiError {
  code: string
  message: string
  details?: Record<string, unknown>
}

export interface Metrics {
  transactions: number
  branches_explored: number
  clarifications: number
  what_if_simulations: number
  commit_versions: number
  commits: number
  verified: number
  diverged: number
  rollbacks: number
  simulation_latency_ms: number | null
  execution_latency_ms: number | null
  verification_latency_ms: number | null
  voice: { time_to_first_audio_ms: number | null; interruptions: number; tool_calls: number; tool_calls_discarded: number } | null
  voice_remaining_seconds?: number | null
}

export interface InterpretedBy {
  model: string
  request_id: string | null
  latency_ms: number
  action: string
}

export interface RealityAlert {
  cause: string
  previous_status: string | null
  new_status: string
  new_version: number
  state: TxState
  say: string
}

// public/replay/manifest.json, written by scripts/export_replay.py from a live AssemblyAI session.
export interface ReplayStep {
  kind: 'turn' | 'reality'
  said: string
  heard: string | null
  agent_said: string[]
  tool_calls: string[]
  user_audio: string | null
  agent_audio: string | null
  agent_offset_s: number | null
  arm_fault?: string
  external_change?: { location_id: string; product_id: string; delta: number; note: string }
  recorded_state: string | null
}

export interface ReplayManifest {
  recorded_at: string
  agent_voice: string
  user_voice: string
  checks: string
  steps: ReplayStep[]
}
