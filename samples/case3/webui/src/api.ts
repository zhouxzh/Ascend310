import type {
  AudioDevice,
  BluetoothAudioActionResponse,
  BluetoothAudioState,
  AudioInput,
  Catalog,
  Job,
  DdspVstStatus,
  MidiFile,
  MidiVoiceAnalysis,
  MidiPort,
  PianoDdspCatalog,
  PianoDdspStatus,
  SpeakerTestStatus,
  SystemStatus,
} from './types'

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: {
      ...(init?.body && typeof init.body === 'string' ? { 'Content-Type': 'application/json' } : {}),
      ...init?.headers,
    },
  })
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`
    let code: string | undefined
    try {
      const body = await response.json()
      if (typeof body.detail === 'string') detail = body.detail
      else if (body.detail?.message) {
        detail = body.detail.message
        if (typeof body.detail.code === 'string') code = body.detail.code
      }
    } catch {
      // Keep the HTTP status when the response is not JSON.
    }
    const error = new Error(detail) as Error & { code?: string }
    error.code = code
    throw error
  }
  return response.json() as Promise<T>
}

export const api = {
  status: () => request<SystemStatus>('/api/v1/status'),
  catalog: () => request<Catalog>('/api/v1/catalog'),
  audioDevices: () =>
    request<{ available: boolean; devices: AudioDevice[]; error: string | null }>(
      '/api/v1/audio-devices',
    ),
  midiDdspAudioDevices: () =>
    request<{ available: boolean; devices: AudioDevice[]; error: string | null }>(
      '/api/v1/midi-ddsp/audio-devices',
    ),
  audioInputs: () =>
    request<{ available: boolean; devices: AudioInput[]; error: string | null }>(
      '/api/v1/audio-inputs',
    ),
  speakerOutputs: () =>
    request<{ available: boolean; devices: AudioDevice[]; error: string | null }>(
      '/api/v1/speaker-outputs',
    ),
  bluetoothAudio: () => request<BluetoothAudioState>('/api/v1/bluetooth-audio'),
  scanBluetoothAudio: (durationSeconds = 8) =>
    request<BluetoothAudioState>('/api/v1/bluetooth-audio/scan', {
      method: 'POST',
      body: JSON.stringify({ duration_seconds: durationSeconds }),
    }),
  connectBluetoothAudio: (payload: { address: string; pair?: boolean; trust?: boolean }) =>
    request<BluetoothAudioActionResponse>('/api/v1/bluetooth-audio/connect', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  disconnectBluetoothAudio: (address: string) =>
    request<BluetoothAudioActionResponse>('/api/v1/bluetooth-audio/disconnect', {
      method: 'POST',
      body: JSON.stringify({ address }),
    }),
  midiPorts: () =>
    request<{ available: boolean; ports: MidiPort[]; error: string | null }>(
      '/api/v1/midi-ports',
    ),
  jobs: () => request<{ jobs: Job[] }>('/api/v1/jobs'),
  pianoDdspCatalog: () => request<PianoDdspCatalog>('/api/v1/piano-ddsp/catalog'),
  pianoDdspAudioDevices: () =>
    request<{ available: boolean; devices: AudioDevice[]; error: string | null }>(
      '/api/v1/piano-ddsp/audio-devices',
    ),
  pianoDdspStatus: () => request<PianoDdspStatus>('/api/v1/piano-ddsp/status'),
  startPianoDdsp: (payload: Record<string, unknown>) =>
    request<PianoDdspStatus>('/api/v1/piano-ddsp/start', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  stopPianoDdsp: () =>
    request<PianoDdspStatus>('/api/v1/piano-ddsp/stop', { method: 'POST' }),
  panicPianoDdsp: () =>
    request<PianoDdspStatus>('/api/v1/piano-ddsp/panic', { method: 'POST' }),
  updatePianoDdsp: (payload: Record<string, unknown>) =>
    request<PianoDdspStatus>('/api/v1/piano-ddsp/parameters', {
      method: 'PATCH',
      body: JSON.stringify(payload),
    }),
  startDdspVst: (payload: Record<string, unknown>) =>
    request<DdspVstStatus>('/api/v1/ddsp-vst/start', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  stopDdspVst: () => request<DdspVstStatus>('/api/v1/ddsp-vst/stop', { method: 'POST' }),
  speakerTestStatus: () => request<SpeakerTestStatus>('/api/v1/speaker-test/status'),
  startSpeakerTest: (payload: Record<string, unknown>) =>
    request<SpeakerTestStatus>('/api/v1/speaker-test/start', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  stopSpeakerTest: () =>
    request<SpeakerTestStatus>('/api/v1/speaker-test/stop', { method: 'POST' }),
  uploadMidi: async (file: File) =>
    request<MidiFile>(`/api/v1/midi-files?filename=${encodeURIComponent(file.name)}`, {
      method: 'POST',
      body: file,
      headers: { 'Content-Type': 'audio/midi' },
    }),
  midiVoices: (midiId: string) =>
    request<MidiVoiceAnalysis>(
      `/api/v1/midi-files/${encodeURIComponent(midiId)}/voices`,
    ),
  startMidiDdsp: (payload: Record<string, unknown>) =>
    request<Job>('/api/v1/midi-ddsp/jobs', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  playMidiDdspRecording: (jobId: string, payload: Record<string, unknown>) =>
    request<Job>(`/api/v1/midi-ddsp/recordings/${jobId}/play`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  controlJob: (jobId: string, action: 'pause' | 'resume' | 'stop') =>
    request<Job>(`/api/v1/jobs/${jobId}/${action}`, { method: 'POST' }),
}

export function websocketUrl(path: string): string {
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${protocol}//${window.location.host}${path}`
}

export function artifactUrl(id: string): string {
  return `/api/v1/artifacts/${encodeURIComponent(id)}`
}

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KiB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MiB`
}
