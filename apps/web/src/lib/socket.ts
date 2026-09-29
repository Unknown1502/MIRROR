import type { MirrorEvent } from './types'

type Handler = (e: MirrorEvent) => void
type StatusHandler = (s: 'connecting' | 'open' | 'closed') => void

/** Session event stream with automatic reconnect and replay of missed events. */
export class SessionSocket {
  private ws: WebSocket | null = null
  private lastSequence = 0
  private closedByUser = false
  private retry = 0
  private authed = false
  // Control messages sent before the socket is authenticated wait here instead of being lost.
  private pending: string[] = []
  private readonly sessionId: string
  private readonly token: string
  private readonly onEvent: Handler
  private readonly onStatus: StatusHandler

  constructor(sessionId: string, token: string, onEvent: Handler, onStatus: StatusHandler) {
    this.sessionId = sessionId
    this.token = token
    this.onEvent = onEvent
    this.onStatus = onStatus
  }

  connect(): void {
    this.closedByUser = false
    this.authed = false
    this.onStatus('connecting')
    const proto = location.protocol === 'https:' ? 'wss' : 'ws'
    // The session token goes in the first message, never in the URL (URLs end up in access logs).
    const url = `${proto}://${location.host}/ws/v1/sessions/${this.sessionId}?after=${this.lastSequence}`
    const ws = new WebSocket(url)
    this.ws = ws
    ws.onopen = () => {
      ws.send(JSON.stringify({ type: 'auth', token: this.token }))
    }
    ws.onmessage = (msg) => {
      const e = JSON.parse(msg.data as string)
      if (e.type === 'auth.ok') {
        this.retry = 0
        this.authed = true
        this.onStatus('open')
        for (const queued of this.pending.splice(0)) ws.send(queued)
        return
      }
      if (e.type === 'pong') return
      if (typeof e.sequence === 'number') {
        if (e.sequence <= this.lastSequence) return // replay overlap
        this.lastSequence = e.sequence
      }
      this.onEvent(e as MirrorEvent)
    }
    ws.onclose = () => {
      this.authed = false
      this.onStatus('closed')
      if (this.closedByUser) return
      const delay = Math.min(8000, 500 * 2 ** this.retry++)
      window.setTimeout(() => this.connect(), delay)
    }
  }

  /** Sends now if the socket is ready. Otherwise a control message (`queue`) waits for the next
   *  authenticated connection, and anything else (audio) is dropped: stale audio is useless. */
  send(message: Record<string, unknown>, queue = false): boolean {
    const data = JSON.stringify(message)
    if (this.ws?.readyState === WebSocket.OPEN && this.authed) {
      this.ws.send(data)
      return true
    }
    if (queue) this.pending.push(data)
    return queue
  }

  close(): void {
    this.closedByUser = true
    this.ws?.close()
  }
}
