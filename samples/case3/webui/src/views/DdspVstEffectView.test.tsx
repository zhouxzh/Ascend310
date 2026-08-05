import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '../api'
import type { DdspVstEffectCatalog, DdspVstEffectStatus } from '../types'
import DdspVstEffectView from './DdspVstEffectView'

vi.mock('../api', async () => {
  const actual = await vi.importActual<typeof import('../api')>('../api')
  return {
    ...actual,
    api: {
      ...actual.api,
      ddspVstEffectCatalog: vi.fn(),
      refreshDdspVstEffectCatalog: vi.fn(),
      ddspVstEffectStatus: vi.fn(),
      startDdspVstEffect: vi.fn(),
      updateDdspVstEffect: vi.fn(),
      stopDdspVstEffect: vi.fn(),
      calibrateDdspVstEffect: vi.fn(),
    },
  }
})

class FakeWebSocket {
  static instances: FakeWebSocket[] = []
  onmessage: ((event: MessageEvent<string>) => void) | null = null

  constructor(_url: string) {
    FakeWebSocket.instances.push(this)
  }

  close() {}

  emit(status: DdspVstEffectStatus) {
    this.onmessage?.({ data: JSON.stringify({ event: 'status', data: status }) } as MessageEvent<string>)
  }
}

const parameters = {
  transpose: { min: -24, max: 24, default: 0 },
  input_pitch: { min: -0.5, max: 0.5, default: 0 },
  input_gain: { min: -0.5, max: 0.5, default: 0 },
  harmonic_gain: { min: 0, max: 1, default: 1 },
  noise_gain: { min: 0, max: 1, default: 1 },
  output_gain_db: { min: -60, max: 6, default: -18 },
  reverb_size: { min: 0, max: 1, default: 0.4 },
  reverb_damping: { min: 0, max: 1, default: 0.1 },
  reverb_wet: { min: 0, max: 1, default: 0 },
  gate_threshold_dbfs: { min: -80, max: -20, default: -40 },
  gate_hysteresis_db: { min: 0, max: 18, default: 6 },
  gate_hold_ms: { min: 0, max: 1000, default: 160 },
  gate_attack_ms: { min: 1, max: 200, default: 10 },
  gate_release_ms: { min: 20, max: 2000, default: 180 },
}

const catalog: DdspVstEffectCatalog = {
  available: true,
  error: null,
  backend: 'acl/om',
  feature_model: { name: 'ddsp_vst_feature_mixed_float16.om', sha256: 'a'.repeat(64), available: true },
  models: Array.from({ length: 11 }, (_, index) => ({
    id: `model-${index}`,
    name: `model-${index}.om`,
    instrument: index === 0 ? 'Violin' : `Tone ${index + 1}`,
    backend: 'om' as const,
    precision: 'mixed_float16',
    size_bytes: 100,
    pitch_min_hz: 180,
    pitch_max_hz: 720,
    power_min_db: -60,
    power_max_db: -20,
  })),
  audio_inputs: [{
    id: 'pulse:ugreen', index: 1, name: 'UGREEN Camera', host_api: 'PulseAudio',
    backend: 'pulse', type: 'capture', max_input_channels: 2,
    default_sample_rate: 48000, state: 'running', available: true,
  }],
  audio_outputs: [{
    id: 'pulse:edifier', index: 2, name: 'EDIFIER M16 Pro', host_api: 'PulseAudio',
    backend: 'pulse', max_output_channels: 2, default_sample_rate: 48000,
  }],
  default_model_id: 'model-0',
  default_audio_input_id: 'pulse:ugreen',
  default_audio_output_id: 'pulse:edifier',
  parameters,
}

function effectStatus(overrides: Partial<DdspVstEffectStatus> = {}): DdspVstEffectStatus {
  return {
    state: 'stopped',
    running: false,
    error: null,
    backend: 'acl/om',
    feature_backend: 'acl/om',
    control_backend: 'acl/om',
    feature_model: 'ddsp_vst_feature_mixed_float16.om',
    config: {},
    parameters: Object.fromEntries(Object.entries(parameters).map(([name, item]) => [name, item.default])),
    hashes: {},
    metrics: {
      frames: 0, f0_hz: 0, pw_db: -96, input_rms_dbfs: -96, input_peak_dbfs: -96,
      output_rms_dbfs: -96, output_peak_dbfs: -96, feature_ms: 0, feature_p95_ms: 0,
      control_ms: 0, control_p95_ms: 0, queue_latency_ms: 0, total_latency_ms: 0,
      capture_overflows: 0, playback_underruns: 0, clipped_samples: 0, safety_muted: false,
      gate_open: false, gate_gain: 0, gate_threshold_dbfs: -40,
      gate_close_threshold_dbfs: -46, gate_hold_frames: 0, gated_frames: 0,
      noise_floor_dbfs: -96, calibrating: false, calibration_progress: 0,
    },
    ...overrides,
  }
}

describe('DDSP-VST Effect workspace', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    FakeWebSocket.instances = []
    vi.stubGlobal('WebSocket', FakeWebSocket)
    vi.mocked(api.ddspVstEffectCatalog).mockResolvedValue(catalog)
    vi.mocked(api.refreshDdspVstEffectCatalog).mockResolvedValue(catalog)
    vi.mocked(api.ddspVstEffectStatus).mockResolvedValue(effectStatus())
    vi.mocked(api.startDdspVstEffect).mockResolvedValue(effectStatus({ state: 'running', running: true }))
    vi.mocked(api.updateDdspVstEffect).mockResolvedValue(effectStatus({ state: 'running', running: true }))
    vi.mocked(api.stopDdspVstEffect).mockResolvedValue(effectStatus())
    vi.mocked(api.calibrateDdspVstEffect).mockResolvedValue(effectStatus({ state: 'running', running: true }))
  })

  afterEach(() => vi.unstubAllGlobals())

  it('shows the OM-only catalog and starts with server-owned IDs', async () => {
    render(<DdspVstEffectView />)
    expect(await screen.findByRole('heading', { name: 'DDSP-VST' })).toBeVisible()
    expect(screen.getByText('FEATURE · ACL/OM')).toBeVisible()
    expect(screen.getByText('CONTROL · ACL/OM')).toBeVisible()
    expect(screen.getByRole('img', { name: 'DDSP-VST 音高与响度轨迹' })).toBeVisible()
    expect(screen.getByRole('option', { name: '小提琴 · 混合半精度' })).toBeVisible()
    expect(screen.queryByRole('option', { name: 'Violin' })).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '启动' }))
    await waitFor(() => expect(api.startDdspVstEffect).toHaveBeenCalledWith(expect.objectContaining({
      model_id: 'model-0', audio_input_id: 'pulse:ugreen', audio_output_id: 'pulse:edifier',
    })))
    expect(await screen.findByRole('button', { name: '停止' })).toBeVisible()
  })

  it('disables start when the board catalog is unavailable', async () => {
    vi.mocked(api.ddspVstEffectCatalog).mockResolvedValue({ ...catalog, available: false, error: 'Feature OM 缺失' })
    render(<DdspVstEffectView />)
    expect(await screen.findByText('Feature OM 缺失')).toBeVisible()
    expect(screen.getByRole('button', { name: '启动' })).toBeDisabled()
  })

  it('applies bounded parameters while running', async () => {
    vi.mocked(api.ddspVstEffectStatus).mockResolvedValue(effectStatus({ state: 'running', running: true }))
    render(<DdspVstEffectView />)
    const harmonic = await screen.findByRole('slider', { name: '谐波' })
    fireEvent.change(harmonic, { target: { value: '0.62' } })
    await waitFor(() => expect(api.updateDdspVstEffect).toHaveBeenCalledWith({ harmonic_gain: 0.62 }))
  })

  it('renders WebSocket fault and safety-mute states explicitly', async () => {
    render(<DdspVstEffectView />)
    await screen.findByRole('heading', { name: 'DDSP-VST' })
    const failed = effectStatus({
      state: 'failed',
      error: '摄像头已断开',
      metrics: { ...effectStatus().metrics, frames: 8, safety_muted: true },
    })
    act(() => FakeWebSocket.instances.at(-1)?.emit(failed))
    expect(await screen.findByText('摄像头已断开')).toBeVisible()
    expect(screen.getAllByText('安全静音').length).toBeGreaterThan(0)
  })

  it('groups official controls and can recalibrate the input gate', async () => {
    vi.mocked(api.ddspVstEffectStatus).mockResolvedValue(effectStatus({ state: 'running', running: true }))
    render(<DdspVstEffectView />)
    await screen.findByRole('heading', { name: 'DDSP-VST' })
    fireEvent.click(screen.getByRole('tab', { name: '输入门' }))
    expect(screen.getByRole('slider', { name: '开启门限' })).toBeVisible()
    fireEvent.click(screen.getByRole('button', { name: '重新校准' }))
    await waitFor(() => expect(api.calibrateDdspVstEffect).toHaveBeenCalledTimes(1))
  })

  it('refreshes the server-owned OM catalog without accepting a file path', async () => {
    render(<DdspVstEffectView />)
    await screen.findByRole('heading', { name: 'DDSP-VST' })
    fireEvent.click(screen.getByRole('button', { name: '刷新已发布 OM 音色' }))
    await waitFor(() => expect(api.refreshDdspVstEffectCatalog).toHaveBeenCalledTimes(1))
  })
})
