import { create } from 'zustand'
import { AgentPlayback, MicCapture } from './lib/audio'
import { api, MirrorApiError, setCredentials } from './lib/api'
import { SessionSocket } from './lib/socket'
import type { ApiError, AuditEvent, ChainReport, Metrics, MirrorEvent, RealityAlert, Transaction, World } from './lib/types'

export interface Line {
  id: string
  role: 'user' | 'agent' | 'system'
  text: string
  via: 'voice' | 'typed' | 'runtime' | 'replay'
  tone?: 'error' | 'warn' | 'info'
  meta?: string
}

type VoiceStatus = 'off' | 'starting' | 'live' | 'error'

interface MirrorState {
  sessionId: string | null
  voiceAvailable: boolean
  demoMode: boolean
  faultInjection: boolean
  sandbox: boolean
  llmModel: string | null
  voiceMaxSeconds: number
  voiceEndsAt: number | null
  realityAlert: RealityAlert | null
  screen: 'landing' | 'console'
  replaying: boolean
  socketStatus: 'connecting' | 'open' | 'closed'
  txnId: string | null
  txn: Transaction | null
  world: World | null
  audit: AuditEvent[]
  chain: ChainReport | null
  metrics: Metrics | null
  lines: Line[]
  partial: { itemId: string | null; text: string } | null
  voice: VoiceStatus
  userSpeaking: boolean
  agentSpeaking: boolean
  micLevel: number
  voiceTools: string[]
  armedFault: string
  busy: boolean
  lastError: ApiError | null
  bootError: string | null

  init: () => Promise<void>
  sendText: (text: string) => Promise<void>
  selectFuture: (key: string) => Promise<void>
  commit: () => Promise<void>
  rollback: () => Promise<void>
  abort: () => Promise<void>
  reverify: () => Promise<void>
  resetDemo: () => Promise<void>
  armFault: (fault: string) => Promise<void>
  externalChange: () => Promise<void>
  toggleVoice: () => Promise<void>
  verifyChain: () => Promise<void>
  dismissReality: () => void
  setScreen: (screen: 'landing' | 'console') => void
  setReplaying: (on: boolean) => void
  replayLine: (role: 'user' | 'agent', text: string) => void
  replayTurn: (heard: string) => Promise<void>
}

let socket: SessionSocket | null = null
const mic = new MicCapture()
const playback = new AgentPlayback()
let refreshTimer: number | undefined
let initializing: Promise<void> | null = null
let lineSeq = 0
const commitKeys = new Map<string, string>()

const nextId = () => `l${++lineSeq}`
// A reality alert concerns an open preview; once the transaction is committed or over, it is history.
const settled = (txn: Transaction) => (txn.committed_version !== null || txn.state === 'ABORTED' ? { realityAlert: null } : {})

export const useMirror = create<MirrorState>((set, get) => {
  const addLine = (line: Omit<Line, 'id'>) => set((s) => ({ lines: [...s.lines.slice(-80), { ...line, id: nextId() }] }))
  const addSystem = (text: string, tone: Line['tone'] = 'info') => addLine({ role: 'system', text, via: 'runtime', tone })

  const report = (err: unknown) => {
    const e =
      err instanceof MirrorApiError
        ? { code: err.code, message: err.message, details: err.details }
        : { code: 'CLIENT', message: String(err) }
    set({ lastError: e })
    addSystem(`${e.code}: ${e.message}`, 'error')
  }

  const refresh = async () => {
    const { txnId } = get()
    try {
      const [world, metrics] = await Promise.all([api.world(), api.metrics()])
      set({ world, metrics, armedFault: world.armed_fault })
      if (txnId) {
        const [txn, audit] = await Promise.all([api.transaction(txnId), api.audit(txnId)])
        if (get().txnId === txnId) set({ txn, audit: audit.events, chain: audit.chain, ...settled(txn) })
      }
    } catch (err) {
      if (err instanceof MirrorApiError && err.code === 'BACKEND_UNREACHABLE') set({ lastError: { code: err.code, message: err.message } })
    }
  }
  // Debounced, but never starved: a burst of events refreshes at most 150 ms after it starts.
  let firstPending = 0
  const scheduleRefresh = () => {
    const now = performance.now()
    if (!firstPending) firstPending = now
    window.clearTimeout(refreshTimer)
    const wait = Math.max(0, Math.min(60, 150 - (now - firstPending)))
    refreshTimer = window.setTimeout(() => {
      firstPending = 0
      void refresh()
    }, wait)
  }

  const adopt = (txn: Transaction | null) => {
    if (!txn) return
    set({ txnId: txn.id, txn, ...settled(txn) })
    void refresh()
  }

  playback.onActivity = (playing) => set({ agentSpeaking: playing })

  const onEvent = (e: MirrorEvent) => {
    const d = e.data
    switch (e.type) {
      case 'TRANSCRIPT_DELTA':
        set({ partial: { itemId: (d.item_id as string) ?? null, text: String(d.text ?? '') } })
        return
      case 'USER_TURN':
        if (d.source === 'VOICE') {
          set({ partial: null })
          addLine({ role: 'user', text: String(d.text), via: 'voice' })
        }
        return
      case 'AGENT_TRANSCRIPT':
        if (d.text) addLine({ role: 'agent', text: String(d.text) + (d.interrupted ? ' …' : ''), via: 'voice' })
        return
      case 'AGENT_AUDIO':
        playback.play(String(d.data ?? ''))
        return
      case 'AUDIO_FLUSH':
        playback.flush()
        return
      case 'VOICE_ACTIVITY':
        set({ userSpeaking: Boolean(d.speaking) })
        return
      case 'VOICE_STARTED': {
        const left = get().metrics?.voice_remaining_seconds ?? get().voiceMaxSeconds
        const again = get().voice === 'live'
        set({ voice: 'live', voiceEndsAt: left > 0 && !get().voiceEndsAt ? Date.now() + left * 1000 : get().voiceEndsAt })
        addSystem(
          d.resumed
            ? 'Voice session resumed.'
            : again
              ? 'Voice reconnected with a fresh AssemblyAI session. Your transaction is intact; repeat anything said during the gap.'
              : 'Voice connected. Speak naturally; the backend decides what executes.',
          again ? 'warn' : 'info',
        )
        return
      }
      case 'VOICE_CONNECTION_LOST':
        addSystem('The connection to AssemblyAI dropped. Reconnecting; nothing pending was executed.', 'warn')
        return
      case 'VOICE_STOPPED':
        set({ voice: 'off', userSpeaking: false, voiceEndsAt: null })
        void mic.stop()
        return
      case 'REALITY_CHANGED':
        set({ realityAlert: d as unknown as RealityAlert })
        addSystem(`Reality changed under your preview: ${d.cause}. MIRROR re-simulated it as v${d.new_version}.`, 'warn')
        break
      case 'VOICE_TOOLS_UPDATED':
        set({ voiceTools: (d.tools as string[]) ?? [] })
        return
      case 'VOICE_METRICS': {
        const m = get().metrics
        if (m) set({ metrics: { ...m, voice: d as unknown as Metrics['voice'] } })
        return
      }
      case 'REPLY_INTERRUPTED': {
        const dropped = (d.discarded_tool_calls as string[]) ?? []
        addSystem(
          dropped.length
            ? `You interrupted. Discarded before execution: ${dropped.join(', ')}.`
            : 'You interrupted. Agent audio flushed.',
          'warn',
        )
        break
      }
      case 'TOOL_CALL_PROPOSED':
        addSystem(`Agent proposed ${d.name}. Held until its reply completes.`)
        break
      case 'COMMIT_TOKEN_INVALIDATED':
        addSystem(`Commit authorization for v${d.version} revoked: ${d.reason}.`, 'warn')
        break
      case 'STALE_TRANSACTION':
        addSystem('Reality changed after the preview. MIRROR re-simulated before allowing a commit.', 'warn')
        break
      case 'ERROR':
        if (d.code === 'ASSEMBLYAI_UNAVAILABLE' || d.code === 'SESSION_EXPIRED') {
          set({ voice: 'error' })
          void mic.stop()
        }
        addSystem(`${d.code}: ${d.message}`, 'error')
        break
      case 'DEMO_RESET':
        set({ txnId: null, txn: null, audit: [], chain: null, lines: [], partial: null, realityAlert: null })
        addSystem(d.sandbox ? 'Demo reset. Your sandbox is back at the baseline world.' : 'Demo reset. The ERP is back at the baseline world.')
        scheduleRefresh()
        return
      case 'FAULT_ARMED':
        set({ armedFault: String(d.fault) })
        return
      case 'WORLD_CHANGED':
        scheduleRefresh()
        return
    }
    if (e.transaction_id) {
      if (e.transaction_id !== get().txnId) set({ txnId: e.transaction_id })
      scheduleRefresh()
    }
  }

  return {
    sessionId: null,
    voiceAvailable: false,
    demoMode: false,
    faultInjection: false,
    sandbox: false,
    llmModel: null,
    voiceMaxSeconds: 0,
    voiceEndsAt: null,
    realityAlert: null,
    screen: 'landing',
    replaying: false,
    socketStatus: 'closed',
    txnId: null,
    txn: null,
    world: null,
    audit: [],
    chain: null,
    metrics: null,
    lines: [],
    partial: null,
    voice: 'off',
    userSpeaking: false,
    agentSpeaking: false,
    micLevel: 0,
    voiceTools: [],
    armedFault: 'none',
    busy: false,
    lastError: null,
    bootError: null,

    init: () => {
      if (get().sessionId) return Promise.resolve()
      initializing ??= (async () => {
      try {
        const s = await api.createSession()
        setCredentials({ sessionId: s.session_id, token: s.session_token })
        set({
          sessionId: s.session_id,
          voiceAvailable: s.voice_available,
          demoMode: s.demo_mode,
          faultInjection: s.fault_injection,
          sandbox: Boolean(s.sandbox),
          llmModel: s.llm_gateway?.enabled ? s.llm_gateway.model : null,
          voiceMaxSeconds: s.voice_max_session_seconds ?? 0,
          bootError: null,
        })
        socket = new SessionSocket(s.session_id, s.session_token, onEvent, (st) => set({ socketStatus: st }))
        socket.connect()
        await refresh()
      } catch (err) {
        set({ bootError: err instanceof Error ? err.message : String(err) })
      } finally {
        initializing = null
      }
      })()
      return initializing
    },

    sendText: async (text) => {
      const { sessionId, txn } = get()
      if (!sessionId || !text.trim()) return
      playback.unlock()
      addLine({ role: 'user', text: text.trim(), via: 'typed' })
      set({ busy: true, lastError: null })
      try {
        const r = await api.turn(sessionId, text.trim(), txn?.current_version)
        const llm = r.interpreted_by
        addLine({
          role: 'agent',
          text: r.reply,
          via: 'typed',
          tone: r.error ? 'warn' : undefined,
          meta: llm
            ? `Phrasing read by AssemblyAI LLM Gateway (${llm.model}, ${(llm.latency_ms / 1000).toFixed(1)} s) as “${llm.action.replaceAll('_', ' ')}”, then checked against your words.`
            : undefined,
        })
        if (r.error) set({ lastError: r.error })
        adopt(r.transaction)
      } catch (err) {
        report(err)
      } finally {
        set({ busy: false })
      }
    },

    selectFuture: async (key) => {
      const { txnId } = get()
      if (!txnId) return
      set({ busy: true })
      try {
        adopt(await api.selectFuture(txnId, key))
        addSystem(`You chose future ${key.toUpperCase()} in the interface.`)
      } catch (err) {
        report(err)
      } finally {
        set({ busy: false })
      }
    },

    commit: async () => {
      const { txn } = get()
      if (!txn?.commit) return
      const slot = `${txn.id}:v${txn.commit.version}`
      // One key per previewed version: double clicks replay, they never execute twice.
      const key = commitKeys.get(slot) ?? `ui:${slot}:${crypto.randomUUID()}`
      commitKeys.set(slot, key)
      set({ busy: true })
      try {
        const r = await api.commit(txn.id, txn.commit.version, txn.commit.token, key)
        adopt(r)
      } catch (err) {
        report(err)
        scheduleRefresh()
      } finally {
        set({ busy: false })
      }
    },

    rollback: async () => {
      const { txnId } = get()
      if (!txnId) return
      set({ busy: true })
      try {
        adopt(await api.rollback(txnId, `ui:rollback:${txnId}:${crypto.randomUUID()}`))
      } catch (err) {
        report(err)
        scheduleRefresh()
      } finally {
        set({ busy: false })
      }
    },

    abort: async () => {
      const { txnId } = get()
      if (!txnId) return
      try {
        adopt(await api.abort(txnId))
      } catch (err) {
        report(err)
      }
    },

    reverify: async () => {
      const { txnId } = get()
      if (!txnId) return
      try {
        adopt(await api.verify(txnId))
      } catch (err) {
        report(err)
      }
    },

    resetDemo: async () => {
      try {
        if (get().voice === 'live') await get().toggleVoice()
        await api.reset()
        commitKeys.clear()
      } catch (err) {
        report(err)
      }
    },

    armFault: async (fault) => {
      try {
        const r = await api.armFault(fault)
        set({ armedFault: r.armed_fault })
        addSystem(fault === 'none' ? 'Fault injection disarmed.' : `Fault armed for the next ERP call: ${fault.replaceAll('_', ' ')}.`, 'warn')
      } catch (err) {
        report(err)
      }
    },

    externalChange: async () => {
      try {
        addSystem('Another system ships 300 XR-17 out of Mumbai, straight in the ERP.', 'warn')
        await api.externalChange('MUMBAI-DC', 'XR-17', -300, 'another team shipped 300')
        scheduleRefresh()
      } catch (err) {
        report(err)
      }
    },

    toggleVoice: async () => {
      const { voice } = get()
      if (voice === 'live' || voice === 'starting') {
        socket?.send({ type: 'voice.stop' })
        await mic.stop()
        set({ voice: 'off', userSpeaking: false, micLevel: 0, voiceEndsAt: null })
        return
      }
      if (!get().voiceAvailable) {
        addSystem('Voice needs ASSEMBLYAI_API_KEY on the server. Typed commands use the same runtime.', 'warn')
        return
      }
      set({ voice: 'starting' })
      playback.unlock()
      try {
        await mic.start(
          (b64) => socket?.send({ type: 'voice.audio', audio: b64 }),
          (level) => set({ micLevel: level }),
        )
        socket?.send({ type: 'voice.start' })
      } catch (err) {
        set({ voice: 'error' })
        addSystem(`Microphone unavailable: ${err instanceof Error ? err.message : String(err)}`, 'error')
      }
    },

    verifyChain: async () => {
      try {
        const chain = await api.verifyLedger()
        set({ chain })
        addSystem(chain.valid ? `Audit chain verified: ${chain.length} events, unbroken.` : `Audit chain BROKEN at event ${chain.first_invalid_sequence}: ${chain.reason}`, chain.valid ? 'info' : 'error')
      } catch (err) {
        report(err)
      }
    },

    dismissReality: () => set({ realityAlert: null }),
    setScreen: (screen) => set({ screen }),
    setReplaying: (on) => set({ replaying: on }),
    replayLine: (role, text) => addLine({ role, text, via: 'replay' }),

    // A recorded voice turn re-run against this server: the recording supplies the words,
    // the backend recomputes every future, check and number shown on screen.
    replayTurn: async (heard) => {
      const { sessionId, txn } = get()
      if (!sessionId) return
      try {
        const r = await api.turn(sessionId, heard, txn?.current_version)
        if (r.error) set({ lastError: r.error })
        adopt(r.transaction)
      } catch (err) {
        report(err)
      }
    },
  }
})
