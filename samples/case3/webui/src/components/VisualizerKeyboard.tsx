import { memo, useMemo, type CSSProperties } from 'react'
import { noteLabel } from './Piano'
import { useLiveNotes } from './realtimeLiveNotes'

interface VisualizerKeyboardProps {
  firstNote?: number
  keyCount?: number
  accentColor: string
  recommendedMin?: number
  recommendedMax?: number
}

const BLACK_PITCH_CLASSES = new Set([1, 3, 6, 8, 10])

function isBlack(note: number) {
  return BLACK_PITCH_CLASSES.has(note % 12)
}

const VisualizerKeyboard = memo(function VisualizerKeyboard({
  firstNote = 21,
  keyCount = 88,
  accentColor,
  recommendedMin,
  recommendedMax,
}: VisualizerKeyboardProps) {
  const activeNotes = useLiveNotes()
  const active = useMemo(() => new Set(activeNotes), [activeNotes])
  const layout = useMemo(() => {
    const notes = Array.from({ length: keyCount }, (_, index) => firstNote + index)
    const whiteNotes = notes.filter((note) => !isBlack(note))
    const blackWidth = 58 / whiteNotes.length
    const blackNotes = notes.filter(isBlack).map((note) => ({
      note,
      left: (whiteNotes.filter((whiteNote) => whiteNote < note).length / whiteNotes.length) * 100 - blackWidth / 2,
      width: blackWidth,
    }))
    return { whiteNotes, blackNotes }
  }, [firstNote, keyCount])
  const rangeClass = (note: number) => (
    recommendedMin !== undefined && recommendedMax !== undefined
      && (note < recommendedMin || note > recommendedMax)
      ? 'is-out-of-range'
      : ''
  )

  return (
    <div
      className="visualizer-keyboard"
      role="img"
      aria-label={`${keyCount} 键钢琴可视化`}
      data-key-count={keyCount}
      style={{ '--visualizer-accent': accentColor } as CSSProperties}
    >
      <div className="visualizer-white-keys" style={{ gridTemplateColumns: `repeat(${layout.whiteNotes.length}, minmax(0, 1fr))` }}>
        {layout.whiteNotes.map((note) => (
          <span
            className={`visualizer-white-key ${active.has(note) ? 'is-active' : ''} ${rangeClass(note)}`}
            style={active.has(note) ? { backgroundColor: accentColor } : undefined}
            key={note}
          >
            {note % 12 === 0 && <small>{noteLabel(note)}</small>}
          </span>
        ))}
      </div>
      {layout.blackNotes.map(({ note, left, width }) => (
        <span
          className={`visualizer-black-key ${active.has(note) ? 'is-active' : ''} ${rangeClass(note)}`}
          style={{ left: `${left}%`, width: `${width}%`, ...(active.has(note) ? { backgroundColor: accentColor } : {}) }}
          key={note}
        />
      ))}
    </div>
  )
})

export default VisualizerKeyboard
