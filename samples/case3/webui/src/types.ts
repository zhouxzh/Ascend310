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

export interface LiveMetrics {
  rendered_blocks: number
  played_blocks: number
  underruns: number
  overruns: number
  max_render_ms: number
  buffered_blocks: number
}

export interface LiveStatus {
  running: boolean
  active_notes: number[]
  backend?: string
  metrics?: LiveMetrics
  config?: Record<string, unknown>
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
  live: LiveStatus
  job_count: number
}

export interface MidiFile {
  id: string
  name: string
  size_bytes: number
  uploaded: boolean
  original_name?: string
}

export interface LiveModel {
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

export interface Instrument {
  id: number
  name: string
  verified: boolean
}

export interface Catalog {
  midi_files: MidiFile[]
  live_models: LiveModel[]
  midi_ddsp_models: MidiDdspModel[]
  instruments: Instrument[]
}

export interface AudioDevice {
  id: string
  index: number
  name: string
  host_api: string
  max_output_channels: number
  default_sample_rate: number
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
  audio_peak?: number
  audio_rms?: number
  underruns?: number
  overruns?: number
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
