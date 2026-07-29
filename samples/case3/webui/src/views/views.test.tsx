import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { vi } from 'vitest'
import { api } from '../api'
import type {
  Catalog,
  Job,
  MidiVoiceAnalysis,
  SpeakerTestStatus,
  SystemStatus,
} from '../types'
import DevicesView from './DevicesView'
import MidiDdspView, { buildVoiceAssignments, selectableMidiDdspBundles } from './MidiDdspView'
import PerformView from './PerformView'
import SpeakerView from './SpeakerView'

vi.mock('../api', async () => {
  const actual = await vi.importActual<typeof import('../api')>('../api')
  return {
    ...actual,
    api: {
      ...actual.api,
      controlJob: vi.fn(),
      bluetoothAudio: vi.fn(),
      scanBluetoothAudio: vi.fn(),
      connectBluetoothAudio: vi.fn(),
      disconnectBluetoothAudio: vi.fn(),
      startDdspVst: vi.fn(),
      stopDdspVst: vi.fn(),
      midiVoices: vi.fn(),
      startMidiDdsp: vi.fn(),
      playMidiDdspRecording: vi.fn(),
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
    voice_count: 1,
    duration_seconds: 1,
    monophonic: true,
    midi_ddsp_mode: 'monophonic',
    midi_ddsp_supported: true,
    unsupported_code: null,
    unsupported_reason: null,
    programs: [40],
    tracks: [{ index: 0, name: 'Violin', note_count: 2, max_polyphony: 1, monophonic: true, programs: [40], instrument_id: 0 }],
  }],
  ddsp_vst_models: [{ id: 'ddsp-vst-1', name: 'Violin.om', instrument: 'Violin', backend: 'om', precision: 'mixed_float16', size_bytes: 1, pitch_min_note: 56.63, pitch_max_note: 74.44, pitch_min_hz: 215.39, pitch_max_hz: 602.44 }],
  midi_ddsp_bundles: [{
    id: 'bundle-1',
    name: 'Stateful v2',
    architecture: 'stateful-v2',
    precision: 'origin',
    recommended: true,
    quality_status: 'om_validated',
    source_commit: 'd7af42704a63b47267ae6a1bc0fee1ed7dc5c855',
    seed: 20260724,
    components: { expression: { size_bytes: 1 } },
  }],
  midi_ddsp_reverb_assets: [{ id: 'ir-1', name: 'midi_ddsp_reverb_ir.npz', sha256: 'abc', size_bytes: 1, instrument_count: 13, sample_rate: 16000, samples_per_instrument: 48000 }],
  instruments: [
    { id: 0, name: 'Violin', verified: true },
    { id: 4, name: 'Flute', verified: true },
  ],
}

const status: SystemStatus = {
  time: '2026-07-23T00:00:00Z',
  hostname: 'board',
  primary_ip: '192.168.1.42',
  ip_addresses: ['192.168.1.42'],
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
const tinyMidiPort = {
  id: 'raw:/dev/snd/midiC1D0',
  index: 0,
  name: 'MIDIPLUS TINY',
  port: 'raw:/dev/snd/midiC1D0',
  backend: 'raw' as const,
  manufacturer: 'MIDIPLUS',
  model: 'TINY',
  key_count: 32,
}
const bluetoothAudioDevice = {
  id: 'pulse:bluez_sink.C8_24_78_D5_B2_9E.a2dp_sink',
  index: 2,
  name: 'EDIFIER M16 Pro',
  host_api: 'PulseAudio',
  backend: 'pulse' as const,
  sink_name: 'bluez_sink.C8_24_78_D5_B2_9E.a2dp_sink',
  max_output_channels: 2,
  default_sample_rate: 44100,
  is_bluetooth: true,
}
const bluetoothSpeaker = {
  address: 'C8:24:78:D5:B2:9E',
  name: 'EDIFIER M16 Pro',
  alias: 'EDIFIER M16 Pro',
  icon: 'audio-card',
  paired: false,
  bonded: false,
  trusted: false,
  blocked: false,
  connected: false,
  rssi: -48,
  uuids: ['Audio Sink (0000110b-0000-1000-8000-00805f9b34fb)'],
  is_audio: true,
  status: 'available' as const,
}

const completedSpeakerTest: SpeakerTestStatus = {
  ...status.speaker_test,
  state: 'succeeded',
  progress: 1,
  device_name: audioDevice.name,
  sample_rate: 48000,
  output_channels: 2,
}

function voiceAnalysis(voiceCount = 1, midiName = 'demo.mid'): MidiVoiceAnalysis {
  return {
    analysis_id: 'a'.repeat(64),
    algorithm: {
      id: 'partitura-chew-wu-contig-v1',
      name: 'Chew/Wu Contig Mapping',
      upstream: 'CPJKU/partitura',
      version: '1.9.0',
      commit: '427ff875bd5a49a0eec894fdd7c6631ed7f597ea',
      source: 'https://github.com/CPJKU/partitura',
      source_sha256: '32d9af3ccc16c75efdf7679ddb810e0b5080cbb459495481dd5205bdbb640eb8',
      license: 'Apache-2.0',
    },
    midi_name: midiName,
    note_count: voiceCount * 2,
    group_count: 1,
    voice_count: voiceCount,
    groups: [{
      id: 'track-0-channel-1-program-40',
      track_index: 0,
      track_name: 'Strings',
      channel: 1,
      program: 40,
      note_count: voiceCount * 2,
      max_polyphony: voiceCount,
      detected_instrument_id: 0,
      detected_instrument: 'Violin',
      voices: Array.from({ length: voiceCount }, (_, index) => ({
        id: `track-0-channel-1-program-40-voice-${index + 1}`,
        voice_index: index,
        track_index: 0,
        track_name: 'Strings',
        channel: 1,
        program: 40,
        note_count: 2,
        start_seconds: 0,
        end_seconds: 1,
        pitch_min: 72 - index * 8,
        pitch_max: 76 - index * 8,
        pitch_median: 74 - index * 8,
        detected_instrument_id: 0,
        detected_instrument: 'Violin',
        suggested_instrument_id: index === 0 ? 0 : Math.min(index, 3),
        suggested_instrument: index === 0 ? 'Violin' : 'Strings',
        suggestion_source: 'midi_program' as const,
      })),
    }],
  }
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
    vi.mocked(api.bluetoothAudio).mockResolvedValue({
      available: true,
      controller: {
        address: '00:11:22:33:44:55',
        name: 'ascend8t',
        powered: true,
        discovering: false,
        pairable: true,
        discoverable: false,
      },
      devices: [],
      error: null,
    })
    vi.mocked(api.scanBluetoothAudio).mockResolvedValue({
      available: true,
      controller: null,
      devices: [],
      error: null,
    })
    vi.mocked(api.connectBluetoothAudio).mockResolvedValue({
      device: { ...bluetoothSpeaker, connected: true, status: 'connected' },
      profile: { selected: 'a2dp_sink', error: null },
    })
    vi.mocked(api.disconnectBluetoothAudio).mockResolvedValue({
      device: bluetoothSpeaker,
      profile: { selected: null, error: null },
    })
    vi.mocked(api.startDdspVst).mockResolvedValue({ running: true, active_notes: [] })
    vi.mocked(api.stopDdspVst).mockResolvedValue({ running: false, active_notes: [] })
    vi.mocked(api.midiVoices).mockImplementation(async (midiId) => (
      voiceAnalysis(midiId === 'polyphonic' ? 4 : 1, midiId === 'polyphonic' ? 'piano.mid' : 'demo.mid')
    ))
    vi.mocked(api.speakerTestStatus).mockResolvedValue(completedSpeakerTest)
    vi.mocked(api.startMidiDdsp).mockResolvedValue({} as Job)
    vi.mocked(api.playMidiDdspRecording).mockResolvedValue({} as Job)
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

  it('shows the active MIDIPLUS TINY 32-key controller profile', async () => {
    render(
      <PerformView
        status={{ ...status, ddsp_vst: { ...status.ddsp_vst, config: { midi_port: tinyMidiPort.port } } }}
        catalog={catalog}
        audioDevices={[audioDevice]}
        midiPorts={[tinyMidiPort]}
        onRefresh={vi.fn()}
      />,
    )

    await waitFor(() => expect(screen.getByLabelText('MIDI 输入')).toHaveValue(tinyMidiPort.port))
    expect(screen.getByText('MIDIPLUS TINY', { selector: '.keyboard-profile strong' })).toBeVisible()
    expect(screen.getByText('32键 · F3-C6')).toBeVisible()
    expect(screen.getByLabelText('触控钢琴')).toHaveAttribute('data-key-count', '32')
  })

  it('disables realtime start when no audio output exists', () => {
    render(
      <PerformView
        status={{ ...status, active_owner: null, ddsp_vst: { running: false, active_notes: [] } }}
        catalog={catalog}
        audioDevices={[]}
        audioError="当前只有板载 3.5 mm 单声道兼容路径；DDSP-VST 需要 USB、蓝牙或其他可用立体声输出。"
        midiPorts={[]}
        onRefresh={vi.fn()}
      />,
    )
    expect(screen.getByRole('button', { name: '启动 Synth' })).toBeDisabled()
    expect(screen.getByText(/当前只有板载 3.5 mm 单声道兼容路径/)).toBeVisible()
  })

  it('uses bluetooth-safe settings when starting realtime synth on a bluetooth output', async () => {
    const onRefresh = vi.fn().mockResolvedValue(undefined)
    render(
      <PerformView
        status={{ ...status, active_owner: null, ddsp_vst: { running: false, active_notes: [] } }}
        catalog={catalog}
        audioDevices={[audioDevice, bluetoothAudioDevice]}
        midiPorts={[]}
        onRefresh={onRefresh}
      />,
    )
    fireEvent.change(screen.getByLabelText('音频输出'), { target: { value: bluetoothAudioDevice.id } })
    expect(screen.getByText(/蓝牙输出已切换为更高缓冲/)).toBeVisible()
    fireEvent.click(screen.getByRole('button', { name: '启动 Synth' }))
    await waitFor(() => expect(api.startDdspVst).toHaveBeenCalledWith(expect.objectContaining({
      audio_device_id: bluetoothAudioDevice.id,
      sample_rate: 44100,
      latency_profile: 'balanced',
      output_gain_db: -18,
      velocity_curve: 0.55,
      attack: 0.02,
    })))
    expect(screen.getByRole('button', { name: '低延时' })).toBeDisabled()
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

  it('allows stateful polyphonic MIDI and explains voice partitioning', async () => {
    const analysis = voiceAnalysis(4, 'piano.mid')
    const completed: Job = {
      id: 'job-completed',
      kind: 'midi-ddsp-render',
      state: 'succeeded',
      created_at: status.time,
      updated_at: status.time,
      progress: 1,
      message: '',
      exit_code: 0,
      metadata: {
        midi_id: 'polyphonic',
        midi_name: 'piano.mid',
        model_bundle_id: 'bundle-1',
        instrument_id: 0,
        seed: 20260724,
        mode: 'render',
        sample_rate: 48000,
        output_gain_db: 0,
        tail_seconds: 2,
      },
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
        voice_count: 4,
        midi_ddsp_mode: 'polyphonic',
        midi_ddsp_supported: true,
        unsupported_code: null,
        unsupported_reason: null,
      }],
    }
    const { rerender } = render(
      <MidiDdspView catalog={polyphonicCatalog} audioDevices={[]} jobs={[completed]} onRefresh={vi.fn()} />,
    )
    expect(screen.getByText(/Chew\/Wu Contig Mapping 拆分为严格单音声部/)).toBeVisible()
    expect(await screen.findByText('4 个声部')).toBeVisible()
    expect(screen.getByRole('button', { name: '开始渲染' })).toBeEnabled()
    expect(screen.queryByText('完成后由开发板播放')).not.toBeInTheDocument()

    fireEvent.change(screen.getByLabelText('全部设置为'), { target: { value: '4' } })
    expect(screen.getAllByText('Flute').length).toBeGreaterThan(0)
    fireEvent.click(screen.getByRole('button', { name: '开始渲染' }))
    await waitFor(() => expect(api.startMidiDdsp).toHaveBeenCalledWith(expect.objectContaining({
      mode: 'render',
      force_render: true,
      instrument_id: 4,
      voice_analysis_id: analysis.analysis_id,
      voice_instruments: Object.fromEntries(
        analysis.groups[0].voices.map((voice) => [voice.id, 4]),
      ),
    })))
    const renderPayload = vi.mocked(api.startMidiDdsp).mock.calls.at(-1)?.[0]
    expect(renderPayload).not.toHaveProperty('audio_device_id')
    expect(renderPayload).not.toHaveProperty('audio_latency_ms')
    expect(renderPayload).not.toHaveProperty('prebuffer')

    rerender(
      <MidiDdspView
        catalog={polyphonicCatalog}
        audioDevices={[]}
        jobs={[{
          ...completed,
          id: 'job-failed',
          state: 'failed',
          message: 'MIDI 文件无音符',
          metadata: {
            ...completed.metadata,
            instrument_id: 4,
            voice_analysis_id: analysis.analysis_id,
            voice_instruments: Object.fromEntries(
              analysis.groups[0].voices.map((voice) => [voice.id, 4]),
            ),
          },
        }]}
        onRefresh={vi.fn()}
      />,
    )
    expect(screen.getByText('MIDI 文件无音符')).toBeVisible()
  })

  it('maps ranked voices across all four roles in each Google preset', () => {
    const analysis = voiceAnalysis(7, 'canon.mid')
    expect(Object.values(buildVoiceAssignments(analysis, 'strings'))).toEqual([
      0, 0, 0, 1, 1, 2, 2,
    ])
    expect(Object.values(buildVoiceAssignments(analysis, 'woodwinds'))).toEqual([
      4, 5, 5, 6, 6, 8, 8,
    ])
    expect(Object.values(buildVoiceAssignments(analysis, 'brass'))).toEqual([
      9, 10, 10, 11, 11, 12, 12,
    ])
  })

  it('hides a legacy bundle covered by the recommended batched bundle', () => {
    const recommended = {
      ...catalog.midi_ddsp_bundles[0],
      voice_batch_sizes: [1, 2, 4],
    }
    const legacy = {
      ...recommended,
      id: 'bundle-legacy',
      name: 'Stateful v2 legacy',
      recommended: false,
      voice_batch_sizes: [1],
    }
    expect(selectableMidiDdspBundles([recommended, legacy])).toEqual([recommended])

    render(
      <MidiDdspView
        catalog={{ ...catalog, midi_ddsp_bundles: [recommended, legacy] }}
        audioDevices={[]}
        jobs={[]}
        onRefresh={vi.fn()}
      />,
    )
    expect(screen.queryByRole('combobox', { name: '模型包' })).not.toBeInTheDocument()
    expect(screen.getByText(recommended.name, { selector: 'output' })).toBeVisible()
  })

  it('keeps generated WAV files available when render inputs change', () => {
    const alternate = {
      ...catalog.midi_files[0],
      id: 'midi-2',
      name: 'alternate.mid',
    }
    const completed: Job = {
      id: 'job-completed',
      kind: 'midi-ddsp-play',
      state: 'succeeded',
      created_at: status.time,
      updated_at: status.time,
      progress: 1,
      message: '',
      exit_code: 0,
      metadata: {
        midi_id: 'midi-1',
        midi_name: 'demo.mid',
        model_bundle_id: 'bundle-1',
        instrument_id: 0,
        seed: 20260724,
        mode: 'render',
        sample_rate: 48000,
        output_gain_db: 0,
        tail_seconds: 2,
      },
      artifacts: [{ id: 'job-completed--output.wav', name: 'output.wav', size_bytes: 4096 }],
    }
    render(
      <MidiDdspView
        catalog={{ ...catalog, midi_files: [catalog.midi_files[0], alternate] }}
        audioDevices={[]}
        jobs={[completed]}
        onRefresh={vi.fn()}
      />,
    )
    expect(screen.getByRole('heading', { name: 'MIDI-DDSP 音频库' })).toBeVisible()
    expect(screen.getByText('已生成音频')).toBeVisible()
    fireEvent.click(screen.getByRole('button', { name: '当前浏览器' }))
    expect(document.querySelector('audio')).toHaveAttribute(
      'src',
      '/api/v1/artifacts/job-completed--output.wav',
    )
    fireEvent.click(screen.getByRole('button', { name: '新建渲染' }))
    expect(document.querySelector('audio')).not.toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('曲目'), { target: { value: 'midi-2' } })
    fireEvent.click(screen.getByRole('button', { name: '音频库' }))
    expect(document.querySelector('audio')).toHaveAttribute(
      'src',
      '/api/v1/artifacts/job-completed--output.wav',
    )
  })

  it('plays a generated WAV through the selected board output', async () => {
    const completed: Job = {
      id: 'job-completed',
      kind: 'midi-ddsp-render',
      state: 'succeeded',
      created_at: status.time,
      updated_at: status.time,
      progress: 1,
      message: '',
      exit_code: 0,
      metadata: { midi_name: 'demo.mid', instrument_id: 0 },
      artifacts: [{ id: 'job-completed--output.wav', name: 'output.wav', size_bytes: 4096 }],
    }
    render(
      <MidiDdspView catalog={catalog} audioDevices={[audioDevice]} jobs={[completed]} onRefresh={vi.fn()} />,
    )
    expect(document.querySelector('audio')).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '当前浏览器' }))
    expect(document.querySelector('audio')).toHaveAttribute(
      'src',
      '/api/v1/artifacts/job-completed--output.wav',
    )
    expect(screen.queryByRole('button', { name: '开发板播放' })).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '开发板喇叭' }))
    expect(screen.getByLabelText('开发板音频输出')).toHaveValue(audioDevice.id)
    expect(screen.queryByRole('option', { name: '系统默认' })).not.toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('开发板音频输出'), { target: { value: audioDevice.id } })
    fireEvent.click(screen.getByRole('button', { name: '开发板播放' }))
    await waitFor(() => expect(api.playMidiDdspRecording).toHaveBeenCalledWith(
      'job-completed',
      { audio_device_id: audioDevice.id, latency_ms: 40, output_gain_db: 0 },
    ))
  })

  it('keeps the audio library selectable while a board WAV is playing', async () => {
    const first: Job = {
      id: 'job-first',
      kind: 'midi-ddsp-render',
      state: 'succeeded',
      created_at: status.time,
      updated_at: status.time,
      progress: 1,
      message: '',
      exit_code: 0,
      metadata: { midi_name: 'first.mid', instrument_id: 0 },
      artifacts: [{ id: 'job-first--output.wav', name: 'output.wav', size_bytes: 4096 }],
    }
    const second: Job = {
      ...first,
      id: 'job-second',
      metadata: { midi_name: 'second.mid', instrument_id: 1 },
      artifacts: [{ id: 'job-second--output.wav', name: 'output.wav', size_bytes: 8192 }],
    }
    const playback: Job = {
      ...first,
      id: 'job-playback',
      kind: 'midi-ddsp-wav-playback',
      state: 'running',
      progress: 0.25,
      metadata: { source_job_id: first.id },
      artifacts: [],
    }
    const { rerender } = render(
      <MidiDdspView catalog={catalog} audioDevices={[audioDevice]} jobs={[playback, first, second]} onRefresh={vi.fn()} />,
    )

    const firstRow = await screen.findByRole('button', { name: /first\.mid/ })
    const secondRow = screen.getByRole('button', { name: /second\.mid/ })
    expect(firstRow).toHaveTextContent('PLAY')
    await waitFor(() => expect(firstRow).toHaveAttribute('aria-pressed', 'true'))
    expect(secondRow).toBeEnabled()
    fireEvent.click(secondRow)
    expect(secondRow).toHaveAttribute('aria-pressed', 'true')

    rerender(
      <MidiDdspView
        catalog={catalog}
        audioDevices={[audioDevice]}
        jobs={[{ ...playback, progress: 0.5 }, first, second]}
        onRefresh={vi.fn()}
      />,
    )
    expect(screen.getByRole('button', { name: /second\.mid/ })).toHaveAttribute('aria-pressed', 'true')
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

  it('includes speaker testing in the devices workspace', async () => {
    render(
      <DevicesView
        status={{ ...status, active_owner: null }}
        catalog={catalog}
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
    expect(screen.getByText('192.168.1.42')).toBeVisible()
    expect(screen.getByRole('heading', { name: '蓝牙音频' })).toBeVisible()
    expect(screen.getByRole('heading', { name: '扬声器输出测试' })).toBeVisible()
    expect(await screen.findByText('Power On')).toBeVisible()
  })

  it('connects a bluetooth speaker from the devices workspace', async () => {
    vi.mocked(api.bluetoothAudio).mockResolvedValue({
      available: true,
      controller: {
        address: '00:11:22:33:44:55',
        name: 'ascend8t',
        powered: true,
        discovering: false,
        pairable: true,
        discoverable: false,
      },
      devices: [bluetoothSpeaker],
      error: null,
    })
    const onRefresh = vi.fn().mockResolvedValue(undefined)
    render(
      <DevicesView
        status={{ ...status, active_owner: null }}
        catalog={catalog}
        speakerOutputs={[audioDevice]}
        audioInputs={[]}
        midiPorts={[]}
        audioError={null}
        audioInputError={null}
        midiError={null}
        onRefresh={onRefresh}
      />,
    )
    fireEvent.click(await screen.findByTitle('连接蓝牙音频'))
    await waitFor(() => expect(api.connectBluetoothAudio).toHaveBeenCalledWith({
      address: 'C8:24:78:D5:B2:9E',
      pair: true,
      trust: true,
    }))
    expect(onRefresh).toHaveBeenCalled()
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
