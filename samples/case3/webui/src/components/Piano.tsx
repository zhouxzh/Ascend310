import { useEffect, useMemo, useRef, type CSSProperties } from 'react'

interface PianoProps {
  octave: number
  firstNote?: number
  keyCount?: number
  velocity: number
  activeNotes: readonly number[]
  recommendedMin?: number
  recommendedMax?: number
  disabled?: boolean
  keyboardShortcuts?: Record<string, number>
  onNoteOn: (note: number, velocity: number) => void
  onNoteOff: (note: number) => void
}

const NOTE_NAMES = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']
const BLACK_PITCH_CLASSES = new Set([1, 3, 6, 8, 10])
export const DEFAULT_PIANO_KEY_COUNT = 32
export const WHITE_KEY_LENGTH_TO_WIDTH_RATIO = 150 / 23
export const BLACK_KEY_WIDTH_TO_WHITE_KEY_WIDTH_RATIO = 11 / 23
export const REFERENCE_WHITE_KEY_WIDTH_PX = 66

// Key geometry and shortcut-label behavior adapted from react-piano at
// a8fac9f1ab0aab8fd21658714f1ad9f14568feee (MIT); pointer safety is local.

export function noteLabel(note: number): string {
  return `${NOTE_NAMES[note % 12]}${Math.floor(note / 12) - 1}`
}

export function pianoNoteRange(octave: number, keyCount = DEFAULT_PIANO_KEY_COUNT) {
  const first = (octave + 1) * 12 - 7
  return { first, last: first + Math.max(1, keyCount) - 1 }
}

export default function Piano({
  octave,
  firstNote,
  keyCount = DEFAULT_PIANO_KEY_COUNT,
  velocity,
  activeNotes,
  recommendedMin,
  recommendedMax,
  disabled = false,
  keyboardShortcuts = {},
  onNoteOn,
  onNoteOff,
}: PianoProps) {
  const held = useRef(new Set<number>())
  const noteOffRef = useRef(onNoteOff)
  const noteOnRef = useRef(onNoteOn)
  const velocityRef = useRef(velocity)
  const disabledRef = useRef(disabled)
  const shortcutsRef = useRef(keyboardShortcuts)
  noteOffRef.current = onNoteOff
  noteOnRef.current = onNoteOn
  velocityRef.current = velocity
  disabledRef.current = disabled
  shortcutsRef.current = keyboardShortcuts
  const shortcutLabels = useMemo(() => {
    const labels = new Map<number, string>()
    for (const [key, note] of Object.entries(keyboardShortcuts)) labels.set(note, key.toUpperCase())
    return labels
  }, [keyboardShortcuts])
  const active = useMemo(() => new Set(activeNotes), [activeNotes])
  const rangeClass = (note: number) => (
    recommendedMin !== undefined && recommendedMax !== undefined
      && (note < recommendedMin || note > recommendedMax)
      ? 'is-out-of-range'
      : ''
  )
  const layout = useMemo(() => {
    const count = Math.max(1, Math.min(88, Math.round(keyCount)))
    const first = firstNote ?? pianoNoteRange(octave, count).first
    const notes = Array.from({ length: count }, (_, index) => first + index)
    const whiteNotes = notes.filter((note) => !BLACK_PITCH_CLASSES.has(note % 12))
    const blackNotes = notes
      .filter((note) => BLACK_PITCH_CLASSES.has(note % 12))
      .map((note) => ({
        note,
        left: (whiteNotes.filter((whiteNote) => whiteNote < note).length / whiteNotes.length) * 100,
      }))
    return { count, whiteNotes, blackNotes }
  }, [firstNote, keyCount, octave])

  const release = (note: number) => {
    if (held.current.delete(note)) onNoteOff(note)
  }

  const press = (note: number, target: HTMLElement, pointerId: number) => {
    if (disabled || held.current.has(note)) return
    held.current.add(note)
    target.setPointerCapture?.(pointerId)
    onNoteOn(note, velocity)
  }

  useEffect(() => {
    const releaseAll = () => {
      for (const note of held.current) noteOffRef.current(note)
      held.current.clear()
    }
    window.addEventListener('blur', releaseAll)
    return () => {
      window.removeEventListener('blur', releaseAll)
      releaseAll()
    }
  }, [])

  useEffect(() => {
    for (const note of held.current) noteOffRef.current(note)
    held.current.clear()
  }, [firstNote, keyCount])

  useEffect(() => {
    const keyboardHeld = new Map<string, number>()
    const isTypingTarget = (target: EventTarget | null) => (
      target instanceof HTMLInputElement
      || target instanceof HTMLSelectElement
      || target instanceof HTMLTextAreaElement
      || (target instanceof HTMLElement && target.isContentEditable)
    )
    const keyDown = (event: KeyboardEvent) => {
      if (event.repeat || isTypingTarget(event.target) || disabledRef.current) return
      const key = event.key.toLowerCase()
      const note = shortcutsRef.current[key]
      if (note === undefined || held.current.has(note)) return
      event.preventDefault()
      keyboardHeld.set(key, note)
      held.current.add(note)
      noteOnRef.current(note, velocityRef.current)
    }
    const keyUp = (event: KeyboardEvent) => {
      const key = event.key.toLowerCase()
      const note = keyboardHeld.get(key)
      if (note === undefined) return
      event.preventDefault()
      keyboardHeld.delete(key)
      if (held.current.delete(note)) noteOffRef.current(note)
    }
    window.addEventListener('keydown', keyDown)
    window.addEventListener('keyup', keyUp)
    return () => {
      window.removeEventListener('keydown', keyDown)
      window.removeEventListener('keyup', keyUp)
      for (const note of keyboardHeld.values()) {
        if (held.current.delete(note)) noteOffRef.current(note)
      }
    }
  }, [])

  return (
    <div
      className={`piano ${disabled ? 'is-disabled' : ''}`}
      aria-label="触控钢琴"
      data-key-count={layout.count}
      data-white-key-count={layout.whiteNotes.length}
      style={{
        '--piano-keyboard-aspect-ratio': `${layout.whiteNotes.length} / ${WHITE_KEY_LENGTH_TO_WIDTH_RATIO}`,
        '--piano-reference-width': `${layout.whiteNotes.length * REFERENCE_WHITE_KEY_WIDTH_PX}px`,
      } as CSSProperties}
    >
      <div className="white-keys" style={{ gridTemplateColumns: `repeat(${layout.whiteNotes.length}, minmax(0, 1fr))` }}>
        {layout.whiteNotes.map((note) => (
          <button
            className={`piano-key white-key ${active.has(note) ? 'is-active' : ''} ${rangeClass(note)}`}
            key={note}
            type="button"
            aria-label={noteLabel(note)}
            onPointerDown={(event) => press(note, event.currentTarget, event.pointerId)}
            onPointerUp={() => release(note)}
            onPointerCancel={() => release(note)}
            onLostPointerCapture={() => release(note)}
          >
            <span className="note-label">{noteLabel(note)}</span>
            {shortcutLabels.has(note) && <span className="shortcut-label">{shortcutLabels.get(note)}</span>}
          </button>
        ))}
      </div>
      {layout.blackNotes.map(({ note, left }) => (
        <button
          className={`piano-key black-key ${active.has(note) ? 'is-active' : ''} ${rangeClass(note)}`}
          style={{
            left: `${left}%`,
            width: `${(BLACK_KEY_WIDTH_TO_WHITE_KEY_WIDTH_RATIO / layout.whiteNotes.length) * 100}%`,
          }}
          key={note}
          type="button"
          aria-label={noteLabel(note)}
          onPointerDown={(event) => press(note, event.currentTarget, event.pointerId)}
          onPointerUp={() => release(note)}
          onPointerCancel={() => release(note)}
          onLostPointerCapture={() => release(note)}
        >
          {shortcutLabels.has(note) && <span className="shortcut-label">{shortcutLabels.get(note)}</span>}
        </button>
      ))}
    </div>
  )
}
