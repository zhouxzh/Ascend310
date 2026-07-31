import { afterEach, describe, expect, it } from 'vitest'
import {
  clearLiveNotes,
  publishLiveNoteEvent,
  publishLiveNotes,
  subscribeLiveNoteEvents,
} from './realtimeLiveNotes'

describe('realtime live notes', () => {
  afterEach(() => {
    clearLiveNotes()
  })

  it('preserves both edges when a note begins and ends before animation-frame commit', () => {
    const events: Array<{ note: number; on: boolean }> = []
    const unsubscribe = subscribeLiveNoteEvents((event) => events.push({ note: event.note, on: event.on }))

    publishLiveNotes([60])
    publishLiveNotes([])

    unsubscribe()
    expect(events).toEqual([
      { note: 60, on: true },
      { note: 60, on: false },
    ])
  })

  it('accepts an immediate browser edge without waiting for a status snapshot', () => {
    const events: Array<{ note: number; on: boolean }> = []
    const unsubscribe = subscribeLiveNoteEvents((event) => events.push({ note: event.note, on: event.on }))

    publishLiveNoteEvent(64, true)
    publishLiveNoteEvent(64, false)

    unsubscribe()
    expect(events).toEqual([
      { note: 64, on: true },
      { note: 64, on: false },
    ])
  })
})
