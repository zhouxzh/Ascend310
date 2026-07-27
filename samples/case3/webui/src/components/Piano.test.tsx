import { fireEvent, render, screen } from '@testing-library/react'
import { vi } from 'vitest'
import Piano from './Piano'

describe('Piano', () => {
  it('renders the 32-key MIDIPLUS layout from F3 through C6', () => {
    render(<Piano octave={4} keyCount={32} velocity={100} activeNotes={[]} onNoteOn={() => undefined} onNoteOff={() => undefined} />)
    const piano = screen.getByLabelText('触控钢琴')
    expect(piano).toHaveAttribute('data-key-count', '32')
    expect(screen.getAllByRole('button')).toHaveLength(32)
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
})
