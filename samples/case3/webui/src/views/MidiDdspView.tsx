import { useEffect, useMemo, useRef, useState } from 'react'
import { Download, FileAudio, FolderUp, Pause, Play, RotateCcw, Square } from 'lucide-react'
import { api, artifactUrl, formatBytes } from '../api'
import AudioWaveform from '../components/AudioWaveform'
import { Field, Metric, Notice, PanelHeader, Segmented, StatusPill } from '../components/ui'
import type { AudioDevice, Catalog, Job } from '../types'

interface Props {
  catalog: Catalog
  audioDevices: AudioDevice[]
  jobs: Job[]
  onRefresh: () => Promise<void>
}

const ACTIVE = new Set(['queued', 'preparing', 'running', 'paused', 'stopping'])

export default function MidiDdspView({ catalog, audioDevices, jobs, onRefresh }: Props) {
  const expressionModels = catalog.midi_ddsp_models.filter((model) => model.component === 'expression')
  const synthesisModels = catalog.midi_ddsp_models.filter((model) => model.component === 'synthesis')
  const defaultExpression = expressionModels.find((model) => model.precision === 'mixed_float16') ?? expressionModels[0]
  const defaultSynthesis = synthesisModels.find((model) => model.precision === 'mixed_float16') ?? synthesisModels[0]
  const latestJob = jobs.find((job) => job.kind.startsWith('midi-ddsp'))
  const activeJob = jobs.find((job) => job.kind.startsWith('midi-ddsp') && ACTIVE.has(job.state))
  const displayJob = activeJob ?? latestJob
  const [mode, setMode] = useState<'play' | 'render'>('play')
  const [midiId, setMidiId] = useState(catalog.midi_files[0]?.id ?? '')
  const [expressionId, setExpressionId] = useState(defaultExpression?.id ?? '')
  const [synthesisId, setSynthesisId] = useState(defaultSynthesis?.id ?? '')
  const [instrumentId, setInstrumentId] = useState(0)
  const [showAdvanced, setShowAdvanced] = useState(false)
  const [audioDeviceId, setAudioDeviceId] = useState('')
  const [gain, setGain] = useState(24)
  const [latency, setLatency] = useState(80)
  const [prebuffer, setPrebuffer] = useState(6)
  const [tail, setTail] = useState(0.5)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const uploadRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    if (!midiId && catalog.midi_files[0]) setMidiId(catalog.midi_files[0].id)
    if (!expressionId && defaultExpression) setExpressionId(defaultExpression.id)
    if (!synthesisId && defaultSynthesis) setSynthesisId(defaultSynthesis.id)
  }, [catalog.midi_files, defaultExpression, defaultSynthesis, expressionId, midiId, synthesisId])

  const report = displayJob?.metadata.report
  const audioArtifact = displayJob?.artifacts.find((artifact) => artifact.name.endsWith('.wav'))
  const visibleInstruments = useMemo(
    () => catalog.instruments.filter((instrument) => showAdvanced || instrument.verified),
    [catalog.instruments, showAdvanced],
  )
  const ready = midiId && expressionId && synthesisId

  async function start() {
    setBusy(true)
    setError('')
    try {
      await api.startMidiDdsp({
        mode,
        midi_id: midiId,
        expression_model_id: expressionId,
        synthesis_model_id: synthesisId,
        instrument_id: instrumentId,
        audio_device_id: mode === 'play' ? audioDeviceId || null : null,
        sample_rate: 48000,
        prebuffer,
        audio_latency_ms: latency,
        output_gain_db: gain,
        tail_seconds: tail,
        device_id: 0,
      })
      await onRefresh()
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause))
    } finally {
      setBusy(false)
    }
  }

  async function control(action: 'pause' | 'resume' | 'stop') {
    if (!activeJob) return
    setBusy(true)
    setError('')
    try {
      await api.controlJob(activeJob.id, action)
      await onRefresh()
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause))
    } finally {
      setBusy(false)
    }
  }

  async function upload(file?: File) {
    if (!file) return
    setBusy(true)
    setError('')
    try {
      const item = await api.uploadMidi(file)
      await onRefresh()
      setMidiId(item.id)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause))
    } finally {
      setBusy(false)
      if (uploadRef.current) uploadRef.current.value = ''
    }
  }

  return (
    <div className="workspace midi-workspace">
      <section className="panel midi-player-panel">
        <PanelHeader
          title="MIDI-DDSP Player"
          subtitle="Expression + Synthesis · Ascend OM"
          action={<Segmented value={mode} options={[{ value: 'play', label: '播放' }, { value: 'render', label: '渲染' }]} onChange={setMode} />}
        />
        {error && <Notice tone="error">{error}</Notice>}

        <div className="track-browser">
          <div className="track-icon"><FileAudio size={27} /></div>
          <div className="track-select">
            <span>TRACK</span>
            <select value={midiId} onChange={(event) => setMidiId(event.target.value)} disabled={Boolean(activeJob)}>
              {catalog.midi_files.map((file) => <option value={file.id} key={file.id}>{file.name}</option>)}
            </select>
          </div>
          <input ref={uploadRef} type="file" accept=".mid,.midi,audio/midi" hidden onChange={(event) => upload(event.target.files?.[0])} />
          <button type="button" className="icon-button" title="上传 MIDI" onClick={() => uploadRef.current?.click()} disabled={busy || Boolean(activeJob)}>
            <FolderUp size={19} />
          </button>
        </div>

        <AudioWaveform artifact={audioArtifact} />

        <div className="transport-bar">
          {!activeJob && (
            <button className="transport-primary" type="button" onClick={start} disabled={busy || !ready}>
              <Play size={22} fill="currentColor" />
            </button>
          )}
          {activeJob?.state === 'paused' ? (
            <button className="transport-primary" type="button" title="继续" onClick={() => control('resume')} disabled={busy}><Play size={22} fill="currentColor" /></button>
          ) : activeJob ? (
            <button className="transport-primary" type="button" title="暂停" onClick={() => control('pause')} disabled={busy || activeJob.kind.endsWith('render')}><Pause size={22} fill="currentColor" /></button>
          ) : null}
          {activeJob && <button className="icon-button" type="button" title="停止" onClick={() => control('stop')} disabled={busy}><Square size={18} fill="currentColor" /></button>}
          <div className="timeline">
            <div className="timeline-meta">
              <StatusPill tone={displayJob?.state === 'succeeded' ? 'ok' : displayJob?.state === 'failed' ? 'error' : activeJob ? 'warn' : 'neutral'}>{displayJob?.state ?? 'ready'}</StatusPill>
              <span>{Math.round((displayJob?.progress ?? 0) * 100)}%</span>
            </div>
            <div className="progress-track"><span style={{ width: `${(displayJob?.progress ?? 0) * 100}%` }} /></div>
          </div>
          {audioArtifact && <a className="icon-button" title="下载 WAV" href={artifactUrl(audioArtifact.id)} download><Download size={18} /></a>}
        </div>

        <div className="metrics-row report-metrics">
          <Metric label="时长" value={(report?.duration_seconds ?? 0).toFixed(2)} unit="s" />
          <Metric label="Expression" value={report?.expression_inference_count ?? 0} />
          <Metric label="合成中位数" value={(report?.synthesis_render_median_ms ?? 0).toFixed(2)} unit="ms" tone="teal" />
          <Metric label="P95" value={(report?.synthesis_render_p95_ms ?? 0).toFixed(2)} unit="ms" tone="amber" />
          <Metric label="下溢" value={report?.underruns ?? 0} tone={(report?.underruns ?? 0) > 0 ? 'red' : undefined} />
        </div>
      </section>

      <aside className="panel settings-panel midi-settings">
        <PanelHeader title="渲染参数" action={<RotateCcw size={18} />} />
        <div className="form-grid single-column">
          <Field label="Expression OM">
            <select value={expressionId} onChange={(event) => setExpressionId(event.target.value)} disabled={Boolean(activeJob)}>
              {expressionModels.map((model) => <option value={model.id} key={model.id}>{model.precision} · {formatBytes(model.size_bytes)}</option>)}
            </select>
          </Field>
          <Field label="Synthesis OM">
            <select value={synthesisId} onChange={(event) => setSynthesisId(event.target.value)} disabled={Boolean(activeJob)}>
              {synthesisModels.map((model) => <option value={model.id} key={model.id}>{model.precision} · {formatBytes(model.size_bytes)}</option>)}
            </select>
          </Field>
          <Field label="乐器">
            <select value={instrumentId} onChange={(event) => setInstrumentId(Number(event.target.value))} disabled={Boolean(activeJob)}>
              {visibleInstruments.map((instrument) => <option value={instrument.id} key={instrument.id}>{instrument.id.toString().padStart(2, '0')} · {instrument.name}</option>)}
            </select>
          </Field>
          <label className="toggle-row">
            <span>高级乐器 ID</span>
            <input type="checkbox" checked={showAdvanced} onChange={(event) => setShowAdvanced(event.target.checked)} />
          </label>
          {mode === 'play' && <Field label="音频输出"><select value={audioDeviceId} onChange={(event) => setAudioDeviceId(event.target.value)} disabled={Boolean(activeJob)}><option value="">系统默认</option>{audioDevices.map((device) => <option value={device.id} key={device.id}>{device.name}</option>)}</select></Field>}
          <Field label={`输出增益 +${gain} dB`}><input type="range" min="0" max="36" value={gain} onChange={(event) => setGain(Number(event.target.value))} /></Field>
          <Field label={`设备延迟 ${latency} ms`}><input type="range" min="20" max="250" step="10" value={latency} onChange={(event) => setLatency(Number(event.target.value))} /></Field>
          <Field label={`预缓冲 ${prebuffer} blocks`}><input type="range" min="1" max="16" value={prebuffer} onChange={(event) => setPrebuffer(Number(event.target.value))} /></Field>
          <Field label={`尾音 ${tail.toFixed(1)} s`}><input type="range" min="0" max="3" step="0.1" value={tail} onChange={(event) => setTail(Number(event.target.value))} /></Field>
        </div>
      </aside>
    </div>
  )
}
