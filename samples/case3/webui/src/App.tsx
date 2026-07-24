import { lazy, Suspense, useCallback, useEffect, useMemo, useState } from 'react'
import { Activity, Cpu, FlaskConical, KeyboardMusic, RefreshCw, Waves } from 'lucide-react'
import { api, websocketUrl } from './api'
import { Notice, StatusPill } from './components/ui'
import type { AudioDevice, AudioInput, BenchmarkSummary, Catalog, Job, MidiPort, SystemStatus } from './types'

type Tab = 'midi-ddsp' | 'ddsp-vst' | 'lab' | 'devices'

const MidiDdspView = lazy(() => import('./views/MidiDdspView'))
const PerformView = lazy(() => import('./views/PerformView'))
const LabView = lazy(() => import('./views/LabView'))
const DevicesView = lazy(() => import('./views/DevicesView'))

const NAVIGATION: { id: Tab; label: string; icon: typeof Waves }[] = [
  { id: 'midi-ddsp', label: 'MIDI-DDSP', icon: Waves },
  { id: 'ddsp-vst', label: 'DDSP-VST', icon: KeyboardMusic },
  { id: 'lab', label: '实验', icon: FlaskConical },
  { id: 'devices', label: '设备', icon: Cpu },
]

function errorMessage(cause: unknown): string {
  return cause instanceof Error ? cause.message : String(cause)
}

export default function App() {
  const [tab, setTab] = useState<Tab>('midi-ddsp')
  const [status, setStatus] = useState<SystemStatus | null>(null)
  const [catalog, setCatalog] = useState<Catalog | null>(null)
  const [audioDevices, setAudioDevices] = useState<AudioDevice[]>([])
  const [speakerOutputs, setSpeakerOutputs] = useState<AudioDevice[]>([])
  const [audioInputs, setAudioInputs] = useState<AudioInput[]>([])
  const [midiPorts, setMidiPorts] = useState<MidiPort[]>([])
  const [audioError, setAudioError] = useState<string | null>(null)
  const [audioInputError, setAudioInputError] = useState<string | null>(null)
  const [midiError, setMidiError] = useState<string | null>(null)
  const [jobs, setJobs] = useState<Job[]>([])
  const [summary, setSummary] = useState<BenchmarkSummary | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [refreshedAt, setRefreshedAt] = useState<Date | null>(null)

  const refresh = useCallback(async () => {
    try {
      const [nextStatus, nextCatalog, audio, inputs, speakerAudio, midi, jobResponse, benchmark] = await Promise.all([
        api.status(),
        api.catalog(),
        api.audioDevices().catch((cause) => ({ available: false, devices: [], error: errorMessage(cause) })),
        api.audioInputs().catch((cause) => ({ available: false, devices: [], error: errorMessage(cause) })),
        api.speakerOutputs().catch((cause) => ({ available: false, devices: [], error: errorMessage(cause) })),
        api.midiPorts().catch((cause) => ({ available: false, ports: [], error: errorMessage(cause) })),
        api.jobs(),
        api.benchmark(),
      ])
      setStatus(nextStatus)
      setCatalog(nextCatalog)
      setAudioDevices(audio.devices)
      setSpeakerOutputs(speakerAudio.devices)
      setAudioInputs(inputs.devices)
      setMidiPorts(midi.ports)
      setAudioError(audio.error)
      setAudioInputError(inputs.error)
      setMidiError(midi.error)
      setJobs(jobResponse.jobs)
      setSummary(benchmark.summary)
      setError('')
      setRefreshedAt(new Date())
    } catch (cause) {
      setError(errorMessage(cause))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    refresh()
    const timer = window.setInterval(refresh, 5000)
    return () => window.clearInterval(timer)
  }, [refresh])

  useEffect(() => {
    const socket = new WebSocket(websocketUrl('/api/v1/events'))
    socket.onmessage = (event) => {
      const message = JSON.parse(event.data)
      if (message.event === 'snapshot') setJobs(message.jobs)
      if (message.job) {
        setJobs((current) => [message.job, ...current.filter((job) => job.id !== message.job.id)].sort((a, b) => b.created_at.localeCompare(a.created_at)))
      }
    }
    return () => socket.close()
  }, [])

  const activeTab = useMemo(() => NAVIGATION.find((item) => item.id === tab)!, [tab])
  const ActiveIcon = activeTab.icon
  const activeJob = jobs.find((job) => ['queued', 'preparing', 'running', 'paused', 'stopping'].includes(job.state))

  if (loading && (!status || !catalog)) {
    return (
      <main className="boot-screen">
        <div className="brand-mark large"><Waves size={34} /></div>
        <strong>MIDI-DDSP STUDIO</strong>
        <Notice tone="loading">正在连接开发板</Notice>
      </main>
    )
  }

  if (!status || !catalog) {
    return (
      <main className="boot-screen error-screen">
        <div className="brand-mark large"><Waves size={34} /></div>
        <strong>MIDI-DDSP STUDIO</strong>
        <Notice tone="error">{error || '服务不可用'}</Notice>
        <button className="primary-button" type="button" onClick={refresh}><RefreshCw size={17} />重新连接</button>
      </main>
    )
  }

  return (
    <div className="app-shell">
      <aside className="side-rail">
        <div className="brand">
          <div className="brand-mark"><Waves size={23} /></div>
          <div><strong>MIDI-DDSP</strong><span>STUDIO</span></div>
        </div>
        <nav>
          {NAVIGATION.map((item) => {
            const Icon = item.icon
            return <button type="button" aria-label={item.label} className={tab === item.id ? 'is-active' : ''} onClick={() => setTab(item.id)} key={item.id}><Icon size={20} /><span>{item.label}</span></button>
          })}
        </nav>
        <div className="rail-footer">
          <span className={`connection-dot ${status.is_ascend_board ? 'online' : ''}`} />
          <div><strong>{status.hostname}</strong><small>{status.is_ascend_board ? 'Ascend 310B4' : '开发预览'}</small></div>
        </div>
      </aside>

      <div className="app-main">
        <header className="top-bar">
          <div className="page-title"><ActiveIcon size={20} /><h1>{activeTab.label}</h1></div>
          <div className="system-strip">
            {activeJob && <StatusPill tone="warn"><Activity size={13} />{activeJob.kind}</StatusPill>}
            <StatusPill tone={status.npu.available ? status.npu.health_alarm ? 'warn' : 'ok' : 'neutral'}>NPU {status.npu.available ? status.npu.health_alarm ? 'ALARM' : 'READY' : 'OFFLINE'}</StatusPill>
            <StatusPill tone={audioDevices.length ? 'ok' : 'error'}>AUDIO {audioDevices.length || '—'}</StatusPill>
            <button className="icon-button" type="button" title="刷新" onClick={refresh}><RefreshCw size={17} /></button>
          </div>
        </header>

        {error && <div className="global-error"><Notice tone="error">{error}</Notice></div>}

        <main className="content-area">
          <Suspense fallback={<Notice tone="loading">正在载入工作区</Notice>}>
            {tab === 'midi-ddsp' && <MidiDdspView catalog={catalog} audioDevices={audioDevices} jobs={jobs} onRefresh={refresh} />}
            {tab === 'ddsp-vst' && <PerformView status={status} catalog={catalog} audioDevices={audioDevices} midiPorts={midiPorts} onRefresh={refresh} />}
            {tab === 'lab' && <LabView jobs={jobs} summary={summary} onRefresh={refresh} />}
            {tab === 'devices' && <DevicesView status={status} catalog={catalog} audioDevices={audioDevices} speakerOutputs={speakerOutputs} audioInputs={audioInputs} midiPorts={midiPorts} audioError={audioError} audioInputError={audioInputError} midiError={midiError} onRefresh={refresh} />}
          </Suspense>
        </main>

        <footer className="status-footer">
          <span>{status.platform}</span>
          <span>Python {status.python}</span>
          <span>{refreshedAt ? `SYNC ${refreshedAt.toLocaleTimeString()}` : 'SYNC —'}</span>
        </footer>
      </div>

      <nav className="bottom-nav">
        {NAVIGATION.map((item) => {
          const Icon = item.icon
          return <button type="button" aria-label={item.label} className={tab === item.id ? 'is-active' : ''} onClick={() => setTab(item.id)} key={item.id}><Icon size={20} /><span>{item.label}</span></button>
        })}
      </nav>
    </div>
  )
}
