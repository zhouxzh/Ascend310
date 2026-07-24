import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Gauge, KeyboardMusic, Octagon, Play, Radio, SlidersHorizontal, Volume2 } from 'lucide-react'
import { api, websocketUrl } from '../api'
import Piano from '../components/Piano'
import { Field, Metric, Notice, PanelHeader, StatusPill, Stepper } from '../components/ui'
import type { AudioDevice, Catalog, DdspVstParameters, DdspVstStatus, MidiPort, SystemStatus } from '../types'

interface Props {
  status: SystemStatus
  catalog: Catalog
  audioDevices: AudioDevice[]
  midiPorts: MidiPort[]
  onRefresh: () => Promise<void>
}

const KEYBOARD_MAP: Record<string, number> = {
  a: 0, w: 1, s: 2, e: 3, d: 4, f: 5, t: 6, g: 7, y: 8,
  h: 9, u: 10, j: 11, k: 12, o: 13, l: 14, p: 15, ';': 16,
}

const DEFAULT_PARAMETERS: DdspVstParameters = {
  pitch_shift: 0,
  harmonic_gain: 1,
  noise_gain: 1,
  output_gain_db: 0,
  attack: 0.1,
  decay: 0,
  sustain: 1,
  release: 1.2,
  input_pitch: 0,
  input_gain: 0,
  reverb_size: 0.4,
  reverb_damping: 0.1,
  reverb_wet: 0,
}

export default function PerformView({ status, catalog, audioDevices, midiPorts, onRefresh }: Props) {
  const preferredModel = useMemo(
    () => catalog.ddsp_vst_models.find((model) => model.instrument.toLowerCase() === 'violin' && model.precision === 'mixed_float16')
      ?? catalog.ddsp_vst_models.find((model) => model.precision === 'mixed_float16')
      ?? catalog.ddsp_vst_models[0],
    [catalog.ddsp_vst_models],
  )
  const [modelId, setModelId] = useState(preferredModel?.id ?? '')
  const [audioDeviceId, setAudioDeviceId] = useState('')
  const [midiPort, setMidiPort] = useState('')
  const [octave, setOctave] = useState(4)
  const [velocity, setVelocity] = useState(100)
  const [voices, setVoices] = useState(1)
  const [advanced, setAdvanced] = useState(false)
  const [parameters, setParameters] = useState<DdspVstParameters>(DEFAULT_PARAMETERS)
  const [pitchBend, setPitchBend] = useState(0)
  const [sustainPedal, setSustainPedal] = useState(false)
  const [synthStatus, setSynthStatus] = useState<DdspVstStatus>(status.ddsp_vst)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [socketState, setSocketState] = useState<'offline' | 'connecting' | 'online'>('offline')
  const socketRef = useRef<WebSocket | null>(null)
  const pressedKeys = useRef(new Map<string, number>())

  const modelOptions = useMemo(
    () => advanced ? catalog.ddsp_vst_models : catalog.ddsp_vst_models.filter((model) => model.precision === 'mixed_float16'),
    [advanced, catalog.ddsp_vst_models],
  )

  useEffect(() => {
    if (!modelId && preferredModel) setModelId(preferredModel.id)
  }, [modelId, preferredModel])

  useEffect(() => {
    if (!advanced && modelOptions.length && !modelOptions.some((model) => model.id === modelId)) {
      setModelId(modelOptions[0].id)
    }
  }, [advanced, modelId, modelOptions])

  useEffect(() => setSynthStatus(status.ddsp_vst), [status.ddsp_vst])

  useEffect(() => {
    if (!synthStatus.running) {
      socketRef.current?.close()
      socketRef.current = null
      setSocketState('offline')
      return
    }
    setSocketState('connecting')
    const socket = new WebSocket(websocketUrl('/api/v1/ddsp-vst/events'))
    socketRef.current = socket
    socket.onopen = () => setSocketState('online')
    socket.onmessage = (event) => {
      const message = JSON.parse(event.data)
      if (message.event === 'status') setSynthStatus(message.data)
      if (message.event === 'error') setError(message.message)
    }
    socket.onclose = () => setSocketState('offline')
    return () => {
      if (socket.readyState === WebSocket.OPEN) {
        socket.send(JSON.stringify({ event: 'all_notes_off' }))
      }
      socket.close()
      if (socketRef.current === socket) socketRef.current = null
    }
  }, [synthStatus.running])

  const send = useCallback((message: Record<string, unknown>) => {
    const socket = socketRef.current
    if (socket?.readyState === WebSocket.OPEN) socket.send(JSON.stringify(message))
  }, [])

  const setParameter = useCallback((name: keyof DdspVstParameters, value: number) => {
    setParameters((current) => ({ ...current, [name]: value }))
    send({ event: 'parameters', values: { [name]: value } })
  }, [send])

  const noteOn = useCallback((note: number, noteVelocity: number) => {
    send({ event: 'note_on', note, velocity: noteVelocity })
  }, [send])
  const noteOff = useCallback((note: number) => send({ event: 'note_off', note }), [send])

  useEffect(() => {
    const isTyping = (target: EventTarget | null) => target instanceof HTMLInputElement || target instanceof HTMLSelectElement || target instanceof HTMLTextAreaElement
    const down = (event: KeyboardEvent) => {
      const key = event.key.toLowerCase()
      if (event.repeat || isTyping(event.target) || !(key in KEYBOARD_MAP)) return
      const note = (octave + 1) * 12 + KEYBOARD_MAP[key]
      pressedKeys.current.set(key, note)
      noteOn(note, velocity)
      event.preventDefault()
    }
    const up = (event: KeyboardEvent) => {
      const key = event.key.toLowerCase()
      const note = pressedKeys.current.get(key)
      if (note === undefined) return
      pressedKeys.current.delete(key)
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
      setSustainPedal(false)
      setPitchBend(0)
      send({ event: 'all_notes_off' })
      send({ event: 'pitch_bend', value: 0 })
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
      const next = await api.startDdspVst({
        model_id: modelId,
        audio_device_id: audioDeviceId || null,
        midi_port: midiPort || null,
        sample_rate: 48000,
        prebuffer: 6,
        max_voices: voices,
        audio_latency_ms: 80,
        ...parameters,
        device_id: 0,
      })
      setSynthStatus(next)
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
      setSynthStatus(await api.stopDdspVst())
      await onRefresh()
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause))
    } finally {
      setBusy(false)
    }
  }

  function toggleSustain() {
    const next = !sustainPedal
    setSustainPedal(next)
    send({ event: 'sustain', enabled: next })
  }

  function changePitchBend(value: number) {
    setPitchBend(value)
    send({ event: 'pitch_bend', value })
  }

  const metrics = synthStatus.metrics
  const unavailable = catalog.ddsp_vst_models.length === 0 || audioDevices.length === 0

  return (
    <div className="workspace perform-workspace">
      <section className="panel performance-stage">
        <PanelHeader
          title="DDSP-VST Synth"
          subtitle={preferredModel ? `${preferredModel.instrument} · ${synthStatus.backend ?? 'OM'}` : '未发现可用模型'}
          action={
            <div className="transport-actions">
              <StatusPill tone={synthStatus.running && socketState === 'online' ? 'ok' : socketState === 'connecting' ? 'warn' : 'neutral'}>
                <Radio size={13} /> {synthStatus.running ? socketState : '待机'}
              </StatusPill>
              <button className={`primary-button ${synthStatus.running ? 'danger-button' : ''}`} type="button" disabled={busy || (!synthStatus.running && unavailable)} onClick={synthStatus.running ? stop : start}>
                {synthStatus.running ? <Octagon size={18} /> : <Play size={18} fill="currentColor" />}
                {busy ? '处理中' : synthStatus.running ? '停止' : '启动 Synth'}
              </button>
            </div>
          }
        />

        {error && <Notice tone="error">{error}</Notice>}
        {unavailable && !error && <Notice tone="error">{catalog.ddsp_vst_models.length === 0 ? '未发现 DDSP-VST OM。' : '未发现可用音频输出。'}</Notice>}

        <div className="performance-readout">
          <div className="note-display">
            <KeyboardMusic size={22} />
            <div><span>ACTIVE NOTES</span><strong>{synthStatus.active_notes.length ? synthStatus.active_notes.join(' · ') : '—'}</strong></div>
          </div>
          <div className="metrics-row compact-metrics">
            <Metric label="声部" value={synthStatus.active_notes.length} />
            <Metric label="P95 推理" value={(metrics?.p95_render_ms ?? 0).toFixed(2)} unit="ms" tone="teal" />
            <Metric label="缓冲" value={metrics?.buffered_blocks ?? 0} />
            <Metric label="下溢" value={metrics?.underruns ?? 0} tone={(metrics?.underruns ?? 0) > 0 ? 'red' : undefined} />
          </div>
        </div>

        <Piano octave={octave} velocity={velocity} activeNotes={synthStatus.active_notes} disabled={!synthStatus.running || socketState !== 'online'} onNoteOn={noteOn} onNoteOff={noteOff} />

        <div className="performance-controls">
          <div className="control-block"><span>OCTAVE</span><Stepper value={octave} min={1} max={7} onChange={setOctave} label="八度" /></div>
          <label className="control-block range-block"><span>VELOCITY <strong>{velocity}</strong></span><input type="range" min="1" max="127" value={velocity} onChange={(event) => setVelocity(Number(event.target.value))} /></label>
          <label className="control-block range-block"><span>PITCH BEND <strong>{pitchBend}</strong></span><input type="range" min="-8192" max="8191" value={pitchBend} onChange={(event) => changePitchBend(Number(event.target.value))} onPointerUp={() => changePitchBend(0)} /></label>
          <button type="button" className={`sustain-button ${sustainPedal ? 'is-active' : ''}`} onClick={toggleSustain} disabled={!synthStatus.running}>SUSTAIN</button>
        </div>
      </section>

      <aside className="panel settings-panel synth-settings">
        <PanelHeader title="Synth 参数" action={<SlidersHorizontal size={18} />} />
        <div className="form-grid single-column">
          <Field label="模型">
            <select value={modelId} onChange={(event) => setModelId(event.target.value)} disabled={synthStatus.running}>
              {modelOptions.map((model) => <option value={model.id} key={model.id}>{model.instrument} · {model.precision === 'mixed_float16' ? 'Mixed' : 'FP16'}</option>)}
            </select>
          </Field>
          <Field label="音频输出">
            <select value={audioDeviceId} onChange={(event) => setAudioDeviceId(event.target.value)} disabled={synthStatus.running}>
              <option value="">系统默认</option>
              {audioDevices.map((device) => <option value={device.id} key={device.id}>{device.name}</option>)}
            </select>
          </Field>
          <Field label="MIDI 输入">
            <select value={midiPort} onChange={(event) => setMidiPort(event.target.value)} disabled={synthStatus.running}>
              <option value="">触控与电脑键盘</option>
              {midiPorts.map((port) => <option value={port.name} key={port.id}>{port.name}</option>)}
            </select>
          </Field>

          <div className="setting-group-title">音色</div>
          <ParameterSlider label="Pitch Shift" value={parameters.pitch_shift} min={-24} max={24} step={1} suffix=" st" onChange={(value) => setParameter('pitch_shift', value)} />
          <ParameterSlider label="Harmonics" value={parameters.harmonic_gain} min={0} max={1} step={0.01} onChange={(value) => setParameter('harmonic_gain', value)} />
          <ParameterSlider label="Noise" value={parameters.noise_gain} min={0} max={1} step={0.01} onChange={(value) => setParameter('noise_gain', value)} />
          <ParameterSlider label="Output Gain" value={parameters.output_gain_db} min={-60} max={0} step={1} suffix=" dB" onChange={(value) => setParameter('output_gain_db', value)} />

          <div className="setting-group-title">包络</div>
          <ParameterSlider label="Attack" value={parameters.attack} min={0.01} max={3} step={0.01} suffix=" s" onChange={(value) => setParameter('attack', value)} />
          <ParameterSlider label="Decay" value={parameters.decay} min={0} max={3} step={0.01} suffix=" s" onChange={(value) => setParameter('decay', value)} />
          <ParameterSlider label="Sustain" value={parameters.sustain} min={0} max={1} step={0.01} onChange={(value) => setParameter('sustain', value)} />
          <ParameterSlider label="Release" value={parameters.release} min={0.01} max={5} step={0.01} suffix=" s" onChange={(value) => setParameter('release', value)} />

          <div className="setting-group-title">混响</div>
          <ParameterSlider label="Size" value={parameters.reverb_size} min={0} max={1} step={0.01} onChange={(value) => setParameter('reverb_size', value)} />
          <ParameterSlider label="Damping" value={parameters.reverb_damping} min={0} max={1} step={0.01} onChange={(value) => setParameter('reverb_damping', value)} />
          <ParameterSlider label="Wet" value={parameters.reverb_wet} min={0} max={1} step={0.01} onChange={(value) => setParameter('reverb_wet', value)} />

          <button type="button" className="secondary-button advanced-toggle" onClick={() => setAdvanced((value) => !value)}>{advanced ? '收起高级设置' : '高级设置'}</button>
          {advanced && <>
            <Field label="最大声部"><Stepper value={voices} min={1} max={8} onChange={setVoices} label="最大声部" /></Field>
            <ParameterSlider label="Input Pitch" value={parameters.input_pitch} min={-0.5} max={0.5} step={0.01} onChange={(value) => setParameter('input_pitch', value)} />
            <ParameterSlider label="Input Gain" value={parameters.input_gain} min={-0.5} max={0.5} step={0.01} onChange={(value) => setParameter('input_gain', value)} />
          </>}
        </div>
        <div className="settings-footer"><Volume2 size={17} /><span>48 kHz · 80 ms</span><Gauge size={17} /><span>{voices} voice{voices > 1 ? 's' : ''}</span></div>
      </aside>
    </div>
  )
}

interface ParameterSliderProps {
  label: string
  value: number
  min: number
  max: number
  step: number
  suffix?: string
  onChange: (value: number) => void
}

function ParameterSlider({ label, value, min, max, step, suffix = '', onChange }: ParameterSliderProps) {
  return (
    <Field label={`${label} ${Number.isInteger(step) ? value : value.toFixed(2)}${suffix}`}>
      <input type="range" min={min} max={max} step={step} value={value} onChange={(event) => onChange(Number(event.target.value))} />
    </Field>
  )
}
