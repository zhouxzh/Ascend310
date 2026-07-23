import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Gauge, KeyboardMusic, Octagon, Play, Radio, SlidersHorizontal, Volume2 } from 'lucide-react'
import { api, websocketUrl } from '../api'
import Piano from '../components/Piano'
import { Field, Metric, Notice, PanelHeader, StatusPill, Stepper } from '../components/ui'
import type { AudioDevice, Catalog, LiveStatus, MidiPort, SystemStatus } from '../types'

interface Props {
  status: SystemStatus
  catalog: Catalog
  audioDevices: AudioDevice[]
  midiPorts: MidiPort[]
  onRefresh: () => Promise<void>
}

const KEYBOARD_MAP: Record<string, number> = {
  a: 0,
  w: 1,
  s: 2,
  e: 3,
  d: 4,
  f: 5,
  t: 6,
  g: 7,
  y: 8,
  h: 9,
  u: 10,
  j: 11,
  k: 12,
  o: 13,
  l: 14,
  p: 15,
  ';': 16,
}

export default function PerformView({ status, catalog, audioDevices, midiPorts, onRefresh }: Props) {
  const preferredModel = useMemo(
    () => catalog.live_models.find((model) => model.instrument.toLowerCase() === 'violin' && model.backend === 'om' && model.precision === 'mixed_float16')
      ?? catalog.live_models.find((model) => model.instrument.toLowerCase() === 'violin')
      ?? catalog.live_models.find((model) => model.backend === 'om' && model.precision === 'mixed_float16')
      ?? catalog.live_models[0],
    [catalog.live_models],
  )
  const [modelId, setModelId] = useState(preferredModel?.id ?? '')
  const [audioDeviceId, setAudioDeviceId] = useState('')
  const [midiPort, setMidiPort] = useState('')
  const [octave, setOctave] = useState(4)
  const [velocity, setVelocity] = useState(100)
  const [voices, setVoices] = useState(8)
  const [gain, setGain] = useState(0)
  const [attack, setAttack] = useState(0.1)
  const [release, setRelease] = useState(1.2)
  const [sustain, setSustain] = useState(false)
  const [liveStatus, setLiveStatus] = useState<LiveStatus>(status.live)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [socketState, setSocketState] = useState<'offline' | 'connecting' | 'online'>('offline')
  const socketRef = useRef<WebSocket | null>(null)
  const pressedKeys = useRef(new Map<string, number>())

  useEffect(() => {
    if (!modelId && preferredModel) setModelId(preferredModel.id)
  }, [modelId, preferredModel])

  useEffect(() => setLiveStatus(status.live), [status.live])

  useEffect(() => {
    if (!liveStatus.running) {
      socketRef.current?.close()
      socketRef.current = null
      setSocketState('offline')
      return
    }
    setSocketState('connecting')
    const socket = new WebSocket(websocketUrl('/api/v1/live/events'))
    socketRef.current = socket
    socket.onopen = () => setSocketState('online')
    socket.onmessage = (event) => {
      const message = JSON.parse(event.data)
      if (message.event === 'status') setLiveStatus(message.data)
      if (message.event === 'error') setError(message.message)
    }
    socket.onclose = () => setSocketState('offline')
    return () => {
      socket.close()
      if (socketRef.current === socket) socketRef.current = null
    }
  }, [liveStatus.running])

  const send = useCallback((message: Record<string, unknown>) => {
    const socket = socketRef.current
    if (socket?.readyState === WebSocket.OPEN) socket.send(JSON.stringify(message))
  }, [])

  const noteOn = useCallback((note: number, noteVelocity: number) => send({ event: 'note_on', note, velocity: noteVelocity }), [send])
  const noteOff = useCallback((note: number) => send({ event: 'note_off', note }), [send])

  useEffect(() => {
    const isTyping = (target: EventTarget | null) => target instanceof HTMLInputElement || target instanceof HTMLSelectElement || target instanceof HTMLTextAreaElement
    const down = (event: KeyboardEvent) => {
      if (event.repeat || isTyping(event.target) || !(event.key.toLowerCase() in KEYBOARD_MAP)) return
      const note = (octave + 1) * 12 + KEYBOARD_MAP[event.key.toLowerCase()]
      pressedKeys.current.set(event.key.toLowerCase(), note)
      noteOn(note, velocity)
      event.preventDefault()
    }
    const up = (event: KeyboardEvent) => {
      const note = pressedKeys.current.get(event.key.toLowerCase())
      if (note === undefined) return
      pressedKeys.current.delete(event.key.toLowerCase())
      noteOff(note)
      event.preventDefault()
    }
    window.addEventListener('keydown', down)
    window.addEventListener('keyup', up)
    return () => {
      window.removeEventListener('keydown', down)
      window.removeEventListener('keyup', up)
    }
  }, [noteOff, noteOn, octave, velocity])

  useEffect(() => {
    const releaseBrowserInput = () => {
      pressedKeys.current.clear()
      setSustain(false)
      send({ event: 'all_notes_off' })
    }
    const visibilityChanged = () => {
      if (document.visibilityState === 'hidden') releaseBrowserInput()
    }
    window.addEventListener('blur', releaseBrowserInput)
    document.addEventListener('visibilitychange', visibilityChanged)
    return () => {
      window.removeEventListener('blur', releaseBrowserInput)
      document.removeEventListener('visibilitychange', visibilityChanged)
    }
  }, [send])

  async function start() {
    setBusy(true)
    setError('')
    try {
      const next = await api.startLive({
        model_id: modelId,
        audio_device_id: audioDeviceId || null,
        midi_port: midiPort || null,
        sample_rate: 48000,
        prebuffer: 6,
        max_voices: voices,
        audio_latency_ms: 80,
        output_gain_db: gain,
        attack,
        decay: 0,
        sustain: 1,
        release,
        device_id: 0,
      })
      setLiveStatus(next)
      await onRefresh()
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause))
    } finally {
      setBusy(false)
    }
  }

  async function stop() {
    setBusy(true)
    setError('')
    try {
      send({ event: 'all_notes_off' })
      setLiveStatus(await api.stopLive())
      await onRefresh()
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause))
    } finally {
      setBusy(false)
    }
  }

  function toggleSustain() {
    const next = !sustain
    setSustain(next)
    send({ event: 'sustain', enabled: next })
  }

  const metrics = liveStatus.metrics
  const unavailable = catalog.live_models.length === 0 || audioDevices.length === 0

  return (
    <div className="workspace perform-workspace">
      <section className="panel performance-stage">
        <PanelHeader
          title="实时演奏"
          subtitle={preferredModel ? `${preferredModel.instrument} · ${liveStatus.backend ?? preferredModel.backend.toUpperCase()}` : '未发现可用模型'}
          action={
            <div className="transport-actions">
              <StatusPill tone={liveStatus.running && socketState === 'online' ? 'ok' : socketState === 'connecting' ? 'warn' : 'neutral'}>
                <Radio size={13} /> {liveStatus.running ? socketState : '待机'}
              </StatusPill>
              <button className={`primary-button ${liveStatus.running ? 'danger-button' : ''}`} type="button" disabled={busy || (!liveStatus.running && unavailable)} onClick={liveStatus.running ? stop : start}>
                {liveStatus.running ? <Octagon size={18} /> : <Play size={18} fill="currentColor" />}
                {busy ? '处理中' : liveStatus.running ? '停止' : '启动引擎'}
              </button>
            </div>
          }
        />

        {error && <Notice tone="error">{error}</Notice>}
        {unavailable && !error && (
          <Notice tone="error">
            {catalog.live_models.length === 0 ? '未发现可用的实时模型。' : '未发现可用的音频输出设备。'}
          </Notice>
        )}

        <div className="performance-readout">
          <div className="note-display">
            <KeyboardMusic size={22} />
            <div>
              <span>ACTIVE NOTES</span>
              <strong>{liveStatus.active_notes.length ? liveStatus.active_notes.join(' · ') : '—'}</strong>
            </div>
          </div>
          <div className="metrics-row compact-metrics">
            <Metric label="声部" value={liveStatus.active_notes.length} />
            <Metric label="最大推理" value={(metrics?.max_render_ms ?? 0).toFixed(2)} unit="ms" tone="teal" />
            <Metric label="缓冲" value={metrics?.buffered_blocks ?? 0} />
            <Metric label="下溢" value={metrics?.underruns ?? 0} tone={(metrics?.underruns ?? 0) > 0 ? 'red' : undefined} />
          </div>
        </div>

        <Piano octave={octave} velocity={velocity} activeNotes={liveStatus.active_notes} disabled={!liveStatus.running || socketState !== 'online'} onNoteOn={noteOn} onNoteOff={noteOff} />

        <div className="performance-controls">
          <div className="control-block">
            <span>OCTAVE</span>
            <Stepper value={octave} min={1} max={7} onChange={setOctave} label="八度" />
          </div>
          <label className="control-block range-block">
            <span>VELOCITY <strong>{velocity}</strong></span>
            <input type="range" min="1" max="127" value={velocity} onChange={(event) => setVelocity(Number(event.target.value))} />
          </label>
          <button type="button" className={`sustain-button ${sustain ? 'is-active' : ''}`} onClick={toggleSustain} disabled={!liveStatus.running}>
            SUSTAIN
          </button>
        </div>
      </section>

      <aside className="panel settings-panel">
        <PanelHeader title="音源设置" action={<SlidersHorizontal size={18} />} />
        <div className="form-grid single-column">
          <Field label="模型">
            <select value={modelId} onChange={(event) => setModelId(event.target.value)} disabled={liveStatus.running}>
              {catalog.live_models.map((model) => <option value={model.id} key={model.id}>{model.instrument} · {model.backend.toUpperCase()} · {model.precision}</option>)}
            </select>
          </Field>
          <Field label="音频输出">
            <select value={audioDeviceId} onChange={(event) => setAudioDeviceId(event.target.value)} disabled={liveStatus.running}>
              <option value="">系统默认</option>
              {audioDevices.map((device) => <option value={device.id} key={device.id}>{device.name}</option>)}
            </select>
          </Field>
          <Field label="MIDI 输入">
            <select value={midiPort} onChange={(event) => setMidiPort(event.target.value)} disabled={liveStatus.running}>
              <option value="">仅触控与电脑键盘</option>
              {midiPorts.map((port) => <option value={port.name} key={port.id}>{port.name}</option>)}
            </select>
          </Field>
          <Field label="最大声部">
            <Stepper value={voices} min={1} max={16} onChange={setVoices} label="最大声部" />
          </Field>
          <Field label={`输出增益 ${gain > 0 ? '+' : ''}${gain} dB`}>
            <input type="range" min="-24" max="30" value={gain} onChange={(event) => setGain(Number(event.target.value))} />
          </Field>
          <Field label={`Attack ${attack.toFixed(2)} s`}>
            <input type="range" min="0.01" max="1" step="0.01" value={attack} onChange={(event) => setAttack(Number(event.target.value))} />
          </Field>
          <Field label={`Release ${release.toFixed(2)} s`}>
            <input type="range" min="0.05" max="3" step="0.05" value={release} onChange={(event) => setRelease(Number(event.target.value))} />
          </Field>
        </div>
        <div className="settings-footer">
          <Volume2 size={17} />
          <span>48 kHz · 80 ms</span>
          <Gauge size={17} />
          <span>{voices} voices</span>
        </div>
      </aside>
    </div>
  )
}
