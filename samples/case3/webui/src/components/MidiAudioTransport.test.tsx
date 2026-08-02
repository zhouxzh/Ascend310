import { fireEvent, render, screen } from '@testing-library/react'
import { vi } from 'vitest'
import MidiAudioTransport from './MidiAudioTransport'

describe('MidiAudioTransport', () => {
  it('forwards browser playback time to the piano roll without a waveform', async () => {
    const onProgress = vi.fn()
    const onPlayingChange = vi.fn()
    const play = vi.spyOn(HTMLMediaElement.prototype, 'play').mockImplementation(function playMedia(this: HTMLMediaElement) {
      this.dispatchEvent(new Event('play'))
      return Promise.resolve()
    })
    const pause = vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(function pauseMedia(this: HTMLMediaElement) {
      this.dispatchEvent(new Event('pause'))
    })

    const { container } = render(
      <MidiAudioTransport
        artifact={{ id: 'render--output.wav', name: 'output.wav', size_bytes: 1024 }}
        onProgress={onProgress}
        onPlayingChange={onPlayingChange}
      />,
    )
    const audio = container.querySelector('audio')!
    Object.defineProperty(audio, 'duration', { configurable: true, value: 20 })
    Object.defineProperty(audio, 'currentTime', { configurable: true, value: 5, writable: true })
    fireEvent.loadedMetadata(audio)
    fireEvent.timeUpdate(audio)

    expect(onProgress).toHaveBeenLastCalledWith(0.25)
    expect(container.querySelector('.waveform')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: /^浏览器播放$/ })).toBeEnabled()
    fireEvent.change(screen.getByRole('slider', { name: '浏览器播放位置' }), { target: { value: '10' } })
    expect(onProgress).toHaveBeenLastCalledWith(0.5)
    fireEvent.click(screen.getByRole('button', { name: '循环播放' }))
    expect(screen.getByRole('button', { name: '关闭循环播放' })).toHaveAttribute('aria-pressed', 'true')
    expect(audio.loop).toBe(true)
    fireEvent.click(screen.getByRole('button', { name: /^浏览器播放$/ }))
    expect(play).toHaveBeenCalledTimes(1)
    expect(onPlayingChange).toHaveBeenLastCalledWith(true)
    expect(screen.getByText('0:10 / 0:20')).toBeVisible()

    fireEvent.click(screen.getByRole('button', { name: '暂停浏览器播放' }))
    expect(pause).toHaveBeenCalledTimes(1)
    expect(onPlayingChange).toHaveBeenLastCalledWith(false)
  })
})
