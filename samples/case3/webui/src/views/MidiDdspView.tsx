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
  const defaultBundle = catalog.midi_ddsp_bundles.find((bundle) => bundle.recommended)
    ?? catalog.midi_ddsp_bundles[0]
  const latestJob = jobs.find((job) => job.kind.startsWith('midi-ddsp'))
  const activeJob = jobs.find((job) => job.kind.startsWith('midi-ddsp') && ACTIVE.has(job.state))
  const displayJob = activeJob ?? latestJob
  const [mode, setMode] = useState<'play' | 'render'>('play')
  const [midiId, setMidiId] = useState(catalog.midi_files[0]?.id ?? '')
  const [bundleId, setBundleId] = useState(defaultBundle?.id ?? '')
  const [instrumentId, setInstrumentId] = useState(0)
  const [audioDeviceId, setAudioDeviceId] = useState('')
  const [gain, setGain] = useState(0)
  const [latency, setLatency] = useState(80)
  const [prebuffer, setPrebuffer] = useState(6)
  const [tail, setTail] = useState(2)
  const [seed, setSeed] = useState(20260724)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const uploadRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    if (!midiId && catalog.midi_files[0]) setMidiId(catalog.midi_files[0].id)
    if (!bundleId && defaultBundle) setBundleId(defaultBundle.id)
  }, [bundleId, catalog.midi_files, defaultBundle, midiId])

  const selectedMidi = catalog.midi_files.find((file) => file.id === midiId)
  const selectedBundle = catalog.midi_ddsp_bundles.find((bundle) => bundle.id === bundleId)
  const report = displayJob?.metadata.report
  const audioArtifact = displayJob?.artifacts.find((artifact) => artifact.name.endsWith('.wav'))
  const reverbAsset = catalog.midi_ddsp_reverb_assets[0]
  const legacyMultitrack = selectedMidi?.midi_ddsp_mode === 'multitrack'
    && selectedBundle?.architecture !== 'stateful-v2'
  const supported = Boolean(selectedMidi?.midi_ddsp_supported) && !legacyMultitrack
  const ready = Boolean(midiId && bundleId && reverbAsset && supported)
  const bundleSize = useMemo(
    () => Object.values(selectedBundle?.components ?? {}).reduce((total, component) => total + component.size_bytes, 0),
    [selectedBundle],
  )

  async function start() {
    setBusy(true)
    setError('')
    try {
      await api.startMidiDdsp({
        mode,
        midi_id: midiId,
        model_bundle_id: bundleId,
        instrument_id: instrumentId,
        seed,
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
          subtitle="Google URMP 单声部模型 · 完整渲染后播放"
          action={<Segmented value={mode} options={[{ value: 'play', label: '播放' }, { value: 'render', label: '渲染' }]} onChange={setMode} />}
        />
        {error && <Notice tone="error">{error}</Notice>}
        {!reverbAsset && <Notice tone="error">缺少 MIDI-DDSP 原版混响资产</Notice>}
        {displayJob?.state === 'failed' && displayJob.message && <Notice tone="error">{displayJob.message}</Notice>}
        {selectedMidi && !selectedMidi.midi_ddsp_supported && (
          <Notice tone="error">{selectedMidi.unsupported_reason ?? '该 MIDI 不符合原版单声部模型的输入要求。'}</Notice>
        )}
        {legacyMultitrack && (
          <Notice tone="error">多轨渲染需要 stateful-v2 模型包；旧模型会重置上下文，因此已禁用。</Notice>
        )}
        {selectedBundle?.quality_status === 'context_resets' && (
          <Notice tone="warn">当前为旧模型包，块边界会重置上下文，仅用于迁移验证，不代表最终音质。</Notice>
        )}

        <div className="track-browser">
          <div className="track-icon"><FileAudio size={27} /></div>
          <div className="track-select">
            <span>TRACK</span>
            <select value={midiId} onChange={(event) => setMidiId(event.target.value)} disabled={Boolean(activeJob)}>
              {catalog.midi_files.map((file) => (
                <option value={file.id} key={file.id}>
                  {file.midi_ddsp_supported ? file.midi_ddsp_mode === 'multitrack' ? '[MULTI]' : '[MONO]' : '[UNSUPPORTED]'} {file.name}
                </option>
              ))}
            </select>
          </div>
          <input ref={uploadRef} type="file" accept=".mid,.midi,audio/midi" hidden onChange={(event) => upload(event.target.files?.[0])} />
          <button type="button" className="icon-button" title="上传 MIDI" onClick={() => uploadRef.current?.click()} disabled={busy || Boolean(activeJob)}>
            <FolderUp size={19} />
          </button>
        </div>

        {selectedMidi && (
          <div className="metrics-row report-metrics">
            <Metric label="时长" value={selectedMidi.duration_seconds.toFixed(2)} unit="s" />
            <Metric label="音符" value={selectedMidi.note_count} />
            <Metric label="有效轨道" value={selectedMidi.track_count} />
            <Metric label="最大复音" value={selectedMidi.max_polyphony} tone={selectedMidi.max_polyphony > 1 ? 'red' : 'teal'} />
          </div>
        )}

        <AudioWaveform artifact={audioArtifact} />

        <div className="transport-bar">
          {!activeJob && (
            <button className="transport-primary" type="button" title="开始" onClick={start} disabled={busy || !ready}>
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
          <Metric label="架构" value={report?.architecture === 'stateful-v2' ? 'STATEFUL V2' : 'LEGACY'} tone={report?.architecture === 'stateful-v2' ? 'teal' : 'amber'} />
          <Metric label="Expression" value={report?.expression_inference_count ?? 0} />
          <Metric label="合成 P95" value={(report?.synthesis_render_p95_ms ?? 0).toFixed(2)} unit="ms" tone="amber" />
          <Metric label="混响" value={report?.reverb_enabled ? 'ORIGINAL' : '-'} tone={report?.reverb_enabled ? 'teal' : undefined} />
          <Metric label="缓存" value={report?.cache_hit ? 'HIT' : 'MISS'} />
          <Metric label="削波" value={report?.clipped_samples ?? 0} tone={(report?.clipped_samples ?? 0) > 0 ? 'red' : undefined} />
        </div>
      </section>

      <aside className="panel settings-panel midi-settings">
        <PanelHeader title="渲染参数" action={<RotateCcw size={18} />} />
        <div className="form-grid single-column">
          <Field label="模型包">
            <select value={bundleId} onChange={(event) => setBundleId(event.target.value)} disabled={Boolean(activeJob)}>
              {catalog.midi_ddsp_bundles.map((bundle) => (
                <option value={bundle.id} key={bundle.id}>
                  {bundle.architecture === 'stateful-v2' ? 'Stateful v2' : 'Legacy'} · {bundle.precision}
                </option>
              ))}
            </select>
          </Field>
          {selectedBundle && <small className="field-hint">{selectedBundle.name} · {formatBytes(bundleSize)}</small>}
          <Field label="乐器">
            <select value={instrumentId} onChange={(event) => setInstrumentId(Number(event.target.value))} disabled={Boolean(activeJob)}>
              {catalog.instruments.map((instrument) => <option value={instrument.id} key={instrument.id}>{instrument.id.toString().padStart(2, '0')} · {instrument.name}</option>)}
            </select>
          </Field>
          {mode === 'play' && <Field label="音频输出"><select value={audioDeviceId} onChange={(event) => setAudioDeviceId(event.target.value)} disabled={Boolean(activeJob)}><option value="">系统默认</option>{audioDevices.map((device) => <option value={device.id} key={device.id}>{device.name}</option>)}</select></Field>}
          <Field label="随机种子"><input type="number" min="0" max="2147483647" value={seed} onChange={(event) => setSeed(Number(event.target.value))} disabled={Boolean(activeJob)} /></Field>
          <Field label={`输出增益 ${gain} dB`}><input type="range" min="-60" max="0" value={gain} onChange={(event) => setGain(Number(event.target.value))} /></Field>
          <Field label={`设备延迟 ${latency} ms`}><input type="range" min="20" max="250" step="10" value={latency} onChange={(event) => setLatency(Number(event.target.value))} /></Field>
          <Field label={`预缓冲 ${prebuffer} blocks`}><input type="range" min="1" max="16" value={prebuffer} onChange={(event) => setPrebuffer(Number(event.target.value))} /></Field>
          <Field label={`额外尾音 ${tail.toFixed(1)} s`}><input type="range" min="0" max="4" step="0.1" value={tail} onChange={(event) => setTail(Number(event.target.value))} /></Field>
        </div>
      </aside>
    </div>
  )
}
