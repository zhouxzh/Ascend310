import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Gauge, KeyboardMusic, Octagon, Play, Radio, SlidersHorizontal, Volume2 } from 'lucide-react'
import { api, websocketUrl } from '../api'
import { audioDeviceLabel, isBluetoothOutput } from '../audio'
import Piano, { DEFAULT_PIANO_KEY_COUNT, noteLabel, pianoNoteRange } from '../components/Piano'
import { Field, Metric, Notice, PanelHeader, StatusPill, Stepper } from '../components/ui'
import type { AudioDevice, Catalog, DdspVstParameters, DdspVstStatus, LatencyProfile, MidiPort, SystemStatus } from '../types'

interface Props {
  status: SystemStatus
  catalog: Catalog
  audioDevices: AudioDevice[]
  audioError?: string | null
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
  output_gain_db: -18,
  velocity_curve: 0.55,
  attack: 0.02,
  decay: 0,
  sustain: 1,
  release: 1.2,
  input_pitch: 0,
  input_gain: 0,
  reverb_size: 0.4,
  reverb_damping: 0.1,
  reverb_wet: 0,
}

const WIRED_SAMPLE_RATE = 48000
const BLUETOOTH_SAMPLE_RATE = 44100
const LATENCY_CONFIG: Record<LatencyProfile, { prebuffer: number; latencyMs: number; label: string }> = {
  low: { prebuffer: 1, latencyMs: 15, label: '低延时' },
  balanced: { prebuffer: 2, latencyMs: 20, label: '均衡' },
  safe: { prebuffer: 3, latencyMs: 60, label: '稳定' },
}

const VELOCITY_CURVES = [
  { value: 0.55, label: '轻触增强' },
  { value: 1, label: '线性' },
  { value: 1.4, label: '宽动态' },
] as const

export default function PerformView({ status, catalog, audioDevices, audioError, midiPorts, onRefresh }: Props) {
  const preferredModel = useMemo(
    () => catalog.ddsp_vst_models.find((model) => model.instrument.toLowerCase() === 'violin' && model.precision === 'mixed_float16')
      ?? catalog.ddsp_vst_models.find((model) => model.precision === 'mixed_float16')
      ?? catalog.ddsp_vst_models[0],
    [catalog.ddsp_vst_models],
  )
  const [modelId, setModelId] = useState(preferredModel?.id ?? '')
  const defaultAudioDeviceId = audioDevices.find((device) => device.is_default)?.id
    ?? audioDevices[0]?.id
    ?? ''
  const [audioDeviceId, setAudioDeviceId] = useState(defaultAudioDeviceId)
  const [midiPort, setMidiPort] = useState('')
  const [octave, setOctave] = useState(4)
  const [velocity, setVelocity] = useState(100)
  const [voices, setVoices] = useState(1)
  const [latencyProfile, setLatencyProfile] = useState<LatencyProfile>('balanced')
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

  useEffect(() => {
    if (!audioDevices.some((device) => device.id === audioDeviceId)) {
      setAudioDeviceId(defaultAudioDeviceId)
    }
  }, [audioDeviceId, audioDevices, defaultAudioDeviceId])

  const modelOptions = useMemo(
    () => advanced ? catalog.ddsp_vst_models : catalog.ddsp_vst_models.filter((model) => model.precision === 'mixed_float16'),
    [advanced, catalog.ddsp_vst_models],
  )
  const selectedAudioDevice = useMemo(
    () => audioDevices.find((device) => device.id === audioDeviceId) ?? null,
    [audioDeviceId, audioDevices],
  )
  const selectedModel = useMemo(
    () => catalog.ddsp_vst_models.find((model) => model.id === modelId) ?? preferredModel,
    [catalog.ddsp_vst_models, modelId, preferredModel],
  )
  const selectedMidiPort = useMemo(
    () => midiPorts.find((port) => (port.port ?? port.name) === midiPort) ?? null,
    [midiPort, midiPorts],
  )
  const keyboardKeyCount = selectedMidiPort?.key_count ?? DEFAULT_PIANO_KEY_COUNT
  const keyboardRange = pianoNoteRange(octave, keyboardKeyCount)
  const keyboardName = selectedMidiPort
    ? [selectedMidiPort.manufacturer, selectedMidiPort.model].filter(Boolean).join(' ') || selectedMidiPort.name
    : '触控键盘'
  const bluetoothOutputSelected = Boolean(selectedAudioDevice && isBluetoothOutput(selectedAudioDevice))
  const outputSampleRate = bluetoothOutputSelected
    ? selectedAudioDevice?.default_sample_rate || BLUETOOTH_SAMPLE_RATE
    : WIRED_SAMPLE_RATE
  const audioLatencyMs = bluetoothOutputSelected
    ? latencyProfile === 'safe' ? 300 : 220
    : LATENCY_CONFIG[latencyProfile].latencyMs
  const pitchMin = selectedModel?.pitch_min_note
  const pitchMax = selectedModel?.pitch_max_note
  const displayPitchMin = pitchMin === undefined ? undefined : Math.ceil(pitchMin)
  const displayPitchMax = pitchMax === undefined ? undefined : Math.floor(pitchMax)
  const outOfRangeNotes = pitchMin === undefined || pitchMax === undefined
    ? []
    : synthStatus.active_notes.filter((note) => note < pitchMin || note > pitchMax)

  useEffect(() => {
    if (!modelId && preferredModel) setModelId(preferredModel.id)
  }, [modelId, preferredModel])

  useEffect(() => {
    const configuredPort = status.ddsp_vst.config?.midi_port
    if (status.ddsp_vst.running) {
      setMidiPort(typeof configuredPort === 'string' ? configuredPort : '')
      const configuredAudio = status.ddsp_vst.config?.audio_device_id
      setAudioDeviceId(typeof configuredAudio === 'string' ? configuredAudio : '')
      const configuredLatency = status.ddsp_vst.config?.latency_profile
      if (configuredLatency === 'low' || configuredLatency === 'balanced' || configuredLatency === 'safe') {
        setLatencyProfile(configuredLatency)
      }
      const configuredVoices = status.ddsp_vst.config?.max_voices
      if (typeof configuredVoices === 'number') setVoices(configuredVoices)
      if (status.ddsp_vst.parameters) setParameters(status.ddsp_vst.parameters)
    } else if (!midiPort && midiPorts.length === 1) {
      setMidiPort(midiPorts[0].port ?? midiPorts[0].name)
    }
  }, [midiPort, midiPorts, status.ddsp_vst.config, status.ddsp_vst.parameters, status.ddsp_vst.running])

  useEffect(() => {
    if (!advanced && modelOptions.length && !modelOptions.some((model) => model.id === modelId)) {
      setModelId(modelOptions[0].id)
    }
  }, [advanced, modelId, modelOptions])

  useEffect(() => setSynthStatus(status.ddsp_vst), [status.ddsp_vst])

  useEffect(() => {
    if (bluetoothOutputSelected && latencyProfile === 'low') setLatencyProfile('balanced')
  }, [bluetoothOutputSelected, latencyProfile])

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
    if (!audioDeviceId) return
    setBusy(true)
    setError('')
    try {
      const next = await api.startDdspVst({
        model_id: modelId,
        audio_device_id: audioDeviceId,
        midi_port: midiPort || null,
        sample_rate: outputSampleRate,
        latency_profile: latencyProfile,
        max_voices: voices,
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
        {unavailable && !error && <Notice tone="error">{catalog.ddsp_vst_models.length === 0 ? '未发现 DDSP-VST OM。' : audioError || '未发现可用音频输出。'}</Notice>}
        {bluetoothOutputSelected && !error && <Notice tone="warn">蓝牙输出已切换为更高缓冲；实时演奏想要更低延迟，建议使用 USB 或有线声卡。</Notice>}
        {outOfRangeNotes.length > 0 && <Notice tone="warn">当前音符 {outOfRangeNotes.join('、')} 超出 {selectedModel?.instrument} 的训练音域。</Notice>}

        <div className="performance-readout">
          <div className="note-display">
            <KeyboardMusic size={22} />
            <div><span>ACTIVE NOTES</span><strong>{synthStatus.active_notes.length ? synthStatus.active_notes.join(' · ') : '—'}</strong></div>
            <div className="keyboard-profile">
              <span>MIDI CONTROLLER</span>
              <strong>{keyboardName}</strong>
              <small>{keyboardKeyCount}键 · {noteLabel(keyboardRange.first)}-{noteLabel(keyboardRange.last)}</small>
              <small>力度 {metrics?.midi_velocity_last ?? '—'} → {metrics?.midi_velocity_mapped_last ?? '—'}</small>
            </div>
          </div>
          <div className="metrics-row compact-metrics">
            <Metric label="总延时" value={(metrics?.estimated_total_latency_ms ?? 0).toFixed(0)} unit="ms" tone="teal" />
            <Metric label="P95 渲染" value={(metrics?.p95_render_ms ?? 0).toFixed(2)} unit="ms" />
            <Metric label="队列" value={(metrics?.queue_latency_ms ?? 0).toFixed(0)} unit="ms" />
            <Metric label="设备" value={(metrics?.device_latency_ms ?? 0).toFixed(0)} unit="ms" />
            <Metric label="Sink" value={(metrics?.sink_latency_ms ?? 0).toFixed(0)} unit="ms" />
            <Metric label="下溢" value={metrics?.underruns ?? 0} tone={(metrics?.underruns ?? 0) > 0 ? 'red' : undefined} />
          </div>
        </div>

        <Piano octave={octave} keyCount={keyboardKeyCount} velocity={velocity} activeNotes={synthStatus.active_notes} recommendedMin={pitchMin} recommendedMax={pitchMax} disabled={!synthStatus.running || socketState !== 'online'} onNoteOn={noteOn} onNoteOff={noteOff} />

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
          {displayPitchMin !== undefined && displayPitchMax !== undefined && <div className="model-range">
            <span>训练音域</span>
            <strong>MIDI {displayPitchMin}-{displayPitchMax}</strong>
            {selectedModel?.pitch_min_hz !== undefined && selectedModel.pitch_max_hz !== undefined && <small>{selectedModel.pitch_min_hz.toFixed(0)}-{selectedModel.pitch_max_hz.toFixed(0)} Hz</small>}
          </div>}
          <Field label="音频输出">
            <select value={audioDeviceId} onChange={(event) => setAudioDeviceId(event.target.value)} disabled={synthStatus.running}>
              {!audioDevices.length && <option value="">无可用输出</option>}
              {audioDevices.map((device) => <option value={device.id} key={device.id}>{audioDeviceLabel(device)}</option>)}
            </select>
          </Field>
          <div className="field">
            <span>延时模式</span>
            <div className="segmented latency-profile" role="group" aria-label="延时模式">
              {(Object.keys(LATENCY_CONFIG) as LatencyProfile[]).map((profile) => (
                <button
                  type="button"
                  className={latencyProfile === profile ? 'is-active' : ''}
                  disabled={synthStatus.running || (bluetoothOutputSelected && profile === 'low')}
                  onClick={() => setLatencyProfile(profile)}
                  key={profile}
                >
                  {LATENCY_CONFIG[profile].label}
                </button>
              ))}
            </div>
          </div>
          <Field label="MIDI 输入">
            <select value={midiPort} onChange={(event) => setMidiPort(event.target.value)} disabled={synthStatus.running}>
              <option value="">触控与电脑键盘</option>
              {midiPorts.map((port) => <option value={port.port ?? port.name} key={port.id}>{port.name}{port.key_count ? ` · ${port.key_count}键` : ''}</option>)}
            </select>
          </Field>
          <div className="field">
            <span>力度响应</span>
            <div className="segmented velocity-profile" role="group" aria-label="力度响应">
              {VELOCITY_CURVES.map((curve) => (
                <button
                  type="button"
                  className={parameters.velocity_curve === curve.value ? 'is-active' : ''}
                  onClick={() => setParameter('velocity_curve', curve.value)}
                  key={curve.value}
                >
                  {curve.label}
                </button>
              ))}
            </div>
          </div>

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
        <div className="settings-footer"><Volume2 size={17} /><span>{outputSampleRate / 1000} kHz · {audioLatencyMs} ms · {bluetoothOutputSelected ? 'A2DP' : `${LATENCY_CONFIG[latencyProfile].prebuffer} blocks`}</span><Gauge size={17} /><span>{voices} voice{voices > 1 ? 's' : ''}</span></div>
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
