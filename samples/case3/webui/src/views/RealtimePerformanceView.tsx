import { memo, useCallback, useEffect, useMemo, useRef, useState, type ComponentProps, type CSSProperties } from 'react'
import {
  Activity,
  Cable,
  ChevronLeft,
  ChevronRight,
  CircleStop,
  Clock3,
  Disc3,
  Download,
  Gauge,
  Headphones,
  Music2,
  Octagon,
  Pause,
  Play,
  RotateCcw,
  SlidersHorizontal,
  Upload,
  Volume2,
} from 'lucide-react'
import { api, websocketUrl } from '../api'
import LivePianoRoll from '../components/LivePianoRoll'
import Piano, { noteLabel } from '../components/Piano'
import VisualizerKeyboard from '../components/VisualizerKeyboard'
import {
  clearLiveNotes,
  publishLiveNoteEvent,
  publishLiveNotes,
  useLiveNotes,
} from '../components/realtimeLiveNotes'
import { Notice, StatusPill } from '../components/ui'
import type {
  LatencyProfile,
  RealtimeCatalog,
  RealtimePatch,
  RealtimePatchCategory,
  RealtimeStatus,
} from '../types'

interface Props {
  onRefresh: () => Promise<void>
  inputMode?: RealtimeInputMode
}

type DrawerTab = 'player' | 'recording' | 'tone' | 'connection' | 'diagnostics'
type RealtimeInputMode = 'touch' | 'midi'
type TouchKeyboardKeyCount = 13 | 25
type TouchKeyboardSize = 'small' | 'medium' | 'large'
type MidiKeyboardKeyCount = 32 | 49 | 61 | 88
type KeyboardKeyCount = 13 | 25 | 32 | 49 | 61 | 88

interface StoredWorkbenchConfig {
  version: 2
  lastPatchId: string
  audioDeviceId: string
  midiPort: string
  latencyProfile: LatencyProfile
  recentPatchIds: string[]
  patchParameters: Record<string, Record<string, number>>
  keyboardSizingVersion?: 1 | 2
  // Version 1 fields are retained only to migrate existing browser settings.
  keyboardKeyCount?: KeyboardKeyCount
  keyboardFirstNote?: number
  touchKeyboardKeyCount?: TouchKeyboardKeyCount
  touchKeyboardFirstNote?: number
  touchKeyboardSize?: TouchKeyboardSize
  midiKeyboardKeyCount?: MidiKeyboardKeyCount
  midiKeyboardFirstNote?: number
}

const STORAGE_KEY = 'case3.realtime-workbench.v2'
const EMPTY_STATUS: RealtimeStatus = {
  state: 'stopped',
  running: false,
  active_notes: [],
  recording: { active: false },
  metrics: {},
  diagnostics: {},
}
const CATEGORIES: { id: RealtimePatchCategory | 'recent'; label: string }[] = [
  { id: 'recent', label: '最近使用' },
  { id: 'piano', label: '钢琴' },
  { id: 'strings', label: '弦乐' },
  { id: 'woodwind', label: '木管' },
  { id: 'brass', label: '铜管' },
  { id: 'other', label: '其他' },
]
const PATCH_ACCENTS: Record<RealtimePatchCategory, string> = {
  piano: '#5ba4f4',
  strings: '#43c89a',
  woodwind: '#35bfd0',
  brass: '#f0a044',
  other: '#e76c6c',
}
const KEYBOARD_KEY_COUNTS: Record<RealtimeInputMode, readonly KeyboardKeyCount[]> = {
  touch: [13, 25],
  midi: [32, 49, 61, 88],
}
const KEYBOARD_DEFAULT_FIRST_NOTE: Record<KeyboardKeyCount, number> = {
  13: 60,
  25: 48,
  32: 41,
  49: 36,
  61: 36,
  88: 21,
}
const TOUCH_KEYBOARD_SIZES: { id: TouchKeyboardSize; label: string }[] = [
  { id: 'small', label: '小' },
  { id: 'medium', label: '中' },
  { id: 'large', label: '大' },
]

export function loadWorkbenchConfig(storage: Pick<Storage, 'getItem'>): StoredWorkbenchConfig | null {
  try {
    const value = JSON.parse(storage.getItem(STORAGE_KEY) ?? 'null')
    if (!value || value.version !== 2 || typeof value.patchParameters !== 'object') return null
    return value as StoredWorkbenchConfig
  } catch {
    return null
  }
}

function patchDefaults(patch: RealtimePatch): Record<string, number> {
  return Object.fromEntries(
    Object.entries(patch.parameters)
      .filter(([, metadata]) => typeof metadata.default === 'number')
      .map(([name, metadata]) => [name, Number(metadata.default)]),
  )
}

function clampPatchParameters(patch: RealtimePatch, values: Record<string, number> | undefined) {
  const result = patchDefaults(patch)
  for (const [name, value] of Object.entries(values ?? {})) {
    const metadata = patch.parameters[name]
    if (!metadata || !Number.isFinite(value)) continue
    const minimum = metadata.min ?? Number.NEGATIVE_INFINITY
    const maximum = metadata.max ?? Number.POSITIVE_INFINITY
    if (value >= minimum && value <= maximum) result[name] = value
  }
  return result
}

function normalizeKeyboardKeyCount(inputMode: RealtimeInputMode, value: number | undefined): KeyboardKeyCount {
  if (KEYBOARD_KEY_COUNTS[inputMode].includes(value as KeyboardKeyCount)) return value as KeyboardKeyCount
  return inputMode === 'touch' ? 25 : 32
}

function getStoredKeyboardKeyCount(config: StoredWorkbenchConfig | null, inputMode: RealtimeInputMode) {
  if (config?.keyboardSizingVersion === 2) {
    return inputMode === 'touch' ? config.touchKeyboardKeyCount : config.midiKeyboardKeyCount
  }
  return config?.keyboardKeyCount
}

function getStoredKeyboardFirstNote(config: StoredWorkbenchConfig | null, inputMode: RealtimeInputMode) {
  if (config?.keyboardSizingVersion === 2) {
    return inputMode === 'touch' ? config.touchKeyboardFirstNote : config.midiKeyboardFirstNote
  }
  return config?.keyboardFirstNote
}

function normalizeTouchKeyboardSize(value: unknown): TouchKeyboardSize {
  return value === 'small' || value === 'large' ? value : 'medium'
}

function clampKeyboardFirstNote(keyCount: KeyboardKeyCount, value: number | undefined) {
  const maximum = 109 - keyCount
  const fallback = KEYBOARD_DEFAULT_FIRST_NOTE[keyCount]
  return Math.min(maximum, Math.max(21, Math.round(value ?? fallback)))
}

function formatTime(seconds = 0) {
  const safe = Math.max(0, Number.isFinite(seconds) ? seconds : 0)
  return `${Math.floor(safe / 60)}:${Math.floor(safe % 60).toString().padStart(2, '0')}`
}

function formatGainDb(value: number) {
  const normalized = Math.abs(value) < 0.05 ? 0 : value
  return `${normalized > 0 ? '+' : ''}${normalized.toFixed(1)} dB`
}

function errorMessage(cause: unknown) {
  return cause instanceof Error ? cause.message : String(cause)
}

type LivePianoProps = Omit<ComponentProps<typeof Piano>, 'activeNotes'>

const LivePiano = memo(function LivePiano(props: LivePianoProps) {
  const activeNotes = useLiveNotes()
  return <Piano {...props} activeNotes={activeNotes} />
})

export default function RealtimePerformanceView({ onRefresh, inputMode = 'midi' }: Props) {
  const savedRef = useRef<StoredWorkbenchConfig | null>(loadWorkbenchConfig(window.localStorage))
  const initialKeyCount = normalizeKeyboardKeyCount(inputMode, getStoredKeyboardKeyCount(savedRef.current, inputMode))
  const socketRef = useRef<WebSocket | null>(null)
  const reconnectTimerRef = useRef<number | null>(null)
  const parameterTimerRef = useRef<number | null>(null)
  const runtimeFlushTimerRef = useRef<number | null>(null)
  const pendingRuntimeRef = useRef<RealtimeStatus | null>(null)
  const audioContextRef = useRef<AudioContext | null>(null)
  const monitorTimeRef = useRef(0)
  const mountedRef = useRef(true)

  const [catalog, setCatalog] = useState<RealtimeCatalog | null>(null)
  const [runtime, setRuntime] = useState<RealtimeStatus>(EMPTY_STATUS)
  const [selectedPatchId, setSelectedPatchId] = useState(savedRef.current?.lastPatchId ?? '')
  const [category, setCategory] = useState<RealtimePatchCategory | 'recent'>('piano')
  const [audioDeviceId, setAudioDeviceId] = useState(savedRef.current?.audioDeviceId ?? '')
  const [midiPort, setMidiPort] = useState(savedRef.current?.midiPort ?? '')
  const [latencyProfile, setLatencyProfile] = useState<LatencyProfile>(savedRef.current?.latencyProfile ?? 'balanced')
  const [recentPatchIds, setRecentPatchIds] = useState(savedRef.current?.recentPatchIds ?? [])
  const [patchParameters, setPatchParameters] = useState<Record<string, Record<string, number>>>(savedRef.current?.patchParameters ?? {})
  const [keyCount, setKeyCount] = useState<KeyboardKeyCount>(initialKeyCount)
  const [windowStart, setWindowStart] = useState(() => clampKeyboardFirstNote(
    initialKeyCount,
    getStoredKeyboardFirstNote(savedRef.current, inputMode),
  ))
  const [touchKeyboardSize, setTouchKeyboardSize] = useState<TouchKeyboardSize>(() => (
    normalizeTouchKeyboardSize(savedRef.current?.touchKeyboardSize)
  ))
  const [patchPickerOpen, setPatchPickerOpen] = useState(false)
  const [drawerTab, setDrawerTab] = useState<DrawerTab>(inputMode === 'midi' ? 'player' : 'recording')
  const [drawerOpen, setDrawerOpen] = useState(inputMode !== 'touch')
  const [rollWindow, setRollWindow] = useState<2 | 4 | 8>(4)
  const [velocity, setVelocity] = useState(96)
  const [sustain, setSustain] = useState(false)
  const [pitchBend, setPitchBend] = useState(0)
  const [midiId, setMidiId] = useState('')
  const [monitor, setMonitor] = useState(false)
  const [recordingUrl, setRecordingUrl] = useState('')
  const [connection, setConnection] = useState<'connecting' | 'connected' | 'disconnected'>('connecting')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const applyRuntime = useCallback((nextStatus: RealtimeStatus) => {
    publishLiveNotes(nextStatus.active_notes ?? [])
    setRuntime(nextStatus)
  }, [])

  const selectedPatch = useMemo(
    () => catalog?.patches.find((patch) => patch.patch_id === selectedPatchId) ?? null,
    [catalog, selectedPatchId],
  )
  const currentPatch = runtime.running && runtime.patch ? runtime.patch : selectedPatch
  const patchAccent = PATCH_ACCENTS[currentPatch?.category ?? selectedPatch?.category ?? 'piano']
  const parameters = selectedPatch ? clampPatchParameters(selectedPatch, patchParameters[selectedPatch.patch_id]) : {}
  const player = runtime.player ?? { state: 'empty', position_seconds: 0, duration_seconds: 0, tempo: 1, loop: false }
  const selectedAudio = catalog?.audio_devices.find((device) => device.id === audioDeviceId)
  const recording = Boolean(runtime.recording?.active)
  const hasAnomaly = Number(runtime.metrics?.underruns ?? 0) > 0
    || Number(runtime.metrics?.overruns ?? 0) > 0
    || Number(runtime.metrics?.audio_tap_drops ?? 0) > 0
    || Number(runtime.metrics?.clipped_samples ?? 0) > 0
    || Boolean(runtime.audio?.device_lost)

  const refreshCatalog = useCallback(async () => {
    const [nextCatalog, nextStatus] = await Promise.all([api.realtimeCatalog(), api.realtimeStatus()])
    if (!mountedRef.current) return
    setCatalog(nextCatalog)
    applyRuntime(nextStatus)
    const saved = savedRef.current
    const currentId = nextStatus.patch_id ?? saved?.lastPatchId
    const patch = nextCatalog.patches.find((item) => item.patch_id === currentId)
      ?? nextCatalog.patches.find((item) => item.patch_id === saved?.lastPatchId)
      ?? nextCatalog.patches.find((item) => item.category === 'piano')
      ?? nextCatalog.patches[0]
    if (!patch) return
    setSelectedPatchId(patch.patch_id)
    setCategory(patch.category)
    setPatchParameters((current) => ({
      ...current,
      [patch.patch_id]: clampPatchParameters(patch, current[patch.patch_id]),
    }))
    const desiredAudioId = nextStatus.audio_device_id ?? saved?.audioDeviceId
    const audio = nextCatalog.audio_devices.find(
      (item) => item.id === desiredAudioId && patch.compatible_audio_device_ids.includes(item.id),
    ) ?? nextCatalog.audio_devices.find(
      (item) => item.is_default && patch.compatible_audio_device_ids.includes(item.id),
    ) ?? nextCatalog.audio_devices.find((item) => patch.compatible_audio_device_ids.includes(item.id))
    setAudioDeviceId(audio?.id ?? '')
    setError('')
  }, [applyRuntime])

  const queueRuntimeUpdate = useCallback((nextStatus: RealtimeStatus) => {
    publishLiveNotes(nextStatus.active_notes ?? [])
    pendingRuntimeRef.current = nextStatus
    if (runtimeFlushTimerRef.current !== null) return
    runtimeFlushTimerRef.current = window.setTimeout(() => {
      runtimeFlushTimerRef.current = null
      const latest = pendingRuntimeRef.current
      pendingRuntimeRef.current = null
      if (latest && mountedRef.current) setRuntime(latest)
    }, 80)
  }, [])

  useEffect(() => () => {
    if (runtimeFlushTimerRef.current !== null) window.clearTimeout(runtimeFlushTimerRef.current)
    runtimeFlushTimerRef.current = null
    pendingRuntimeRef.current = null
    clearLiveNotes()
  }, [])

  useEffect(() => {
    mountedRef.current = true
    refreshCatalog().catch((cause) => setError(errorMessage(cause)))
    return () => {
      mountedRef.current = false
    }
  }, [refreshCatalog])

  const playMonitorBlock = useCallback((encoded: string, sampleRate: number) => {
    const context = audioContextRef.current
    if (!context) return
    const binary = atob(encoded)
    const bytes = new Uint8Array(binary.length)
    for (let index = 0; index < binary.length; index += 1) bytes[index] = binary.charCodeAt(index)
    const samples = new Float32Array(bytes.buffer)
    const frames = Math.floor(samples.length / 2)
    const buffer = context.createBuffer(2, frames, sampleRate)
    for (let frame = 0; frame < frames; frame += 1) {
      buffer.getChannelData(0)[frame] = samples[frame * 2]
      buffer.getChannelData(1)[frame] = samples[frame * 2 + 1]
    }
    const source = context.createBufferSource()
    source.buffer = buffer
    source.connect(context.destination)
    const at = Math.max(context.currentTime + 0.01, monitorTimeRef.current)
    source.start(at)
    monitorTimeRef.current = at + buffer.duration
  }, [])

  useEffect(() => {
    let closed = false
    const connect = () => {
      if (closed) return
      setConnection('connecting')
      const socket = new WebSocket(websocketUrl('/api/v1/realtime/events'))
      socketRef.current = socket
      socket.onopen = () => setConnection('connected')
      socket.onmessage = (event) => {
        const message = JSON.parse(event.data)
        if ((message.event === 'status' || message.event === 'heartbeat') && message.data) {
          queueRuntimeUpdate(message.data)
          if (message.data.last_switch && !message.data.last_switch.ok) {
            setError(message.data.last_switch.error || '音色切换失败，已恢复原音色')
          }
        }
        if (message.event === 'note') {
          publishLiveNoteEvent(Number(message.note), Boolean(message.on))
        }
        if (message.event === 'monitor') {
          const audio = typeof message.audio === 'string'
            ? message.audio
            : typeof message.pcm === 'string'
              ? message.pcm
              : null
          if (audio) playMonitorBlock(audio, Number(message.sample_rate || 48000))
        }
        if (message.event === 'ack' && message.request === 'record_stop' && message.data?.download_url) {
          setRecordingUrl(message.data.download_url)
        }
        if (message.event === 'error') setError(String(message.message || '实时会话错误'))
      }
      socket.onclose = () => {
        socketRef.current = null
        setConnection('disconnected')
        if (!closed) reconnectTimerRef.current = window.setTimeout(connect, 1500)
      }
    }
    connect()
    return () => {
      closed = true
      if (reconnectTimerRef.current !== null) window.clearTimeout(reconnectTimerRef.current)
      socketRef.current?.close()
    }
  }, [playMonitorBlock, queueRuntimeUpdate])

  const send = useCallback((message: Record<string, unknown>) => {
    const socket = socketRef.current
    if (socket?.readyState !== WebSocket.OPEN) {
      setError('实时连接尚未就绪')
      return false
    }
    socket.send(JSON.stringify(message))
    return true
  }, [])

  useEffect(() => {
    const release = () => {
      setSustain(false)
      send({ event: 'all_notes_off' })
    }
    const visibility = () => {
      if (document.visibilityState === 'hidden') release()
    }
    window.addEventListener('blur', release)
    document.addEventListener('visibilitychange', visibility)
    return () => {
      window.removeEventListener('blur', release)
      document.removeEventListener('visibilitychange', visibility)
    }
  }, [send])

  useEffect(() => {
    setWindowStart((value) => clampKeyboardFirstNote(keyCount, value))
  }, [keyCount])

  useEffect(() => {
    const previous = savedRef.current
    const stored: StoredWorkbenchConfig = {
      version: 2,
      lastPatchId: selectedPatchId,
      audioDeviceId,
      midiPort,
      latencyProfile,
      recentPatchIds,
      patchParameters,
      keyboardSizingVersion: 2,
      touchKeyboardKeyCount: inputMode === 'touch'
        ? keyCount as TouchKeyboardKeyCount
        : previous?.touchKeyboardKeyCount,
      touchKeyboardFirstNote: inputMode === 'touch'
        ? windowStart
        : previous?.touchKeyboardFirstNote,
      touchKeyboardSize: inputMode === 'touch'
        ? touchKeyboardSize
        : previous?.touchKeyboardSize,
      midiKeyboardKeyCount: inputMode === 'midi'
        ? keyCount as MidiKeyboardKeyCount
        : previous?.midiKeyboardKeyCount,
      midiKeyboardFirstNote: inputMode === 'midi'
        ? windowStart
        : previous?.midiKeyboardFirstNote,
    }
    savedRef.current = stored
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(stored))
  }, [audioDeviceId, inputMode, keyCount, latencyProfile, midiPort, patchParameters, recentPatchIds, selectedPatchId, touchKeyboardSize, windowStart])

  useEffect(() => {
    if (runtime.last_switch?.rolled_back && runtime.patch_id) setSelectedPatchId(runtime.patch_id)
  }, [runtime.last_switch, runtime.patch_id])

  const choosePatch = async (patch: RealtimePatch) => {
    if (recording) {
      setError('录音期间音色已锁定，请先停止录音')
      setDrawerTab('recording')
      setDrawerOpen(true)
      return
    }
    if (runtime.running && !patch.compatible_audio_device_ids.includes(audioDeviceId)) {
      setError('当前输出设备不兼容该音色；当前音色会继续运行，请停止会话后更换输出设备')
      setDrawerTab('connection')
      setDrawerOpen(true)
      return
    }
    setSelectedPatchId(patch.patch_id)
    setCategory(patch.category)
    setPatchParameters((current) => ({
      ...current,
      [patch.patch_id]: clampPatchParameters(patch, current[patch.patch_id]),
    }))
    setRecentPatchIds((current) => [patch.patch_id, ...current.filter((id) => id !== patch.patch_id)].slice(0, 8))
    if (!runtime.running || runtime.patch_id === patch.patch_id) return
    setBusy(true)
    setError('')
    try {
      const next = await api.switchRealtime({
        patch_id: patch.patch_id,
        audio_device_id: audioDeviceId,
        parameters: clampPatchParameters(patch, patchParameters[patch.patch_id]),
      })
      applyRuntime(next)
      setSustain(false)
      if (next.last_switch && !next.last_switch.ok) setError(next.last_switch.error || '音色切换失败，已恢复原音色')
      await onRefresh()
    } catch (cause) {
      setError(errorMessage(cause))
    } finally {
      setBusy(false)
    }
  }

  const start = async () => {
    if (!selectedPatch || !audioDeviceId) return
    setBusy(true)
    setError('')
    try {
      const next = await api.startRealtime({
        patch_id: selectedPatch.patch_id,
        audio_device_id: audioDeviceId,
        midi_port: inputMode === 'midi' ? midiPort || null : null,
        latency_profile: latencyProfile,
        parameters,
      })
      applyRuntime(next)
      setRecentPatchIds((current) => [selectedPatch.patch_id, ...current.filter((id) => id !== selectedPatch.patch_id)].slice(0, 8))
      await onRefresh()
    } catch (cause) {
      setError(errorMessage(cause))
    } finally {
      setBusy(false)
    }
  }

  const stop = async () => {
    setBusy(true)
    try {
      const next = await api.stopRealtime()
      applyRuntime(next)
      setSustain(false)
      setMonitor(false)
      await onRefresh()
    } catch (cause) {
      setError(errorMessage(cause))
    } finally {
      setBusy(false)
    }
  }

  const updateParameter = (name: string, value: number) => {
    if (!selectedPatch) return
    setPatchParameters((current) => ({
      ...current,
      [selectedPatch.patch_id]: {
        ...clampPatchParameters(selectedPatch, current[selectedPatch.patch_id]),
        [name]: value,
      },
    }))
    if (!runtime.running || runtime.patch_id !== selectedPatch.patch_id) return
    if (parameterTimerRef.current !== null) window.clearTimeout(parameterTimerRef.current)
    parameterTimerRef.current = window.setTimeout(() => {
      api.updateRealtime({ [name]: value }).then(applyRuntime).catch((cause) => setError(errorMessage(cause)))
    }, 120)
  }

  const toggleMonitor = async () => {
    const enabled = !monitor
    if (enabled) {
      const AudioContextClass = window.AudioContext
      const context = audioContextRef.current ?? new AudioContextClass()
      audioContextRef.current = context
      await context.resume()
      monitorTimeRef.current = context.currentTime
    }
    if (send({ event: 'monitor', enabled })) setMonitor(enabled)
  }

  const filteredPatches = useMemo(() => {
    if (!catalog) return []
    if (category === 'recent') {
      const byId = new Map(catalog.patches.map((patch) => [patch.patch_id, patch]))
      return recentPatchIds.map((id) => byId.get(id)).filter((patch): patch is RealtimePatch => Boolean(patch))
    }
    return catalog.patches.filter((patch) => patch.category === category)
  }, [catalog, category, recentPatchIds])

  const canShiftOctaveLeft = keyCount < 88 && windowStart - 12 >= 21
  const canShiftOctaveRight = keyCount < 88 && windowStart + 12 <= 109 - keyCount

  const selectKeyCount = (nextKeyCount: KeyboardKeyCount) => {
    if (!KEYBOARD_KEY_COUNTS[inputMode].includes(nextKeyCount)) return
    if (runtime.running) send({ event: 'all_notes_off' })
    setSustain(false)
    setKeyCount(nextKeyCount)
    setWindowStart(KEYBOARD_DEFAULT_FIRST_NOTE[nextKeyCount])
  }

  const shiftKeyboardOctave = (direction: -1 | 1) => {
    if ((direction < 0 && !canShiftOctaveLeft) || (direction > 0 && !canShiftOctaveRight)) return
    if (runtime.running) send({ event: 'all_notes_off' })
    setSustain(false)
    setWindowStart((value) => value + direction * 12)
  }

  const handleNoteOn = useCallback((note: number, noteVelocity: number) => {
    if (send({ event: 'note_on', note, velocity: noteVelocity })) publishLiveNoteEvent(note, true)
  }, [send])

  const handleNoteOff = useCallback((note: number) => {
    if (send({ event: 'note_off', note })) publishLiveNoteEvent(note, false)
  }, [send])

  const playerLoaded = Boolean(player.path) && Boolean(midiId)
  const bottomTabs: { id: DrawerTab; label: string; icon: typeof Play }[] = [
    ...(inputMode === 'midi' ? [{ id: 'player' as const, label: 'MIDI 文件', icon: Play }] : []),
    { id: 'recording', label: '录音监听', icon: Disc3 },
    { id: 'tone', label: '音色参数', icon: SlidersHorizontal },
    { id: 'connection', label: '连接设置', icon: Cable },
    { id: 'diagnostics', label: '性能', icon: Gauge },
  ]

  const isTouchPerformance = inputMode === 'touch'
  const keyCounts = KEYBOARD_KEY_COUNTS[inputMode]
  const patchPickerClass = isTouchPerformance ? 'touch-patch-picker' : 'midi-patch-picker'
  const patchPickerContentClass = isTouchPerformance ? 'touch-patch-picker-content' : 'midi-patch-picker-content'

  return (
    <section className={`realtime-stage realtime-stage--${inputMode} ${isTouchPerformance ? `touch-keyboard-size--${touchKeyboardSize}` : ''}`}>
      <header className="stage-session-bar">
        <div className="stage-current-patch">
          <Music2 size={20} />
          <div><span>当前音色</span><strong>{currentPatch?.name ?? '未选择音色'}</strong></div>
        </div>
        <div className="stage-session-meta">
          <StatusPill tone={connection === 'connected' ? 'ok' : connection === 'connecting' ? 'warn' : 'error'}>
            {connection === 'connected' ? '已连接' : connection === 'connecting' ? '连接中' : '连接断开'}
          </StatusPill>
          <StatusPill tone={runtime.running ? 'ok' : runtime.state === 'failed' ? 'error' : 'neutral'}>
            {runtime.state === 'switching' ? '切换中' : runtime.running ? '演奏中' : runtime.state === 'failed' ? '失败' : '待机'}
          </StatusPill>
        </div>
        <div className="stage-session-actions">
          <button className="icon-command danger" type="button" title="Panic / 全部停音" disabled={!runtime.running} onClick={() => api.panicRealtime().then(applyRuntime).catch((cause) => setError(errorMessage(cause)))}><Octagon size={18} /></button>
          {runtime.running
            ? <button className="secondary-button" type="button" disabled={busy} onClick={stop}><CircleStop size={17} />停止</button>
            : <button className="primary-button" type="button" disabled={busy || !selectedPatch || !audioDeviceId} onClick={start}><Play size={17} />开始演奏</button>}
        </div>
      </header>

      {error && <Notice tone="error">{error}</Notice>}
      {runtime.audio?.device_lost && <Notice tone="error">音频设备已断开，会话已执行全部停音</Notice>}
      {!catalog && <Notice tone="loading">正在加载统一音色库</Notice>}

      <details
        className={patchPickerClass}
        open={patchPickerOpen}
        onToggle={(event) => setPatchPickerOpen(event.currentTarget.open)}
      >
        <summary aria-label="切换音色">
          <span>音色</span>
          <strong>{currentPatch?.name ?? '未选择音色'}</strong>
          <span className="patch-picker-action">更换</span>
        </summary>
        <div className={patchPickerContentClass} aria-label="音色库">
          <div className="patch-category-tabs" role="tablist">
            {CATEGORIES.map((item) => (
              <button type="button" role="tab" aria-selected={category === item.id} className={category === item.id ? 'is-active' : ''} key={item.id} onClick={() => setCategory(item.id)}>{item.label}</button>
            ))}
          </div>
          <div className="patch-strip">
            {filteredPatches.length === 0 && <span className="patch-empty">该分类暂无可用音色</span>}
            {filteredPatches.map((patch) => (
              <button
                type="button"
                className={`patch-tile ${selectedPatchId === patch.patch_id ? 'is-active' : ''}`}
                aria-pressed={selectedPatchId === patch.patch_id}
                disabled={busy || (recording && runtime.patch_id !== patch.patch_id)}
                onClick={() => {
                  choosePatch(patch)
                  setPatchPickerOpen(false)
                }}
                key={patch.patch_id}
              >
                <strong>{patch.name}</strong>
                <span>{patch.pitch_min}–{patch.pitch_max} · {patch.polyphony} 音</span>
              </button>
            ))}
          </div>
        </div>
      </details>

      <section
        className={`keyboard-stage is-${inputMode}`}
        aria-label={isTouchPerformance ? '触控实时演奏' : 'MIDI 键盘实时演奏'}
        style={{ '--stage-accent': patchAccent } as CSSProperties}
      >
        <div className="roll-toolbar">
          <div className="roll-heading">
            <Activity size={17} />
            <div>
              <strong>{isTouchPerformance ? '实时卷帘' : 'MIDI 键盘演奏'}</strong>
              <span>{isTouchPerformance ? '触控演奏' : currentPatch?.name ?? '等待音色'}</span>
            </div>
          </div>
          {!isTouchPerformance && (
            <label className="midi-input-control">
              <Cable size={15} />
              <span>实体 MIDI 输入</span>
              <select aria-label="实体 MIDI 输入" value={midiPort} disabled={runtime.running} onChange={(event) => setMidiPort(event.target.value)}>
                <option value="">选择 MIDI 输入</option>
                {(catalog?.midi_ports ?? []).map((port) => <option value={port.port ?? port.id} key={port.id}>{port.name}</option>)}
              </select>
            </label>
          )}
          <label className="stage-gain-control">
            <Volume2 size={14} />
            <span>输出增益</span>
            <input aria-label="输出增益" type="range" min="-60" max="6" step="0.5" value={parameters.output_gain_db ?? 0} onChange={(event) => updateParameter('output_gain_db', Number(event.target.value))} />
            <strong>{formatGainDb(parameters.output_gain_db ?? 0)}</strong>
          </label>
          <div className="roll-window-control" aria-label="卷帘时间范围">
            <Clock3 size={14} />
            {[2, 4, 8].map((seconds) => (
              <button
                type="button"
                className={rollWindow === seconds ? 'is-active' : ''}
                aria-label={`${seconds} 秒时间窗`}
                aria-pressed={rollWindow === seconds}
                onClick={() => setRollWindow(seconds as 2 | 4 | 8)}
                key={seconds}
              >
                {seconds}s
              </button>
            ))}
          </div>
        </div>
        <div className="keyboard-range-bar">
          <span className="keyboard-range-label">琴键数量</span>
          <div className="key-count-control" role="group" aria-label="琴键数量">
            {keyCounts.map((count) => (
              <button
                type="button"
                className={keyCount === count ? 'is-active' : ''}
                aria-pressed={keyCount === count}
                aria-label={`使用 ${count} 键`}
                onClick={() => selectKeyCount(count)}
                key={count}
              >
                {count}
              </button>
            ))}
          </div>
          {isTouchPerformance && (
            <div className="touch-keyboard-size-control" role="group" aria-label="触控键盘大小">
              <span>键盘大小</span>
              {TOUCH_KEYBOARD_SIZES.map((size) => (
                <button
                  type="button"
                  className={touchKeyboardSize === size.id ? 'is-active' : ''}
                  aria-label={`使用${size.label}键盘`}
                  aria-pressed={touchKeyboardSize === size.id}
                  onClick={() => setTouchKeyboardSize(size.id)}
                  key={size.id}
                >
                  {size.label}
                </button>
              ))}
            </div>
          )}
          <div className="keyboard-range-summary">
            {keyCount < 88 && (
              <button type="button" className="icon-command" aria-label="向低音区移动一个八度" title="向低音区移动一个八度" disabled={!canShiftOctaveLeft} onClick={() => shiftKeyboardOctave(-1)}><ChevronLeft size={19} /></button>
            )}
            <strong>{noteLabel(windowStart)}–{noteLabel(windowStart + keyCount - 1)}</strong>
            <span>{keyCount < 88 ? '八度移位' : '完整音域'}</span>
            {keyCount < 88 && (
              <button type="button" className="icon-command" aria-label="向高音区移动一个八度" title="向高音区移动一个八度" disabled={!canShiftOctaveRight} onClick={() => shiftKeyboardOctave(1)}><ChevronRight size={19} /></button>
            )}
          </div>
        </div>
        {isTouchPerformance ? (
          <>
            <LivePianoRoll
              firstNote={windowStart}
              keyCount={keyCount}
              historySeconds={rollWindow}
              running={runtime.running}
              accentColor={patchAccent}
            />
            <LivePiano
              octave={3}
              firstNote={windowStart}
              keyCount={keyCount}
              velocity={velocity}
              recommendedMin={currentPatch?.pitch_min}
              recommendedMax={currentPatch?.pitch_max}
              disabled={!runtime.running || busy}
              keyboardShortcuts={{}}
              onNoteOn={handleNoteOn}
              onNoteOff={handleNoteOff}
            />
            <div className="performance-control-bar">
              <label><span>力度</span><input type="range" min="1" max="127" value={velocity} onChange={(event) => setVelocity(Number(event.target.value))} /><strong>{velocity}</strong></label>
              <label><span>移调</span><input type="range" min="-24" max="24" value={parameters.transpose ?? 0} onChange={(event) => updateParameter('transpose', Number(event.target.value))} /><strong>{parameters.transpose ?? 0}</strong></label>
              <label><span>混响</span><input type="range" min="0" max="1" step="0.01" value={parameters.reverb ?? 0} onChange={(event) => updateParameter('reverb', Number(event.target.value))} /><strong>{Math.round((parameters.reverb ?? 0) * 100)}%</strong></label>
              <label className="bend-control"><span>弯音</span><input type="range" min="-8192" max="8191" value={pitchBend} onChange={(event) => { const value = Number(event.target.value); setPitchBend(value); send({ event: 'pitch_bend', value }) }} onPointerUp={() => { setPitchBend(0); send({ event: 'pitch_bend', value: 0 }) }} /><strong>{pitchBend}</strong></label>
              <button type="button" className={`sustain-control ${sustain ? 'is-active' : ''}`} disabled={!runtime.running} aria-pressed={sustain} onClick={() => { const enabled = !sustain; setSustain(enabled); send({ event: 'sustain', enabled }) }}>延音踏板</button>
            </div>
          </>
        ) : (
          <>
            <div className="instrument-legend" aria-label="音色声部图例">
              <span className="instrument-legend-label">音色声部</span>
              {CATEGORIES.filter((item) => item.id !== 'recent').map((item) => (
                <span className={currentPatch?.category === item.id ? 'is-active' : ''} key={item.id}>
                  <i style={{ backgroundColor: PATCH_ACCENTS[item.id as RealtimePatchCategory] }} />{item.label}
                </span>
              ))}
              <span className="instrument-hit-line"><i />命中线</span>
            </div>
            <LivePianoRoll
              firstNote={windowStart}
              keyCount={keyCount}
              historySeconds={rollWindow}
              running={runtime.running}
              accentColor={patchAccent}
            />
            <VisualizerKeyboard
              firstNote={windowStart}
              keyCount={keyCount}
              accentColor={patchAccent}
              recommendedMin={currentPatch?.pitch_min}
              recommendedMax={currentPatch?.pitch_max}
            />
          </>
        )}
      </section>

      <section className={`stage-drawer ${drawerOpen ? 'is-open' : ''}`}>
        <div className="drawer-tabs" role="tablist">
          {bottomTabs.map((tab) => {
            const Icon = tab.icon
            return <button type="button" role="tab" aria-label={tab.label} aria-selected={drawerTab === tab.id} className={`${drawerTab === tab.id ? 'is-active' : ''} ${tab.id === 'diagnostics' && hasAnomaly ? 'has-alert' : ''}`} key={tab.id} onClick={() => { setDrawerTab(tab.id); setDrawerOpen(drawerTab !== tab.id || !drawerOpen) }}><Icon size={16} /><span>{tab.label}</span></button>
          })}
        </div>
        <div className="drawer-content">
          {drawerTab === 'player' && (
            <div className="transport-layout">
              <select aria-label="MIDI 文件" value={midiId} onChange={(event) => { const id = event.target.value; setMidiId(id); if (runtime.running && id) send({ event: 'player', action: 'load', values: { midi_id: id } }) }}>
                <option value="">选择 MIDI 文件</option>
                {(catalog?.midi_files ?? []).map((item) => <option value={item.id} key={item.id}>{item.name}</option>)}
              </select>
              <label className="icon-command" title="上传 MIDI"><Upload size={17} /><input type="file" accept=".mid,.midi,audio/midi" onChange={async (event) => { const file = event.target.files?.[0]; if (!file) return; try { const item = await api.uploadMidi(file); await refreshCatalog(); setMidiId(item.id) } catch (cause) { setError(errorMessage(cause)) } }} /></label>
              <button className="icon-command" type="button" title={player.state === 'playing' ? '暂停' : '播放'} disabled={!runtime.running || !midiId} onClick={() => { if (!playerLoaded) send({ event: 'player', action: 'load', values: { midi_id: midiId } }); send({ event: 'player', action: player.state === 'playing' ? 'pause' : 'play', values: {} }) }}>{player.state === 'playing' ? <Pause size={18} /> : <Play size={18} />}</button>
              <button className="icon-command" type="button" title="停止并回到开头" disabled={!runtime.running || !playerLoaded} onClick={() => send({ event: 'player', action: 'stop', values: {} })}><RotateCcw size={18} /></button>
              <input className="transport-seek" aria-label="播放位置" type="range" min="0" max={Math.max(0.01, player.duration_seconds)} step="0.01" value={Math.min(player.position_seconds, Math.max(0.01, player.duration_seconds))} disabled={!playerLoaded} onChange={(event) => send({ event: 'player', action: 'seek', values: { position_seconds: Number(event.target.value) } })} />
              <span className="transport-time">{formatTime(player.position_seconds)} / {formatTime(player.duration_seconds)}</span>
              <select aria-label="播放速度" value={player.tempo} disabled={!runtime.running} onChange={(event) => send({ event: 'player', action: 'tempo', values: { value: Number(event.target.value) } })}><option value="0.75">0.75×</option><option value="1">1.00×</option><option value="1.25">1.25×</option><option value="1.5">1.50×</option></select>
              <label className="compact-check"><input type="checkbox" checked={player.loop} disabled={!runtime.running} onChange={(event) => send({ event: 'player', action: 'loop', values: { enabled: event.target.checked } })} />循环</label>
            </div>
          )}

          {drawerTab === 'recording' && (
            <div className="recording-layout">
              <button className={recording ? 'danger-button' : 'primary-button'} type="button" disabled={!runtime.running} onClick={() => send({ event: recording ? 'record_stop' : 'record_start' })}><Disc3 size={17} />{recording ? '停止录音' : '开始录音'}</button>
              <button className={`secondary-button ${monitor ? 'is-active' : ''}`} type="button" disabled={!runtime.running} onClick={() => toggleMonitor().catch((cause) => setError(errorMessage(cause)))}><Headphones size={17} />{monitor ? '关闭监听' : '浏览器监听'}</button>
              {recordingUrl && <a className="download-button" href={recordingUrl} download><Download size={17} />下载 WAV</a>}
              <span>{recording ? '录音中，音色切换已锁定' : '录音覆盖当前输出设备听到的实时合成结果'}</span>
            </div>
          )}

          {drawerTab === 'tone' && (
            <div className="tone-parameter-grid">
              {selectedPatch && Object.entries(selectedPatch.parameters)
                .filter(([name, metadata]) => !['velocity_curve', 'transpose', 'output_gain_db', 'reverb', 'piano_year'].includes(name) && metadata.min !== undefined && metadata.max !== undefined)
                .map(([name, metadata]) => (
                  <label key={name}><span>{name.replaceAll('_', ' ')}</span><input type="range" min={metadata.min} max={metadata.max} step={(metadata.max! - metadata.min!) / 100} value={parameters[name] ?? metadata.default ?? 0} onChange={(event) => updateParameter(name, Number(event.target.value))} /><strong>{(parameters[name] ?? metadata.default ?? 0).toFixed(2)}</strong></label>
                ))}
              {selectedPatch?.parameters.piano_year?.options && <label><span>钢琴年份</span><select value={parameters.piano_year ?? 2018} onChange={(event) => updateParameter('piano_year', Number(event.target.value))}>{selectedPatch.parameters.piano_year.options.map((year) => <option key={year} value={year}>{year}</option>)}</select></label>}
              <div className="patch-detail"><span>可用音域</span><strong>{selectedPatch?.pitch_min}–{selectedPatch?.pitch_max}</strong><span>最大复音</span><strong>{selectedPatch?.polyphony}</strong></div>
            </div>
          )}

          {drawerTab === 'connection' && (
            <div className="connection-layout">
              <label><span>音频输出</span><select value={audioDeviceId} disabled={runtime.running} onChange={(event) => setAudioDeviceId(event.target.value)}>{(catalog?.audio_devices ?? []).filter((device) => selectedPatch?.compatible_audio_device_ids.includes(device.id)).map((device) => <option value={device.id} key={device.id}>{device.name}</option>)}</select></label>
              {!isTouchPerformance && <label><span>实体 MIDI 输入</span><select value={midiPort} disabled={runtime.running} onChange={(event) => setMidiPort(event.target.value)}><option value="">选择 MIDI 输入</option>{(catalog?.midi_ports ?? []).map((port) => <option value={port.port ?? port.id} key={port.id}>{port.name}</option>)}</select></label>}
              <label><span>延时档位</span><select value={latencyProfile} disabled={runtime.running} onChange={(event) => setLatencyProfile(event.target.value as LatencyProfile)}><option value="low" disabled={Boolean(selectedAudio?.is_bluetooth)}>低延时</option><option value="balanced">均衡</option><option value="safe">稳定</option></select></label>
              <span>{runtime.running ? isTouchPerformance ? '停止会话后可更改输出设备' : '停止会话后可更改输出设备和 MIDI 端口' : selectedAudio?.warning ?? '配置会在浏览器中自动保存'}</span>
            </div>
          )}

          {drawerTab === 'diagnostics' && (
            <div className="diagnostics-layout">
              <div><span>切换耗时</span><strong>{runtime.last_switch?.duration_ms?.toFixed(0) ?? '–'} ms</strong></div>
              <div><span>NPU P95</span><strong>{String(runtime.metrics?.npu_p95_ms ?? runtime.metrics?.p95_render_ms ?? '–')} ms</strong></div>
              <div className={Number(runtime.metrics?.underruns ?? 0) > 0 ? 'is-alert' : ''}><span>Underrun</span><strong>{String(runtime.metrics?.underruns ?? 0)}</strong></div>
              <div className={Number(runtime.metrics?.audio_tap_drops ?? 0) > 0 ? 'is-alert' : ''}><span>Tap 丢弃</span><strong>{String(runtime.metrics?.audio_tap_drops ?? 0)}</strong></div>
              <div className={Number(runtime.metrics?.clipped_samples ?? 0) > 0 ? 'is-alert' : ''}><span>削波样本</span><strong>{String(runtime.metrics?.clipped_samples ?? 0)}</strong></div>
              <details open={hasAnomaly}><summary>运行时详情</summary><pre>{JSON.stringify({ engine: runtime.diagnostics?.engine, metrics: runtime.metrics, audio: runtime.audio, midi: runtime.midi }, null, 2)}</pre></details>
            </div>
          )}
        </div>
      </section>
    </section>
  )
}
