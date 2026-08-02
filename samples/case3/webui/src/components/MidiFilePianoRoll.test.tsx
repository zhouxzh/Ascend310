import { fireEvent, render, screen } from '@testing-library/react'
import MidiFilePianoRoll from './MidiFilePianoRoll'
import type { MidiPianoRoll } from '../types'

function fixture(noteCount = 2): MidiPianoRoll {
  return {
    midi_id: 'midi-fixture',
    midi_sha256: 'a'.repeat(64),
    midi_name: 'fixture.mid',
    duration_seconds: 60,
    note_count: noteCount,
    pitch_min: 48,
    pitch_max: 84,
    timing: {
      ticks_per_beat: 480,
      tempo_changes: [{ tick: 0, time_seconds: 0, bpm: 120 }],
      time_signatures: [{ tick: 0, time_seconds: 0, numerator: 4, denominator: 4 }],
    },
    voices: [{
      id: 'voice-1',
      track_index: 0,
      track_name: 'Track 1',
      channel: 1,
      program: 40,
      suggested_instrument_id: 0,
      notes: Array.from({ length: noteCount }, (_, index) => ({
        start_seconds: (index % 600) / 10,
        duration_seconds: index % 2 ? 0.001 : 0.25,
        pitch: 48 + (index % 37),
        velocity: 80,
      })),
    }],
  }
}

describe('MidiFilePianoRoll', () => {
  it('draws large files into three canvas layers without note DOM nodes', () => {
    const { container } = render(<MidiFilePianoRoll data={fixture(10_000)} />)
    expect(screen.getByText('10000 音符 · 1 声部')).toBeVisible()
    expect(container.querySelectorAll('canvas')).toHaveLength(3)
    expect(container.querySelectorAll('[data-note]')).toHaveLength(0)
  })

  it('offers bounded zoom and a mobile-friendly collapse control', () => {
    render(<MidiFilePianoRoll data={fixture()} />)
    expect(screen.getByRole('img', { name: 'MIDI 文件音符时间轴' })).toBeVisible()
    fireEvent.click(screen.getByTitle('放大时间轴'))
    expect(screen.getByText('2×')).toBeVisible()
    fireEvent.click(screen.getByTitle('折叠卷帘'))
    expect(screen.queryByRole('img', { name: 'MIDI 文件音符时间轴' })).not.toBeInTheDocument()
    expect(screen.getByTitle('展开卷帘')).toBeVisible()
  })

  it('exposes playback following and keeps transport controls inside the roll', () => {
    render(
      <MidiFilePianoRoll
        data={fixture()}
        progress={0.25}
        playing
        transport={<button type="button">播放控制</button>}
      />,
    )
    expect(screen.getByText(/播放中/)).toBeVisible()
    expect(screen.getByRole('button', { name: '播放控制' })).toBeVisible()
    expect(screen.getByTitle('关闭自动跟随')).toBeVisible()
    expect(screen.getByRole('region', { name: 'MIDI 文件钢琴卷帘' }).querySelectorAll('canvas')).toHaveLength(3)
  })
})
