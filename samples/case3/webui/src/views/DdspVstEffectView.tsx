import {
  Activity,
  Gauge,
  Mic2,
  Play,
  RefreshCw,
  RotateCcw,
  ShieldAlert,
  SlidersHorizontal,
  Speaker,
  Square,
} from 'lucide-react'
import { useCallback, useEffect, useMemo, useRef, useState, type PointerEvent as ReactPointerEvent } from 'react'
import { api, websocketUrl } from '../api'
import { Notice, StatusPill } from '../components/ui'
import { ddspVstTimbreNameZh } from '../timbres'
import type {
  DdspVstEffectCatalog,
  DdspVstEffectMetrics,
  DdspVstEffectStatus,
  DdspVstModel,
} from '../types'

const PARAMETER_LABELS: Record<string, { label: string; unit?: string; step: number }> = {
  transpose: { label: '移调', unit: 'st', step: 1 },
  input_pitch: { label: '音高校准', step: 0.01 },
  input_gain: { label: '力度校准', step: 0.01 },
  harmonic_gain: { label: '谐波', step: 0.01 },
  noise_gain: { label: '噪声', step: 0.01 },
  output_gain_db: { label: '输出增益', unit: 'dB', step: 1 },
  reverb_size: { label: '混响空间', step: 0.01 },
  reverb_damping: { label: '混响阻尼', step: 0.01 },
  reverb_wet: { label: '混响', step: 0.01 },
  gate_threshold_dbfs: { label: '开启门限', unit: 'dBFS', step: 1 },
  gate_hysteresis_db: { label: '迟滞', unit: 'dB', step: 1 },
  gate_hold_ms: { label: '保持', unit: 'ms', step: 10 },
  gate_attack_ms: { label: '开启时间', unit: 'ms', step: 1 },
  gate_release_ms: { label: '关闭时间', unit: 'ms', step: 10 },
}

const PARAMETER_GROUPS = {
  tone: ['transpose', 'input_pitch', 'input_gain', 'harmonic_gain', 'noise_gain'],
  gate: ['gate_threshold_dbfs', 'gate_hysteresis_db', 'gate_hold_ms', 'gate_attack_ms', 'gate_release_ms'],
  effects: ['output_gain_db', 'reverb_size', 'reverb_damping', 'reverb_wet'],
} as const

type ParameterGroup = keyof typeof PARAMETER_GROUPS

const EMPTY_METRICS: DdspVstEffectMetrics = {
  frames: 0,
  f0_hz: 0,
  pw_db: -96,
  input_rms_dbfs: -96,
  input_peak_dbfs: -96,
  output_rms_dbfs: -96,
  output_peak_dbfs: -96,
  feature_ms: 0,
  feature_p95_ms: 0,
  control_ms: 0,
  control_p95_ms: 0,
  queue_latency_ms: 0,
  total_latency_ms: 0,
  capture_overflows: 0,
  playback_underruns: 0,
  clipped_samples: 0,
  safety_muted: false,
  gate_open: false,
  gate_gain: 0,
  gate_threshold_dbfs: -40,
  gate_close_threshold_dbfs: -46,
  gate_hold_frames: 0,
  gated_frames: 0,
  noise_floor_dbfs: -96,
  calibrating: false,
  calibration_progress: 0,
}

function errorMessage(cause: unknown): string {
  return cause instanceof Error ? cause.message : String(cause)
}

function formatMetric(value: number, digits = 1): string {
  return Number.isFinite(value) ? value.toFixed(digits) : '0.0'
}

function clamp(value: number, minimum = 0, maximum = 1) {
  return Math.max(minimum, Math.min(maximum, value))
}

function normalizedPitch(frequency: number) {
  const midi = 12 * (Math.log2(clamp(frequency, 8.18, 12_543.84)) - Math.log2(440)) + 69
  return clamp(midi / 127)
}

function normalizedLoudness(db: number) {
  return clamp(db / 80 + 1)
}

function SignalTrace({
  metrics,
  model,
  parameters,
  onCalibrationChange,
}: {
  metrics: DdspVstEffectMetrics
  model?: DdspVstModel
  parameters: Record<string, number>
  onCalibrationChange: (name: 'input_pitch' | 'input_gain', value: number) => void
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const historyRef = useRef<{ pitch: number; level: number }[]>([])
  const frameRef = useRef<number | null>(null)
  const dragRef = useRef<{
    pointerId: number
    x: number
    y: number
    pitch: number
    gain: number
  } | null>(null)

  const rangeRect = useCallback((width: number, height: number, adjusted: boolean) => {
    const pitchOffset = adjusted ? parameters.input_pitch ?? 0 : 0
    const loudnessOffset = adjusted ? parameters.input_gain ?? 0 : 0
    const pitchMin = normalizedPitch(model?.pitch_min_hz ?? 40)
    const pitchMax = normalizedPitch(model?.pitch_max_hz ?? 1_200)
    const loudnessMin = normalizedLoudness(model?.power_min_db ?? -80)
    const loudnessMax = normalizedLoudness(model?.power_max_db ?? 0)
    return {
      x: (pitchMin + pitchOffset) * width,
      y: height - (loudnessMax + loudnessOffset) * height,
      width: (pitchMax - pitchMin) * width,
      height: (loudnessMax - loudnessMin) * height,
    }
  }, [model, parameters.input_gain, parameters.input_pitch])

  const draw = useCallback(() => {
    frameRef.current = null
    const canvas = canvasRef.current
    if (!canvas) return
    const rect = canvas.getBoundingClientRect()
    const ratio = Math.min(window.devicePixelRatio || 1, 1.25)
    const width = Math.max(1, Math.round(rect.width * ratio))
    const height = Math.max(1, Math.round(rect.height * ratio))
    if (canvas.width !== width || canvas.height !== height) {
      canvas.width = width
      canvas.height = height
    }
    const context = canvas.getContext('2d')
    if (!context) return
    context.setTransform(ratio, 0, 0, ratio, 0, 0)
    const cssWidth = width / ratio
    const cssHeight = height / ratio
    context.clearRect(0, 0, cssWidth, cssHeight)
    context.fillStyle = '#11191d'
    context.fillRect(0, 0, cssWidth, cssHeight)
    context.strokeStyle = '#2d3c41'
    context.lineWidth = 1
    for (let index = 1; index < 5; index += 1) {
      const y = (cssHeight * index) / 5
      context.beginPath()
      context.moveTo(0, y)
      context.lineTo(cssWidth, y)
      context.stroke()
    }
    context.fillStyle = '#738087'
    context.font = '12px sans-serif'
    for (let midi = 0; midi <= 120; midi += 12) {
      const x = (midi / 127) * cssWidth
      context.beginPath()
      context.moveTo(x, 0)
      context.lineTo(x, cssHeight - 18)
      context.stroke()
      context.fillText(`C${midi / 12 - 1}`, Math.max(2, x + 3), cssHeight - 5)
    }
    const original = rangeRect(cssWidth, cssHeight, false)
    const adjusted = rangeRect(cssWidth, cssHeight, true)
    context.strokeStyle = 'rgba(67, 193, 181, 0.55)'
    context.setLineDash?.([7, 5])
    context.strokeRect(original.x, original.y, original.width, original.height)
    context.setLineDash?.([])
    context.fillStyle = metrics.gate_open ? 'rgba(67, 193, 181, 0.22)' : 'rgba(211, 138, 32, 0.16)'
    context.fillRect(adjusted.x, adjusted.y, adjusted.width, adjusted.height)
    context.strokeStyle = metrics.gate_open ? '#43c1b5' : '#d38a20'
    context.lineWidth = 2
    context.strokeRect(adjusted.x, adjusted.y, adjusted.width, adjusted.height)
    const history = historyRef.current
    history.forEach((sample, index) => {
      const age = (index + 1) / history.length
      const radius = 2 + age * 8
      const x = normalizedPitch(sample.pitch) * cssWidth
      const y = (1 - normalizedLoudness(sample.level)) * cssHeight
      context.beginPath()
      context.fillStyle = `rgba(67, 193, 181, ${0.08 + age * 0.65})`
      context.arc(x, y, radius, 0, Math.PI * 2)
      context.fill()
    })
  }, [metrics.gate_open, rangeRect])

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas || canvas.offsetParent === null) return
    if (metrics.frames > 0) {
      historyRef.current.push({ pitch: metrics.f0_hz, level: metrics.pw_db })
      if (historyRef.current.length > 24) historyRef.current.splice(0, historyRef.current.length - 24)
    }
    if (frameRef.current === null) frameRef.current = window.requestAnimationFrame(draw)
  }, [draw, metrics.f0_hz, metrics.frames, metrics.pw_db])

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return undefined
    const observer = new ResizeObserver(() => {
      if (frameRef.current === null) frameRef.current = window.requestAnimationFrame(draw)
    })
    observer.observe(canvas)
    return () => {
      observer.disconnect()
      if (frameRef.current !== null) window.cancelAnimationFrame(frameRef.current)
    }
  }, [draw])

  const pointerPosition = (event: ReactPointerEvent<HTMLCanvasElement>) => {
    const rect = event.currentTarget.getBoundingClientRect()
    return { x: event.clientX - rect.left, y: event.clientY - rect.top, rect }
  }

  return (
    <canvas
      ref={canvasRef}
      className="effect-trace"
      role="img"
      aria-label="DDSP-VST 音高与响度轨迹"
      title="拖动模型范围可调整音高与响度校准，双击复位"
      onPointerDown={(event) => {
        const { x, y, rect } = pointerPosition(event)
        const range = rangeRect(rect.width, rect.height, true)
        if (x < range.x || x > range.x + range.width || y < range.y || y > range.y + range.height) return
        dragRef.current = {
          pointerId: event.pointerId,
          x,
          y,
          pitch: parameters.input_pitch ?? 0,
          gain: parameters.input_gain ?? 0,
        }
        event.currentTarget.setPointerCapture(event.pointerId)
      }}
      onPointerMove={(event) => {
        const drag = dragRef.current
        if (!drag || drag.pointerId !== event.pointerId) return
        const { x, y, rect } = pointerPosition(event)
        onCalibrationChange('input_pitch', clamp(
          drag.pitch + (x - drag.x) / rect.width,
          -0.5,
          0.5,
        ))
        onCalibrationChange('input_gain', clamp(
          drag.gain - (y - drag.y) / rect.height,
          -0.5,
          0.5,
        ))
      }}
      onPointerUp={(event) => {
        if (dragRef.current?.pointerId !== event.pointerId) return
        dragRef.current = null
        event.currentTarget.releasePointerCapture(event.pointerId)
      }}
      onPointerCancel={() => { dragRef.current = null }}
      onDoubleClick={() => {
        onCalibrationChange('input_pitch', 0)
        onCalibrationChange('input_gain', 0)
      }}
    />
  )
}

function ParameterControl({
  name,
  value,
  catalog,
  onChange,
}: {
  name: string
  value: number
  catalog: DdspVstEffectCatalog
  onChange: (name: string, value: number) => void
}) {
  const metadata = catalog.parameters[name]
  const display = PARAMETER_LABELS[name]
  if (!metadata || !display) return null
  return (
    <label className="effect-parameter">
      <span>{display.label}</span>
      <input
        type="range"
        aria-label={display.label}
        min={metadata.min}
        max={metadata.max}
        step={display.step}
        value={value}
        onChange={(event) => onChange(name, Number(event.target.value))}
      />
      <output>{display.step < 1 ? value.toFixed(2) : value.toFixed(0)}{display.unit ? ` ${display.unit}` : ''}</output>
    </label>
  )
}

export default function DdspVstEffectView() {
  const [catalog, setCatalog] = useState<DdspVstEffectCatalog | null>(null)
  const [status, setStatus] = useState<DdspVstEffectStatus | null>(null)
  const [modelId, setModelId] = useState('')
  const [inputId, setInputId] = useState('')
  const [outputId, setOutputId] = useState('')
  const [parameters, setParameters] = useState<Record<string, number>>({})
  const [loading, setLoading] = useState(true)
  const [pending, setPending] = useState(false)
  const [error, setError] = useState('')
  const [parameterGroup, setParameterGroup] = useState<ParameterGroup>('tone')

  const applyStatus = useCallback((next: DdspVstEffectStatus) => {
    setStatus(next)
    if (next.running || next.state === 'failed') setParameters(next.parameters)
  }, [])

  const load = useCallback(async () => {
    try {
      const [nextCatalog, nextStatus] = await Promise.all([
        api.ddspVstEffectCatalog(),
        api.ddspVstEffectStatus(),
      ])
      setCatalog(nextCatalog)
      applyStatus(nextStatus)
      setModelId(nextStatus.config.model_id ?? nextCatalog.default_model_id ?? '')
      setInputId(nextStatus.config.audio_input_id ?? nextCatalog.default_audio_input_id ?? '')
      setOutputId(nextStatus.config.audio_output_id ?? nextCatalog.default_audio_output_id ?? '')
      setParameters(Object.fromEntries(Object.entries(nextCatalog.parameters).map(([name, item]) => [name, nextStatus.parameters[name] ?? item.default])))
      setError('')
    } catch (cause) {
      setError(errorMessage(cause))
    } finally {
      setLoading(false)
    }
  }, [applyStatus])

  useEffect(() => { void load() }, [load])

  useEffect(() => {
    if (typeof WebSocket === 'undefined') return undefined
    const socket = new WebSocket(websocketUrl('/api/v1/ddsp-vst-effect/events'))
    socket.onmessage = (event) => {
      const message = JSON.parse(event.data)
      if (message.event === 'status') applyStatus(message.data as DdspVstEffectStatus)
    }
    return () => socket.close()
  }, [applyStatus])

  const updateParameter = useCallback((name: string, value: number) => {
    setParameters((current) => ({ ...current, [name]: value }))
    if (status?.running) {
      void api.updateDdspVstEffect({ [name]: value })
        .then(applyStatus)
        .catch((cause) => setError(errorMessage(cause)))
    }
  }, [applyStatus, status?.running])

  const start = async () => {
    if (!modelId || !inputId || !outputId) return
    setPending(true)
    try {
      applyStatus(await api.startDdspVstEffect({
        model_id: modelId,
        audio_input_id: inputId,
        audio_output_id: outputId,
        parameters,
      }))
      setError('')
    } catch (cause) {
      setError(errorMessage(cause))
    } finally {
      setPending(false)
    }
  }

  const stop = async () => {
    setPending(true)
    try {
      applyStatus(await api.stopDdspVstEffect())
      setError('')
    } catch (cause) {
      setError(errorMessage(cause))
    } finally {
      setPending(false)
    }
  }

  const calibrate = async () => {
    setPending(true)
    try {
      applyStatus(await api.calibrateDdspVstEffect())
      setError('')
    } catch (cause) {
      setError(errorMessage(cause))
    } finally {
      setPending(false)
    }
  }

  const refreshModels = async () => {
    setPending(true)
    try {
      const nextCatalog = await api.refreshDdspVstEffectCatalog()
      setCatalog(nextCatalog)
      if (!nextCatalog.models.some((model) => model.id === modelId)) {
        setModelId(nextCatalog.default_model_id ?? '')
      }
      setError('')
    } catch (cause) {
      setError(errorMessage(cause))
    } finally {
      setPending(false)
    }
  }

  const metrics = status?.metrics ?? EMPTY_METRICS
  const running = status?.running ?? false
  const selectedModel = catalog?.models.find((model) => model.id === modelId)
  const ready = Boolean(catalog?.available && modelId && inputId && outputId)
  const stateLabel = useMemo(() => ({
    stopped: '已停止', starting: '启动中', running: '运行中', stopping: '停止中', failed: '故障',
  })[status?.state ?? 'stopped'], [status?.state])

  if (loading) return <Notice tone="loading">正在读取 DDSP-VST 设备与模型</Notice>
  if (!catalog) return <Notice tone="error">{error || 'DDSP-VST 服务不可用'}</Notice>

  return (
    <section className="effect-workspace" aria-label="DDSP-VST Effect 工作区">
      <header className="effect-command-bar">
        <div className="effect-title">
          <Activity size={23} />
          <div><h1>DDSP-VST</h1><span>实时音色转换</span></div>
        </div>
        <div className="effect-route-summary">
          <Mic2 size={18} /><span>{status?.config.input_device_name || catalog.audio_inputs.find((item) => item.id === inputId)?.name || '未选择输入'}</span>
          <span className="effect-route-arrow">→</span>
          <Speaker size={18} /><span>{status?.config.output_device_name || catalog.audio_outputs.find((item) => item.id === outputId)?.name || '未选择输出'}</span>
        </div>
        <StatusPill tone={metrics.safety_muted || status?.state === 'failed' ? 'error' : running ? 'ok' : catalog.available ? 'neutral' : 'warn'}>
          {metrics.safety_muted ? '安全静音' : metrics.calibrating ? '输入校准中' : stateLabel}
        </StatusPill>
        {running
          ? <button className="danger-button effect-run-button" type="button" disabled={pending} onClick={stop}><Square size={19} />停止</button>
          : <button className="primary-button effect-run-button" type="button" disabled={pending || !ready} onClick={start}><Play size={20} />启动</button>}
      </header>

      {(error || status?.error) && <Notice tone="error">{error || status?.error}</Notice>}
      {!catalog.available && <Notice tone="warn">{catalog.error || 'DDSP-VST Effect 当前不可用'}</Notice>}
      {metrics.safety_muted && <Notice tone="error"><ShieldAlert size={18} />持续过载，输出已安全静音</Notice>}

      <div className="effect-body">
        <section className="effect-monitor" aria-labelledby="effect-monitor-title">
          <div className="effect-section-title"><Gauge size={19} /><h2 id="effect-monitor-title">实时监测</h2><span>{formatMetric(metrics.f0_hz, 0)} Hz · {formatMetric(metrics.input_rms_dbfs)} dBFS</span></div>
          <SignalTrace metrics={metrics} model={selectedModel} parameters={parameters} onCalibrationChange={updateParameter} />
          <dl className="effect-metrics">
            <div><dt>输入峰值</dt><dd>{formatMetric(metrics.input_peak_dbfs)} <small>dBFS</small></dd></div>
            <div><dt>输出峰值</dt><dd>{formatMetric(metrics.output_peak_dbfs)} <small>dBFS</small></dd></div>
            <div><dt>Feature</dt><dd>{formatMetric(metrics.feature_ms, 2)} <small>ms</small></dd></div>
            <div><dt>Control</dt><dd>{formatMetric(metrics.control_ms, 2)} <small>ms</small></dd></div>
            <div><dt>总延迟</dt><dd>{formatMetric(metrics.total_latency_ms)} <small>ms</small></dd></div>
            <div><dt>异常</dt><dd className={metrics.capture_overflows + metrics.playback_underruns + metrics.clipped_samples > 0 ? 'is-error' : ''}>{metrics.capture_overflows + metrics.playback_underruns + metrics.clipped_samples}</dd></div>
          </dl>
          <div className="effect-backend-strip">
            <StatusPill tone={metrics.calibrating ? 'warn' : metrics.gate_open ? 'ok' : 'neutral'}>
              {metrics.calibrating ? `校准 ${Math.round(metrics.calibration_progress * 100)}%` : metrics.gate_open ? '输入门开启' : '输入门关闭'}
            </StatusPill>
            <StatusPill tone={catalog.feature_model.available ? 'ok' : 'error'}>FEATURE · ACL/OM</StatusPill>
            <StatusPill tone={catalog.models.length === 11 ? 'ok' : 'warn'}>CONTROL · ACL/OM</StatusPill>
            <span>底噪 {formatMetric(metrics.noise_floor_dbfs)} · 门限 {formatMetric(metrics.gate_threshold_dbfs)} dBFS</span>
          </div>
        </section>

        <section className="effect-controls" aria-labelledby="effect-controls-title">
          <div className="effect-section-title">
            <SlidersHorizontal size={19} /><h2 id="effect-controls-title">音色与参数</h2>
            <div className="effect-control-tabs" role="tablist" aria-label="DDSP-VST 参数分组">
              {([
                ['tone', '音色'],
                ['gate', '输入门'],
                ['effects', '效果'],
              ] as const).map(([id, label]) => (
                <button key={id} type="button" role="tab" aria-selected={parameterGroup === id} className={parameterGroup === id ? 'is-active' : ''} onClick={() => setParameterGroup(id)}>{label}</button>
              ))}
            </div>
          </div>
          <div className="effect-select-grid">
            <label><span>输入</span><select aria-label="音频输入" value={inputId} disabled={running} onChange={(event) => setInputId(event.target.value)}>{catalog.audio_inputs.map((item) => <option value={item.id} key={item.id}>{item.name}</option>)}</select></label>
            <label><span>输出</span><select aria-label="音频输出" value={outputId} disabled={running} onChange={(event) => setOutputId(event.target.value)}>{catalog.audio_outputs.map((item) => <option value={item.id} key={item.id}>{item.name}</option>)}</select></label>
            <div className="effect-model-select"><span>音色</span><select aria-label="DDSP-VST 音色" value={modelId} disabled={running} onChange={(event) => setModelId(event.target.value)}>{catalog.models.map((item) => <option value={item.id} key={item.id}>{ddspVstTimbreNameZh(item)}</option>)}</select><button className="icon-command" type="button" aria-label="刷新已发布 OM 音色" title="刷新已发布 OM 音色" disabled={running || pending} onClick={refreshModels}><RefreshCw size={17} /></button></div>
          </div>
          <div className="effect-parameter-grid">
            {parameterGroup === 'gate' && (
              <div className="effect-gate-actions">
                <div><strong>{metrics.calibrating ? '正在测量底噪' : metrics.gate_open ? '输入信号通过' : '环境噪声已抑制'}</strong><span>{formatMetric(metrics.input_rms_dbfs)} dBFS</span></div>
                <button className="secondary-button" type="button" disabled={!running || pending} onClick={calibrate}><RotateCcw size={17} />重新校准</button>
              </div>
            )}
            {PARAMETER_GROUPS[parameterGroup].map((name) => (
              <ParameterControl key={name} name={name} value={parameters[name] ?? catalog.parameters[name]?.default ?? 0} catalog={catalog} onChange={updateParameter} />
            ))}
          </div>
        </section>
      </div>
    </section>
  )
}
