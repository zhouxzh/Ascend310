import { useEffect, useMemo, useState } from 'react'
import { AudioLines, CircleStop, Play, Speaker, Timer, Volume2 } from 'lucide-react'
import { api } from '../api'
import { Field, Metric, Notice, PanelHeader, Segmented, StatusPill, Stepper } from '../components/ui'
import type { AudioDevice, SpeakerChannelMode, SpeakerTestStatus, SystemStatus } from '../types'

interface Props {
  status: SystemStatus
  audioDevices: AudioDevice[]
  onRefresh: () => Promise<void>
}

const CHANNEL_OPTIONS: { value: SpeakerChannelMode; label: string }[] = [
  { value: 'left', label: '左声道' },
  { value: 'both', label: '双声道' },
  { value: 'right', label: '右声道' },
]

export default function SpeakerView({ status, audioDevices, onRefresh }: Props) {
  const preferredDevice = useMemo(
    () => audioDevices.find((device) => device.is_default)
      ?? audioDevices.find((device) => device.backend === 'pulse')
      ?? audioDevices.find((device) => device.name.toLowerCase() === 'pulse')
      ?? audioDevices.find((device) => device.name.toLowerCase().includes('default'))
      ?? audioDevices[0],
    [audioDevices],
  )
  const [deviceId, setDeviceId] = useState(preferredDevice?.id ?? '')
  const [channelMode, setChannelMode] = useState<SpeakerChannelMode>('both')
  const [frequency, setFrequency] = useState(440)
  const [level, setLevel] = useState(-18)
  const [duration, setDuration] = useState(3)
  const [testStatus, setTestStatus] = useState<SpeakerTestStatus>(status.speaker_test)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const selectedDevice = audioDevices.find((device) => device.id === deviceId) ?? preferredDevice
  const resourceBusy = status.active_owner !== null && status.active_owner !== 'speaker-test'
  const unavailable = !selectedDevice || resourceBusy
  const activeMode = testStatus.running
    ? (testStatus.config.channel_mode ?? channelMode)
    : channelMode

  useEffect(() => {
    if (preferredDevice && !audioDevices.some((device) => device.id === deviceId)) {
      setDeviceId(preferredDevice.id)
    }
  }, [audioDevices, deviceId, preferredDevice])

  useEffect(() => setTestStatus(status.speaker_test), [status.speaker_test])

  useEffect(() => {
    if (!testStatus.running) return
    let cancelled = false
    const timer = window.setInterval(async () => {
      try {
        const next = await api.speakerTestStatus()
        if (cancelled) return
        setTestStatus(next)
        if (!next.running) await onRefresh()
      } catch (cause) {
        if (!cancelled) setError(cause instanceof Error ? cause.message : String(cause))
      }
    }, 300)
    return () => {
      cancelled = true
      window.clearInterval(timer)
    }
  }, [onRefresh, testStatus.running])

  useEffect(() => {
    if (selectedDevice && selectedDevice.max_output_channels < 2 && channelMode === 'right') {
      setChannelMode('both')
    }
  }, [channelMode, selectedDevice])

  async function start() {
    if (!selectedDevice) return
    setBusy(true)
    setError('')
    try {
      const next = await api.startSpeakerTest({
        audio_device_id: selectedDevice.id,
        channel_mode: channelMode,
        frequency_hz: frequency,
        level_db: level,
        duration_seconds: duration,
      })
      setTestStatus(next)
      await onRefresh()
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause))
    } finally {
      setBusy(false)
    }
  }

  async function stop() {
    setBusy(true)
    setError('')
    try {
      setTestStatus(await api.stopSpeakerTest())
      await onRefresh()
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause))
    } finally {
      setBusy(false)
    }
  }

  const progress = Math.round(testStatus.progress * 100)
  const leftActive = testStatus.running && activeMode !== 'right'
  const rightActive = testStatus.running && activeMode !== 'left'
  const statusTone = testStatus.state === 'failed' ? 'error' : testStatus.running ? 'warn' : testStatus.state === 'succeeded' ? 'ok' : 'neutral'

  return (
    <div className="workspace speaker-workspace">
      <section className="panel speaker-main">
        <PanelHeader
          title="扬声器输出测试"
          subtitle={selectedDevice?.name ?? '未发现音频输出'}
          action={<StatusPill tone={statusTone}>{testStatus.state}</StatusPill>}
        />

        {(error || testStatus.error) && <Notice tone="error">{error || testStatus.error}</Notice>}
        {resourceBusy && <Notice tone="error">音频资源正在被 {status.active_owner} 占用</Notice>}
        {!selectedDevice && <Notice tone="error">未发现可用的音频输出设备</Notice>}

        <div className="speaker-monitor">
          <div className={`speaker-channel ${leftActive ? 'is-active' : ''}`}>
            <Speaker size={58} strokeWidth={1.5} />
            <div className="speaker-level" aria-hidden="true"><i /><i /><i /><i /></div>
            <strong>LEFT</strong>
            <span>左声道</span>
          </div>
          <div className={`speaker-channel ${rightActive ? 'is-active' : ''}`}>
            <Speaker size={58} strokeWidth={1.5} />
            <div className="speaker-level" aria-hidden="true"><i /><i /><i /><i /></div>
            <strong>RIGHT</strong>
            <span>右声道</span>
          </div>
        </div>

        <div className="speaker-progress" aria-label={`测试进度 ${progress}%`}>
          <div style={{ width: `${progress}%` }} />
        </div>

        <div className="metrics-row speaker-metrics">
          <Metric label="采样率" value={testStatus.sample_rate || selectedDevice?.default_sample_rate || 0} unit="Hz" tone="teal" />
          <Metric label="输出声道" value={testStatus.output_channels || selectedDevice?.max_output_channels || 0} />
          <Metric label="剩余时间" value={testStatus.remaining_seconds.toFixed(1)} unit="s" />
          <Metric label="下溢" value={testStatus.underruns} tone={testStatus.underruns ? 'red' : undefined} />
        </div>

        <div className="speaker-actions">
          <button className={`primary-button ${testStatus.running ? 'danger-button' : ''}`} type="button" disabled={busy || (!testStatus.running && unavailable)} onClick={testStatus.running ? stop : start}>
            {testStatus.running ? <CircleStop size={18} /> : <Play size={18} fill="currentColor" />}
            {busy ? '处理中' : testStatus.running ? '立即停止' : '开始测试'}
          </button>
        </div>
      </section>

      <aside className="panel settings-panel speaker-settings">
        <PanelHeader title="输出设置" action={<AudioLines size={18} />} />
        <div className="form-grid single-column">
          <Field label="音频输出">
            <select value={deviceId} onChange={(event) => setDeviceId(event.target.value)} disabled={testStatus.running}>
              {audioDevices.map((device) => (
                <option value={device.id} key={device.id}>
                  {device.name}{device.is_default ? '（默认）' : ''}
                </option>
              ))}
            </select>
          </Field>
          <Field label="测试声道">
            <Segmented value={channelMode} options={CHANNEL_OPTIONS} onChange={setChannelMode} />
          </Field>
          <Field label={`频率 ${frequency} Hz`}>
            <input type="range" min="100" max="2000" step="10" value={frequency} disabled={testStatus.running} onChange={(event) => setFrequency(Number(event.target.value))} />
          </Field>
          <Field label={`音量 ${level} dBFS`}>
            <input type="range" min="-40" max="-3" step="1" value={level} disabled={testStatus.running} onChange={(event) => setLevel(Number(event.target.value))} />
          </Field>
          <Field label="持续时间">
            <Stepper value={duration} min={1} max={10} onChange={setDuration} label="测试持续秒数" />
          </Field>
        </div>
        <div className="settings-footer">
          <Volume2 size={17} />
          <span>{selectedDevice?.host_api ?? 'Audio'}</span>
          <Timer size={17} />
          <span>{duration} seconds</span>
        </div>
      </aside>
    </div>
  )
}
