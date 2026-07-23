import { useEffect, useMemo, useRef } from 'react'

interface PianoProps {
  octave: number
  velocity: number
  activeNotes: number[]
  disabled?: boolean
  onNoteOn: (note: number, velocity: number) => void
  onNoteOff: (note: number) => void
}

const WHITE_OFFSETS = [0, 2, 4, 5, 7, 9, 11]
const BLACK_KEYS = [
  { offset: 1, left: 1 },
  { offset: 3, left: 2 },
  { offset: 6, left: 4 },
  { offset: 8, left: 5 },
  { offset: 10, left: 6 },
]
const NOTE_NAMES = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']

function noteLabel(note: number): string {
  return `${NOTE_NAMES[note % 12]}${Math.floor(note / 12) - 1}`
}

export default function Piano({
  octave,
  velocity,
  activeNotes,
  disabled = false,
  onNoteOn,
  onNoteOff,
}: PianoProps) {
  const held = useRef(new Set<number>())
  const base = (octave + 1) * 12
  const active = useMemo(() => new Set(activeNotes), [activeNotes])
  const whiteNotes = [...WHITE_OFFSETS.map((offset) => base + offset), ...WHITE_OFFSETS.map((offset) => base + 12 + offset)]
  const blackNotes = [
    ...BLACK_KEYS.map((key) => ({ ...key, note: base + key.offset })),
    ...BLACK_KEYS.map((key) => ({ ...key, note: base + 12 + key.offset, left: key.left + 7 })),
  ]

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
    <div className={`piano ${disabled ? 'is-disabled' : ''}`} aria-label="触控钢琴">
      <div className="white-keys">
        {whiteNotes.map((note) => (
          <button
            className={`piano-key white-key ${active.has(note) ? 'is-active' : ''}`}
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
      {blackNotes.map(({ note, left }) => (
        <button
          className={`piano-key black-key ${active.has(note) ? 'is-active' : ''}`}
          style={{ left: `calc(${((left + 0.72) / 14) * 100}% - 17px)` }}
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
