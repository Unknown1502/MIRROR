import type { ApiError, AuditEvent, ChainReport, Comparison, InterpretedBy, Metrics, ReplayManifest, Transaction, World } from './types'

export class MirrorApiError extends Error {
  readonly code: string
  readonly details: Record<string, unknown>
  readonly status: number
  constructor(status: number, err: ApiError) {
    super(err.message)
    this.code = err.code
    this.details = err.details ?? {}
    this.status = status
  }
}

export interface SessionCredentials {
  sessionId: string
  token: string
}

let creds: SessionCredentials | null = null
export const setCredentials = (c: SessionCredentials) => {
  creds = c
}

async function call<T>(method: 'GET' | 'POST', path: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = { 'Content-Type': 'application/json' }
  if (creds) {
    headers['X-Session-Id'] = creds.sessionId
    headers.Authorization = `Bearer ${creds.token}`
  }
  let res: Response
  try {
    res = await fetch(`/api/v1${path}`, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) })
  } catch {
    throw new MirrorApiError(0, { code: 'BACKEND_UNREACHABLE', message: 'The MIRROR backend is not reachable. Start it and retry.' })
  }
  const data = await res.json().catch(() => ({}))
  if (!res.ok) {
    const err: ApiError = data.error ?? { code: 'HTTP_' + res.status, message: res.statusText }
    if (res.status === 429) {
      const wait = Number(res.headers.get('Retry-After') ?? err.details?.retry_after_seconds ?? 0)
      err.message = `Too many requests from this browser. Try again in ${wait > 0 ? `${Math.ceil(wait)} s` : 'a moment'}.`
    }
    throw new MirrorApiError(res.status, err)
  }
  return data as T
}

export interface TurnResult {
  reply: string
  command: Record<string, unknown>
  transaction: Transaction | null
  comparison: Comparison | null
  error: ApiError | null
  interpreted_by: InterpretedBy | null
}

export interface SessionInfo {
  session_id: string
  session_token: string
  voice_available: boolean
  demo_mode: boolean
  fault_injection: boolean
  sandbox?: boolean
  voice_max_session_seconds?: number
  llm_gateway?: { enabled: boolean; model: string } | null
}

export const replayManifest = async (): Promise<ReplayManifest> => {
  const res = await fetch('/replay/manifest.json', { cache: 'no-cache' })
  if (!res.ok) throw new Error('The replay recording is not part of this build.')
  return res.json()
}

export const api = {
  createSession: () => call<SessionInfo>('POST', '/sessions'),
  turn: (sessionId: string, text: string, contextVersion?: number) =>
    call<TurnResult>('POST', `/sessions/${sessionId}/turns`, { text, context_version: contextVersion }),
  transaction: (id: string) => call<Transaction>('GET', `/transactions/${id}`),
  selectFuture: (id: string, branchKey: string) => call<Transaction>('POST', `/transactions/${id}/clarify`, { branch_key: branchKey }),
  commit: (id: string, version: number, token: string, idempotencyKey: string) =>
    call<Transaction>('POST', `/transactions/${id}/commit`, { version, commit_token: token, idempotency_key: idempotencyKey }),
  rollback: (id: string, idempotencyKey: string) =>
    call<Transaction>('POST', `/transactions/${id}/rollback`, { idempotency_key: idempotencyKey }),
  abort: (id: string) => call<Transaction>('POST', `/transactions/${id}/abort`),
  verify: (id: string) => call<Transaction>('POST', `/transactions/${id}/verify`),
  audit: (id: string) => call<{ events: AuditEvent[]; chain: ChainReport }>('GET', `/transactions/${id}/audit`),
  verifyLedger: () => call<ChainReport>('GET', '/audit/verify'),
  world: () => call<World>('GET', '/world'),
  metrics: () => call<Metrics>('GET', '/metrics'),
  demoStatus: () => call<{ armed_fault: string; faults: string[]; demo_mode: boolean; fault_injection: boolean }>('GET', '/demo/status'),
  reset: () => call<World>('POST', '/demo/reset'),
  armFault: (fault: string) => call<{ armed_fault: string }>('POST', '/demo/faults', { fault }),
  externalChange: (location_id: string, product_id: string, delta: number, note: string) =>
    call<World>('POST', '/demo/external-change', { location_id, product_id, delta, note }),
}
