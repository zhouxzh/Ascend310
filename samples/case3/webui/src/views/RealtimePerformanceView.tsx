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
  Headphones,
  KeyboardMusic,
  Music2,
  Octagon,
  Play,
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
import { realtimePatchNameZh } from '../timbres'
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
let realtimeCatalogSnapshot: RealtimeCatalog | null = null
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
const TONE_PARAMETER_LABELS: Record<string, string> = {
  velocity_curve: '力度曲线',
  harmonic_gain: '谐波',
  noise_gain: '噪声',
  attack: '起音',
  decay: '衰减',
  sustain: '延音电平',
  release: '释音',
  input_pitch: '输入音高校准',
  input_gain: '输入响度校准',
  reverb_size: '混响空间',
  reverb_damping: '混响阻尼',
}

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

export default function RealtimePerformanceView({ onRefresh, inputMode: initialInputMode = 'midi' }: Props) {
  const [inputMode, setInputMode] = useState<RealtimeInputMode>(initialInputMode)
  const isTouchPerformance = inputMode === 'touch'
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

  const [catalog, setCatalog] = useState<RealtimeCatalog | null>(() => realtimeCatalogSnapshot)
  const [runtime, setRuntime] = useState<RealtimeStatus>(EMPTY_STATUS)
  const [selectedPatchId, setSelectedPatchId] = useState(savedRef.current?.lastPatchId ?? '')
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
  const [rollWindow, setRollWindow] = useState<2 | 4 | 8>(4)
  const [velocity, setVelocity] = useState(96)
  const [sustain, setSustain] = useState(false)
  const [pitchBend, setPitchBend] = useState(0)
  const [monitor, setMonitor] = useState(false)
  const [recordingUrl, setRecordingUrl] = useState('')
  const [connection, setConnection] = useState<'connecting' | 'connected' | 'disconnected'>('connecting')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const applyRuntime = useCallback((nextStatus: RealtimeStatus) => {
    publishLiveNotes(nextStatus.active_notes ?? [])
    setRuntime(nextStatus)
  }, [])

  const selectablePatches = useMemo(() => (
    catalog?.patches.filter((patch) => patch.category === 'piano') ?? []
  ), [catalog])
  const selectedPatch = useMemo(
    () => selectablePatches.find((patch) => patch.patch_id === selectedPatchId) ?? null,
    [selectablePatches, selectedPatchId],
  )
  const currentPatch = runtime.running && runtime.patch ? runtime.patch : selectedPatch
  const sessionUsesUnsupportedPatch = runtime.running
    && currentPatch?.category !== 'piano'
  const patchAccent = PATCH_ACCENTS[currentPatch?.category ?? selectedPatch?.category ?? 'piano']
  const parameters = selectedPatch ? clampPatchParameters(selectedPatch, patchParameters[selectedPatch.patch_id]) : {}
  const selectedAudio = catalog?.audio_devices.find((device) => device.id === audioDeviceId)
  const recording = Boolean(runtime.recording?.active)
    || Number(runtime.metrics?.overruns ?? 0) > 0
    || Number(runtime.metrics?.audio_tap_drops ?? 0) > 0
    || Number(runtime.metrics?.clipped_samples ?? 0) > 0
    || Boolean(runtime.audio?.device_lost)

  const refreshCatalog = useCallback(async () => {
    const [nextCatalog, nextStatus] = await Promise.all([api.realtimeCatalog(), api.realtimeStatus()])
    if (!mountedRef.current) return
    realtimeCatalogSnapshot = nextCatalog
    setCatalog(nextCatalog)
    applyRuntime(nextStatus)
    const saved = savedRef.current
    const availablePatches = nextCatalog.patches.filter((item) => item.category === 'piano')
    const runningPatchId = nextStatus.patch?.category !== 'piano'
      ? null
      : nextStatus.patch_id
    const currentId = runningPatchId ?? saved?.lastPatchId
    const patch = availablePatches.find((item) => item.patch_id === currentId)
      ?? availablePatches.find((item) => item.patch_id === saved?.lastPatchId)
      ?? availablePatches.find((item) => item.category === 'piano')
      ?? availablePatches[0]
    if (!patch) return
    setSelectedPatchId(patch.patch_id)
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
      if (runtime.running) send({ event: 'all_notes_off' })
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
  }, [runtime.running, send])

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
    if (
      runtime.last_switch?.rolled_back
      && runtime.patch_id
      && runtime.patch?.category === 'piano'
    ) setSelectedPatchId(runtime.patch_id)
  }, [runtime.last_switch, runtime.patch, runtime.patch_id])

  const choosePatch = async (patch: RealtimePatch) => {
    if (patch.category !== 'piano') return
    if (recording) {
      setError('录音期间音色已锁定，请先停止录音')
      return
    }
    if (runtime.running && !patch.compatible_audio_device_ids.includes(audioDeviceId)) {
      setError('当前输出设备不兼容该音色；当前音色会继续运行，请停止会话后更换输出设备')
      return
    }
    setSelectedPatchId(patch.patch_id)
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

  const canShiftOctaveLeft = keyCount < 88 && windowStart - 12 >= 21
  const canShiftOctaveRight = keyCount < 88 && windowStart + 12 <= 109 - keyCount

  const selectKeyCount = (nextKeyCount: KeyboardKeyCount) => {
    if (!KEYBOARD_KEY_COUNTS[inputMode].includes(nextKeyCount)) return
    if (runtime.running) send({ event: 'all_notes_off' })
    setSustain(false)
    setKeyCount(nextKeyCount)
    setWindowStart(KEYBOARD_DEFAULT_FIRST_NOTE[nextKeyCount])
  }

  const selectInputMode = (nextInputMode: RealtimeInputMode) => {
    if (nextInputMode === inputMode || runtime.running || busy || recording) return
    const stored = savedRef.current
    const nextKeyCount = normalizeKeyboardKeyCount(
      nextInputMode,
      getStoredKeyboardKeyCount(stored, nextInputMode),
    )
    setSustain(false)
    setPitchBend(0)
    clearLiveNotes()
    setKeyCount(nextKeyCount)
    setWindowStart(clampKeyboardFirstNote(
      nextKeyCount,
      getStoredKeyboardFirstNote(stored, nextInputMode),
    ))
    setInputMode(nextInputMode)
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

  const keyCounts = KEYBOARD_KEY_COUNTS[inputMode]
  const outputGain = parameters.output_gain_db ?? 0
  const reverb = parameters.reverb ?? 0
  const transpose = parameters.transpose ?? 0
  const compatibleAudioDevices = (catalog?.audio_devices ?? []).filter((device) => selectedPatch?.compatible_audio_device_ids.includes(device.id))
  const npuP95 = runtime.metrics?.npu_p95_ms ?? runtime.metrics?.p95_render_ms
  const midiToPcmP95 = runtime.metrics?.midi_to_pcm_p95_ms
  return (
    <section className={`realtime-stage realtime-stage--${inputMode} ${isTouchPerformance ? `touch-keyboard-size--${touchKeyboardSize}` : ''}`}>
      <header className="stage-session-bar has-input-mode realtime-session-bar">
        <div className="realtime-input-mode" role="tablist" aria-label="演奏输入方式">
          <button
            type="button"
            role="tab"
            aria-selected={isTouchPerformance}
            className={isTouchPerformance ? 'is-active' : ''}
            disabled={runtime.running || busy || recording}
            title={runtime.running ? '停止演奏后切换输入方式' : '使用屏幕琴键演奏'}
            onClick={() => selectInputMode('touch')}
          >
            <KeyboardMusic size={18} />
            <span>触摸屏</span>
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={!isTouchPerformance}
            className={!isTouchPerformance ? 'is-active' : ''}
            disabled={runtime.running || busy || recording}
            title={runtime.running ? '停止演奏后切换输入方式' : '使用实体 MIDI 键盘'}
            onClick={() => selectInputMode('midi')}
          >
            <Cable size={18} />
            <span>MIDI 键盘</span>
          </button>
        </div>
        <label className="touch-session-field touch-session-patch">
          <Music2 size={20} />
          <span>当前音色</span>
          <select
            aria-label="当前音色"
            value={selectedPatchId}
            disabled={busy || recording}
            onChange={(event) => {
              const patch = selectablePatches.find((item) => item.patch_id === event.target.value)
              if (patch) choosePatch(patch)
            }}
          >
            {selectablePatches.map((patch) => <option value={patch.patch_id} key={patch.patch_id}>{realtimePatchNameZh(patch)}</option>)}
          </select>
        </label>
        <div className="touch-session-routing" aria-label="连接设置">
          <Cable size={18} />
          <label>
            <span>音频输出</span>
            <select aria-label="音频输出" value={audioDeviceId} disabled={runtime.running} onChange={(event) => setAudioDeviceId(event.target.value)}>
              {compatibleAudioDevices.map((device) => <option value={device.id} key={device.id}>{device.name}</option>)}
            </select>
          </label>
          <label>
            <span>延时</span>
            <select aria-label="延时档位" value={latencyProfile} disabled={runtime.running} onChange={(event) => setLatencyProfile(event.target.value as LatencyProfile)}>
              <option value="low" disabled={Boolean(selectedAudio?.is_bluetooth)}>低延时</option>
              <option value="balanced">均衡</option>
              <option value="safe">稳定</option>
            </select>
          </label>
        </div>
        <div className="touch-session-health" aria-label="会话状态">
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
      {sessionUsesUnsupportedPatch && <Notice tone="warn">当前会话不是 Piano-DDSP 钢琴模型。请停止演奏后，从上方统一音色菜单重新选择钢琴模型。</Notice>}
      {!catalog && <Notice tone="loading">正在加载统一音色库</Notice>}

      <section className="touch-control-deck" aria-label="实时演奏控制台">
        <div className="touch-shaping-controls">
          <label className="touch-slider-control">
            <span>输出增益</span>
            <strong>{formatGainDb(outputGain)}</strong>
            <input aria-label="输出增益" type="range" min="-60" max="6" step="0.5" value={outputGain} onChange={(event) => updateParameter('output_gain_db', Number(event.target.value))} />
          </label>
          <label className="touch-slider-control">
            <span>混响</span>
            <strong>{Math.round(reverb * 100)}%</strong>
            <input aria-label="混响" type="range" min="0" max="1" step="0.01" value={reverb} onChange={(event) => updateParameter('reverb', Number(event.target.value))} />
          </label>
          {isTouchPerformance && (
            <label className="touch-slider-control">
              <span>力度</span>
              <strong>{velocity}</strong>
              <input aria-label="触控力度" type="range" min="1" max="127" value={velocity} onChange={(event) => setVelocity(Number(event.target.value))} />
            </label>
          )}
          <label className="touch-slider-control">
            <span>移调</span>
            <strong>{transpose > 0 ? `+${transpose}` : transpose}</strong>
            <input aria-label="移调" type="range" min="-24" max="24" value={transpose} onChange={(event) => updateParameter('transpose', Number(event.target.value))} />
          </label>
          {selectedPatch && Object.entries(selectedPatch.parameters)
            .filter(([name, metadata]) => (
              !['transpose', 'output_gain_db', 'reverb', 'piano_year'].includes(name)
              && metadata.min !== undefined
              && metadata.max !== undefined
            ))
            .map(([name, metadata]) => {
              const label = TONE_PARAMETER_LABELS[name] ?? name.replaceAll('_', ' ')
              const value = parameters[name] ?? metadata.default ?? 0
              return (
                <label className="touch-slider-control" key={name}>
                  <span>{label}</span>
                  <strong>{value.toFixed(2)}</strong>
                  <input aria-label={label} type="range" min={metadata.min} max={metadata.max} step={(metadata.max! - metadata.min!) / 100} value={value} onChange={(event) => updateParameter(name, Number(event.target.value))} />
                </label>
              )
            })}
          {selectedPatch?.parameters.piano_year?.options && (
            <label className="touch-option-control">
              <span>钢琴年份</span>
              <select aria-label="钢琴年份" value={parameters.piano_year ?? 2018} onChange={(event) => updateParameter('piano_year', Number(event.target.value))}>
                {selectedPatch.parameters.piano_year.options.map((year) => <option key={year} value={year}>{year}</option>)}
              </select>
            </label>
          )}
        </div>
      </section>

      <section
        className={`keyboard-stage is-${inputMode}`}
        aria-label={isTouchPerformance ? '触控实时演奏' : 'MIDI 键盘实时演奏'}
        style={{ '--stage-accent': patchAccent } as CSSProperties}
      >
        <div className="roll-toolbar">
          <div className="roll-heading">
            <Activity size={17} />
            <div>
              <strong>实时卷帘</strong>
              <span>{isTouchPerformance ? '触控演奏' : '实体 MIDI 键盘'}</span>
            </div>
          </div>
          <div className="touch-runtime-metrics" aria-label="实时性能">
            <span title="按键事件到首个 PCM 音频块的延迟 P95"><span>按键 P95</span><strong>{midiToPcmP95 === undefined ? '–' : Number(midiToPcmP95).toFixed(1)} ms</strong></span>
            <span title="NPU 推理耗时 P95"><span>NPU P95</span><strong>{npuP95 === undefined ? '–' : Number(npuP95).toFixed(1)} ms</strong></span>
            <span className={Number(runtime.metrics?.underruns ?? 0) > 0 ? 'is-alert' : ''}><span>欠载</span><strong>{String(runtime.metrics?.underruns ?? 0)}</strong></span>
            <span className={Number(runtime.metrics?.audio_tap_drops ?? 0) > 0 ? 'is-alert' : ''}><span>监听丢弃</span><strong>{String(runtime.metrics?.audio_tap_drops ?? 0)}</strong></span>
            <span className={Number(runtime.metrics?.clipped_samples ?? 0) > 0 ? 'is-alert' : ''}><span>削波</span><strong>{String(runtime.metrics?.clipped_samples ?? 0)}</strong></span>
          </div>
          <div className="touch-capture-controls" aria-label="录音与监听">
            <button className={recording ? 'danger-button' : 'secondary-button'} type="button" disabled={!runtime.running} onClick={() => send({ event: recording ? 'record_stop' : 'record_start' })}><Disc3 size={17} />{recording ? '停止录音' : '录音'}</button>
            <button className={`secondary-button ${monitor ? 'is-active' : ''}`} type="button" disabled={!runtime.running} onClick={() => toggleMonitor().catch((cause) => setError(errorMessage(cause)))}><Headphones size={17} />{monitor ? '关闭监听' : '监听'}</button>
            {recordingUrl && <a className="download-button" href={recordingUrl} download title="下载录音"><Download size={17} />WAV</a>}
          </div>
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
        <div className={`keyboard-range-bar ${isTouchPerformance ? 'touch-keyboard-command-bar' : ''}`}>
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
          {!isTouchPerformance && (
            <label className="midi-keyboard-port-control">
              <Cable size={18} />
              <span>实体 MIDI 输入</span>
              <select aria-label="实体 MIDI 输入" value={midiPort} disabled={runtime.running} onChange={(event) => setMidiPort(event.target.value)}>
                <option value="">选择 MIDI 输入</option>
                {(catalog?.midi_ports ?? []).map((port) => <option value={port.port ?? port.id} key={port.id}>{port.name}</option>)}
              </select>
            </label>
          )}
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
          {isTouchPerformance && (
            <label className="touch-bend-control">
              <span>弯音</span>
              <strong>{pitchBend}</strong>
              <input
                aria-label="弯音"
                type="range"
                min="-8192"
                max="8191"
                value={pitchBend}
                onChange={(event) => {
                  const value = Number(event.target.value)
                  setPitchBend(value)
                  send({ event: 'pitch_bend', value })
                }}
                onPointerUp={() => {
                  setPitchBend(0)
                  send({ event: 'pitch_bend', value: 0 })
                }}
              />
            </label>
          )}
          {isTouchPerformance && (
            <button
              type="button"
              className={`sustain-control touch-sustain-control ${sustain ? 'is-active' : ''}`}
              disabled={!runtime.running}
              aria-pressed={sustain}
              onClick={() => {
                const enabled = !sustain
                setSustain(enabled)
                send({ event: 'sustain', enabled })
              }}
            >
              <Music2 size={20} />
              <span>延音</span>
              <strong>{sustain ? '开启' : '关闭'}</strong>
            </button>
          )}
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
            <div className="keyboard-frame keyboard-frame--touch">
              <LivePiano
                octave={3}
                firstNote={windowStart}
                keyCount={keyCount}
                velocity={velocity}
                recommendedMin={currentPatch?.pitch_min}
                recommendedMax={currentPatch?.pitch_max}
                disabled={!runtime.running || busy || sessionUsesUnsupportedPatch}
                keyboardShortcuts={{}}
                onNoteOn={handleNoteOn}
                onNoteOff={handleNoteOff}
              />
            </div>
          </>
        ) : (
          <>
            <LivePianoRoll
              firstNote={windowStart}
              keyCount={keyCount}
              historySeconds={rollWindow}
              running={runtime.running}
              accentColor={patchAccent}
            />
            <div className="keyboard-frame keyboard-frame--midi">
              <VisualizerKeyboard
                firstNote={windowStart}
                keyCount={keyCount}
                accentColor={patchAccent}
                recommendedMin={currentPatch?.pitch_min}
                recommendedMax={currentPatch?.pitch_max}
              />
            </div>
          </>
        )}
      </section>

    </section>
  )
}
