import { memo, useEffect, useRef } from 'react'
import { noteLabel } from './Piano'
import {
  subscribeLiveNoteEvents,
  type LiveNoteEvent,
  useLiveNotes,
} from './realtimeLiveNotes'

interface LivePianoRollProps {
  firstNote: number
  keyCount: number
  historySeconds: number
  running: boolean
  accentColor: string
}

interface NoteTrail {
  note: number
  startedAt: number
  endedAt: number | null
}

interface NotePosition {
  x: number
  width: number
}

interface RollGeometry {
  width: number
  height: number
  dpr: number
  hitLineY: number
  positions: Map<number, NotePosition>
}

const BLACK_PITCH_CLASSES = new Set([1, 3, 6, 8, 10])
const EMPTY_GEOMETRY: RollGeometry = {
  width: 0,
  height: 0,
  dpr: 1,
  hitLineY: 0,
  positions: new Map(),
}
const MAX_TRAILS = 192

function isBlack(note: number) {
  return BLACK_PITCH_CLASSES.has(note % 12)
}

function setCanvasSize(canvas: HTMLCanvasElement, width: number, height: number, dpr: number) {
  const pixelWidth = Math.round(width * dpr)
  const pixelHeight = Math.round(height * dpr)
  if (canvas.width !== pixelWidth) canvas.width = pixelWidth
  if (canvas.height !== pixelHeight) canvas.height = pixelHeight
}

function resizeCanvases(
  background: HTMLCanvasElement,
  trails: HTMLCanvasElement,
  firstNote: number,
  keyCount: number,
): RollGeometry {
  const bounds = background.getBoundingClientRect()
  const dpr = Math.min(1.25, Math.max(1, window.devicePixelRatio || 1))
  const width = Math.max(1, Math.round(bounds.width))
  const height = Math.max(1, Math.round(bounds.height))
  setCanvasSize(background, width, height, dpr)
  setCanvasSize(trails, width, height, dpr)

  const notes = Array.from({ length: keyCount }, (_, index) => firstNote + index)
  const whiteNotes = notes.filter((note) => !isBlack(note))
  const whiteWidth = width / Math.max(1, whiteNotes.length)
  const positions = new Map<number, NotePosition>()
  for (const note of notes) {
    if (!isBlack(note)) {
      const index = whiteNotes.indexOf(note)
      if (index >= 0) positions.set(note, { x: index * whiteWidth + 1, width: Math.max(3, whiteWidth - 2) })
      continue
    }
    const whiteBefore = whiteNotes.filter((value) => value < note).length
    const noteWidth = Math.max(4, whiteWidth * 0.58)
    positions.set(note, { x: whiteBefore * whiteWidth - noteWidth / 2, width: noteWidth })
  }
  return { width, height, dpr, hitLineY: height * 0.88, positions }
}

function roundedRect(
  context: CanvasRenderingContext2D,
  x: number,
  y: number,
  width: number,
  height: number,
  radius: number,
) {
  context.beginPath()
  if (context.roundRect) context.roundRect(x, y, width, height, radius)
  else context.rect(x, y, width, height)
}

function withGeometry(context: CanvasRenderingContext2D, geometry: RollGeometry, draw: () => void) {
  context.setTransform(1, 0, 0, 1, 0, 0)
  context.clearRect(0, 0, geometry.width * geometry.dpr, geometry.height * geometry.dpr)
  context.setTransform(geometry.dpr, 0, 0, geometry.dpr, 0, 0)
  draw()
}

function drawGrid(canvas: HTMLCanvasElement, geometry: RollGeometry) {
  const context = canvas.getContext('2d')
  if (!context || geometry.width <= 0 || geometry.height <= 0) return
  const { width, height, dpr, hitLineY, positions } = geometry
  withGeometry(context, geometry, () => {
    context.fillStyle = '#0e1317'
    context.fillRect(0, 0, width, height)
    context.fillStyle = 'rgba(255, 255, 255, 0.022)'
    for (const [note, position] of positions) {
      if (isBlack(note)) context.fillRect(position.x, 0, position.width, hitLineY)
    }
    const whiteCount = [...positions.keys()].filter((note) => !isBlack(note)).length
    const whiteWidth = width / Math.max(1, whiteCount)
    context.strokeStyle = 'rgba(160, 178, 188, 0.12)'
    context.lineWidth = 1
    for (let index = 0; index <= whiteCount; index += 1) {
      const x = Math.round(index * whiteWidth) + 0.5
      context.beginPath()
      context.moveTo(x, 0)
      context.lineTo(x, hitLineY)
      context.stroke()
    }
    for (let index = 1; index < 4; index += 1) {
      const y = Math.round((hitLineY * index) / 4) + 0.5
      context.strokeStyle = index === 2 ? 'rgba(160, 178, 188, 0.14)' : 'rgba(160, 178, 188, 0.08)'
      context.beginPath()
      context.moveTo(0, y)
      context.lineTo(width, y)
      context.stroke()
    }
    context.setTransform(dpr, 0, 0, dpr, 0, 0)
  })
}

function drawTrails(
  canvas: HTMLCanvasElement,
  geometry: RollGeometry,
  trails: NoteTrail[],
  now: number,
  historySeconds: number,
  accentColor: string,
) {
  const context = canvas.getContext('2d')
  if (!context || geometry.width <= 0 || geometry.height <= 0) return false
  const { width, height, dpr, hitLineY, positions } = geometry
  const historyMs = Math.max(1000, historySeconds * 1000)
  const travelHeight = Math.max(1, hitLineY - 6)
  const visibleTrails = trails.filter((trail) => now - (trail.endedAt ?? now) <= historyMs)
  trails.splice(0, trails.length, ...visibleTrails.slice(-MAX_TRAILS))
  withGeometry(context, geometry, () => {
    for (const trail of trails) {
      const position = positions.get(trail.note)
      if (!position) continue
      const endedAt = trail.endedAt ?? now
      const startAge = Math.max(0, now - trail.startedAt)
      const endAge = Math.max(0, now - endedAt)
      const top = hitLineY - Math.min(1, startAge / historyMs) * travelHeight
      const bottom = hitLineY - Math.min(1, endAge / historyMs) * travelHeight
      const blockHeight = Math.max(trail.endedAt === null ? 22 : 14, bottom - top)
      const y = Math.max(1, bottom - blockHeight)
      context.globalAlpha = trail.endedAt === null ? 1 : Math.max(0.24, 1 - endAge / historyMs)
      context.fillStyle = accentColor
      if (trail.endedAt === null) {
        context.shadowColor = accentColor
        context.shadowBlur = 7
      }
      roundedRect(context, position.x + 1, y, Math.max(3, position.width - 2), blockHeight, 3)
      context.fill()
      context.shadowColor = 'transparent'
      context.shadowBlur = 0
      if (blockHeight > 7) {
        context.fillStyle = 'rgba(255, 255, 255, 0.18)'
        roundedRect(context, position.x + 1, y, Math.max(3, position.width - 2), Math.min(3, blockHeight), 2)
        context.fill()
      }
      if (trail.endedAt === null) {
        context.strokeStyle = '#dff8ff'
        context.lineWidth = 1
        context.stroke()
      }
      if (position.width >= 24 && blockHeight >= 18) {
        context.fillStyle = '#071013'
        context.font = '700 8px "Cascadia Mono", Consolas, monospace'
        context.fillText(noteLabel(trail.note), position.x + 4, y + 11)
      }
    }
    context.globalAlpha = 1
    context.fillStyle = 'rgba(98, 174, 247, 0.18)'
    context.fillRect(0, hitLineY - 2, width, 5)
    context.fillStyle = '#62aef7'
    context.fillRect(0, hitLineY, width, 1)
    context.setTransform(dpr, 0, 0, dpr, 0, 0)
  })
  return trails.length > 0
}

const LivePianoRoll = memo(function LivePianoRoll({
  firstNote,
  keyCount,
  historySeconds,
  running,
  accentColor,
}: LivePianoRollProps) {
  const activeNotes = useLiveNotes()
  const backgroundRef = useRef<HTMLCanvasElement | null>(null)
  const trailsCanvasRef = useRef<HTMLCanvasElement | null>(null)
  const geometryRef = useRef<RollGeometry>(EMPTY_GEOMETRY)
  const trailsRef = useRef<NoteTrail[]>([])
  const activeRef = useRef(new Set<number>())
  const drawRef = useRef<() => boolean>(() => false)
  const requestAnimationRef = useRef<() => void>(() => undefined)
  const frameIdRef = useRef<number | null>(null)
  const lastFrameRef = useRef(0)
  const dirtyRef = useRef(false)
  const applyEventRef = useRef<(event: LiveNoteEvent) => void>(() => undefined)

  drawRef.current = () => {
    const canvas = trailsCanvasRef.current
    if (!canvas || document.hidden) return trailsRef.current.length > 0
    return drawTrails(
      canvas,
      geometryRef.current,
      trailsRef.current,
      performance.now(),
      historySeconds,
      accentColor,
    )
  }

  requestAnimationRef.current = () => {
    dirtyRef.current = true
    if (frameIdRef.current !== null) return
    const reducedMotion = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false
    const frameInterval = reducedMotion ? 100 : 1000 / 30
    const render = (timestamp: number) => {
      frameIdRef.current = null
      if (timestamp - lastFrameRef.current < frameInterval) {
        frameIdRef.current = window.requestAnimationFrame(render)
        return
      }
      lastFrameRef.current = timestamp
      dirtyRef.current = false
      const hasTrails = drawRef.current()
      if (!hasTrails && activeRef.current.size === 0 && !dirtyRef.current) return
      frameIdRef.current = window.requestAnimationFrame(render)
    }
    frameIdRef.current = window.requestAnimationFrame(render)
  }

  applyEventRef.current = (event) => {
    if (event.on) {
      if (!activeRef.current.has(event.note)) {
        activeRef.current.add(event.note)
        trailsRef.current.push({ note: event.note, startedAt: event.at, endedAt: null })
      }
      return
    }
    if (!activeRef.current.delete(event.note)) return
    const openTrail = [...trailsRef.current].reverse()
      .find((trail) => trail.note === event.note && trail.endedAt === null)
    if (openTrail) openTrail.endedAt = event.at
  }

  useEffect(() => {
    const background = backgroundRef.current
    const trails = trailsCanvasRef.current
    if (!background || !trails) return
    const resize = () => {
      geometryRef.current = resizeCanvases(background, trails, firstNote, keyCount)
      drawGrid(background, geometryRef.current)
      requestAnimationRef.current()
    }
    const observer = new ResizeObserver(resize)
    observer.observe(background)
    resize()
    return () => observer.disconnect()
  }, [firstNote, keyCount])

  useEffect(() => {
    const now = performance.now()
    const next = new Set(activeNotes)
    for (const note of activeRef.current) {
      if (!next.has(note)) applyEventRef.current({ note, on: false, at: now })
    }
    for (const note of next) {
      if (!activeRef.current.has(note)) applyEventRef.current({ note, on: true, at: now })
    }
    requestAnimationRef.current()
  }, [activeNotes])

  useEffect(() => subscribeLiveNoteEvents((event) => {
    applyEventRef.current(event)
    requestAnimationRef.current()
  }), [])

  useEffect(() => {
    requestAnimationRef.current()
  }, [accentColor, historySeconds, running])

  useEffect(() => () => {
    if (frameIdRef.current !== null) window.cancelAnimationFrame(frameIdRef.current)
  }, [])

  return (
    <div className={`live-piano-roll ${running ? 'is-running' : ''}`}>
      <canvas ref={backgroundRef} className="roll-background" aria-hidden="true" />
      <canvas ref={trailsCanvasRef} className="roll-trails" aria-label="动态钢琴卷帘" role="img" />
      <div className="roll-readout" aria-hidden="true">
        <span className="roll-live-state"><i />{running ? 'LIVE' : 'READY'}</span>
        <span>{activeNotes.length.toString().padStart(2, '0')} NOTES</span>
      </div>
    </div>
  )
})

export default LivePianoRoll
