import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  CircleStop,
  Download,
  Gauge,
  Music2,
  Octagon,
  Pause,
  Play,
  Radio,
  RefreshCw,
  SlidersHorizontal,
  Upload,
  Volume2,
} from 'lucide-react'
import { api, websocketUrl } from '../api'
import { audioDeviceLabel, isBluetoothOutput } from '../audio'
import Piano, { pianoNoteRange } from '../components/Piano'
import { Field, Metric, Notice, PanelHeader, StatusPill, Stepper } from '../components/ui'
import type {
  AudioDevice,
  LatencyProfile,
  MidiFile,
  MidiPort,
  PianoDdspCatalog,
  PianoDdspStatus,
} from '../types'

interface Props {
  status: PianoDdspStatus
  midiFiles: MidiFile[]
  audioDevices: AudioDevice[]
  audioError: string | null
  midiPorts: MidiPort[]
  onRefresh: () => Promise<void>
}

const EMPTY_STATUS: PianoDdspStatus = { state: 'stopped', running: false }

function stateTone(state: PianoDdspStatus['state']): 'ok' | 'warn' | 'error' | 'neutral' {
  if (state === 'running') return 'ok'
  if (state === 'failed') return 'error'
  if (['starting', 'switching', 'stopping'].includes(state)) return 'warn'
  return 'neutral'
}

function fixed(value: number | undefined, digits = 1): string {
  return Number.isFinite(value) ? Number(value).toFixed(digits) : '--'
}

export default function PianoDdspView({
  status: initialStatus,
  midiFiles,
  audioDevices,
  audioError,
  midiPorts,
  onRefresh,
}: Props) {
  const [catalog, setCatalog] = useState<PianoDdspCatalog | null>(null)
  const [runtime, setRuntime] = useState(initialStatus ?? EMPTY_STATUS)
  const [modelId, setModelId] = useState('paper_ir')
  const [pianoYear, setPianoYear] = useState(2018)
  const [audioDeviceId, setAudioDeviceId] = useState('')
  const [midiPort, setMidiPort] = useState('')
  const [latencyProfile, setLatencyProfile] = useState<LatencyProfile>('balanced')
  const [velocity, setVelocity] = useState(100)
  const [velocityCurve, setVelocityCurve] = useState(1)
  const [transpose, setTranspose] = useState(0)
  const [outputGainDb, setOutputGainDb] = useState(-12)
  const [reverbMix, setReverbMix] = useState(1)
  const [sustain, setSustain] = useState(false)
  const [keyboardOctave, setKeyboardOctave] = useState(3)
  const [midiId, setMidiId] = useState('')
  const [tempo, setTempo] = useState(1)
  const [loop, setLoop] = useState(false)
  const [recordingUrl, setRecordingUrl] = useState('')
  const [monitor, setMonitor] = useState(false)
  const [socketState, setSocketState] = useState<'connecting' | 'online' | 'offline'>('connecting')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const socketRef = useRef<WebSocket | null>(null)
  const audioContextRef = useRef<AudioContext | null>(null)
  const monitorTimeRef = useRef(0)
  const uploadRef = useRef<HTMLInputElement | null>(null)
  const selectedAudioDevice = audioDevices.find((device) => device.id === audioDeviceId)

  useEffect(() => setRuntime(initialStatus ?? EMPTY_STATUS), [initialStatus])

  useEffect(() => {
    api.pianoDdspCatalog().then(setCatalog).catch((cause) => setError(String(cause)))
  }, [])

  useEffect(() => {
    if (!audioDeviceId && audioDevices.length) {
      setAudioDeviceId((audioDevices.find((device) => device.is_default) ?? audioDevices[0]).id)
    }
    if (!midiPort && midiPorts.length) setMidiPort(String(midiPorts[0].port ?? midiPorts[0].id))
  }, [audioDeviceId, audioDevices, midiPort, midiPorts])

  useEffect(() => {
    let disposed = false
    let reconnectTimer: number | undefined
    const connect = () => {
      if (disposed) return
      setSocketState('connecting')
      const socket = new WebSocket(websocketUrl('/api/v1/piano-ddsp/events'))
      socketRef.current = socket
      socket.onopen = () => setSocketState('online')
      socket.onclose = () => {
        if (socketRef.current === socket) socketRef.current = null
        if (disposed) return
        setSocketState('offline')
        reconnectTimer = window.setTimeout(connect, 1000)
      }
      socket.onmessage = (event) => {
        const message = JSON.parse(event.data)
        if (message.event === 'status') setRuntime(message.data)
        if (message.event === 'error') setError(String(message.message))
        if (message.event === 'ack' && message.request === 'record_stop' && message.data?.download_url) {
          setRecordingUrl(message.data.download_url)
        }
        if (message.event === 'monitor' && message.pcm) playMonitorBlock(message.pcm, message.sample_rate)
      }
    }
    connect()
    return () => {
      disposed = true
      if (reconnectTimer !== undefined) window.clearTimeout(reconnectTimer)
      const socket = socketRef.current
      if (!socket) return
      if (socket.readyState === WebSocket.OPEN) {
        socket.send(JSON.stringify({ event: 'monitor', enabled: false }))
      }
      socket.close()
      if (socketRef.current === socket) socketRef.current = null
    }
  }, [])

  const send = useCallback((message: Record<string, unknown>) => {
    const socket = socketRef.current
    if (socket?.readyState !== WebSocket.OPEN) {
      setError('Piano-DDSP WebSocket 未连接')
      return false
    }
    socket.send(JSON.stringify(message))
    return true
  }, [])

  const playMonitorBlock = useCallback((encoded: string, sampleRate: number) => {
    const context = audioContextRef.current
    if (!context) return
    const binary = atob(encoded)
    const bytes = new Uint8Array(binary.length)
    for (let index = 0; index < binary.length; index += 1) bytes[index] = binary.charCodeAt(index)
    const samples = new Float32Array(bytes.buffer)
    const frames = Math.floor(samples.length / 2)
    const buffer = context.createBuffer(2, frames, sampleRate)
    const left = buffer.getChannelData(0)
    const right = buffer.getChannelData(1)
    for (let frame = 0; frame < frames; frame += 1) {
      left[frame] = samples[frame * 2]
      right[frame] = samples[frame * 2 + 1]
    }
    const source = context.createBufferSource()
    source.buffer = buffer
    source.connect(context.destination)
    const at = Math.max(context.currentTime + 0.01, monitorTimeRef.current)
    source.start(at)
    monitorTimeRef.current = at + buffer.duration
  }, [])

  const selectedAudio = audioDevices.find((device) => device.id === audioDeviceId)
  const bluetooth = selectedAudio ? isBluetoothOutput(selectedAudio) : false
  useEffect(() => {
    if (bluetooth && latencyProfile === 'low') setLatencyProfile('balanced')
  }, [bluetooth, latencyProfile])

  const selectedModel = catalog?.models.find((model) => model.id === modelId)
  const bundleId = selectedModel?.bundle_ids.includes(catalog?.active_bundle_id ?? '')
    ? (catalog?.active_bundle_id ?? '')
    : (selectedModel?.bundle_ids[0] ?? '')
  const activeNotes = runtime.midi?.active_notes ?? []
  const metrics = runtime.metrics
  const player = runtime.player
  const windowRange = pianoNoteRange(keyboardOctave, 32)

  async function start() {
    setBusy(true)
    setError('')
    try {
      const next = await api.startPianoDdsp({
        bundle_id: bundleId || null,
        model_id: modelId,
        piano_year: pianoYear,
        midi_port: midiPort || null,
        audio_device_id: audioDeviceId || null,
        latency_profile: latencyProfile,
        seed: 0,
        velocity_curve: velocityCurve,
        transpose,
        output_gain_db: outputGainDb,
        reverb_mix: reverbMix,
      })
      setRuntime(next)
      await onRefresh()
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause))
    } finally {
      setBusy(false)
    }
  }

  async function stop() {
    setBusy(true)
    try {
      setRuntime(await api.stopPianoDdsp())
      setMonitor(false)
      await onRefresh()
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause))
    } finally {
      setBusy(false)
    }
  }

  function updateParameter(name: string, value: number | boolean) {
    send({ event: 'parameters', values: { [name]: value } })
  }

  function switchModel(next: string) {
    setModelId(next)
    if (runtime.running) send({ event: 'parameters', values: { model_id: next } })
  }

  function switchYear(next: number) {
    setPianoYear(next)
    if (runtime.running) send({ event: 'parameters', values: { piano_year: next } })
  }

  function noteOn(note: number, noteVelocity: number) {
    send({ event: 'note_on', note, velocity: noteVelocity })
  }

  function noteOff(note: number) {
    send({ event: 'note_off', note })
  }

  async function uploadMidi(file: File) {
    setBusy(true)
    try {
      const item = await api.uploadMidi(file)
      setMidiId(item.id)
      send({ event: 'player', action: 'load', values: { midi_id: item.id } })
      await onRefresh()
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause))
    } finally {
      setBusy(false)
    }
  }

  async function toggleMonitor(enabled: boolean) {
    if (enabled) {
      const AudioContextClass = window.AudioContext
      const context = audioContextRef.current ?? new AudioContextClass()
      audioContextRef.current = context
      await context.resume()
      monitorTimeRef.current = context.currentTime
    }
    setMonitor(enabled)
    send({ event: 'monitor', enabled })
  }

  const overview = useMemo(() => {
    const active = new Set(activeNotes)
    return Array.from({ length: 88 }, (_, index) => {
      const note = index + 21
      const inWindow = note >= windowRange.first && note <= windowRange.last
      return <span key={note} className={`${active.has(note) ? 'is-active' : ''} ${inWindow ? 'is-window' : ''}`} />
    })
  }, [activeNotes, windowRange.first, windowRange.last])

  return (
    <section className="piano-ddsp-workspace">
      <div className="piano-session-bar">
        <div className="piano-session-state">
          <StatusPill tone={stateTone(runtime.state)}>{runtime.state.toUpperCase()}</StatusPill>
          <span>{runtime.midi?.connected ? 'MIDI 已连接' : midiPort ? 'MIDI 未连接' : '网页输入'}</span>
          <span>{runtime.audio?.device_lost ? '音频设备丢失' : selectedAudio?.name ?? '未选择音频'}</span>
        </div>
        <div className="piano-session-actions">
          <button className="danger-button" type="button" title="Panic" disabled={!runtime.running} onClick={() => api.panicPianoDdsp().then(setRuntime).catch((cause) => setError(String(cause)))}><Octagon size={16} />Panic</button>
          {runtime.running
            ? <button className="secondary-button" type="button" onClick={stop} disabled={busy}><CircleStop size={16} />停止</button>
            : <button className="primary-button" type="button" onClick={start} disabled={busy || !bundleId || !audioDeviceId}><Play size={16} />启动</button>}
        </div>
      </div>

      {error && <Notice tone="error">{error}</Notice>}
      {audioError && <Notice tone="error">{audioError}</Notice>}
      {selectedAudioDevice?.warning && <Notice tone="warn">{selectedAudioDevice.warning}</Notice>}
      {!bundleId && <Notice tone="warn">当前没有可用的 Piano-DDSP FP32 OM bundle</Notice>}
      {runtime.error && <Notice tone="error">{runtime.error}</Notice>}
      {bluetooth && <Notice tone="warn">蓝牙输出使用 A2DP 缓冲；low 档位已禁用</Notice>}

      <div className="piano-config-strip">
        <Field label="模型">
          <select value={modelId} onChange={(event) => switchModel(event.target.value)}>
            {(catalog?.models ?? []).map((model) => <option key={model.id} value={model.id} disabled={!model.available}>{model.name}{model.available ? '' : '（未转换）'}</option>)}
          </select>
        </Field>
        <Field label="钢琴年份">
          <select value={pianoYear} onChange={(event) => switchYear(Number(event.target.value))}>
            {(catalog?.piano_years ?? [2018]).map((year) => <option key={year} value={year}>{year}</option>)}
          </select>
        </Field>
        <Field label="MIDI 输入">
          <select value={midiPort} onChange={(event) => setMidiPort(event.target.value)} disabled={runtime.running}>
            <option value="">仅网页 / MIDI 文件</option>
            {midiPorts.map((port) => <option key={port.id} value={port.port ?? port.id}>{port.name}</option>)}
          </select>
        </Field>
        <Field label="音频输出">
          <select value={audioDeviceId} onChange={(event) => setAudioDeviceId(event.target.value)} disabled={runtime.running}>
            {audioDevices.map((device) => <option key={device.id} value={device.id}>{audioDeviceLabel(device)} [{device.backend === 'portaudio' ? 'PA' : device.backend === 'alsa_mono' ? 'ALSA mono' : 'Pulse'}]</option>)}
          </select>
        </Field>
        <Field label="延时档位">
          <select value={latencyProfile} onChange={(event) => setLatencyProfile(event.target.value as LatencyProfile)} disabled={runtime.running}>
            <option value="low" disabled={bluetooth}>Low · 16 ms</option>
            <option value="balanced">Balanced · 32 ms</option>
            <option value="safe">Safe · 64 ms</option>
          </select>
        </Field>
      </div>

      <section className="panel piano-surface">
        <PanelHeader title="88 键实时演奏" subtitle={`${activeNotes.length}/16 声部 · 力度 ${runtime.midi?.last_velocity || velocity}`} action={<StatusPill tone={socketState === 'online' ? 'ok' : 'error'}><Radio size={12} />{socketState}</StatusPill>} />
        <div className="piano-overview" aria-label="88 键概览">{overview}</div>
        <div className="desktop-piano">
          <Piano octave={1} firstNote={21} keyCount={88} velocity={velocity} activeNotes={activeNotes} recommendedMin={windowRange.first} recommendedMax={windowRange.last} disabled={!runtime.running || socketState !== 'online'} onNoteOn={noteOn} onNoteOff={noteOff} />
        </div>
        <div className="mobile-piano">
          <div className="mobile-octave"><span>32 键窗口</span><Stepper value={keyboardOctave} min={1} max={6} label="键盘八度" onChange={setKeyboardOctave} /></div>
          <Piano octave={keyboardOctave} keyCount={32} velocity={velocity} activeNotes={activeNotes} disabled={!runtime.running || socketState !== 'online'} onNoteOn={noteOn} onNoteOff={noteOff} />
        </div>
      </section>

      <div className="piano-lower-grid">
        <section className="panel piano-parameters">
          <PanelHeader title="演奏参数" action={<SlidersHorizontal size={17} />} />
          <div className="piano-control-grid">
            <Field label={`网页力度 ${velocity}`}><input type="range" min="1" max="127" value={velocity} onChange={(event) => setVelocity(Number(event.target.value))} /></Field>
            <Field label={`力度曲线 ${velocityCurve.toFixed(2)}`}><input type="range" min="0.25" max="2" step="0.05" value={velocityCurve} onChange={(event) => { const value = Number(event.target.value); setVelocityCurve(value); updateParameter('velocity_curve', value) }} /></Field>
            <Field label={`移调 ${transpose > 0 ? '+' : ''}${transpose}`}><input type="range" min="-24" max="24" step="1" value={transpose} onChange={(event) => { const value = Number(event.target.value); setTranspose(value); updateParameter('transpose', value) }} /></Field>
            <Field label={`主音量 ${outputGainDb} dB`}><input type="range" min="-60" max="0" step="1" value={outputGainDb} onChange={(event) => { const value = Number(event.target.value); setOutputGainDb(value); updateParameter('output_gain_db', value) }} /></Field>
            <Field label={`混响 ${Math.round(reverbMix * 100)}%`}><input type="range" min="0" max="1" step="0.01" value={reverbMix} onChange={(event) => { const value = Number(event.target.value); setReverbMix(value); updateParameter('reverb_mix', value) }} /></Field>
            <button type="button" className={`sustain-button ${sustain ? 'is-active' : ''}`} disabled={!runtime.running} onClick={() => { const next = !sustain; setSustain(next); send({ event: 'sustain', enabled: next }) }}><Volume2 size={17} />延音踏板</button>
          </div>
        </section>

        <section className="panel piano-player">
          <PanelHeader title="MIDI 播放器" action={<Music2 size={17} />} />
          <div className="piano-player-body">
            <div className="piano-file-row">
              <select value={midiId} onChange={(event) => { setMidiId(event.target.value); send({ event: 'player', action: 'load', values: { midi_id: event.target.value } }) }} disabled={!runtime.running}>
                <option value="">选择 MIDI 文件</option>
                {midiFiles.map((file) => <option key={file.id} value={file.id}>{file.name}</option>)}
              </select>
              <button className="icon-button" type="button" title="上传 MIDI" onClick={() => uploadRef.current?.click()} disabled={busy}><Upload size={16} /></button>
              <input ref={uploadRef} hidden type="file" accept=".mid,.midi,audio/midi" onChange={(event) => { const file = event.target.files?.[0]; if (file) uploadMidi(file) }} />
            </div>
            <input className="player-seek" type="range" min="0" max={Math.max(0.01, player?.duration_seconds ?? 0.01)} step="0.01" value={Math.min(player?.position_seconds ?? 0, player?.duration_seconds ?? 0)} onChange={(event) => send({ event: 'player', action: 'seek', values: { position_seconds: Number(event.target.value) } })} disabled={!runtime.running || !midiId} />
            <div className="piano-player-time"><span>{fixed(player?.position_seconds)} s</span><span>{fixed(player?.duration_seconds)} s</span></div>
            <div className="piano-transport">
              <button className="icon-button" title="播放" type="button" onClick={() => send({ event: 'player', action: 'play' })} disabled={!runtime.running || !midiId}><Play size={17} /></button>
              <button className="icon-button" title="暂停" type="button" onClick={() => send({ event: 'player', action: 'pause' })} disabled={!runtime.running || !midiId}><Pause size={17} /></button>
              <button className="icon-button" title="停止" type="button" onClick={() => send({ event: 'player', action: 'stop' })} disabled={!runtime.running || !midiId}><CircleStop size={17} /></button>
              <label className="compact-check"><input type="checkbox" checked={loop} onChange={(event) => { setLoop(event.target.checked); send({ event: 'player', action: 'loop', values: { enabled: event.target.checked } }) }} />循环</label>
              <select aria-label="播放速度" value={tempo} onChange={(event) => { const value = Number(event.target.value); setTempo(value); send({ event: 'player', action: 'tempo', values: { value } }) }}>
                {[0.5, 0.75, 1, 1.25, 1.5, 2].map((value) => <option key={value} value={value}>{value.toFixed(2)}×</option>)}
              </select>
            </div>
            <div className="piano-record-row">
              <button className="secondary-button" type="button" disabled={!runtime.running} onClick={() => send({ event: runtime.recording?.active ? 'record_stop' : 'record_start' })}><Radio size={16} />{runtime.recording?.active ? '停止录音' : '录音'}</button>
              <label className="compact-check"><input type="checkbox" checked={monitor} disabled={!runtime.running} onChange={(event) => toggleMonitor(event.target.checked)} />浏览器监听</label>
              {recordingUrl && <a className="icon-button" title="下载 WAV" href={recordingUrl}><Download size={17} /></a>}
            </div>
          </div>
        </section>

        <section className="panel piano-metrics">
          <PanelHeader title="实时状态" action={<button className="icon-button" type="button" title="刷新状态" onClick={() => api.pianoDdspStatus().then(setRuntime)}><RefreshCw size={16} /></button>} />
          <div className="piano-metric-grid">
            <Metric label="活跃声部" value={activeNotes.length} />
            <Metric label="NPU P50" value={fixed(metrics?.npu_p50_ms, 2)} unit="ms" />
            <Metric label="NPU P95" value={fixed(metrics?.npu_p95_ms, 2)} unit="ms" />
            <Metric label="NPU P99" value={fixed(metrics?.npu_p99_ms, 2)} unit="ms" tone={(metrics?.npu_p99_ms ?? 0) >= 4 ? 'red' : 'teal'} />
            <Metric label="DSP P95" value={fixed(metrics?.dsp_p95_ms, 2)} unit="ms" />
            <Metric label="队列" value={fixed(metrics?.queue_latency_ms)} unit="ms" />
            <Metric label="设备" value={fixed(metrics?.device_latency_ms)} unit="ms" />
            <Metric label="总估算" value={fixed(metrics?.estimated_total_latency_ms)} unit="ms" tone={(metrics?.estimated_total_latency_ms ?? 0) > 100 ? 'amber' : 'teal'} />
            <Metric label="Underrun" value={metrics?.underruns ?? 0} tone={(metrics?.underruns ?? 0) ? 'red' : undefined} />
            <Metric label="Clipped" value={metrics?.clipped_samples ?? 0} tone={(metrics?.clipped_samples ?? 0) ? 'red' : undefined} />
            <Metric label="Voice steal" value={runtime.midi?.voice_steals ?? 0} />
            <Metric label="心跳" value={fixed(runtime.heartbeat_age_seconds ?? undefined)} unit="s" />
          </div>
          <div className="piano-health-row">
            <StatusPill tone={runtime.midi?.connected || !midiPort ? 'ok' : 'error'}>MIDI</StatusPill>
            <StatusPill tone={runtime.audio?.device_lost ? 'error' : runtime.running ? 'ok' : 'neutral'}>AUDIO</StatusPill>
            <StatusPill tone={(runtime.heartbeat_age_seconds ?? 0) > 3 ? 'error' : runtime.running ? 'ok' : 'neutral'}>HEARTBEAT</StatusPill>
            <StatusPill tone="neutral"><Gauge size={12} />{metrics?.npu_samples ?? 0}</StatusPill>
          </div>
        </section>
      </div>
    </section>
  )
}
