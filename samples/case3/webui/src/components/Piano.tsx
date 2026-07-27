import { useEffect, useMemo, useRef } from 'react'

interface PianoProps {
  octave: number
  keyCount?: number
  velocity: number
  activeNotes: number[]
  recommendedMin?: number
  recommendedMax?: number
  disabled?: boolean
  onNoteOn: (note: number, velocity: number) => void
  onNoteOff: (note: number) => void
}

const NOTE_NAMES = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']
const BLACK_PITCH_CLASSES = new Set([1, 3, 6, 8, 10])
export const DEFAULT_PIANO_KEY_COUNT = 32

export function noteLabel(note: number): string {
  return `${NOTE_NAMES[note % 12]}${Math.floor(note / 12) - 1}`
}

export function pianoNoteRange(octave: number, keyCount = DEFAULT_PIANO_KEY_COUNT) {
  const first = (octave + 1) * 12 - 7
  return { first, last: first + Math.max(1, keyCount) - 1 }
}

export default function Piano({
  octave,
  keyCount = DEFAULT_PIANO_KEY_COUNT,
  velocity,
  activeNotes,
  recommendedMin,
  recommendedMax,
  disabled = false,
  onNoteOn,
  onNoteOff,
}: PianoProps) {
  const held = useRef(new Set<number>())
  const active = useMemo(() => new Set(activeNotes), [activeNotes])
  const rangeClass = (note: number) => (
    recommendedMin !== undefined && recommendedMax !== undefined
      && (note < recommendedMin || note > recommendedMax)
      ? 'is-out-of-range'
      : ''
  )
  const layout = useMemo(() => {
    const count = Math.max(1, Math.min(88, Math.round(keyCount)))
    const { first } = pianoNoteRange(octave, count)
    const notes = Array.from({ length: count }, (_, index) => first + index)
    const whiteNotes = notes.filter((note) => !BLACK_PITCH_CLASSES.has(note % 12))
    const blackNotes = notes
      .filter((note) => BLACK_PITCH_CLASSES.has(note % 12))
      .map((note) => ({
        note,
        left: (whiteNotes.filter((whiteNote) => whiteNote < note).length / whiteNotes.length) * 100,
      }))
    return { count, whiteNotes, blackNotes }
  }, [keyCount, octave])

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
      for (const note of held.current) onNoteOff(note)
      held.current.clear()
    }
    window.addEventListener('blur', releaseAll)
    return () => {
      window.removeEventListener('blur', releaseAll)
      releaseAll()
    }
  }, [onNoteOff])

  return (
    <div className={`piano ${disabled ? 'is-disabled' : ''}`} aria-label="触控钢琴" data-key-count={layout.count}>
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
            <span>{noteLabel(note)}</span>
          </button>
        ))}
      </div>
      {layout.blackNotes.map(({ note, left }) => (
        <button
          className={`piano-key black-key ${active.has(note) ? 'is-active' : ''} ${rangeClass(note)}`}
          style={{ left: `${left}%` }}
          key={note}
          type="button"
          aria-label={noteLabel(note)}
          onPointerDown={(event) => press(note, event.currentTarget, event.pointerId)}
          onPointerUp={() => release(note)}
          onPointerCancel={() => release(note)}
          onLostPointerCapture={() => release(note)}
        />
      ))}
    </div>
  )
}
