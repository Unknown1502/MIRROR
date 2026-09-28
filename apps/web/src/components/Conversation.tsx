import { useEffect, useRef, useState } from 'react'
import { useMirror } from '../store'

const SCRIPT = [
  'Move 700 units of XR-17 from Mumbai to the Pune site.',
  'Site 2.',
  'What if I move 900?',
  'What about 500?',
  'Compare 500 and 900.',
  'Commit 500.',
  'Rollback.',
]

export function Conversation() {
  const lines = useMirror((s) => s.lines)
  const partial = useMirror((s) => s.partial)
  const busy = useMirror((s) => s.busy || s.replaying) // the replay owns the screen while it runs
  const sendText = useMirror((s) => s.sendText)
  const voice = useMirror((s) => s.voice)
  const replaying = useMirror((s) => s.replaying)
  const [text, setText] = useState('')
  const scroller = useRef<HTMLDivElement>(null)

  useEffect(() => {
    scroller.current?.scrollTo({ top: scroller.current.scrollHeight })
  }, [lines, partial])

  const submit = (value: string) => {
    if (!value.trim() || busy) return
    void sendText(value)
    setText('')
  }

  return (
    <section className="flex min-h-0 flex-col" aria-label="Conversation">
      <div className="flex items-baseline justify-between px-4 pt-3 pb-2">
        <h2 className="text-[15px] font-semibold">Conversation</h2>
        <span className="text-[12px] text-ink-faint">{voice === 'live' ? 'Voice and typing' : 'Typing'}</span>
      </div>

      <div ref={scroller} className="min-h-0 flex-1 space-y-2.5 overflow-y-auto px-4 pb-3" aria-live="polite">
        {lines.length === 0 && !partial && (
          <p className="pt-2 text-[13px] leading-relaxed text-ink-soft">
            Give an instruction that would change inventory. MIRROR simulates every reading of it before anything real happens.
          </p>
        )}
        {lines.map((l) =>
          l.role === 'system' ? (
            <p
              key={l.id}
              className={`border-l-2 pl-2.5 text-[12px] leading-snug ${
                l.tone === 'error' ? 'border-blocked text-blocked' : l.tone === 'warn' ? 'border-warn text-warn' : 'border-rule text-ink-soft'
              }`}
            >
              {l.text}
            </p>
          ) : (
            <div key={l.id}>
              <p className="text-[11px] font-semibold text-ink-faint">
                {l.role === 'user' ? 'You' : 'MIRROR'}
                <span className="font-normal">
                  {' '}
                  {l.via === 'replay' ? (l.role === 'user' ? 'spoke, as AssemblyAI heard it (recording)' : 'said (recording)') : l.via === 'voice' ? 'spoke' : 'typed'}
                </span>
              </p>
              <p className={`text-[14px] leading-snug ${l.role === 'user' ? 'font-medium text-ink' : 'text-ink-soft'} ${l.tone === 'warn' ? 'text-warn' : ''}`}>
                {l.text}
              </p>
              {l.meta && <p className="mt-1 text-[11px] leading-snug text-shadow">{l.meta}</p>}
            </div>
          ),
        )}
        {partial && (
          <div>
            <p className="text-[11px] font-semibold text-shadow">You, live</p>
            <p className="text-[14px] italic leading-snug text-shadow">{partial.text || '…'}</p>
          </div>
        )}
      </div>

      <form
        className="border-t border-rule p-3"
        onSubmit={(e) => {
          e.preventDefault()
          submit(text)
        }}
      >
        <label htmlFor="command" className="sr-only">
          Command
        </label>
        <div className="flex gap-2">
          <input
            id="command"
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder={replaying ? 'The replay is running. Exit it to type.' : 'Type a command'}
            disabled={replaying}
            autoComplete="off"
            maxLength={500}
            className="min-w-0 flex-1 rounded-md border border-rule bg-glass px-3 py-2 text-[14px] placeholder:text-ink-faint focus:border-ink focus:outline-none"
          />
          <button
            type="submit"
            disabled={busy || !text.trim()}
            className="rounded-md bg-ink px-3.5 text-[13px] font-semibold text-glass disabled:opacity-40"
          >
            Send
          </button>
        </div>
        <p className="label mt-3 mb-1.5">Demo script</p>
        <ol className="flex flex-wrap gap-1.5">
          {SCRIPT.map((s, i) => (
            <li key={s}>
              <button
                type="button"
                disabled={busy}
                onClick={() => submit(s)}
                className="rounded-full border border-rule px-2.5 py-1 text-left text-[12px] text-ink-soft hover:border-ink hover:text-ink disabled:opacity-40"
              >
                <span className="tabular mr-1 text-ink-faint">{i + 1}</span>
                {s}
              </button>
            </li>
          ))}
        </ol>
      </form>
    </section>
  )
}
