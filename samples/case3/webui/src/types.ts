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

export type LatencyProfile = 'low' | 'balanced' | 'safe'

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

export interface AudioInputTestConfig {
  audio_input_id?: string
  device_name?: string
  duration_seconds?: number
  threshold_dbfs?: number
}

export interface AudioInputTestStatus {
  running: boolean
  state: 'idle' | 'starting' | 'running' | 'stopping' | 'stopped' | 'succeeded' | 'failed'
  error: string | null
  device_name: string
  sample_rate: number
  input_channels: number
  captured_frames: number
  total_frames: number
  overflows: number
  rms_dbfs: number
  peak_dbfs: number
  signal_detected: boolean
  progress: number
  elapsed_seconds: number
  remaining_seconds: number
  config: AudioInputTestConfig
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
  realtime?: RealtimeStatus
  speaker_test: SpeakerTestStatus
  audio_input_test: AudioInputTestStatus
  job_count: number
}

export type RealtimePatchCategory = 'piano' | 'woodwind' | 'brass' | 'strings' | 'other'

export interface RealtimeParameterMetadata {
  min?: number
  max?: number
  default?: number
  options?: number[]
}

export interface RealtimePatch {
  patch_id: string
  name: string
  category: RealtimePatchCategory
  available: boolean
  pitch_min: number
  pitch_max: number
  polyphony: number
  compatible_audio_device_ids: string[]
  parameters: Record<string, RealtimeParameterMetadata>
  details: {
    engine?: string
    architecture?: string
    quality_status?: string
    model?: string
    precision?: string
    backend?: string
    n_harmonics?: number
    n_noise_bands?: number
  }
}

export interface RealtimeCatalog {
  schema_version: number
  patches: RealtimePatch[]
  audio_devices: AudioDevice[]
  midi_ports: MidiPort[]
  midi_error: string | null
  midi_files: MidiFile[]
  latency_profiles: LatencyProfile[]
}

export interface RealtimePlayerStatus {
  state: 'empty' | 'loaded' | 'paused' | 'playing'
  path?: string | null
  position_seconds: number
  duration_seconds: number
  tempo: number
  loop: boolean
}

export interface RealtimeStatus {
  state: 'stopped' | 'starting' | 'running' | 'switching' | 'stopping' | 'failed'
  running: boolean
  session_id?: string | null
  patch_id?: string | null
  patch?: RealtimePatch | null
  active_notes: number[]
  audio_device_id?: string | null
  latency_profile?: LatencyProfile | null
  player?: RealtimePlayerStatus | null
  recording: { active: boolean; id?: string | null }
  metrics: Record<string, number | boolean | string | null>
  audio?: { device_lost?: boolean; error?: string | null }
  midi?: { connected?: boolean; reconnects?: number; error?: string | null }
  diagnostics: { engine?: string | null; runtime?: Record<string, unknown> }
  last_switch?: {
    ok: boolean
    rolled_back: boolean
    error?: string
    rollback_error?: string
    duration_ms?: number
  } | null
}

export interface MidiFile {
  id: string
  name: string
  sha256?: string
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
  raw_name?: string
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

export interface MidiPianoRollNote {
  start_seconds: number
  duration_seconds: number
  pitch: number
  velocity: number
}

export interface MidiPianoRollVoice {
  id: string
  track_index: number
  track_name: string
  channel: number
  program: number
  suggested_instrument_id: number
  notes: MidiPianoRollNote[]
}

export interface MidiPianoRoll {
  midi_id: string
  midi_sha256: string
  midi_name: string
  duration_seconds: number
  note_count: number
  pitch_min: number
  pitch_max: number
  timing: {
    ticks_per_beat: number
    tempo_changes: { tick: number; time_seconds: number; bpm: number }[]
    time_signatures: {
      tick: number
      time_seconds: number
      numerator: number
      denominator: number
    }[]
  }
  voices: MidiPianoRollVoice[]
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

export interface DdspVstEffectParameterMetadata {
  min: number
  max: number
  default: number
}

export interface DdspVstEffectCatalog {
  available: boolean
  error: string | null
  backend: 'acl/om'
  feature_model: {
    name: string
    sha256: string
    available: boolean
    contract?: Record<string, number[]>
  }
  models: DdspVstModel[]
  audio_inputs: AudioInput[]
  audio_outputs: AudioDevice[]
  default_model_id: string | null
  default_audio_input_id: string | null
  default_audio_output_id: string | null
  parameters: Record<string, DdspVstEffectParameterMetadata>
}

export interface DdspVstEffectMetrics {
  frames: number
  f0_hz: number
  pw_db: number
  input_rms_dbfs: number
  input_peak_dbfs: number
  output_rms_dbfs: number
  output_peak_dbfs: number
  feature_ms: number
  feature_p95_ms: number
  control_ms: number
  control_p95_ms: number
  queue_latency_ms: number
  total_latency_ms: number
  capture_overflows: number
  playback_underruns: number
  clipped_samples: number
  safety_muted: boolean
  gate_open: boolean
  gate_gain: number
  gate_threshold_dbfs: number
  gate_close_threshold_dbfs: number
  gate_hold_frames: number
  gated_frames: number
  noise_floor_dbfs: number
  calibrating: boolean
  calibration_progress: number
  captured_frames?: number
  played_frames?: number
  elapsed_seconds?: number
}

export interface DdspVstEffectStatus {
  state: 'stopped' | 'starting' | 'running' | 'stopping' | 'failed'
  running: boolean
  error: string | null
  backend: 'acl/om'
  feature_backend: 'acl/om'
  control_backend: 'acl/om'
  feature_model: string
  config: {
    model_id?: string
    audio_input_id?: string
    audio_output_id?: string
    input_device_name?: string
    output_device_name?: string
  }
  parameters: Record<string, number>
  hashes: Record<string, string>
  metrics: DdspVstEffectMetrics
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
  backend?: 'pulse' | 'portaudio' | 'alsa_mono'
  sink_name?: string
  max_output_channels: number
  default_sample_rate: number
  is_default?: boolean
  is_bluetooth?: boolean
  is_onboard?: boolean
  is_mono?: boolean
  system_volume_percent?: number | null
  system_volume_db?: number | null
  system_muted?: boolean
  warning?: string
  alsa_device?: string
  alsa_card?: number
  alsa_route_device_id?: number
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

export interface MidiDdspLibraryVersion {
  render_id: string
  source_id: string
  configuration_hash: string
  version_label: string
  state: JobState
  model_bundle_id: string | null
  model_bundle: string | null
  voice_instruments: Record<string, number> | null
  instrument_ids: number[]
  seed: number
  output_gain_db: number
  tail_seconds: number
  sample_rate: number
  reverb: string | null
  available: boolean
  artifact: Artifact
  report_available: boolean
  metadata: Record<string, unknown>
  report: MidiDdspReport | null
  created_at: string
  updated_at: string
}

export interface MidiDdspLibraryTrack {
  source_id: string
  midi_sha256: string
  midi_id: string | null
  display_name: string
  duration_seconds: number
  note_count: number
  track_count: number
  legacy: boolean
  version_count: number
  available_version_count: number
  preferred_render_id: string | null
  default_render_id: string | null
  default_version: MidiDdspLibraryVersion | null
  updated_at: string
}
