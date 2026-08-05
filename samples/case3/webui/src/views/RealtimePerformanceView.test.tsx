import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '../api'
import type { RealtimeCatalog, RealtimeStatus } from '../types'
import RealtimePerformanceView, { loadWorkbenchConfig } from './RealtimePerformanceView'

vi.mock('../api', async () => {
  const actual = await vi.importActual<typeof import('../api')>('../api')
  return {
    ...actual,
    api: {
      ...actual.api,
      realtimeCatalog: vi.fn(),
      realtimeStatus: vi.fn(),
      startRealtime: vi.fn(),
      switchRealtime: vi.fn(),
      stopRealtime: vi.fn(),
      panicRealtime: vi.fn(),
      updateRealtime: vi.fn(),
      uploadMidi: vi.fn(),
    },
  }
})

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

  send(value: string) {
    this.sent.push(value)
  }

  close() {
    this.readyState = 3
  }

  emit(value: object) {
    this.onmessage?.({ data: JSON.stringify(value) } as MessageEvent)
  }
}

const sockets: FakeWebSocket[] = []
const pianoPatch = {
  patch_id: 'piano.paper-ir',
  name: 'Concert Grand',
  category: 'piano' as const,
  available: true,
  pitch_min: 21,
  pitch_max: 108,
  polyphony: 16,
  compatible_audio_device_ids: ['shared'],
  parameters: {
    velocity_curve: { min: 0.25, max: 2, default: 1 },
    transpose: { min: -24, max: 24, default: 0 },
    output_gain_db: { min: -60, max: 6, default: 0 },
    reverb: { min: 0, max: 1, default: 1 },
  },
  details: { engine: 'piano-ddsp' },
}
const violinPatch = {
  patch_id: 'neural.violin',
  name: 'Violin',
  category: 'strings' as const,
  available: true,
  pitch_min: 55,
  pitch_max: 88,
  polyphony: 4,
  compatible_audio_device_ids: ['shared'],
  parameters: {
    velocity_curve: { min: 0.25, max: 2, default: 0.55 },
    transpose: { min: -24, max: 24, default: 0 },
    output_gain_db: { min: -60, max: 6, default: 0 },
    reverb: { min: 0, max: 1, default: 0 },
    harmonic_gain: { min: 0, max: 1, default: 1 },
    noise_gain: { min: 0, max: 1, default: 1 },
    attack: { min: 0.1, max: 3, default: 0.1 },
    decay: { min: 0, max: 3, default: 0 },
    sustain: { min: 0, max: 1, default: 1 },
    release: { min: 0.1, max: 5, default: 1.2 },
    input_pitch: { min: -0.5, max: 0.5, default: 0 },
    input_gain: { min: -0.5, max: 0.5, default: 0 },
    reverb_size: { min: 0, max: 1, default: 0.4 },
    reverb_damping: { min: 0, max: 1, default: 0.1 },
  },
  details: { engine: 'ddsp-vst' },
}
const catalog: RealtimeCatalog = {
  schema_version: 1,
  patches: [pianoPatch, violinPatch],
  audio_devices: [{ id: 'shared', index: 1, name: 'USB Audio', host_api: 'ALSA', max_output_channels: 2, default_sample_rate: 48000, is_default: true }],
  midi_ports: [],
  midi_error: null,
  midi_files: [],
  latency_profiles: ['low', 'balanced', 'safe'],
}
const stopped: RealtimeStatus = {
  state: 'stopped',
  running: false,
  active_notes: [],
  recording: { active: false },
  metrics: { midi_to_pcm_p95_ms: 18.4, npu_p95_ms: 11.3 },
  diagnostics: {},
}
const runningPiano: RealtimeStatus = {
  ...stopped,
  state: 'running',
  running: true,
  patch_id: pianoPatch.patch_id,
  patch: pianoPatch,
  audio_device_id: 'shared',
  active_notes: [60],
  recording: { active: false },
}

describe('RealtimePerformanceView', () => {
  beforeEach(() => {
    sockets.length = 0
    localStorage.clear()
    vi.stubGlobal('WebSocket', FakeWebSocket)
    vi.mocked(api.realtimeCatalog).mockResolvedValue(catalog)
    vi.mocked(api.realtimeStatus).mockResolvedValue(stopped)
    vi.mocked(api.startRealtime).mockResolvedValue(runningPiano)
    vi.mocked(api.switchRealtime).mockResolvedValue({
      ...runningPiano,
      patch_id: violinPatch.patch_id,
      patch: violinPatch,
      last_switch: { ok: true, rolled_back: false, duration_ms: 245 },
    })
  })

  afterEach(() => {
    vi.clearAllMocks()
    vi.unstubAllGlobals()
  })

  it('starts the MIDI keyboard workspace with its 32-key default and selected input port', async () => {
    render(<RealtimePerformanceView onRefresh={vi.fn().mockResolvedValue(undefined)} />)

    expect((await screen.findAllByText('音乐会三角钢琴'))[0]).toBeVisible()
    expect(screen.queryByText('Concert Grand')).not.toBeInTheDocument()
    expect(screen.getByRole('img', { name: '动态钢琴卷帘' })).toBeVisible()
    expect(screen.getByRole('img', { name: '32 键钢琴可视化' })).toBeVisible()
    expect(screen.getByRole('button', { name: '使用 32 键' })).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByLabelText('实体 MIDI 输入')).toBeVisible()
    expect(screen.getByRole('combobox', { name: '当前音色' })).toBeVisible()
    expect(screen.getByRole('button', { name: '4 秒时间窗' })).toHaveAttribute('aria-pressed', 'true')
    expect(screen.queryByText('Piano-DDSP')).not.toBeInTheDocument()
    expect(screen.queryByText('DDSP-VST')).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /开始演奏/ }))

    await waitFor(() => expect(api.startRealtime).toHaveBeenCalledWith(expect.objectContaining({
      patch_id: 'piano.paper-ir',
      audio_device_id: 'shared',
      midi_port: null,
      latency_profile: 'balanced',
    })))
  })

  it('changes the live roll history window without changing the session', async () => {
    render(<RealtimePerformanceView onRefresh={vi.fn().mockResolvedValue(undefined)} />)
    await screen.findAllByText('音乐会三角钢琴')

    fireEvent.click(screen.getByRole('button', { name: '8 秒时间窗' }))
    expect(screen.getByRole('button', { name: '8 秒时间窗' })).toHaveAttribute('aria-pressed', 'true')
    expect(api.startRealtime).not.toHaveBeenCalled()
  })

  it('uses the shared workbench controls in MIDI mode while retaining its physical input controls', async () => {
    render(<RealtimePerformanceView onRefresh={vi.fn().mockResolvedValue(undefined)} />)
    await screen.findAllByText('音乐会三角钢琴')

    expect(screen.getByLabelText('实时演奏控制台')).toBeVisible()
    expect(screen.getByRole('combobox', { name: '当前音色' })).toBeVisible()
    expect(screen.getByRole('combobox', { name: '音频输出' })).toBeVisible()
    expect(screen.getByRole('combobox', { name: '延时档位' })).toBeVisible()
    const midiInput = screen.getByRole('combobox', { name: '实体 MIDI 输入' })
    expect(midiInput).toBeVisible()
    expect(midiInput.closest('.keyboard-range-bar')).toBeTruthy()
    expect(midiInput.closest('.midi-keyboard-port-control')).toBeTruthy()
    expect(screen.getByRole('slider', { name: '输出增益' })).toBeVisible()
    expect(screen.getByRole('slider', { name: '混响' })).toBeVisible()
    expect(screen.getByRole('slider', { name: '移调' })).toBeVisible()
    expect(screen.getByRole('slider', { name: '力度曲线' })).toBeVisible()
    expect(screen.getByLabelText('实时性能')).toBeVisible()
    expect(screen.getByRole('button', { name: '录音' })).toBeVisible()
    expect(screen.getByRole('button', { name: '监听' })).toBeVisible()
    expect(screen.queryByRole('tab', { name: 'MIDI 文件' })).not.toBeInTheDocument()
    expect(screen.queryByRole('tab', { name: '额外音色参数' })).not.toBeInTheDocument()
  })

  it('keeps the large interactive keyboard in touch input mode', async () => {
    render(<RealtimePerformanceView inputMode="touch" onRefresh={vi.fn().mockResolvedValue(undefined)} />)
    await screen.findAllByText('音乐会三角钢琴')

    expect(screen.getByLabelText('实时演奏控制台')).toBeVisible()
    expect(screen.getByRole('tab', { name: '触摸屏' })).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByRole('tab', { name: 'MIDI 键盘' })).toHaveAttribute('aria-selected', 'false')
    const pianoSelector = screen.getByRole('combobox', { name: '当前音色' })
    expect(pianoSelector).toBeVisible()
    expect(pianoSelector.querySelectorAll('option')).toHaveLength(1)
    expect(screen.queryByRole('option', { name: /小提琴/ })).not.toBeInTheDocument()
    expect(screen.getByRole('combobox', { name: '音频输出' })).toBeVisible()
    expect(screen.getByRole('combobox', { name: '延时档位' })).toBeVisible()
    expect(screen.getByLabelText('会话状态')).toBeVisible()
    expect(screen.getByLabelText('实时性能')).toBeVisible()
    expect(screen.getByText('按键 P95')).toBeVisible()
    expect(screen.getByText('18.4 ms')).toBeVisible()
    expect(screen.getByText('NPU P95')).toBeVisible()
    expect(screen.getByText('监听丢弃')).toBeVisible()
    expect(screen.getByRole('slider', { name: '输出增益' })).toHaveValue('0')
    expect(screen.getByRole('slider', { name: '混响' })).toHaveValue('1')
    expect(screen.getByRole('slider', { name: '力度曲线' })).toHaveValue('1')
    expect(document.querySelector('.touch-dial-face')).not.toBeInTheDocument()
    expect(screen.getByRole('slider', { name: '触控力度' })).toHaveValue('96')
    expect(screen.getByRole('slider', { name: '移调' })).toHaveValue('0')
    expect(screen.getByRole('slider', { name: '弯音' })).toHaveValue('0')
    expect(screen.getByRole('button', { name: /延音/ })).toHaveAttribute('aria-pressed', 'false')
    const touchStage = screen.getByRole('region', { name: '触控实时演奏' })
    const settingsDrawer = touchStage.parentElement?.querySelector('.stage-drawer')
    expect(settingsDrawer).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: '录音' })).toBeVisible()
    expect(screen.getByRole('button', { name: '监听' })).toBeVisible()
    expect(screen.getByLabelText('触控钢琴')).toBeVisible()
    expect(screen.getByLabelText('触控钢琴')).toHaveAttribute('data-key-count', '25')
    expect(screen.queryByRole('img', { name: /键钢琴可视化/ })).not.toBeInTheDocument()
    expect(screen.getByRole('img', { name: '动态钢琴卷帘' })).toBeVisible()
    expect(screen.getByRole('button', { name: '4 秒时间窗' })).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByRole('button', { name: '使用中键盘' })).toHaveAttribute('aria-pressed', 'true')
    fireEvent.click(screen.getByRole('button', { name: '使用大键盘' }))
    expect(screen.getByRole('button', { name: '使用大键盘' })).toHaveAttribute('aria-pressed', 'true')
    expect(screen.queryByLabelText('实体 MIDI 输入')).not.toBeInTheDocument()
  })

  it('shows the cached patch catalog while a remount refresh is still pending', async () => {
    const first = render(<RealtimePerformanceView inputMode="touch" onRefresh={vi.fn().mockResolvedValue(undefined)} />)
    await screen.findAllByText('音乐会三角钢琴')
    first.unmount()

    let finishRefresh: ((value: RealtimeCatalog) => void) | undefined
    vi.mocked(api.realtimeCatalog).mockReturnValueOnce(new Promise((resolve) => {
      finishRefresh = resolve
    }))
    const second = render(<RealtimePerformanceView inputMode="midi" onRefresh={vi.fn().mockResolvedValue(undefined)} />)

    expect(screen.getAllByText('音乐会三角钢琴')[0]).toBeVisible()
    expect(screen.queryByText('正在加载统一音色库')).not.toBeInTheDocument()

    await act(async () => finishRefresh?.(catalog))
    second.unmount()
  })

  it('keeps MIDI file playback and extra tone controls out of the realtime workspace', async () => {
    render(<RealtimePerformanceView onRefresh={vi.fn().mockResolvedValue(undefined)} />)
    await screen.findAllByText('音乐会三角钢琴')

    expect(screen.queryByRole('tab', { name: 'MIDI 文件' })).not.toBeInTheDocument()
    expect(screen.queryByRole('tab', { name: '额外音色参数' })).not.toBeInTheDocument()
    expect(screen.queryByRole('tab', { name: '录音监听' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: '录音' })).toBeVisible()
    expect(screen.getByRole('button', { name: '监听' })).toBeVisible()
  })

  it('switches input mode while retaining independent touch and MIDI keyboard ranges', async () => {
    render(<RealtimePerformanceView inputMode="touch" onRefresh={vi.fn().mockResolvedValue(undefined)} />)
    await screen.findAllByText('音乐会三角钢琴')

    expect(screen.queryByRole('button', { name: '使用 32 键' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '使用 88 键' })).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '使用 13 键' }))
    expect(screen.getByLabelText('触控钢琴')).toHaveAttribute('data-key-count', '13')
    expect(screen.getByText('C4–C5')).toBeVisible()

    fireEvent.click(screen.getByRole('tab', { name: 'MIDI 键盘' }))
    await screen.findByRole('region', { name: 'MIDI 键盘实时演奏' })

    expect(screen.queryByRole('button', { name: '使用 13 键' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '使用 25 键' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: '使用 32 键' })).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByRole('img', { name: '32 键钢琴可视化' })).toHaveAttribute('data-key-count', '32')
    expect(screen.getByText('F2–C5')).toBeVisible()

    fireEvent.click(screen.getByRole('button', { name: '向低音区移动一个八度' }))
    expect(screen.getByText('F1–C4')).toBeVisible()
    expect(screen.getByRole('button', { name: '向低音区移动一个八度' })).toBeDisabled()

    fireEvent.click(screen.getByRole('button', { name: '向高音区移动一个八度' }))
    fireEvent.click(screen.getByRole('button', { name: '向高音区移动一个八度' }))
    expect(screen.getByText('F3–C6')).toBeVisible()

    fireEvent.click(screen.getByRole('button', { name: '使用 61 键' }))
    expect(screen.getByRole('img', { name: '61 键钢琴可视化' })).toHaveAttribute('data-key-count', '61')
    expect(screen.getByText('C2–C7')).toBeVisible()

    fireEvent.click(screen.getByRole('button', { name: '使用 88 键' }))
    expect(screen.getByRole('img', { name: '88 键钢琴可视化' })).toHaveAttribute('data-key-count', '88')
    expect(screen.getByText('A0–C8')).toBeVisible()
    expect(screen.queryByRole('button', { name: '向低音区移动一个八度' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '向高音区移动一个八度' })).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('tab', { name: '触摸屏' }))
    await screen.findByRole('region', { name: '触控实时演奏' })
    expect(screen.getByLabelText('触控钢琴')).toHaveAttribute('data-key-count', '13')
    expect(screen.getByText('C4–C5')).toBeVisible()
  })

  it('locks the input mode while a realtime session is running', async () => {
    vi.mocked(api.realtimeStatus).mockResolvedValue(runningPiano)
    render(<RealtimePerformanceView inputMode="touch" onRefresh={vi.fn().mockResolvedValue(undefined)} />)
    await screen.findAllByText('音乐会三角钢琴')

    expect(screen.getByRole('tab', { name: '触摸屏' })).toBeDisabled()
    expect(screen.getByRole('tab', { name: 'MIDI 键盘' })).toBeDisabled()
  })

  it('keeps both input modes on the same Piano-DDSP patch catalog', async () => {
    render(<RealtimePerformanceView onRefresh={vi.fn().mockResolvedValue(undefined)} />)

    await screen.findAllByText('音乐会三角钢琴')
    expect(screen.getByRole('combobox', { name: '当前音色' }).querySelectorAll('option')).toHaveLength(1)
    expect(screen.queryByRole('option', { name: 'Violin' })).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('tab', { name: '触摸屏' }))
    await screen.findByRole('region', { name: '触控实时演奏' })
    expect(screen.getByRole('combobox', { name: '当前音色' }).querySelectorAll('option')).toHaveLength(1)
    expect(screen.queryByRole('option', { name: 'Violin' })).not.toBeInTheDocument()
  })

  it('locks other patches while recording', async () => {
    vi.mocked(api.realtimeStatus).mockResolvedValue({
      ...runningPiano,
      recording: { active: true, id: 'take-1' },
    })
    render(<RealtimePerformanceView onRefresh={vi.fn().mockResolvedValue(undefined)} />)

    await screen.findAllByText('音乐会三角钢琴')
    expect(screen.getByRole('combobox', { name: '当前音色' })).toBeDisabled()
  })

  it('updates the running session gain in dB without restarting it', async () => {
    vi.mocked(api.realtimeStatus).mockResolvedValue(runningPiano)
    vi.mocked(api.updateRealtime).mockResolvedValue(runningPiano)
    render(<RealtimePerformanceView onRefresh={vi.fn().mockResolvedValue(undefined)} />)

    const gain = await screen.findByRole('slider', { name: '输出增益' })
    expect((gain as HTMLInputElement).value).toBe('0')
    fireEvent.change(gain, { target: { value: '3' } })
    expect(screen.getByText('+3.0 dB')).toBeVisible()
    await waitFor(
      () => expect(api.updateRealtime).toHaveBeenCalledWith({ output_gain_db: 3 }),
      { timeout: 1_000 },
    )
    expect(api.startRealtime).not.toHaveBeenCalled()
  })

  it('plays Piano-DDSP monitor blocks that use the worker pcm field', async () => {
    const start = vi.fn()
    const connect = vi.fn()
    const createBuffer = vi.fn(() => ({
      duration: 0.004,
      getChannelData: vi.fn(() => new Float32Array(2)),
    }))
    const createBufferSource = vi.fn(() => ({ buffer: null, connect, start }))
    class FakeAudioContext {
      currentTime = 0
      destination = {}
      createBuffer = createBuffer
      createBufferSource = createBufferSource
      resume = vi.fn().mockResolvedValue(undefined)
    }
    vi.stubGlobal('AudioContext', FakeAudioContext)
    vi.mocked(api.realtimeStatus).mockResolvedValue(runningPiano)
    render(<RealtimePerformanceView onRefresh={vi.fn().mockResolvedValue(undefined)} />)

    await screen.findAllByText('音乐会三角钢琴')
    fireEvent.click(screen.getByRole('button', { name: '监听' }))
    await waitFor(() => expect(sockets[0].sent.map((value) => JSON.parse(value))).toContainEqual({ event: 'monitor', enabled: true }))

    act(() => sockets[0].emit({
      event: 'monitor',
      pcm: 'AAAAAAAAAAA=',
      sample_rate: 48000,
    }))

    expect(createBuffer).toHaveBeenCalledWith(2, 1, 48000)
    expect(connect).toHaveBeenCalled()
    expect(start).toHaveBeenCalled()
  })

  it('sends all-notes-off when the page loses focus', async () => {
    vi.mocked(api.realtimeStatus).mockResolvedValue(runningPiano)
    render(<RealtimePerformanceView onRefresh={vi.fn().mockResolvedValue(undefined)} />)
    await screen.findAllByText('音乐会三角钢琴')
    await screen.findByText('演奏中')

    act(() => window.dispatchEvent(new Event('blur')))
    const sent = sockets.flatMap((socket) => socket.sent.map((value) => JSON.parse(value)))
    expect(sent).toContainEqual({ event: 'all_notes_off' })
  })

  it('does not report a stop error when the idle page loses focus', async () => {
    render(<RealtimePerformanceView onRefresh={vi.fn().mockResolvedValue(undefined)} />)
    await screen.findAllByText('音乐会三角钢琴')

    act(() => window.dispatchEvent(new Event('blur')))
    const sent = sockets.flatMap((socket) => socket.sent.map((value) => JSON.parse(value)))
    expect(sent).not.toContainEqual({ event: 'all_notes_off' })
  })

  it('falls back when the versioned local configuration is invalid', () => {
    expect(loadWorkbenchConfig({ getItem: () => '{invalid' })).toBeNull()
    expect(loadWorkbenchConfig({ getItem: () => JSON.stringify({ version: 1, patchParameters: {} }) })).toBeNull()
  })

})
