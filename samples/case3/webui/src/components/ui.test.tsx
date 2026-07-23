import { fireEvent, render, screen } from '@testing-library/react'
import { vi } from 'vitest'
import { Segmented, Stepper } from './ui'

describe('studio controls', () => {
  it('changes segmented mode', () => {
    const change = vi.fn()
    render(<Segmented value="play" options={[{ value: 'play', label: '播放' }, { value: 'render', label: '渲染' }]} onChange={change} />)
    fireEvent.click(screen.getByRole('button', { name: '渲染' }))
    expect(change).toHaveBeenCalledWith('render')
  })

  it('keeps stepper values inside bounds', () => {
    const change = vi.fn()
    render(<Stepper value={1} min={1} max={2} onChange={change} label="声部" />)
    expect(screen.getByTitle('减小')).toBeDisabled()
    fireEvent.click(screen.getByTitle('增大'))
    expect(change).toHaveBeenCalledWith(2)
  })
})
