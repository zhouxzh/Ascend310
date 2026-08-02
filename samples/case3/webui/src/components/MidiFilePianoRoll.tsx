import { ChevronDown, ChevronUp, LocateFixed, Maximize2, ZoomIn, ZoomOut } from 'lucide-react'
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import type { MidiPianoRoll } from '../types'

const VOICE_COLORS = [
  '#18a7a0', '#ef9f32', '#d65d67', '#5b8def', '#9b72cf', '#3d9b65',
  '#d17c45', '#398fa8', '#b65f9f', '#7e9c3a', '#ba735c', '#6678b8', '#2d887b',
]

interface Props {
  data: MidiPianoRoll | null
  assignments?: Record<string, number> | null
  progress?: number | null
  playing?: boolean
  errorMessage?: string
  compact?: boolean
  activeVoiceId?: string | null
  onVoiceSelect?: (voiceId: string) => void
  transport?: ReactNode
}

interface Size {
  width: number
  height: number
  ratio: number
}

function canvasContext(canvas: HTMLCanvasElement, size: Size) {
  const width = Math.max(1, Math.round(size.width * size.ratio))
  const height = Math.max(1, Math.round(size.height * size.ratio))
  if (canvas.width !== width) canvas.width = width
  if (canvas.height !== height) canvas.height = height
  const context = canvas.getContext('2d')
  context?.setTransform(size.ratio, 0, 0, size.ratio, 0, 0)
  return context
}

export default function MidiFilePianoRoll({
  data,
  assignments,
  progress = null,
  playing = false,
  errorMessage,
  compact = false,
  activeVoiceId = null,
  onVoiceSelect,
  transport,
}: Props) {
  const rootRef = useRef<HTMLDivElement>(null)
  const gridRef = useRef<HTMLCanvasElement>(null)
  const notesRef = useRef<HTMLCanvasElement>(null)
  const playheadRef = useRef<HTMLCanvasElement>(null)
  const animationRef = useRef<number | null>(null)
  const displayProgressRef = useRef(0)
  const targetProgressRef = useRef<number | null>(progress)
  const progressSampleRef = useRef({ value: progress ?? 0, at: performance.now() })
  const lastPaintRef = useRef(0)
  const dragRef = useRef<{ x: number; start: number } | null>(null)
  const wasPlayingRef = useRef(false)
  const [size, setSize] = useState<Size>({ width: 1, height: compact ? 196 : 230, ratio: 1 })
  const [zoom, setZoom] = useState(1)
  const [viewStart, setViewStart] = useState(0)
  const [followPlayback, setFollowPlayback] = useState(true)
  const [collapsed, setCollapsed] = useState(() => window.matchMedia?.('(max-width: 600px)').matches ?? false)

  const duration = Math.max(0.001, data?.duration_seconds ?? 1)
  const visibleDuration = duration / zoom
  const maxViewStart = Math.max(0, duration - visibleDuration)
  const pitchMin = Math.max(0, (data?.pitch_min ?? 60) - 2)
  const pitchMax = Math.min(127, (data?.pitch_max ?? 72) + 2)
  const pitchSpan = Math.max(1, pitchMax - pitchMin + 1)
  const keyboardWidth = compact ? 46 : 54
  const rollWidth = Math.max(1, size.width - keyboardWidth)

  useEffect(() => {
    const root = rootRef.current
    if (!root) return undefined
    const update = () => {
      const bounds = root.getBoundingClientRect()
      setSize({
        width: Math.max(1, bounds.width),
        height: Math.max(1, bounds.height),
        ratio: Math.min(2, window.devicePixelRatio || 1),
      })
    }
    update()
    const observer = new ResizeObserver(update)
    observer.observe(root)
    return () => observer.disconnect()
  }, [collapsed])

  useEffect(() => {
    setViewStart((current) => Math.min(current, maxViewStart))
  }, [maxViewStart])

  const geometry = useMemo(() => ({
    x: (seconds: number) => keyboardWidth + ((seconds - viewStart) / visibleDuration) * rollWidth,
    y: (pitch: number) => ((pitchMax - pitch + 0.25) / pitchSpan) * size.height,
    noteHeight: Math.max(2, size.height / pitchSpan * 0.64),
  }), [keyboardWidth, pitchMax, pitchSpan, rollWidth, size.height, viewStart, visibleDuration])

  const indexedNotes = useMemo(() => {
    const notes = data?.voices.flatMap((voice, voiceIndex) => {
      const instrumentId = assignments?.[voice.id] ?? voice.suggested_instrument_id ?? voiceIndex
      return voice.notes.map((note) => ({
        note,
        voiceId: voice.id,
        color: VOICE_COLORS[Math.abs(instrumentId) % VOICE_COLORS.length],
      }))
    }) ?? []
    notes.sort((left, right) => left.note.start_seconds - right.note.start_seconds)
    return {
      notes,
      maxDuration: notes.reduce((maximum, item) => Math.max(maximum, item.note.duration_seconds), 0),
    }
  }, [assignments, data])

  useEffect(() => {
    const canvas = gridRef.current
    if (!canvas || collapsed) return
    const context = canvasContext(canvas, size)
    if (!context) return
    context.clearRect(0, 0, size.width, size.height)
    context.fillStyle = '#10181c'
    context.fillRect(0, 0, size.width, size.height)
    for (let pitch = pitchMin; pitch <= pitchMax; pitch += 1) {
      const y = geometry.y(pitch)
      const rowHeight = size.height / pitchSpan
      const pitchClass = pitch % 12
      const blackKey = [1, 3, 6, 8, 10].includes(pitchClass)
      context.fillStyle = blackKey ? '#151f23' : '#1a2529'
      context.fillRect(keyboardWidth, y - rowHeight * 0.5, rollWidth, rowHeight)
      context.strokeStyle = pitch % 12 === 0 ? '#405159' : '#26343a'
      context.lineWidth = pitch % 12 === 0 ? 1.2 : 0.5
      context.beginPath()
      context.moveTo(keyboardWidth, y)
      context.lineTo(size.width, y)
      context.stroke()

      context.fillStyle = blackKey ? '#26343a' : '#e7ecee'
      context.fillRect(0, y - rowHeight * 0.48, blackKey ? keyboardWidth * 0.68 : keyboardWidth, rowHeight * 0.92)
      if (pitchClass === 0 && rowHeight >= 5) {
        context.fillStyle = '#53636a'
        context.font = `${Math.max(9, Math.min(12, rowHeight * 0.72))}px system-ui, sans-serif`
        context.fillText(`C${Math.floor(pitch / 12) - 1}`, 5, y + Math.min(4, rowHeight * 0.3))
      }
    }
    context.strokeStyle = '#566970'
    context.lineWidth = 1
    context.beginPath()
    context.moveTo(keyboardWidth, 0)
    context.lineTo(keyboardWidth, size.height)
    context.stroke()
    const bpm = data?.timing.tempo_changes[0]?.bpm ?? 120
    const beatsPerMeasure = data?.timing.time_signatures[0]?.numerator ?? 4
    const beatSeconds = 60 / Math.max(1, bpm)
    const firstBeat = Math.floor(viewStart / beatSeconds)
    const lastBeat = Math.ceil((viewStart + visibleDuration) / beatSeconds)
    const measureWidth = beatsPerMeasure * beatSeconds / visibleDuration * rollWidth
    const labelEvery = Math.max(1, Math.ceil(52 / Math.max(1, measureWidth)))
    context.font = '12px system-ui, sans-serif'
    for (let beat = firstBeat; beat <= lastBeat; beat += 1) {
      const x = geometry.x(beat * beatSeconds)
      const measure = beat % beatsPerMeasure === 0
      context.strokeStyle = measure ? '#607078' : '#314047'
      context.lineWidth = measure ? 1.2 : 0.6
      context.beginPath()
      context.moveTo(x, 0)
      context.lineTo(x, size.height)
      context.stroke()
      const measureNumber = Math.floor(beat / beatsPerMeasure) + 1
      if (measure && (measureNumber - 1) % labelEvery === 0) {
        context.fillStyle = '#9aabb2'
        context.fillText(String(measureNumber), x + 5, 15)
      }
    }
  }, [collapsed, data, geometry, keyboardWidth, pitchMax, pitchMin, pitchSpan, rollWidth, size, viewStart, visibleDuration])

  useEffect(() => {
    const canvas = notesRef.current
    if (!canvas || collapsed) return
    const context = canvasContext(canvas, size)
    if (!context) return
    context.clearRect(0, 0, size.width, size.height)
    data?.voices.forEach((voice, voiceIndex) => {
      const instrumentId = assignments?.[voice.id] ?? voice.suggested_instrument_id ?? voiceIndex
      const color = VOICE_COLORS[Math.abs(instrumentId) % VOICE_COLORS.length]
      context.fillStyle = color
      context.globalAlpha = activeVoiceId && activeVoiceId !== voice.id ? 0.35 : 0.9
      for (const note of voice.notes) {
        const noteEnd = note.start_seconds + note.duration_seconds
        if (noteEnd < viewStart || note.start_seconds > viewStart + visibleDuration) continue
        const x = Math.max(keyboardWidth, geometry.x(note.start_seconds))
        const endX = Math.min(size.width, geometry.x(noteEnd))
        const width = Math.max(2, endX - x)
        context.fillRect(x, geometry.y(note.pitch) - geometry.noteHeight / 2, width, geometry.noteHeight)
      }
    })
    context.globalAlpha = 1
  }, [activeVoiceId, assignments, collapsed, data, geometry, keyboardWidth, size, viewStart, visibleDuration])

  const paintPlayhead = useCallback((value: number | null) => {
    const canvas = playheadRef.current
    if (!canvas || collapsed) return
    const context = canvasContext(canvas, size)
    if (!context) return
    context.clearRect(0, 0, size.width, size.height)
    if (value == null) return
    const playTime = Math.max(0, Math.min(1, value)) * duration
    const x = geometry.x(playTime)
    if (x < keyboardWidth || x > size.width) return

    let low = 0
    let high = indexedNotes.notes.length
    const earliest = playTime - indexedNotes.maxDuration
    while (low < high) {
      const middle = (low + high) >>> 1
      if (indexedNotes.notes[middle].note.start_seconds < earliest) low = middle + 1
      else high = middle
    }
    for (let index = low; index < indexedNotes.notes.length; index += 1) {
      const item = indexedNotes.notes[index]
      if (item.note.start_seconds > playTime) break
      if (item.note.start_seconds + item.note.duration_seconds < playTime) continue
      const noteX = Math.max(keyboardWidth, geometry.x(item.note.start_seconds))
      const noteEndX = Math.min(size.width, geometry.x(item.note.start_seconds + item.note.duration_seconds))
      context.globalAlpha = activeVoiceId && activeVoiceId !== item.voiceId ? 0.45 : 1
      context.fillStyle = '#f6d06e'
      context.fillRect(noteX, geometry.y(item.note.pitch) - geometry.noteHeight, Math.max(3, noteEndX - noteX), geometry.noteHeight * 1.7)
      context.fillStyle = item.color
      context.fillRect(0, geometry.y(item.note.pitch) - geometry.noteHeight, keyboardWidth, geometry.noteHeight * 1.7)
    }
    context.globalAlpha = 1
    context.strokeStyle = '#f3b94d'
    context.lineWidth = 2
    context.beginPath()
    context.moveTo(x, 0)
    context.lineTo(x, size.height)
    context.stroke()
  }, [activeVoiceId, collapsed, duration, geometry, indexedNotes, keyboardWidth, size])

  useEffect(() => {
    targetProgressRef.current = progress
    progressSampleRef.current = { value: progress ?? 0, at: performance.now() }
    if (progress == null) {
      displayProgressRef.current = 0
      paintPlayhead(null)
      if (animationRef.current != null) cancelAnimationFrame(animationRef.current)
      animationRef.current = null
      return undefined
    }
    const animate = (timestamp: number) => {
      const target = targetProgressRef.current
      if (target == null) return
      if (timestamp - lastPaintRef.current < 1000 / 30) {
        animationRef.current = requestAnimationFrame(animate)
        return
      }
      lastPaintRef.current = timestamp
      const projected = playing
        ? Math.min(1, progressSampleRef.current.value + (timestamp - progressSampleRef.current.at) / 1000 / duration)
        : target
      const current = displayProgressRef.current
      const next = Math.abs(projected - current) < 0.0005 ? projected : current + (projected - current) * 0.24
      displayProgressRef.current = next
      paintPlayhead(next)
      const playTime = next * duration
      const playheadX = geometry.x(playTime)
      if (followPlayback && zoom > 1 && (playheadX > size.width * 0.8 || playheadX < keyboardWidth)) {
        setViewStart(Math.max(0, Math.min(maxViewStart, playTime - visibleDuration * 0.22)))
      }
      if ((playing && projected < 1) || Math.abs(next - projected) >= 0.0005) {
        animationRef.current = requestAnimationFrame(animate)
      } else {
        animationRef.current = null
      }
    }
    if (animationRef.current == null) animationRef.current = requestAnimationFrame(animate)
    return () => {
      if (animationRef.current != null) cancelAnimationFrame(animationRef.current)
      animationRef.current = null
    }
  }, [duration, followPlayback, geometry, keyboardWidth, maxViewStart, paintPlayhead, playing, progress, size.width, visibleDuration, zoom])

  useEffect(() => {
    if (playing && !wasPlayingRef.current) {
      setFollowPlayback(true)
      if (zoom === 1) {
        const nextZoom = 4
        const nextVisible = duration / nextZoom
        setZoom(nextZoom)
        setViewStart(Math.max(0, Math.min(duration - nextVisible, displayProgressRef.current * duration - nextVisible * 0.22)))
      }
    }
    wasPlayingRef.current = playing
  }, [duration, playing, zoom])

  function changeZoom(next: number) {
    const clamped = Math.max(1, Math.min(16, next))
    const center = viewStart + visibleDuration / 2
    const nextVisible = duration / clamped
    setZoom(clamped)
    setViewStart(Math.max(0, Math.min(duration - nextVisible, center - nextVisible / 2)))
  }

  function pointerDown(event: React.PointerEvent<HTMLDivElement>) {
    event.currentTarget.setPointerCapture(event.pointerId)
    dragRef.current = { x: event.clientX, start: viewStart }
    setFollowPlayback(false)
  }

  function pointerMove(event: React.PointerEvent<HTMLDivElement>) {
    if (!dragRef.current || zoom <= 1) return
    const delta = (dragRef.current.x - event.clientX) / Math.max(1, size.width) * visibleDuration
    setViewStart(Math.max(0, Math.min(maxViewStart, dragRef.current.start + delta)))
  }

  function pointerUp(event: React.PointerEvent<HTMLDivElement>) {
    const dragged = dragRef.current && Math.abs(dragRef.current.x - event.clientX) > 4
    dragRef.current = null
    if (dragged || !data || !onVoiceSelect) return
    const bounds = event.currentTarget.getBoundingClientRect()
    if (event.clientX - bounds.left < keyboardWidth) return
    const seconds = viewStart + ((event.clientX - bounds.left - keyboardWidth) / Math.max(1, bounds.width - keyboardWidth)) * visibleDuration
    const pitch = Math.round(pitchMax - ((event.clientY - bounds.top) / bounds.height) * pitchSpan)
    const voice = data.voices.find((candidate) => candidate.notes.some((note) => (
      note.pitch === pitch
      && seconds >= note.start_seconds
      && seconds <= note.start_seconds + note.duration_seconds
    )))
    if (voice) onVoiceSelect(voice.id)
  }

  return (
    <section className={`midi-file-roll ${compact ? 'is-compact' : ''} ${collapsed ? 'is-collapsed' : ''}`} aria-label="MIDI 文件钢琴卷帘">
      <div className="midi-file-roll-toolbar">
        <div className="midi-file-roll-title">
          <strong>PIANO ROLL</strong>
          <span>{playing ? '播放中 · ' : ''}{data ? `${data.note_count} 音符 · ${data.voices.length} 声部` : errorMessage ? '卷帘不可用' : '正在载入音符'}</span>
        </div>
        {transport}
        <div className="midi-file-roll-actions">
          <button type="button" className="icon-button" title="缩小时间轴" onClick={() => changeZoom(zoom / 2)} disabled={zoom <= 1}><ZoomOut size={18} /></button>
          <output>{zoom.toFixed(0)}×</output>
          <button type="button" className="icon-button" title="放大时间轴" onClick={() => changeZoom(zoom * 2)} disabled={zoom >= 16}><ZoomIn size={18} /></button>
          <button type="button" className={`icon-button ${followPlayback ? 'is-active' : ''}`} title={followPlayback ? '关闭自动跟随' : '开启自动跟随'} onClick={() => setFollowPlayback((value) => !value)}><LocateFixed size={18} /></button>
          <button type="button" className="icon-button" title="显示完整曲目" onClick={() => { setZoom(1); setViewStart(0); setFollowPlayback(false) }}><Maximize2 size={18} /></button>
          <button type="button" className="icon-button roll-collapse" title={collapsed ? '展开卷帘' : '折叠卷帘'} onClick={() => setCollapsed((value) => !value)}>
            {collapsed ? <ChevronDown size={20} /> : <ChevronUp size={20} />}
          </button>
        </div>
      </div>
      {!collapsed && (
        <div
          ref={rootRef}
          className="midi-file-roll-canvas"
          role="img"
          aria-label="MIDI 文件音符时间轴"
          onPointerDown={pointerDown}
          onPointerMove={pointerMove}
          onPointerUp={pointerUp}
          onPointerCancel={() => { dragRef.current = null }}
        >
          <canvas ref={gridRef} />
          <canvas ref={notesRef} />
          <canvas ref={playheadRef} />
          {!data && <span className={`midi-file-roll-loading ${errorMessage ? 'is-error' : ''}`}>{errorMessage ?? '正在分析 MIDI'}</span>}
        </div>
      )}
    </section>
  )
}
