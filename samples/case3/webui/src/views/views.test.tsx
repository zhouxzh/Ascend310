import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { vi } from 'vitest'
import { api } from '../api'
import type { Catalog, Job, SpeakerTestStatus, SystemStatus } from '../types'
import DevicesView from './DevicesView'
import LabView from './LabView'
import MidiDdspView from './MidiDdspView'
import PerformView from './PerformView'
import SpeakerView from './SpeakerView'

vi.mock('../api', async () => {
  const actual = await vi.importActual<typeof import('../api')>('../api')
  return {
    ...actual,
    api: {
      ...actual.api,
      controlJob: vi.fn(),
      startMidiDdsp: vi.fn(),
      speakerTestStatus: vi.fn(),
      startSpeakerTest: vi.fn(),
      stopSpeakerTest: vi.fn(),
    },
  }
})

const catalog: Catalog = {
  midi_files: [{
    id: 'midi-1',
    name: 'demo.mid',
    size_bytes: 4,
    uploaded: false,
    note_count: 2,
    track_count: 1,
    max_polyphony: 1,
    duration_seconds: 1,
    monophonic: true,
    midi_ddsp_mode: 'monophonic',
    midi_ddsp_supported: true,
    unsupported_code: null,
    unsupported_reason: null,
    programs: [40],
    tracks: [{ index: 0, name: 'Violin', note_count: 2, max_polyphony: 1, monophonic: true, programs: [40], instrument_id: 0 }],
  }],
  ddsp_vst_models: [{ id: 'ddsp-vst-1', name: 'Violin.om', instrument: 'Violin', backend: 'om', precision: 'mixed_float16', size_bytes: 1 }],
  midi_ddsp_models: [
    { id: 'expression-1', name: 'expression.om', component: 'expression', precision: 'mixed_float16', size_bytes: 1 },
    { id: 'synthesis-1', name: 'synthesis.om', component: 'synthesis', precision: 'mixed_float16', size_bytes: 1 },
  ],
  midi_ddsp_bundles: [{
    id: 'bundle-1',
    name: 'Stateful v2',
    architecture: 'stateful-v2',
    precision: 'mixed_float16',
    recommended: true,
    quality_status: 'candidate',
    source_commit: 'd7af42704a63b47267ae6a1bc0fee1ed7dc5c855',
    seed: 20260724,
    components: { expression: { size_bytes: 1 } },
  }],
  midi_ddsp_reverb_assets: [{ id: 'ir-1', name: 'midi_ddsp_reverb_ir.npz', sha256: 'abc', size_bytes: 1, instrument_count: 13, sample_rate: 16000, samples_per_instrument: 48000 }],
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
  active_owner: 'ddsp-vst',
  ddsp_vst: { running: true, active_notes: [] },
  speaker_test: {
    running: false,
    state: 'idle',
    error: null,
    device_name: '',
    sample_rate: 0,
    output_channels: 0,
    played_frames: 0,
    total_frames: 0,
    underruns: 0,
    progress: 0,
    elapsed_seconds: 0,
    remaining_seconds: 0,
    config: {},
  },
  job_count: 0,
}

const audioDevice = { id: '1', index: 1, name: 'USB Audio', host_api: 'ALSA', max_output_channels: 2, default_sample_rate: 48000 }

const completedSpeakerTest: SpeakerTestStatus = {
  ...status.speaker_test,
  state: 'succeeded',
  progress: 1,
  device_name: audioDevice.name,
  sample_rate: 48000,
  output_channels: 2,
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
    vi.mocked(api.speakerTestStatus).mockResolvedValue(completedSpeakerTest)
    vi.mocked(api.startMidiDdsp).mockResolvedValue({} as Job)
    vi.mocked(api.startSpeakerTest).mockResolvedValue(completedSpeakerTest)
    vi.mocked(api.stopSpeakerTest).mockResolvedValue(completedSpeakerTest)
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
      { event: 'pitch_bend', value: 0 },
    ])
  })

  it('disables realtime start when no audio output exists', () => {
    render(
      <PerformView
        status={{ ...status, active_owner: null, ddsp_vst: { running: false, active_notes: [] } }}
        catalog={catalog}
        audioDevices={[]}
        midiPorts={[]}
        onRefresh={vi.fn()}
      />,
    )
    expect(screen.getByRole('button', { name: '启动 Synth' })).toBeDisabled()
    expect(screen.getByText('未发现可用音频输出。')).toBeVisible()
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

  it('shows MIDI job errors and blocks unsupported polyphonic files', () => {
    const completed: Job = {
      id: 'job-completed',
      kind: 'midi-ddsp-play',
      state: 'succeeded',
      created_at: status.time,
      updated_at: status.time,
      progress: 1,
      message: '',
      exit_code: 0,
      metadata: {},
      artifacts: [],
    }
    const polyphonicCatalog: Catalog = {
      ...catalog,
      midi_files: [{
        ...catalog.midi_files[0],
        id: 'polyphonic',
        name: 'piano.mid',
        monophonic: false,
        max_polyphony: 4,
        midi_ddsp_mode: 'unsupported',
        midi_ddsp_supported: false,
        unsupported_code: 'polyphonic_track',
        unsupported_reason: 'MIDI-DDSP supports monophonic parts; this file contains chords',
      }],
    }
    const { rerender } = render(
      <MidiDdspView catalog={polyphonicCatalog} audioDevices={[]} jobs={[completed]} onRefresh={vi.fn()} />,
    )
    expect(screen.getByText('MIDI-DDSP supports monophonic parts; this file contains chords')).toBeVisible()
    expect(screen.getByTitle('开始')).toBeDisabled()

    rerender(
      <MidiDdspView
        catalog={polyphonicCatalog}
        audioDevices={[]}
        jobs={[{ ...completed, id: 'job-failed', state: 'failed', message: 'MIDI 文件无音符' }]}
        onRefresh={vi.fn()}
      />,
    )
    expect(screen.getByText('MIDI 文件无音符')).toBeVisible()
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

  it('starts a right-channel speaker test with the selected device', async () => {
    const onRefresh = vi.fn().mockResolvedValue(undefined)
    render(
      <SpeakerView
        status={{ ...status, active_owner: null }}
        audioDevices={[audioDevice]}
        onRefresh={onRefresh}
      />,
    )
    fireEvent.click(screen.getByRole('button', { name: '右声道' }))
    fireEvent.click(screen.getByRole('button', { name: '开始测试' }))
    await waitFor(() => expect(api.startSpeakerTest).toHaveBeenCalledWith(expect.objectContaining({
      audio_device_id: '1',
      channel_mode: 'right',
      frequency_hz: 440,
      level_db: -18,
      duration_seconds: 3,
    })))
  })

  it('includes speaker testing in the devices workspace', () => {
    render(
      <DevicesView
        status={{ ...status, active_owner: null }}
        catalog={catalog}
        audioDevices={[audioDevice]}
        speakerOutputs={[audioDevice]}
        audioInputs={[]}
        midiPorts={[]}
        audioError={null}
        audioInputError={null}
        midiError={null}
        onRefresh={vi.fn().mockResolvedValue(undefined)}
      />,
    )
    expect(screen.getByRole('heading', { name: '系统与设备' })).toBeVisible()
    expect(screen.getByRole('heading', { name: '扬声器输出测试' })).toBeVisible()
  })

  it('disables speaker testing when no output device exists', () => {
    render(
      <SpeakerView
        status={{ ...status, active_owner: null }}
        audioDevices={[]}
        onRefresh={vi.fn()}
      />,
    )
    expect(screen.getByRole('button', { name: '开始测试' })).toBeDisabled()
    expect(screen.getByText('未发现可用的音频输出设备')).toBeVisible()
  })
})
