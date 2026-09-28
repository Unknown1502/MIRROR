import { useEffect, useState } from 'react'
import { AuditTimeline } from './components/AuditTimeline'
import { Conversation } from './components/Conversation'
import { Futures } from './components/Futures'
import { Header } from './components/Header'
import { IntentStrip } from './components/IntentStrip'
import { Landing } from './components/Landing'
import { MetricsPanel } from './components/MetricsPanel'
import { MirrorLine } from './components/MirrorLine'
import { Reality } from './components/Reality'
import { RealityBanner } from './components/RealityBanner'
import { ReplayBar } from './components/ReplayBar'
import { TransactionPanel } from './components/TransactionPanel'
import { useMirror } from './store'

export default function App() {
  const init = useMirror((s) => s.init)
  const bootError = useMirror((s) => s.bootError)
  const screen = useMirror((s) => s.screen)
  const setScreen = useMirror((s) => s.setScreen)
  const [replay, setReplay] = useState(false)

  useEffect(() => {
    void init()
  }, [init])

  const watch = () => {
    setScreen('console')
    setReplay(true)
  }

  if (screen === 'landing') return <Landing onWatch={watch} />

  return (
    <div className={`flex min-h-full flex-col lg:h-full lg:min-h-0 ${replay ? 'pb-72 sm:pb-56' : ''}`}>
      <Header onReplay={watch} />
      {bootError && (
        <div className="border-b border-blocked bg-blocked-soft px-5 py-2 text-[13px] text-blocked" role="alert">
          Could not start a session: {bootError}{' '}
          <button className="font-semibold underline" onClick={() => void init()}>
            Retry
          </button>
        </div>
      )}
      <main className="grid flex-1 grid-cols-1 lg:min-h-0 lg:grid-cols-[300px_minmax(0,1fr)_320px]">
        <aside className="order-2 flex h-105 flex-col border-y border-rule bg-glass/60 lg:order-0 lg:h-auto lg:min-h-0 lg:border-y-0 lg:border-r">
          <Conversation />
        </aside>

        <div id="mirror-center" className="order-1 flex flex-col gap-3 px-4 py-4 lg:order-0 lg:min-h-0 lg:overflow-y-auto lg:px-6">
          <div className="shrink-0">
            <RealityBanner />
          </div>
          <div className="shrink-0">
            <IntentStrip />
          </div>
          <div className="shrink-0">
            <Futures />
          </div>
          <div className="shrink-0">
            <MirrorLine />
          </div>
          <div className="shrink-0">
            <Reality />
          </div>
        </div>

        <aside className="order-3 border-rule bg-glass/60 lg:min-h-0 lg:overflow-y-auto lg:border-l">
          <TransactionPanel />
          <AuditTimeline />
          <MetricsPanel />
        </aside>
      </main>
      {replay && <ReplayBar onExit={() => setReplay(false)} />}
    </div>
  )
}
