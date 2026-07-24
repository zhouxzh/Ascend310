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
}

export interface DdspVstParameters {
  pitch_shift: number
  harmonic_gain: number
  noise_gain: number
  output_gain_db: number
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
  duration_seconds: number
  monophonic: boolean
  midi_ddsp_mode: 'monophonic' | 'multitrack' | 'unsupported' | 'invalid'
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
  programs: number[]
  instrument_id: number | null
}

export interface DdspVstModel {
  id: string
  name: string
  instrument: string
  backend: 'om'
  precision: string
  size_bytes: number
}

export interface MidiDdspModel {
  id: string
  name: string
  component: 'expression' | 'synthesis'
  precision: string
  size_bytes: number
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
  architecture: 'legacy-static-v1' | 'stateful-v2'
  precision: string
  recommended: boolean
  quality_status: string
  source_commit: string
  seed: number
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
  midi_ddsp_models: MidiDdspModel[]
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
  state?: string
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
  cache_hit?: boolean
  cache_key?: string
  inference_and_dsp_wall_seconds?: number
}

export interface Job {
  id: string
  kind: string
  state: JobState
  created_at: string
  updated_at: string
  progress: number
  message: string
  exit_code: number | null
  metadata: Record<string, unknown> & { report?: MidiDdspReport }
  artifacts: Artifact[]
}

export interface BenchmarkRow {
  component: string
  precision: string
  npu_median_ms: number
  end_to_end_median_ms: number
  [key: string]: string | number | boolean
}

export interface BenchmarkSummary {
  name: string
  format: 'json' | 'markdown'
  data: string | { rows?: BenchmarkRow[]; comparisons?: Record<string, unknown>[] }
}
