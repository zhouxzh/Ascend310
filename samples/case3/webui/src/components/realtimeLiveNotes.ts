import { useSyncExternalStore } from 'react'

const EMPTY_NOTES: readonly number[] = []

let notesSnapshot: readonly number[] = EMPTY_NOTES
let observedNotes: readonly number[] = EMPTY_NOTES
let pendingSnapshot: readonly number[] | null = null
let frameId: number | null = null
const listeners = new Set<() => void>()
const noteEventListeners = new Set<(event: LiveNoteEvent) => void>()

export interface LiveNoteEvent {
  note: number
  on: boolean
  at: number
}

function normalizeNotes(notes: readonly number[]): readonly number[] {
  const unique = new Set<number>()
  for (const value of notes) {
    const note = Math.round(Number(value))
    if (Number.isInteger(note) && note >= 0 && note <= 127) unique.add(note)
  }
  return unique.size === 0 ? EMPTY_NOTES : Array.from(unique).sort((a, b) => a - b)
}

function sameNotes(left: readonly number[], right: readonly number[]) {
  if (left === right || left.length !== right.length) return left === right
  return left.every((note, index) => note === right[index])
}

function eventTimestamp() {
  return typeof performance === 'undefined' ? Date.now() : performance.now()
}

function hasNote(notes: readonly number[], note: number) {
  return notes.includes(note)
}

function commitPendingNotes() {
  frameId = null
  const next = pendingSnapshot
  pendingSnapshot = null
  if (!next || sameNotes(notesSnapshot, next)) return
  notesSnapshot = next
  for (const listener of listeners) listener()
}

function queueNotesSnapshot(next: readonly number[]) {
  pendingSnapshot = next
  if (frameId !== null) return
  if (typeof window === 'undefined') {
    commitPendingNotes()
    return
  }
  frameId = window.requestAnimationFrame(commitPendingNotes)
}

export function publishLiveNotes(notes: readonly number[]) {
  const next = normalizeNotes(notes)
  const previous = observedNotes
  observedNotes = next
  for (const note of previous) {
    if (!hasNote(next, note)) publishLiveNoteEvent(note, false)
  }
  for (const note of next) {
    if (!hasNote(previous, note)) publishLiveNoteEvent(note, true)
  }
  queueNotesSnapshot(next)
}

export function publishLiveNoteEvent(noteValue: number, on: boolean) {
  const [note] = normalizeNotes([noteValue])
  if (note === undefined) return
  const active = pendingSnapshot ?? notesSnapshot
  const next = on
    ? normalizeNotes([...active, note])
    : active.filter((value) => value !== note)
  queueNotesSnapshot(next)
  const event: LiveNoteEvent = { note, on: Boolean(on), at: eventTimestamp() }
  for (const listener of noteEventListeners) listener(event)
}

export function clearLiveNotes() {
  const active = pendingSnapshot ?? notesSnapshot
  const notes = new Set([...observedNotes, ...active])
  observedNotes = EMPTY_NOTES
  for (const note of notes) publishLiveNoteEvent(note, false)
  queueNotesSnapshot(EMPTY_NOTES)
}

export function subscribeLiveNoteEvents(listener: (event: LiveNoteEvent) => void) {
  noteEventListeners.add(listener)
  return () => {
    noteEventListeners.delete(listener)
  }
}

function subscribe(listener: () => void) {
  listeners.add(listener)
  return () => listeners.delete(listener)
}

function getSnapshot() {
  return notesSnapshot
}

export function useLiveNotes() {
  return useSyncExternalStore(subscribe, getSnapshot, getSnapshot)
}
