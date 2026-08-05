import { lazy, Suspense, useCallback, useEffect, useState } from 'react'
import { Activity, AudioLines, Cpu, KeyboardMusic, RefreshCw, Waves } from 'lucide-react'
import { api, websocketUrl } from './api'
import { Notice, StatusPill } from './components/ui'
import type { AudioDevice, AudioInput, Catalog, Job, MidiPort, SystemStatus } from './types'

type Tab = 'realtime-performance' | 'midi-ddsp' | 'ddsp-vst-effect' | 'devices'

const MidiDdspView = lazy(() => import('./views/MidiDdspView'))
const DdspVstEffectView = lazy(() => import('./views/DdspVstEffectView'))
const RealtimePerformanceView = lazy(() => import('./views/RealtimePerformanceView'))
const DevicesView = lazy(() => import('./views/DevicesView'))

const NAVIGATION: { id: Tab; label: string; icon: typeof Waves }[] = [
  { id: 'realtime-performance', label: '实时演奏', icon: KeyboardMusic },
  { id: 'midi-ddsp', label: 'MIDI-DDSP', icon: Waves },
  { id: 'ddsp-vst-effect', label: 'DDSP-VST', icon: AudioLines },
  { id: 'devices', label: '设备', icon: Cpu },
]

function errorMessage(cause: unknown): string {
  return cause instanceof Error ? cause.message : String(cause)
}

export default function App() {
  const coarsePointer = window.matchMedia?.('(any-pointer: coarse)').matches ?? false
  const boardSizedDisplay = window.innerWidth >= 1500 && window.innerHeight <= 1100
  const isTouchDisplay = coarsePointer || navigator.maxTouchPoints > 0 || boardSizedDisplay
  const [tab, setTab] = useState<Tab>('realtime-performance')
  const [status, setStatus] = useState<SystemStatus | null>(null)
  const [catalog, setCatalog] = useState<Catalog | null>(null)
  const [midiDdspAudioDevices, setMidiDdspAudioDevices] = useState<AudioDevice[]>([])
  const [speakerOutputs, setSpeakerOutputs] = useState<AudioDevice[]>([])
  const [speakerAudioError, setSpeakerAudioError] = useState<string | null>(null)
  const [audioInputs, setAudioInputs] = useState<AudioInput[]>([])
  const [midiPorts, setMidiPorts] = useState<MidiPort[]>([])
  const [audioInputError, setAudioInputError] = useState<string | null>(null)
  const [midiError, setMidiError] = useState<string | null>(null)
  const [jobs, setJobs] = useState<Job[]>([])
  const [keepDdspVstMounted, setKeepDdspVstMounted] = useState(false)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [refreshedAt, setRefreshedAt] = useState<Date | null>(null)

  const refresh = useCallback(async () => {
    try {
      const [nextStatus, nextCatalog, midiDdspAudio, inputs, speakerAudio, midi, jobResponse] = await Promise.all([
        api.status(),
        api.catalog(),
        api.midiDdspAudioDevices().catch((cause) => ({ available: false, devices: [], error: errorMessage(cause) })),
        api.audioInputs().catch((cause) => ({ available: false, devices: [], error: errorMessage(cause) })),
        api.speakerOutputs().catch((cause) => ({ available: false, devices: [], error: errorMessage(cause) })),
        api.midiPorts().catch((cause) => ({ available: false, ports: [], error: errorMessage(cause) })),
        api.jobs(),
      ])
      setStatus(nextStatus)
      setCatalog(nextCatalog)
      setMidiDdspAudioDevices(midiDdspAudio.devices)
      setSpeakerOutputs(speakerAudio.devices)
      setSpeakerAudioError(speakerAudio.error)
      setAudioInputs(inputs.devices)
      setMidiPorts(midi.ports)
      setAudioInputError(inputs.error)
      setMidiError(midi.error)
      setJobs(jobResponse.jobs)
      setError('')
      setRefreshedAt(new Date())
    } catch (cause) {
      setError(errorMessage(cause))
    } finally {
      setLoading(false)
    }
  }, [])

  const refreshRuntime = useCallback(async () => {
    try {
      const [nextStatus, jobResponse] = await Promise.all([
        api.status(),
        api.jobs(),
      ])
      setStatus(nextStatus)
      setJobs(jobResponse.jobs)
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
    const timer = window.setInterval(refreshRuntime, 5000)
    return () => window.clearInterval(timer)
  }, [refresh, refreshRuntime])

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

  useEffect(() => {
    const socket = new WebSocket(websocketUrl('/api/v1/audio-output-events'))
    socket.onmessage = (event) => {
      const message = JSON.parse(event.data) as {
        event?: string
        devices?: AudioDevice[]
        error?: string | null
      }
      if ((message.event === 'snapshot' || message.event === 'audio_outputs') && message.devices) {
        setSpeakerOutputs(message.devices)
        setSpeakerAudioError(message.error ?? null)
        setRefreshedAt(new Date())
      }
      if (message.event === 'error' && message.error) setSpeakerAudioError(message.error)
    }
    return () => socket.close()
  }, [])

  useEffect(() => {
    if (tab === 'ddsp-vst-effect') setKeepDdspVstMounted(true)
  }, [tab])

  const activeJob = jobs.find((job) => ['queued', 'preparing', 'running', 'paused', 'stopping'].includes(job.state))
  const isRealtimeWorkspace = tab === 'realtime-performance'

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
    <div className={`app-shell${isTouchDisplay ? ' app-shell--touch-display' : ''}${isRealtimeWorkspace ? ' app-shell--realtime' : ''}`}>
      <header className="app-header">
        <div className="brand">
          <div className="brand-mark"><Waves size={23} /></div>
          <div><strong>MIDI-DDSP</strong><span>STUDIO</span></div>
        </div>
        <nav className="primary-nav" aria-label="主工作区">
          {NAVIGATION.map((item) => {
            const Icon = item.icon
            return <button type="button" aria-current={tab === item.id ? 'page' : undefined} aria-label={item.label} className={tab === item.id ? 'is-active' : ''} onClick={() => setTab(item.id)} key={item.id}><Icon size={20} /><span>{item.label}</span></button>
          })}
        </nav>
        <div className="system-strip">
          {activeJob && <StatusPill tone="warn"><Activity size={13} />{activeJob.kind}</StatusPill>}
          <StatusPill tone={status.primary_ip.startsWith('127.') ? 'warn' : 'ok'}>IP {status.primary_ip}</StatusPill>
          <StatusPill tone={status.npu.available ? status.npu.health_alarm ? 'warn' : 'ok' : 'neutral'}>NPU {status.npu.available ? status.npu.health_alarm ? 'ALARM' : 'READY' : 'OFFLINE'}</StatusPill>
          <StatusPill tone={speakerOutputs.length ? 'ok' : 'error'}>AUDIO {speakerOutputs.length || '—'}</StatusPill>
          <button className="icon-button" type="button" title="刷新" onClick={refresh}><RefreshCw size={17} /></button>
        </div>
      </header>

      <div className="app-main">
        {error && <div className="global-error"><Notice tone="error">{error}</Notice></div>}

        <main className="content-area">
          <Suspense fallback={<Notice tone="loading">正在载入工作区</Notice>}>
            {tab === 'midi-ddsp' && <MidiDdspView catalog={catalog} audioDevices={midiDdspAudioDevices} jobs={jobs} onRefresh={refresh} />}
            {(tab === 'ddsp-vst-effect' || keepDdspVstMounted) && (
              <div className="workspace-cache" hidden={tab !== 'ddsp-vst-effect'}>
                <DdspVstEffectView />
              </div>
            )}
            {tab === 'realtime-performance' && <RealtimePerformanceView inputMode={isTouchDisplay ? 'touch' : 'midi'} onRefresh={refresh} />}
            {tab === 'devices' && <DevicesView status={status} catalog={catalog} speakerOutputs={speakerOutputs} audioInputs={audioInputs} midiPorts={midiPorts} audioError={speakerAudioError} audioInputError={audioInputError} midiError={midiError} onRefresh={refresh} />}
          </Suspense>
        </main>

        {!isRealtimeWorkspace && (
          <footer className="status-footer">
            <span>{status.platform}</span>
            <span>{status.ip_addresses.join(' / ')}</span>
            <span>Python {status.python}</span>
            <span>{refreshedAt ? `SYNC ${refreshedAt.toLocaleTimeString()}` : 'SYNC —'}</span>
          </footer>
        )}
      </div>

      <nav className="bottom-nav">
        {NAVIGATION.map((item) => {
          const Icon = item.icon
          return <button type="button" aria-label={item.label} aria-current={tab === item.id ? 'page' : undefined} className={tab === item.id ? 'is-active' : ''} onClick={() => setTab(item.id)} key={item.id}><Icon size={20} /><span>{item.label}</span></button>
        })}
      </nav>
    </div>
  )
}
