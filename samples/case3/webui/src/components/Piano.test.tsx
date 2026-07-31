import { fireEvent, render, screen } from '@testing-library/react'
import { vi } from 'vitest'
import Piano from './Piano'

describe('Piano', () => {
  it('renders the 32-key MIDIPLUS layout from F3 through C6', () => {
    render(<Piano octave={4} keyCount={32} velocity={100} activeNotes={[]} onNoteOn={() => undefined} onNoteOff={() => undefined} />)
    const piano = screen.getByLabelText('触控钢琴')
    expect(piano).toHaveAttribute('data-key-count', '32')
    expect(piano).toHaveAttribute('data-white-key-count', '19')
    expect(piano.style.getPropertyValue('--piano-keyboard-aspect-ratio')).toBe('19 / 6.521739130434782')
    expect(piano.style.getPropertyValue('--piano-reference-width')).toBe('1254px')
    expect(screen.getAllByRole('button')).toHaveLength(32)
    const blackKeyWidth = Number.parseFloat(screen.getByRole('button', { name: 'F#3' }).style.width)
    expect(blackKeyWidth).toBeCloseTo(((11 / 23) / 19) * 100, 10)
    expect(screen.getByRole('button', { name: 'F3' })).toBeVisible()
    expect(screen.getByRole('button', { name: 'C6' })).toBeVisible()
  })

  it('sends paired note events for pointer input', () => {
    const noteOn = vi.fn()
    const noteOff = vi.fn()
    render(
      <Piano
        octave={4}
        velocity={96}
        activeNotes={[]}
        onNoteOn={noteOn}
        onNoteOff={noteOff}
      />,
    )

    const c4 = screen.getByRole('button', { name: 'C4' })
    fireEvent.pointerDown(c4, { pointerId: 1 })
    fireEvent.pointerUp(c4, { pointerId: 1 })

    expect(noteOn).toHaveBeenCalledWith(60, 96)
    expect(noteOff).toHaveBeenCalledWith(60)
  })

  it('does not release a held note when the parent supplies a new callback', () => {
    const firstNoteOff = vi.fn()
    const secondNoteOff = vi.fn()
    const { rerender } = render(
      <Piano octave={4} velocity={96} activeNotes={[]} onNoteOn={() => undefined} onNoteOff={firstNoteOff} />,
    )

    fireEvent.pointerDown(screen.getByRole('button', { name: 'C4' }), { pointerId: 1 })
    rerender(
      <Piano octave={4} velocity={96} activeNotes={[60]} onNoteOn={() => undefined} onNoteOff={secondNoteOff} />,
    )

    expect(firstNoteOff).not.toHaveBeenCalled()
    expect(secondNoteOff).not.toHaveBeenCalled()
    fireEvent.pointerUp(screen.getByRole('button', { name: 'C4' }), { pointerId: 1 })
    expect(secondNoteOff).toHaveBeenCalledWith(60)
  })

  it('releases held notes before the visible keyboard range changes', () => {
    const noteOn = vi.fn()
    const noteOff = vi.fn()
    const { rerender } = render(
      <Piano octave={4} firstNote={60} keyCount={32} velocity={96} activeNotes={[]} onNoteOn={noteOn} onNoteOff={noteOff} />,
    )

    fireEvent.pointerDown(screen.getByRole('button', { name: 'C4' }), { pointerId: 1 })
    rerender(
      <Piano octave={4} firstNote={72} keyCount={32} velocity={96} activeNotes={[]} onNoteOn={noteOn} onNoteOff={noteOff} />,
    )

    expect(noteOff).toHaveBeenCalledWith(60)
  })

  it('shows active notes and blocks input when disabled', () => {
    const noteOn = vi.fn()
    const { rerender } = render(
      <Piano octave={4} velocity={100} activeNotes={[60]} disabled onNoteOn={noteOn} onNoteOff={() => undefined} />,
    )
    expect(screen.getByRole('button', { name: 'C4' })).toHaveClass('is-active')
    fireEvent.pointerDown(screen.getByRole('button', { name: 'C4' }))
    expect(noteOn).not.toHaveBeenCalled()

    rerender(<Piano octave={4} velocity={100} activeNotes={[]} onNoteOn={noteOn} onNoteOff={() => undefined} />)
    expect(screen.getByRole('button', { name: 'C4' })).not.toHaveClass('is-active')
  })

  it('maps computer keyboard shortcuts and releases them on keyup', () => {
    const noteOn = vi.fn()
    const noteOff = vi.fn()
    render(
      <Piano
        octave={4}
        velocity={88}
        activeNotes={[]}
        keyboardShortcuts={{ a: 60 }}
        onNoteOn={noteOn}
        onNoteOff={noteOff}
      />,
    )

    expect(screen.getByText('A')).toHaveClass('shortcut-label')
    fireEvent.keyDown(window, { key: 'a' })
    fireEvent.keyUp(window, { key: 'a' })
    expect(noteOn).toHaveBeenCalledWith(60, 88)
    expect(noteOff).toHaveBeenCalledWith(60)
  })
})
