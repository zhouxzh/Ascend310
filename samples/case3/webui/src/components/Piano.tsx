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
// Per-contact ownership and slide-to-play follow Magenta AI Jam's KeyboardElement
// at 1146656d0ac54951ddf89486c264582c037f11e4 (Apache-2.0).

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
  const pianoRef = useRef<HTMLDivElement>(null)
  const heldInputs = useRef(new Map<string, number>())
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

  const setPressed = (note: number, pressed: boolean) => {
    pianoRef.current
      ?.querySelector<HTMLElement>(`.piano-key[data-note="${note}"]`)
      ?.classList.toggle('is-pressed', pressed)
  }

  const noteAtPoint = (clientX: number, clientY: number) => {
    const root = pianoRef.current
    const element = document.elementFromPoint(clientX, clientY)
    const key = element instanceof Element ? element.closest<HTMLElement>('.piano-key') : null
    if (!root || !key || !root.contains(key)) return null
    const note = Number(key.dataset.note)
    return Number.isInteger(note) ? note : null
  }

  const releaseInput = (token: string) => {
    const note = heldInputs.current.get(token)
    if (note === undefined) return
    heldInputs.current.delete(token)
    if (![...heldInputs.current.values()].includes(note)) {
      setPressed(note, false)
      noteOffRef.current(note)
    }
  }

  const holdInput = (token: string, note: number) => {
    if (disabledRef.current || heldInputs.current.get(token) === note) return
    releaseInput(token)
    const noteAlreadyHeld = [...heldInputs.current.values()].includes(note)
    heldInputs.current.set(token, note)
    if (!noteAlreadyHeld) {
      setPressed(note, true)
      noteOnRef.current(note, velocityRef.current)
    }
  }

  const moveInput = (token: string, clientX: number, clientY: number) => {
    if (!heldInputs.current.has(token)) return
    const note = noteAtPoint(clientX, clientY)
    if (note === null) releaseInput(token)
    else holdInput(token, note)
  }

  const pressPointer = (note: number, target: HTMLElement, pointerId: number) => {
    holdInput(`pointer:${pointerId}`, note)
    try {
      target.setPointerCapture?.(pointerId)
    } catch {
      // Firefox can reject capture while a touch contact is changing targets.
    }
  }

  useEffect(() => {
    const releaseAll = () => {
      for (const note of new Set(heldInputs.current.values())) {
        setPressed(note, false)
        noteOffRef.current(note)
      }
      heldInputs.current.clear()
    }
    const releaseWhenHidden = () => {
      if (document.hidden) releaseAll()
    }
    window.addEventListener('blur', releaseAll)
    document.addEventListener('visibilitychange', releaseWhenHidden)
    return () => {
      window.removeEventListener('blur', releaseAll)
      document.removeEventListener('visibilitychange', releaseWhenHidden)
      releaseAll()
    }
  }, [])

  useEffect(() => {
    for (const note of new Set(heldInputs.current.values())) {
      setPressed(note, false)
      noteOffRef.current(note)
    }
    heldInputs.current.clear()
  }, [firstNote, keyCount])

  useEffect(() => {
    if (!disabled) return
    for (const token of [...heldInputs.current.keys()]) releaseInput(token)
  }, [disabled])

  useEffect(() => {
    const root = pianoRef.current
    if (!root) return
    const touchStart = (event: TouchEvent) => {
      let handled = false
      for (const touch of Array.from(event.changedTouches)) {
        const note = noteAtPoint(touch.clientX, touch.clientY)
        if (note === null) continue
        holdInput(`touch:${touch.identifier}`, note)
        handled = true
      }
      if (handled) event.preventDefault()
    }
    const touchMove = (event: TouchEvent) => {
      let handled = false
      for (const touch of Array.from(event.changedTouches)) {
        const token = `touch:${touch.identifier}`
        if (!heldInputs.current.has(token)) continue
        moveInput(token, touch.clientX, touch.clientY)
        handled = true
      }
      if (handled) event.preventDefault()
    }
    const touchEnd = (event: TouchEvent) => {
      let handled = false
      for (const touch of Array.from(event.changedTouches)) {
        const token = `touch:${touch.identifier}`
        if (!heldInputs.current.has(token)) continue
        releaseInput(token)
        handled = true
      }
      if (handled) event.preventDefault()
    }
    root.addEventListener('touchstart', touchStart, { passive: false })
    root.addEventListener('touchmove', touchMove, { passive: false })
    root.addEventListener('touchend', touchEnd, { passive: false })
    root.addEventListener('touchcancel', touchEnd, { passive: false })
    return () => {
      root.removeEventListener('touchstart', touchStart)
      root.removeEventListener('touchmove', touchMove)
      root.removeEventListener('touchend', touchEnd)
      root.removeEventListener('touchcancel', touchEnd)
    }
  }, [])

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
      if (note === undefined) return
      event.preventDefault()
      keyboardHeld.set(key, note)
      holdInput(`keyboard:${key}`, note)
    }
    const keyUp = (event: KeyboardEvent) => {
      const key = event.key.toLowerCase()
      const note = keyboardHeld.get(key)
      if (note === undefined) return
      event.preventDefault()
      keyboardHeld.delete(key)
      releaseInput(`keyboard:${key}`)
    }
    window.addEventListener('keydown', keyDown)
    window.addEventListener('keyup', keyUp)
    return () => {
      window.removeEventListener('keydown', keyDown)
      window.removeEventListener('keyup', keyUp)
      for (const key of keyboardHeld.keys()) releaseInput(`keyboard:${key}`)
    }
  }, [])

  return (
    <div
      ref={pianoRef}
      className={`piano ${disabled ? 'is-disabled' : ''}`}
      aria-label="触控钢琴"
      data-key-count={layout.count}
      data-white-key-count={layout.whiteNotes.length}
      onContextMenu={(event) => event.preventDefault()}
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
            data-note={note}
            type="button"
            disabled={disabled}
            aria-label={noteLabel(note)}
            onPointerDown={(event) => pressPointer(note, event.currentTarget, event.pointerId)}
            onPointerMove={(event) => moveInput(`pointer:${event.pointerId}`, event.clientX, event.clientY)}
            onPointerUp={(event) => releaseInput(`pointer:${event.pointerId}`)}
            onPointerCancel={(event) => releaseInput(`pointer:${event.pointerId}`)}
            onLostPointerCapture={(event) => releaseInput(`pointer:${event.pointerId}`)}
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
          data-note={note}
          type="button"
          disabled={disabled}
          aria-label={noteLabel(note)}
          onPointerDown={(event) => pressPointer(note, event.currentTarget, event.pointerId)}
          onPointerMove={(event) => moveInput(`pointer:${event.pointerId}`, event.clientX, event.clientY)}
          onPointerUp={(event) => releaseInput(`pointer:${event.pointerId}`)}
          onPointerCancel={(event) => releaseInput(`pointer:${event.pointerId}`)}
          onLostPointerCapture={(event) => releaseInput(`pointer:${event.pointerId}`)}
        >
          {shortcutLabels.has(note) && <span className="shortcut-label">{shortcutLabels.get(note)}</span>}
        </button>
      ))}
    </div>
  )
}
