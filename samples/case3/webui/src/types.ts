export type JobState =
  | 'queued'
  | 'preparing'
  | 'running'
  | 'paused'
  | 'stopping'
  | 'succeeded'
  | 'failed'
  | 'cancelled'

export interface DependencyMap {
  [name: string]: boolean
}

export interface DdspVstMetrics {
  rendered_blocks: number
  played_blocks: number
  underruns: number
  overruns: number
  max_render_ms: number
  p95_render_ms: number
  buffered_blocks: number
  queue_latency_ms?: number
  device_latency_ms?: number
  pulse_buffer_latency_ms?: number
  sink_latency_ms?: number
  resampler_latency_ms?: number
  audio_path_latency_ms?: number
  estimated_total_latency_ms?: number
  midi_to_render_p95_ms?: number
  write_block_p95_ms?: number
  output_peak?: number
  clipped_samples?: number
  midi_connected?: boolean
  midi_reconnects?: number
  midi_velocity_last?: number
  midi_velocity_min?: number
  midi_velocity_max?: number
  midi_velocity_p50?: number
  midi_velocity_mapped_last?: number
}

export type LatencyProfile = 'low' | 'balanced' | 'safe'

export interface DdspVstParameters {
  pitch_shift: number
  harmonic_gain: number
  noise_gain: number
  output_gain_db: number
  velocity_curve: number
  attack: number
  decay: number
  sustain: number
  release: number
  input_pitch: number
  input_gain: number
  reverb_size: number
  reverb_damping: number
  reverb_wet: number
}

export interface DdspVstStatus {
  running: boolean
  active_notes: number[]
  backend?: string
  metrics?: DdspVstMetrics
  parameters?: DdspVstParameters
  config?: Record<string, unknown>
}

export type SpeakerChannelMode = 'left' | 'both' | 'right'

export interface SpeakerTestConfig {
  audio_device_id?: string
  device_name?: string
  channel_mode?: SpeakerChannelMode
  frequency_hz?: number
  level_db?: number
  duration_seconds?: number
}

export interface SpeakerTestStatus {
  running: boolean
  state: 'idle' | 'starting' | 'running' | 'stopping' | 'stopped' | 'succeeded' | 'failed'
  error: string | null
  device_name: string
  sample_rate: number
  output_channels: number
  played_frames: number
  total_frames: number
  underruns: number
  progress: number
  elapsed_seconds: number
  remaining_seconds: number
  config: SpeakerTestConfig
}

export interface SystemStatus {
  time: string
  hostname: string
  primary_ip: string
  ip_addresses: string[]
  platform: string
  machine: string
  python: string
  python_executable: string
  is_ascend_board: boolean
  dependencies: DependencyMap
  npu: {
    available: boolean
    exit_code: number | null
    output: string
    health_alarm: boolean
  }
  active_owner: string | null
  ddsp_vst: DdspVstStatus
  speaker_test: SpeakerTestStatus
  job_count: number
}

export interface MidiFile {
  id: string
  name: string
  size_bytes: number
  uploaded: boolean
  original_name?: string
  note_count: number
  track_count: number
  max_polyphony: number
  voice_count: number
  duration_seconds: number
  monophonic: boolean
  midi_ddsp_mode: 'monophonic' | 'multitrack' | 'polyphonic' | 'unsupported' | 'invalid'
  midi_ddsp_supported: boolean
  unsupported_code: string | null
  unsupported_reason: string | null
  programs: number[]
  tracks: MidiTrackAnalysis[]
}

export interface MidiTrackAnalysis {
  index: number
  name: string
  note_count: number
  max_polyphony: number
  monophonic: boolean
  channels?: number[]
  programs: number[]
  instrument_id: number | null
}

export interface MidiVoiceSeparationAlgorithm {
  id: string
  name: string
  upstream: string
  version: string
  commit: string
  source: string
  source_sha256: string
  license: string
}

export interface MidiVoiceAnalysisVoice {
  id: string
  voice_index: number
  track_index: number
  track_name: string
  channel: number
  program: number
  note_count: number
  start_seconds: number
  end_seconds: number
  pitch_min: number
  pitch_max: number
  pitch_median: number
  detected_instrument_id: number | null
  detected_instrument: string | null
  suggested_instrument_id: number
  suggested_instrument: string
  suggestion_source: 'midi_program' | 'register_fallback'
}

export interface MidiVoiceAnalysisGroup {
  id: string
  track_index: number
  track_name: string
  channel: number
  program: number
  note_count: number
  max_polyphony: number
  detected_instrument_id: number | null
  detected_instrument: string | null
  voices: MidiVoiceAnalysisVoice[]
}

export interface MidiVoiceAnalysis {
  analysis_id: string
  algorithm: MidiVoiceSeparationAlgorithm
  midi_name: string
  note_count: number
  group_count: number
  voice_count: number
  groups: MidiVoiceAnalysisGroup[]
}

export interface DdspVstModel {
  id: string
  name: string
  instrument: string
  backend: 'om'
  precision: string
  size_bytes: number
  pitch_min_note?: number
  pitch_max_note?: number
  pitch_min_hz?: number
  pitch_max_hz?: number
  power_min_db?: number
  power_max_db?: number
}

export interface MidiDdspBundleComponent {
  file?: string
  name?: string
  sha256?: string
  size_bytes: number
}

export interface MidiDdspBundle {
  id: string
  name: string
  architecture: 'stateful-v2'
  precision: string
  onnx_dtype?: string
  recommended: boolean
  quality_status: string
  source_commit: string
  seed: number
  voice_batch_sizes?: number[]
  components: Record<string, MidiDdspBundleComponent>
}

export interface Instrument {
  id: number
  name: string
  verified: boolean
}

export interface Catalog {
  midi_files: MidiFile[]
  ddsp_vst_models: DdspVstModel[]
  midi_ddsp_bundles: MidiDdspBundle[]
  midi_ddsp_reverb_assets: ReverbAsset[]
  instruments: Instrument[]
}

export interface ReverbAsset {
  id: string
  name: string
  sha256: string
  size_bytes: number
  instrument_count: number
  sample_rate: number
  samples_per_instrument: number
}

export interface AudioDevice {
  id: string
  index: number
  name: string
  host_api: string
  backend?: 'pulse' | 'portaudio'
  sink_name?: string
  max_output_channels: number
  default_sample_rate: number
  is_default?: boolean
  is_bluetooth?: boolean
  state?: string
}

export interface BluetoothController {
  address: string
  name: string
  powered: boolean | null
  discovering: boolean | null
  pairable: boolean | null
  discoverable: boolean | null
}

export type BluetoothAudioDeviceStatus = 'available' | 'paired' | 'connected' | 'blocked'

export interface BluetoothAudioDevice {
  address: string
  name: string
  alias: string
  icon: string
  paired: boolean
  bonded: boolean
  trusted: boolean
  blocked: boolean
  connected: boolean
  rssi: number | null
  uuids: string[]
  is_audio: boolean
  status: BluetoothAudioDeviceStatus
}

export interface BluetoothAudioState {
  available: boolean
  controller: BluetoothController | null
  devices: BluetoothAudioDevice[]
  error: string | null
}

export interface BluetoothAudioActionResponse {
  device: BluetoothAudioDevice
  profile: {
    selected: string | null
    sink?: string | null
    error: string | null
  }
}

export interface AudioInput {
  id: string
  index: number
  name: string
  host_api: string
  backend: 'pulse' | 'portaudio'
  type: 'capture' | 'monitor'
  max_input_channels: number
  default_sample_rate: number
  state: string
  available: boolean
}

export interface MidiPort {
  id: string
  index: number
  name: string
  port?: string
  backend?: 'rtmidi' | 'raw'
  manufacturer?: string
  model?: string
  key_count?: number
}

export interface Artifact {
  id: string
  name: string
  size_bytes: number
}

export interface MidiDdspReport {
  duration_seconds?: number
  expression_inference_count?: number
  synthesis_block_count?: number
  synthesis_render_mean_ms?: number
  synthesis_render_median_ms?: number
  synthesis_render_p95_ms?: number
  synthesis_render_max_ms?: number
  reverb_enabled?: boolean
  reverb_length_samples?: number
  reverb_process_mean_ms?: number
  reverb_process_p95_ms?: number
  dry_peak?: number
  dry_rms?: number
  reverberated_peak?: number
  reverberated_rms?: number
  preclip_peak?: number
  clipped_samples?: number
  audio_peak?: number
  audio_rms?: number
  underruns?: number
  overruns?: number
  architecture?: string
  model_bundle_id?: string
  seed?: number
  max_polyphony?: number
  voice_count?: number
  instrument_id?: number
  instrument_ids?: number[]
  instrument_mode?: 'per_voice' | 'global_fallback'
  voice_analysis_id?: string
  voice_instruments?: Record<string, number>
  voice_separation?: MidiVoiceSeparationAlgorithm
  overload_samples?: number
  mix_gain?: number
  mix_gain_db?: number
  peak_protection_enabled?: boolean
  cache_hit?: boolean
  cache_key?: string
  inference_and_dsp_wall_seconds?: number
  render_wall_seconds?: number
  playback_wall_seconds?: number
  total_wall_seconds?: number
  realtime_factor?: number
  model_load_seconds?: number
  npu_inference_seconds?: number
  dsp_seconds?: number
  resampling_seconds?: number
  write_disk_seconds?: number
  voice_batch_sizes_used?: number[]
  dsp_workers?: number
}

export interface JobProgressDetail {
  stage?: string
  stage_progress?: number
  overall_progress?: number
  completed?: number
  total?: number
  voice_batch_index?: number | null
  voice_batch_count?: number | null
  component?: string | null
  activity?: string | null
  elapsed_seconds?: number
  eta_seconds?: number | null
  heartbeat_at?: number
  paused?: boolean
}

export interface Job {
  id: string
  kind: string
  state: JobState
  created_at: string
  updated_at: string
  progress: number
  progress_detail?: JobProgressDetail | null
  message: string
  exit_code: number | null
  metadata: Record<string, unknown> & { report?: MidiDdspReport }
  artifacts: Artifact[]
}
