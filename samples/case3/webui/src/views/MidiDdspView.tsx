import { useEffect, useMemo, useRef, useState } from 'react'
import {
  Check,
  Circle,
  Download,
  FileAudio,
  FolderUp,
  Library,
  ListMusic,
  LoaderCircle,
  Pause,
  Play,
  RotateCcw,
  Square,
  Volume2,
} from 'lucide-react'
import { api, artifactUrl, formatBytes } from '../api'
import { audioDeviceLabel } from '../audio'
import AudioWaveform from '../components/AudioWaveform'
import { Field, Metric, Notice, PanelHeader, Segmented, StatusPill } from '../components/ui'
import type {
  Artifact,
  AudioDevice,
  Catalog,
  Job,
  MidiDdspBundle,
  MidiVoiceAnalysis,
  MidiVoiceAnalysisVoice,
} from '../types'

interface Props {
  catalog: Catalog
  audioDevices: AudioDevice[]
  jobs: Job[]
  onRefresh: () => Promise<void>
}

interface Recording {
  job: Job
  artifact: Artifact
}

type VoiceScheme = 'auto' | 'strings' | 'woodwinds' | 'brass' | 'custom'

const SCHEME_ROLES: Record<Exclude<VoiceScheme, 'auto' | 'custom'>, number[]> = {
  strings: [0, 0, 1, 2],
  woodwinds: [4, 5, 6, 8],
  brass: [9, 10, 11, 12],
}

const ACTIVE = new Set(['queued', 'preparing', 'running', 'paused', 'stopping'])
const RENDER_STAGES = [
  ['preparing', '准备'],
  ['loading_models', '加载模型'],
  ['expression', '表现生成'],
  ['pitch_context', '音高与上下文'],
  ['timbre', '音色参数'],
  ['dsp_reverb', 'DSP / 混响'],
  ['mixing', '混音'],
  ['writing_cache', '写入缓存'],
] as const

export function selectableMidiDdspBundles(bundles: MidiDdspBundle[]): MidiDdspBundle[] {
  return bundles.filter((bundle) => !bundles.some((candidate) => {
    if (bundle.recommended || !candidate.recommended || candidate.id === bundle.id) return false
    if (
      candidate.architecture !== bundle.architecture
      || candidate.precision !== bundle.precision
      || candidate.onnx_dtype !== bundle.onnx_dtype
      || candidate.source_commit !== bundle.source_commit
    ) return false
    const candidateBatches = new Set(candidate.voice_batch_sizes ?? [1])
    return (bundle.voice_batch_sizes ?? [1]).every((size) => candidateBatches.has(size))
  }))
}

function analysisVoices(analysis?: MidiVoiceAnalysis | null): MidiVoiceAnalysisVoice[] {
  return analysis?.groups.flatMap((group) => group.voices) ?? []
}

export function buildVoiceAssignments(
  analysis: MidiVoiceAnalysis,
  scheme: Exclude<VoiceScheme, 'custom'>,
): Record<string, number> {
  const voices = analysisVoices(analysis)
  if (scheme === 'auto') {
    return Object.fromEntries(voices.map((voice) => [voice.id, voice.suggested_instrument_id]))
  }
  const ranked = [...voices].sort((left, right) => (
    right.pitch_median - left.pitch_median || left.id.localeCompare(right.id)
  ))
  const roles = SCHEME_ROLES[scheme]
  return Object.fromEntries(ranked.map((voice, rank) => {
    const role = ranked.length === 1
      ? 0
      : Math.round((rank * 3) / (ranked.length - 1))
    return [voice.id, roles[role]]
  }))
}

function assignmentsMatch(value: unknown, expected: Record<string, number>): boolean {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false
  const actual = value as Record<string, unknown>
  const keys = Object.keys(expected)
  return Object.keys(actual).length === keys.length
    && keys.every((key) => actual[key] === expected[key])
}

function noteName(pitch: number): string {
  const names = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']
  return `${names[pitch % 12]}${Math.floor(pitch / 12) - 1}`
}

function recordingInstrumentLabelForJob(job: Job, catalog: Catalog): string {
  const instrumentIds = Array.isArray(job.metadata.instrument_ids)
    ? job.metadata.instrument_ids.filter((value): value is number => typeof value === 'number')
    : []
  const voiceCount = metadataNumber(job, 'voice_count', Object.keys(
    (job.metadata.voice_instruments as Record<string, unknown> | undefined) ?? {},
  ).length)
  if (instrumentIds.length > 1) return `多音色 · ${voiceCount} 个声部`
  const instrumentId = instrumentIds[0] ?? metadataNumber(job, 'instrument_id')
  return catalog.instruments.find((item) => item.id === instrumentId)?.name
    ?? `Instrument ${instrumentId}`
}

function formatDuration(value?: number | null): string {
  if (value == null || !Number.isFinite(value)) return '-'
  const seconds = Math.max(0, Math.round(value))
  const minutes = Math.floor(seconds / 60)
  const remainder = seconds % 60
  return minutes ? `${minutes}:${remainder.toString().padStart(2, '0')}` : `${remainder}s`
}

function formatTimestamp(value: string): string {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).format(date)
}

function metadataString(job: Job, key: string, fallback = '-'): string {
  const value = job.metadata[key]
  return typeof value === 'string' && value ? value : fallback
}

function metadataNumber(job: Job, key: string, fallback = 0): number {
  const value = job.metadata[key]
  return typeof value === 'number' && Number.isFinite(value) ? value : fallback
}

function midiOptionTag(file: Catalog['midi_files'][number]): string {
  if (file.midi_ddsp_mode === 'polyphonic') return '[POLY]'
  if (file.midi_ddsp_mode === 'multitrack') return '[MULTI]'
  if (file.midi_ddsp_supported) return '[MONO]'
  return '[UNSUPPORTED]'
}

function midiUnsupportedMessage(file: Catalog['midi_files'][number]): string {
  if (file.unsupported_code === 'unsupported_program') {
    return '当前多轨 MIDI 的乐器不在 MIDI-DDSP 支持的 13 种乐器映射内。'
  }
  return file.unsupported_reason ?? '该 MIDI 不符合原版单声部模型的输入要求。'
}

export default function MidiDdspView({ catalog, audioDevices, jobs, onRefresh }: Props) {
  const preferredMidi = catalog.midi_files.find((file) => file.midi_ddsp_supported)
    ?? catalog.midi_files[0]
  const modelBundles = useMemo(
    () => selectableMidiDdspBundles(catalog.midi_ddsp_bundles),
    [catalog.midi_ddsp_bundles],
  )
  const defaultBundle = modelBundles.find((bundle) => bundle.recommended)
    ?? modelBundles[0]
  const recordings = useMemo<Recording[]>(() => jobs.flatMap((job) => {
    if (!job.kind.startsWith('midi-ddsp') || job.kind === 'midi-ddsp-wav-playback') return []
    const artifact = job.artifacts.find((item) => item.name === 'output.wav')
    return artifact ? [{ job, artifact }] : []
  }), [jobs])
  const activeRenderJob = jobs.find((job) => (
    job.kind !== 'midi-ddsp-wav-playback'
    && job.kind.startsWith('midi-ddsp')
    && ACTIVE.has(job.state)
  ))
  const activePlaybackJob = jobs.find((job) => (
    job.kind === 'midi-ddsp-wav-playback' && ACTIVE.has(job.state)
  ))
  const activePlaybackSourceId = activePlaybackJob
    ? metadataString(activePlaybackJob, 'source_job_id', '')
    : ''
  const activeJob = activeRenderJob ?? activePlaybackJob
  const [view, setView] = useState<'library' | 'render'>(recordings.length ? 'library' : 'render')
  const [midiId, setMidiId] = useState(preferredMidi?.id ?? '')
  const [bundleId, setBundleId] = useState(defaultBundle?.id ?? '')
  const [instrumentId, setInstrumentId] = useState(0)
  const [voiceAnalysis, setVoiceAnalysis] = useState<MidiVoiceAnalysis | null>(null)
  const [voiceAssignments, setVoiceAssignments] = useState<Record<string, number>>({})
  const [voiceScheme, setVoiceScheme] = useState<VoiceScheme>('auto')
  const [voiceAnalysisLoading, setVoiceAnalysisLoading] = useState(false)
  const [voiceAnalysisError, setVoiceAnalysisError] = useState('')
  const [voiceAnalysisRevision, setVoiceAnalysisRevision] = useState(0)
  const [audioDeviceId, setAudioDeviceId] = useState('')
  const [selectedRecordingId, setSelectedRecordingId] = useState(recordings[0]?.job.id ?? '')
  const [playbackTarget, setPlaybackTarget] = useState<'browser' | 'board'>('board')
  const [playbackGain, setPlaybackGain] = useState(0)
  const [gain, setGain] = useState(0)
  const [tail, setTail] = useState(2)
  const [seed, setSeed] = useState(20260724)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [clock, setClock] = useState(() => Date.now() / 1000)
  const uploadRef = useRef<HTMLInputElement>(null)
  const libraryAutoSelected = useRef(recordings.length > 0)
  const playbackAutoSelectedJobId = useRef('')

  useEffect(() => {
    if (!midiId && preferredMidi) setMidiId(preferredMidi.id)
    if (midiId && !catalog.midi_files.some((file) => file.id === midiId) && preferredMidi) {
      setMidiId(preferredMidi.id)
    }
    if ((!bundleId || !modelBundles.some((bundle) => bundle.id === bundleId)) && defaultBundle) {
      setBundleId(defaultBundle.id)
    }
  }, [bundleId, catalog.midi_files, defaultBundle, midiId, modelBundles, preferredMidi])

  useEffect(() => {
    let cancelled = false
    setVoiceAnalysis(null)
    setVoiceAssignments({})
    setVoiceScheme('auto')
    setVoiceAnalysisError('')
    if (!midiId) {
      setVoiceAnalysisLoading(false)
      return () => { cancelled = true }
    }
    setVoiceAnalysisLoading(true)
    api.midiVoices(midiId)
      .then((analysis) => {
        if (cancelled) return
        setVoiceAnalysis(analysis)
        setVoiceAssignments(buildVoiceAssignments(analysis, 'auto'))
      })
      .catch((cause: unknown) => {
        if (cancelled) return
        setVoiceAnalysisError(cause instanceof Error ? cause.message : String(cause))
      })
      .finally(() => {
        if (!cancelled) setVoiceAnalysisLoading(false)
      })
    return () => { cancelled = true }
  }, [midiId, voiceAnalysisRevision])

  useEffect(() => {
    if (!recordings.length) {
      setSelectedRecordingId('')
      return
    }
    if (!recordings.some((recording) => recording.job.id === selectedRecordingId)) {
      setSelectedRecordingId(recordings[0].job.id)
    }
  }, [recordings, selectedRecordingId])

  useEffect(() => {
    if (recordings.length && !libraryAutoSelected.current && !activeRenderJob) {
      libraryAutoSelected.current = true
      setView('library')
    }
  }, [activeRenderJob, recordings.length])

  useEffect(() => {
    if (activeRenderJob) setView('render')
    else if (
      activePlaybackJob
      && playbackAutoSelectedJobId.current !== activePlaybackJob.id
    ) {
      playbackAutoSelectedJobId.current = activePlaybackJob.id
      if (activePlaybackSourceId) setSelectedRecordingId(activePlaybackSourceId)
      setPlaybackTarget('board')
      setView('library')
    }
  }, [activePlaybackJob, activePlaybackSourceId, activeRenderJob])

  useEffect(() => {
    if (!activeJob) return undefined
    const timer = window.setInterval(() => setClock(Date.now() / 1000), 1000)
    return () => window.clearInterval(timer)
  }, [activeJob])

  const selectedMidi = catalog.midi_files.find((file) => file.id === midiId)
  const selectedBundle = modelBundles.find((bundle) => bundle.id === bundleId)
  const voices = analysisVoices(voiceAnalysis)
  const voiceAssignmentsComplete = Boolean(
    voiceAnalysis
    && voices.length > 0
    && Object.keys(voiceAssignments).length === voices.length
    && voices.every((voice) => Number.isInteger(voiceAssignments[voice.id])),
  )
  const matchingJob = jobs.find((job) => (
    job.kind === 'midi-ddsp-render'
    && job.metadata.midi_id === midiId
    && job.metadata.model_bundle_id === bundleId
    && job.metadata.voice_analysis_id === voiceAnalysis?.analysis_id
    && assignmentsMatch(job.metadata.voice_instruments, voiceAssignments)
    && job.metadata.seed === seed
    && job.metadata.sample_rate === 48000
    && job.metadata.output_gain_db === gain
    && job.metadata.tail_seconds === tail
  ))
  const renderJob = activeRenderJob ?? matchingJob
  const renderReport = renderJob?.metadata.report
  const renderAudioArtifact = renderJob?.artifacts.find((artifact) => artifact.name === 'output.wav')
  const progressDetail = renderJob?.progress_detail
  const currentStage = progressDetail?.stage ?? (activeRenderJob ? 'preparing' : '')
  const currentStageIndex = RENDER_STAGES.findIndex(([stage]) => stage === currentStage)
  const heartbeatAge = progressDetail?.heartbeat_at == null
    ? Math.max(0, clock - Date.parse(activeRenderJob?.updated_at ?? new Date().toISOString()) / 1000)
    : Math.max(0, clock - progressDetail.heartbeat_at)
  const heartbeatStale = Boolean(activeRenderJob && activeRenderJob.state !== 'paused' && heartbeatAge > 10)
  const currentStageLabel = progressDetail?.activity === 'reading_cache'
    ? '读取缓存'
    : RENDER_STAGES.find(([stage]) => stage === currentStage)?.[1]
  const selectedRecording = recordings.find((recording) => recording.job.id === selectedRecordingId)
    ?? recordings[0]
  const recordingReport = selectedRecording?.job.metadata.report
  const recordingInstrumentId = selectedRecording
    ? metadataNumber(selectedRecording.job, 'instrument_id')
    : 0
  const recordingInstrumentLabel = selectedRecording
    ? recordingInstrumentLabelForJob(selectedRecording.job, catalog)
    : `Instrument ${recordingInstrumentId}`
  const playbackProgress = activePlaybackJob?.progress_detail?.overall_progress
    ?? activePlaybackJob?.progress
    ?? 0
  const reverbAsset = catalog.midi_ddsp_reverb_assets[0]
  const supported = Boolean(selectedMidi?.midi_ddsp_supported)
  const configuredInstrumentIds = [...new Set(Object.values(voiceAssignments))]
  const configuredInstrumentLabel = configuredInstrumentIds.length > 1
    ? `多音色 · ${voices.length} 个声部`
    : catalog.instruments.find((item) => item.id === configuredInstrumentIds[0])?.name ?? '-'
  const ready = Boolean(
    midiId
    && bundleId
    && reverbAsset
    && supported
    && voiceAnalysis
    && !voiceAnalysisLoading
    && !voiceAnalysisError
    && voiceAssignmentsComplete,
  )
  const bundleSize = useMemo(
    () => Object.values(selectedBundle?.components ?? {}).reduce((total, component) => total + component.size_bytes, 0),
    [selectedBundle],
  )
  const midiOptions = useMemo(
    () => [...catalog.midi_files].sort((left, right) => {
      if (left.midi_ddsp_supported !== right.midi_ddsp_supported) {
        return left.midi_ddsp_supported ? -1 : 1
      }
      return left.name.localeCompare(right.name)
    }),
    [catalog.midi_files],
  )

  function applyVoiceScheme(scheme: Exclude<VoiceScheme, 'custom'>) {
    if (!voiceAnalysis) return
    setVoiceAssignments(buildVoiceAssignments(voiceAnalysis, scheme))
    setVoiceScheme(scheme)
  }

  function setVoiceInstrument(voiceId: string, nextInstrumentId: number) {
    setVoiceAssignments((current) => ({ ...current, [voiceId]: nextInstrumentId }))
    setVoiceScheme('custom')
  }

  function setAllVoiceInstruments(nextInstrumentId: number) {
    setInstrumentId(nextInstrumentId)
    setVoiceAssignments(Object.fromEntries(voices.map((voice) => [voice.id, nextInstrumentId])))
    setVoiceScheme('custom')
  }

  async function startRender() {
    if (!voiceAnalysis || !voiceAssignmentsComplete) return
    setBusy(true)
    setError('')
    try {
      await api.startMidiDdsp({
        mode: 'render',
        force_render: true,
        midi_id: midiId,
        model_bundle_id: bundleId,
        instrument_id: instrumentId,
        voice_analysis_id: voiceAnalysis.analysis_id,
        voice_instruments: voiceAssignments,
        seed,
        sample_rate: 48000,
        output_gain_db: gain,
        tail_seconds: tail,
        device_id: 0,
      })
      setView('render')
      await onRefresh()
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : String(cause)
      if ((cause as { code?: string })?.code === 'voice_analysis_stale') {
        setVoiceAnalysis(null)
        setVoiceAssignments({})
        setVoiceAnalysisError('声部分析已过期，请重新分析后再渲染。')
      }
      setError(message)
    } finally {
      setBusy(false)
    }
  }

  async function playRecording() {
    if (!selectedRecording) return
    setBusy(true)
    setError('')
    try {
      await api.playMidiDdspRecording(selectedRecording.job.id, {
        audio_device_id: audioDeviceId || null,
        latency_ms: 40,
        output_gain_db: playbackGain,
      })
      await onRefresh()
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause))
    } finally {
      setBusy(false)
    }
  }

  async function control(job: Job | undefined, action: 'pause' | 'resume' | 'stop') {
    if (!job) return
    setBusy(true)
    setError('')
    try {
      await api.controlJob(job.id, action)
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
      <section className={`panel midi-player-panel ${activeRenderJob ? 'has-active-render' : ''}`}>
        <PanelHeader
          title={view === 'library' ? 'MIDI-DDSP 音频库' : 'MIDI-DDSP 新建渲染'}
          subtitle={view === 'library' ? '选择已有 WAV 立即播放' : '完整质量渲染与进度'}
          action={(
            <Segmented
              value={view}
              options={[
                { value: 'library', label: '音频库' },
                { value: 'render', label: '新建渲染' },
              ]}
              onChange={setView}
            />
          )}
        />
        {error && <Notice tone="error">{error}</Notice>}

        {view === 'library' ? (
          <>
            {!selectedRecording ? (
              <div className="recording-empty">
                <Library size={30} />
                <strong>暂无已生成音频</strong>
                <button type="button" className="primary-button" onClick={() => setView('render')}>
                  <ListMusic size={17} />新建渲染
                </button>
              </div>
            ) : (
              <>
                <div className="recording-heading">
                  <div className="track-icon"><FileAudio size={27} /></div>
                  <div>
                    <span>正在查看</span>
                    <strong>{metadataString(selectedRecording.job, 'midi_name', 'MIDI-DDSP output.wav')}</strong>
                    <small>{recordingInstrumentLabel} · {formatTimestamp(selectedRecording.job.created_at)}</small>
                  </div>
                  <a className="icon-button" title="下载 WAV" href={artifactUrl(selectedRecording.artifact.id)} download>
                    <Download size={18} />
                  </a>
                </div>
                <div className="recording-playback-route">
                  <span>播放位置</span>
                  <Segmented
                    value={playbackTarget}
                    options={[
                      { value: 'browser', label: '当前浏览器' },
                      { value: 'board', label: '开发板喇叭' },
                    ]}
                    onChange={setPlaybackTarget}
                    disabled={Boolean(activePlaybackJob)}
                  />
                </div>
                <AudioWaveform artifact={selectedRecording.artifact} showControls={playbackTarget === 'browser'} />
                {playbackTarget === 'board' && (
                  <>
                    <div className="recording-output-bar">
                      <Field label="开发板音频输出">
                        <select value={audioDeviceId} onChange={(event) => setAudioDeviceId(event.target.value)} disabled={Boolean(activeJob)}>
                          <option value="">系统默认</option>
                          {audioDevices.map((device) => <option value={device.id} key={device.id}>{audioDeviceLabel(device)}</option>)}
                        </select>
                      </Field>
                      <Field label={`开发板播放音量 ${playbackGain} dB`}>
                        <input
                          type="range"
                          aria-label="开发板播放音量"
                          min={-60}
                          max={0}
                          step={1}
                          value={playbackGain}
                          onChange={(event) => setPlaybackGain(Number(event.target.value))}
                          disabled={Boolean(activeJob)}
                        />
                      </Field>
                      {!activePlaybackJob ? (
                        <button type="button" className="primary-button" onClick={playRecording} disabled={busy || Boolean(activeRenderJob)}>
                          <Volume2 size={17} />开发板播放
                        </button>
                      ) : (
                        <div className="recording-playback-controls">
                          {activePlaybackJob.state === 'paused' ? (
                            <button className="icon-button" type="button" title="继续" onClick={() => control(activePlaybackJob, 'resume')} disabled={busy}><Play size={18} fill="currentColor" /></button>
                          ) : (
                            <button className="icon-button" type="button" title="暂停" onClick={() => control(activePlaybackJob, 'pause')} disabled={busy}><Pause size={18} fill="currentColor" /></button>
                          )}
                          <button className="icon-button" type="button" title="停止" onClick={() => control(activePlaybackJob, 'stop')} disabled={busy}><Square size={17} fill="currentColor" /></button>
                        </div>
                      )}
                    </div>
                    {activePlaybackJob && (
                      <div className="recording-playback-progress" aria-label="已有 WAV 播放进度">
                        <div className="timeline-meta">
                          <StatusPill tone={activePlaybackJob.state === 'paused' ? 'warn' : 'ok'}>{activePlaybackJob.state}</StatusPill>
                          <span>{Math.round(playbackProgress * 100)}%</span>
                        </div>
                        <div className="progress-track"><span style={{ width: `${playbackProgress * 100}%` }} /></div>
                      </div>
                    )}
                  </>
                )}
                <div className="metrics-row report-metrics recording-metrics">
                  <Metric label="音色" value={recordingInstrumentLabel} tone="teal" />
                  <Metric label="文件" value={formatBytes(selectedRecording.artifact.size_bytes)} />
                  <Metric label="时长" value={formatDuration(recordingReport?.duration_seconds)} />
                  <Metric label="渲染耗时" value={(recordingReport?.render_wall_seconds ?? recordingReport?.inference_and_dsp_wall_seconds ?? 0).toFixed(1)} unit="s" />
                  <Metric label="缓存" value={recordingReport?.cache_hit ? 'HIT' : 'MISS'} />
                </div>
              </>
            )}
          </>
        ) : (
          <>
            {!reverbAsset && <Notice tone="error">缺少 MIDI-DDSP 原版混响资产</Notice>}
            {renderJob?.state === 'failed' && renderJob.message && <Notice tone="error">{renderJob.message}</Notice>}
            {heartbeatStale && <Notice tone="warn">渲染进程超过 10 秒没有心跳，正在等待连接恢复。</Notice>}
            {selectedMidi && !selectedMidi.midi_ddsp_supported && <Notice tone="error">{midiUnsupportedMessage(selectedMidi)}</Notice>}
            {selectedMidi?.midi_ddsp_mode === 'polyphonic' && (
              <Notice tone="warn">
                复音轨将使用 Chew/Wu Contig Mapping 拆分为严格单音声部，并按下方配置逐声部合成与应用 Google 原版混响。
              </Notice>
            )}

            <div className="track-browser">
              <div className="track-icon"><FileAudio size={27} /></div>
              <div className="track-select">
                <span>TRACK</span>
                <select aria-label="曲目" value={midiId} onChange={(event) => setMidiId(event.target.value)} disabled={Boolean(activeJob)}>
                  {midiOptions.map((file) => <option value={file.id} key={file.id}>{midiOptionTag(file)} {file.name}</option>)}
                </select>
              </div>
              <input ref={uploadRef} type="file" accept=".mid,.midi,audio/midi" hidden onChange={(event) => upload(event.target.files?.[0])} />
              <button type="button" className="icon-button" title="上传 MIDI" onClick={() => uploadRef.current?.click()} disabled={busy || Boolean(activeJob)}><FolderUp size={19} /></button>
            </div>

            {selectedMidi && (
              <div className="metrics-row report-metrics midi-track-metrics">
                <Metric label="时长" value={selectedMidi.duration_seconds.toFixed(2)} unit="s" />
                <Metric label="音符" value={selectedMidi.note_count} />
                <Metric label="有效轨道" value={selectedMidi.track_count} />
                <Metric label="声部" value={selectedMidi.voice_count} tone={selectedMidi.voice_count > 1 ? 'amber' : 'teal'} />
                <Metric label="最大复音" value={selectedMidi.max_polyphony} tone={selectedMidi.max_polyphony > 1 ? 'amber' : 'teal'} />
                <Metric label="音色配置" value={configuredInstrumentLabel} tone="teal" />
              </div>
            )}

            <section className="voice-assignment-section" aria-label="MIDI 声部音色分配">
              <div className="voice-assignment-heading">
                <div>
                  <span>VOICE ASSIGNMENT</span>
                  <strong>检测到的声部</strong>
                </div>
                {voiceAnalysisLoading ? (
                  <span className="voice-analysis-status"><LoaderCircle className="spin" size={14} />分析中</span>
                ) : voiceAnalysis ? (
                  <StatusPill tone="ok">{voiceAnalysis.voice_count} 个声部</StatusPill>
                ) : null}
              </div>
              {voiceAnalysisError && (
                <div className="voice-analysis-error">
                  <span>{voiceAnalysisError}</span>
                  <button type="button" className="secondary-button" onClick={() => setVoiceAnalysisRevision((value) => value + 1)} disabled={Boolean(activeJob)}>重试</button>
                </div>
              )}
              {!voiceAnalysisLoading && voiceAnalysis && (
                <>
                  <div className="voice-analysis-source">
                    <span>{voiceAnalysis.algorithm.name}</span>
                    <span>Partitura {voiceAnalysis.algorithm.version}</span>
                    <span>{voiceAnalysis.algorithm.commit.slice(0, 8)}</span>
                  </div>
                  <div className="voice-assignment-table" role="table">
                    <div className="voice-assignment-row voice-assignment-header" role="row">
                      <span role="columnheader">声部</span>
                      <span role="columnheader">来源</span>
                      <span role="columnheader">GM Program</span>
                      <span role="columnheader">音符</span>
                      <span role="columnheader">音域</span>
                      <span role="columnheader">合成音色</span>
                    </div>
                    {voices.map((voice, index) => {
                      const detectedInstrument = voice.detected_instrument_id == null
                        ? null
                        : catalog.instruments.find((item) => item.id === voice.detected_instrument_id)
                      return (
                        <div className="voice-assignment-row" role="row" key={voice.id}>
                          <span className="voice-number" role="cell" title={voice.id}>V{index + 1}</span>
                          <span className="voice-source" role="cell">
                            <strong>Track {voice.track_index} · Ch {voice.channel}</strong>
                            <small>{voice.track_name}</small>
                          </span>
                          <span className="voice-program" role="cell">
                            <strong>{voice.program}</strong>
                            <small>{detectedInstrument?.name ?? '按音域建议'}</small>
                          </span>
                          <span className="voice-notes" role="cell">{voice.note_count}</span>
                          <span className="voice-range" role="cell">
                            <strong>{noteName(voice.pitch_min)} - {noteName(voice.pitch_max)}</strong>
                            <small>MIDI {voice.pitch_min}-{voice.pitch_max}</small>
                          </span>
                          <span className="voice-instrument" role="cell">
                            <select
                              aria-label={`声部 ${index + 1} 音色`}
                              value={voiceAssignments[voice.id] ?? voice.suggested_instrument_id}
                              onChange={(event) => setVoiceInstrument(voice.id, Number(event.target.value))}
                              disabled={Boolean(activeJob)}
                            >
                              {catalog.instruments.map((instrument) => <option value={instrument.id} key={instrument.id}>{instrument.id.toString().padStart(2, '0')} · {instrument.name}</option>)}
                            </select>
                          </span>
                        </div>
                      )
                    })}
                  </div>
                </>
              )}
            </section>

            {renderAudioArtifact && <AudioWaveform artifact={renderAudioArtifact} compact={Boolean(activeRenderJob)} showControls={false} />}

            {renderJob && (
              <div className="midi-render-progress" aria-label="MIDI-DDSP 渲染进度">
                <div className="render-progress-header">
                  <div><span>当前阶段</span><strong>{currentStageLabel ?? (renderJob.state === 'succeeded' ? '完成' : '等待')}</strong></div>
                  <div className={heartbeatStale ? 'heartbeat stale' : 'heartbeat'}>
                    <span className="heartbeat-dot" />
                    {!activeRenderJob ? '任务结束' : heartbeatStale ? '心跳中断' : `心跳 ${Math.round(heartbeatAge)}s`}
                  </div>
                </div>
                <div className="render-stage-list" role="list">
                  {RENDER_STAGES.map(([stage, label], index) => {
                    const complete = currentStageIndex > index || renderJob.state === 'succeeded'
                    const active = currentStage === stage && ACTIVE.has(renderJob.state)
                    const Icon = complete ? Check : active ? LoaderCircle : Circle
                    return <div className={`render-stage ${complete ? 'complete' : ''} ${active ? 'active' : ''}`} role="listitem" key={stage}><Icon size={14} /><span>{label}</span></div>
                  })}
                </div>
                <div className="stage-progress-row">
                  <span>阶段进度</span>
                  <div className="progress-track"><span style={{ width: `${Math.max(0, Math.min(1, progressDetail?.stage_progress ?? 0)) * 100}%` }} /></div>
                  <strong>{Math.round((progressDetail?.stage_progress ?? 0) * 100)}%</strong>
                </div>
                <div className="render-progress-details">
                  <span>批次 <strong>{progressDetail?.voice_batch_index ?? '-'}/{progressDetail?.voice_batch_count ?? '-'}</strong></span>
                  <span>工作量 <strong>{progressDetail?.completed ?? '-'}/{progressDetail?.total ?? '-'}</strong></span>
                  <span>已用 <strong>{formatDuration(progressDetail?.elapsed_seconds)}</strong></span>
                  <span>剩余 <strong>{formatDuration(progressDetail?.eta_seconds)}</strong></span>
                </div>
              </div>
            )}

            {renderJob && (
              <div className="metrics-row report-metrics">
                <Metric label="架构" value={renderReport?.architecture?.startsWith('stateful-v2') ? 'STATEFUL V2' : '-'} tone="teal" />
                <Metric label="Expression" value={renderReport?.expression_inference_count ?? 0} />
                <Metric label="合成 P95" value={(renderReport?.synthesis_render_p95_ms ?? 0).toFixed(2)} unit="ms" tone="amber" />
                <Metric label="混响" value={renderReport?.reverb_enabled ? 'ORIGINAL' : '-'} tone={renderReport?.reverb_enabled ? 'teal' : undefined} />
                <Metric label="混音保护" value={renderReport?.peak_protection_enabled ? `${(renderReport.mix_gain_db ?? 0).toFixed(1)} dB` : '-'} tone={renderReport?.peak_protection_enabled ? 'amber' : undefined} />
                <Metric label="缓存" value={renderReport?.cache_hit ? 'HIT' : 'MISS'} />
                <Metric label="渲染耗时" value={(renderReport?.render_wall_seconds ?? renderReport?.inference_and_dsp_wall_seconds ?? 0).toFixed(1)} unit="s" />
                <Metric label="削波" value={renderReport?.clipped_samples ?? 0} tone={(renderReport?.clipped_samples ?? 0) > 0 ? 'red' : undefined} />
              </div>
            )}
          </>
        )}
      </section>

      {view === 'library' ? (
        <aside className="panel recording-library-panel">
          <PanelHeader title="已生成音频" subtitle={`${recordings.length} 个 WAV`} action={<Library size={18} />} />
          <div className="recording-list">
            {recordings.map((recording) => {
              const itemInstrumentLabel = recordingInstrumentLabelForJob(recording.job, catalog)
              const isPlaying = recording.job.id === activePlaybackSourceId
              return (
                <button
                  type="button"
                  className={`recording-row ${recording.job.id === selectedRecording?.job.id ? 'is-selected' : ''}`}
                  key={recording.job.id}
                  onClick={() => setSelectedRecordingId(recording.job.id)}
                  aria-pressed={recording.job.id === selectedRecording?.job.id}
                >
                  <FileAudio size={18} />
                  <span>
                    <strong>{metadataString(recording.job, 'midi_name', 'output.wav')}</strong>
                    <small>{itemInstrumentLabel} · {formatTimestamp(recording.job.created_at)} · {formatBytes(recording.artifact.size_bytes)}</small>
                  </span>
                  <StatusPill tone={isPlaying ? 'warn' : recording.job.state === 'succeeded' ? 'ok' : 'neutral'}>{isPlaying ? 'PLAY' : 'WAV'}</StatusPill>
                </button>
              )
            })}
          </div>
        </aside>
      ) : (
        <aside className="panel settings-panel midi-settings">
          <PanelHeader
            title="渲染参数"
            action={(
              <button type="button" className="icon-button" title="恢复自动建议" onClick={() => applyVoiceScheme('auto')} disabled={Boolean(activeJob) || !voiceAnalysis}>
                <RotateCcw size={18} />
              </button>
            )}
          />
          <div className="form-grid single-column">
            <Field label="模型包">
              {modelBundles.length > 1 ? (
                <select value={bundleId} onChange={(event) => setBundleId(event.target.value)} disabled={Boolean(activeJob)}>
                  {modelBundles.map((bundle) => <option value={bundle.id} key={bundle.id}>{bundle.name}{bundle.recommended ? ' · 推荐' : ''}</option>)}
                </select>
              ) : (
                <output className="field-readonly">{selectedBundle?.name ?? 'Stateful v2'}</output>
              )}
            </Field>
            {selectedBundle && <small className="field-hint">{selectedBundle.quality_status === 'om_validated' ? 'OM 已验证' : selectedBundle.quality_status} · {formatBytes(bundleSize)} · batch {(selectedBundle.voice_batch_sizes ?? [1]).join('/')}</small>}
            <Field label="音色方案">
              <select
                value={voiceScheme}
                onChange={(event) => applyVoiceScheme(event.target.value as Exclude<VoiceScheme, 'custom'>)}
                disabled={Boolean(activeJob) || !voiceAnalysis}
              >
                <option value="auto">自动建议</option>
                <option value="strings">Google 弦乐</option>
                <option value="woodwinds">Google 木管</option>
                <option value="brass">Google 铜管</option>
                <option value="custom" disabled>自定义</option>
              </select>
            </Field>
            <Field label="全部设置为">
              <select value={instrumentId} onChange={(event) => setAllVoiceInstruments(Number(event.target.value))} disabled={Boolean(activeJob) || !voiceAnalysis}>
                {catalog.instruments.map((instrument) => <option value={instrument.id} key={instrument.id}>{instrument.id.toString().padStart(2, '0')} · {instrument.name}</option>)}
              </select>
            </Field>
            <Field label="随机种子"><input type="number" min="0" max="2147483647" value={seed} onChange={(event) => setSeed(Number(event.target.value))} disabled={Boolean(activeJob)} /></Field>
            <Field label={`输出增益 ${gain} dB`}><input type="range" min="-60" max="0" value={gain} onChange={(event) => setGain(Number(event.target.value))} disabled={Boolean(activeJob)} /></Field>
            <Field label={`额外尾音 ${tail.toFixed(1)} s`}><input type="range" min="0" max="4" step="0.1" value={tail} onChange={(event) => setTail(Number(event.target.value))} disabled={Boolean(activeJob)} /></Field>
          </div>
          <div className="midi-settings-footer">
            <div className="midi-render-readiness">
              <span>{voiceAnalysisLoading ? '正在分析声部' : ready ? `${voices.length} 个声部已就绪` : '等待完整配置'}</span>
              <strong>{configuredInstrumentLabel}</strong>
            </div>
            <div className="transport-bar render-transport">
              {!activeRenderJob && (
                <button className="primary-button render-start-button" type="button" title="开始渲染" onClick={startRender} disabled={busy || !ready || Boolean(activePlaybackJob)}>
                  {busy ? <LoaderCircle className="spin" size={18} /> : <FileAudio size={18} />}
                  开始渲染
                </button>
              )}
              {activeRenderJob && <button className="icon-button" type="button" title="停止" onClick={() => control(activeRenderJob, 'stop')} disabled={busy}><Square size={18} fill="currentColor" /></button>}
              {renderJob && (
                <div className="timeline">
                  <div className="timeline-meta">
                    <StatusPill tone={renderJob.state === 'succeeded' ? 'ok' : renderJob.state === 'failed' ? 'error' : activeRenderJob ? 'warn' : 'neutral'}>{renderJob.state}</StatusPill>
                    <span>{Math.round(renderJob.progress * 100)}%</span>
                  </div>
                  <div className="progress-track"><span style={{ width: `${renderJob.progress * 100}%` }} /></div>
                </div>
              )}
              {renderAudioArtifact && <a className="icon-button" title="下载 WAV" href={artifactUrl(renderAudioArtifact.id)} download><Download size={18} /></a>}
            </div>
          </div>
        </aside>
      )}
    </div>
  )
}
