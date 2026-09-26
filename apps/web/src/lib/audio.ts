// Browser audio for the AssemblyAI Voice Agent path.
// Mic -> AudioWorklet -> resample to 24 kHz -> Float32 to PCM16 -> base64 -> backend -> input.audio
// reply.audio (PCM16 24 kHz) -> AudioBufferSourceNodes scheduled on the AudioContext clock.

const TARGET_RATE = 24000
const CHUNK_SAMPLES = 1200 // 50 ms at 24 kHz, the chunk size AssemblyAI recommends

const WORKLET = `
class MirrorCapture extends AudioWorkletProcessor {
  constructor(options) {
    super();
    this.ratio = sampleRate / ${TARGET_RATE};
    this.pos = 0;
    this.out = new Int16Array(${CHUNK_SAMPLES});
    this.n = 0;
    this.level = 0;
    this.levelFrames = 0;
  }
  process(inputs) {
    const ch = inputs[0] && inputs[0][0];
    if (!ch) return true;
    // Linear-interpolation resampler from the device rate down to 24 kHz.
    while (this.pos < ch.length - 1) {
      const i = Math.floor(this.pos), f = this.pos - i;
      const s = ch[i] * (1 - f) + ch[i + 1] * f;
      const c = Math.max(-1, Math.min(1, s));
      this.out[this.n++] = c < 0 ? c * 0x8000 : c * 0x7fff;
      this.level = Math.max(this.level * 0.999, Math.abs(c));
      if (this.n === ${CHUNK_SAMPLES}) {
        this.port.postMessage({ pcm: this.out.buffer, level: this.level }, [this.out.buffer]);
        this.out = new Int16Array(${CHUNK_SAMPLES});
        this.n = 0;
      }
      this.pos += this.ratio;
    }
    this.pos -= ch.length;
    return true;
  }
}
registerProcessor('mirror-capture', MirrorCapture);
`

function toBase64(buf: ArrayBuffer): string {
  const bytes = new Uint8Array(buf)
  let binary = ''
  for (let i = 0; i < bytes.length; i += 0x8000) {
    binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000))
  }
  return btoa(binary)
}

export class MicCapture {
  private ctx: AudioContext | null = null
  private stream: MediaStream | null = null
  private node: AudioWorkletNode | null = null

  async start(onChunk: (b64: string) => void, onLevel: (level: number) => void): Promise<void> {
    this.stream = await navigator.mediaDevices.getUserMedia({
      audio: {
        channelCount: 1,
        echoCancellation: true, // the agent must not hear itself
        noiseSuppression: false, // AssemblyAI denoises server-side; double denoising hurts accuracy
        autoGainControl: true,
      },
    })
    this.ctx = new AudioContext()
    const url = URL.createObjectURL(new Blob([WORKLET], { type: 'application/javascript' }))
    await this.ctx.audioWorklet.addModule(url)
    URL.revokeObjectURL(url)
    const source = this.ctx.createMediaStreamSource(this.stream)
    this.node = new AudioWorkletNode(this.ctx, 'mirror-capture')
    this.node.port.onmessage = (e: MessageEvent<{ pcm: ArrayBuffer; level: number }>) => {
      onChunk(toBase64(e.data.pcm))
      onLevel(e.data.level)
    }
    source.connect(this.node)
  }

  async stop(): Promise<void> {
    this.node?.disconnect()
    this.stream?.getTracks().forEach((t) => t.stop())
    await this.ctx?.close().catch(() => undefined)
    this.node = null
    this.stream = null
    this.ctx = null
  }
}

export class AgentPlayback {
  private ctx: AudioContext | null = null
  private nextTime = 0
  private sources = new Set<AudioBufferSourceNode>()
  onActivity: (playing: boolean) => void = () => undefined

  private ensure(): AudioContext {
    if (!this.ctx) this.ctx = new AudioContext()
    if (this.ctx.state === 'suspended') void this.ctx.resume()
    return this.ctx
  }

  play(b64: string): void {
    const ctx = this.ensure()
    const bin = atob(b64)
    const count = Math.floor(bin.length / 2)
    if (count === 0) return
    const buffer = ctx.createBuffer(1, count, TARGET_RATE)
    const data = buffer.getChannelData(0)
    for (let i = 0; i < count; i++) {
      let v = bin.charCodeAt(2 * i) | (bin.charCodeAt(2 * i + 1) << 8)
      if (v >= 0x8000) v -= 0x10000
      data[i] = v / 0x8000
    }
    const src = ctx.createBufferSource()
    src.buffer = buffer
    src.connect(ctx.destination)
    const startAt = Math.max(ctx.currentTime + 0.02, this.nextTime)
    src.start(startAt)
    this.nextTime = startAt + buffer.duration
    this.sources.add(src)
    this.onActivity(true)
    src.onended = () => {
      this.sources.delete(src)
      if (this.sources.size === 0) this.onActivity(false)
    }
  }

  /** Barge-in: drop everything queued so the user never hears stale speech. */
  flush(): void {
    for (const s of this.sources) {
      try {
        s.stop()
      } catch {
        /* already stopped */
      }
    }
    this.sources.clear()
    this.nextTime = 0
    this.onActivity(false)
  }

  unlock(): void {
    this.ensure()
  }
}
