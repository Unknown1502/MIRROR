import type { MirrorEvent } from './types'

type Handler = (e: MirrorEvent) => void
type StatusHandler = (s: 'connecting' | 'open' | 'closed') => void

/** Session event stream with automatic reconnect and replay of missed events. */
export class SessionSocket {
  private ws: WebSocket | null = null
  private lastSequence = 0
  private closedByUser = false
  private retry = 0
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
        this.onStatus('open')
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
      this.onStatus('closed')
      if (this.closedByUser) return
      const delay = Math.min(8000, 500 * 2 ** this.retry++)
      window.setTimeout(() => this.connect(), delay)
    }
  }

  send(message: Record<string, unknown>): boolean {
    if (this.ws?.readyState !== WebSocket.OPEN) return false
    this.ws.send(JSON.stringify(message))
    return true
  }

  close(): void {
    this.closedByUser = true
    this.ws?.close()
  }
}
