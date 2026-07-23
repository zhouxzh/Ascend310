import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { vi } from 'vitest'
import { api } from '../api'
import type { Catalog, Job, SystemStatus } from '../types'
import LabView from './LabView'
import MidiDdspView from './MidiDdspView'
import PerformView from './PerformView'

vi.mock('../api', async () => {
  const actual = await vi.importActual<typeof import('../api')>('../api')
  return {
    ...actual,
    api: {
      ...actual.api,
      controlJob: vi.fn(),
    },
  }
})

const catalog: Catalog = {
  midi_files: [{ id: 'midi-1', name: 'demo.mid', size_bytes: 4, uploaded: false }],
  live_models: [{ id: 'live-1', name: 'Violin.om', instrument: 'Violin', backend: 'om', precision: 'mixed_float16', size_bytes: 1 }],
  midi_ddsp_models: [
    { id: 'expression-1', name: 'expression.om', component: 'expression', precision: 'mixed_float16', size_bytes: 1 },
    { id: 'synthesis-1', name: 'synthesis.om', component: 'synthesis', precision: 'mixed_float16', size_bytes: 1 },
  ],
  instruments: [{ id: 0, name: 'Violin', verified: true }],
}

const status: SystemStatus = {
  time: '2026-07-23T00:00:00Z',
  hostname: 'board',
  platform: 'Linux',
  machine: 'aarch64',
  python: '3.9',
  python_executable: '/usr/bin/python',
  is_ascend_board: true,
  dependencies: {},
  npu: { available: true, exit_code: 0, output: '', health_alarm: false },
  active_owner: 'live-session',
  live: { running: true, active_notes: [] },
  job_count: 0,
}

class FakeWebSocket {
  static OPEN = 1
  readyState = FakeWebSocket.OPEN
  sent: string[] = []
  onopen: (() => void) | null = null
  onmessage: ((event: MessageEvent) => void) | null = null
  onclose: (() => void) | null = null

  constructor() {
    sockets.push(this)
  }

  send(message: string) {
    this.sent.push(message)
  }

  close() {
    this.onclose?.()
  }
}

const sockets: FakeWebSocket[] = []

describe('workspace behavior', () => {
  beforeEach(() => {
    sockets.length = 0
    vi.clearAllMocks()
    vi.stubGlobal('WebSocket', FakeWebSocket)
  })

  afterEach(() => vi.unstubAllGlobals())

  it('sends computer keyboard notes and releases them when focus is lost', async () => {
    render(
      <PerformView
        status={status}
        catalog={catalog}
        audioDevices={[{ id: 'audio-1', index: 1, name: 'USB Audio', host_api: 'ALSA', max_output_channels: 2, default_sample_rate: 48000 }]}
        midiPorts={[]}
        onRefresh={vi.fn()}
      />,
    )
    await waitFor(() => expect(sockets).toHaveLength(1))
    fireEvent.keyDown(window, { key: 'a' })
    fireEvent.keyUp(window, { key: 'a' })
    fireEvent.blur(window)
    expect(sockets[0].sent.map((message) => JSON.parse(message))).toEqual([
      { event: 'note_on', note: 60, velocity: 100 },
      { event: 'note_off', note: 60 },
      { event: 'all_notes_off' },
    ])
  })

  it('disables realtime start when no audio output exists', () => {
    render(
      <PerformView
        status={{ ...status, active_owner: null, live: { running: false, active_notes: [] } }}
        catalog={catalog}
        audioDevices={[]}
        midiPorts={[]}
        onRefresh={vi.fn()}
      />,
    )
    expect(screen.getByRole('button', { name: '启动引擎' })).toBeDisabled()
    expect(screen.getByText('未发现可用的音频输出设备。')).toBeVisible()
  })

  it('shows playback control failures', async () => {
    vi.mocked(api.controlJob).mockRejectedValueOnce(new Error('设备忙'))
    const job: Job = {
      id: 'job-1',
      kind: 'midi-ddsp-play',
      state: 'running',
      created_at: status.time,
      updated_at: status.time,
      progress: 0.5,
      message: '',
      exit_code: null,
      metadata: {},
      artifacts: [],
    }
    render(<MidiDdspView catalog={catalog} audioDevices={[]} jobs={[job]} onRefresh={vi.fn()} />)
    fireEvent.click(screen.getByTitle('停止'))
    expect(await screen.findByText('设备忙')).toBeVisible()
  })

  it('accepts structured benchmark rows for the comparison chart', () => {
    render(
      <LabView
        jobs={[]}
        summary={{
          name: 'summary.json',
          format: 'json',
          data: { rows: [{ component: 'expression', precision: 'force_fp16', npu_median_ms: 6, end_to_end_median_ms: 7 }] },
        }}
        onRefresh={vi.fn()}
      />,
    )
    expect(screen.getByText('已加载')).toBeVisible()
    expect(screen.queryByText('暂无结构化基准数据')).not.toBeInTheDocument()
  })
})
