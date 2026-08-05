import { useEffect, useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import { CircleStop, Mic2, Play } from 'lucide-react'
import { api } from '../api'
import { Field, Metric, Notice, PanelHeader, StatusPill, Stepper } from '../components/ui'
import type { AudioInput, AudioInputTestStatus, SystemStatus } from '../types'

interface Props {
  status: SystemStatus
  audioInputs: AudioInput[]
  onRefresh: () => Promise<void>
  modeControl?: ReactNode
}

function formatDbfs(value: number): string {
  return `${value.toFixed(1)}`
}

function meterPosition(value: number): number {
  return Math.min(Math.max(((value + 60) / 60) * 100, 0), 100)
}

export default function AudioInputTestView({ status, audioInputs, onRefresh, modeControl }: Props) {
  const availableInputs = useMemo(
    () => audioInputs.filter((input) => input.type === 'capture' && input.available),
    [audioInputs],
  )
  const preferredInput = availableInputs[0]
  const [inputId, setInputId] = useState(preferredInput?.id ?? '')
  const [duration, setDuration] = useState(3)
  const [threshold, setThreshold] = useState(-45)
  const [testStatus, setTestStatus] = useState<AudioInputTestStatus>(status.audio_input_test)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const selectedInput = availableInputs.find((input) => input.id === inputId) ?? preferredInput
  const resourceBusy = status.active_owner !== null && status.active_owner !== 'audio-input-test'
  const unavailable = !selectedInput || resourceBusy

  useEffect(() => {
    if (preferredInput && !availableInputs.some((input) => input.id === inputId)) {
      setInputId(preferredInput.id)
    }
  }, [availableInputs, inputId, preferredInput])

  useEffect(() => setTestStatus(status.audio_input_test), [status.audio_input_test])

  useEffect(() => {
    if (!testStatus.running) return
    let cancelled = false
    const timer = window.setInterval(async () => {
      try {
        const next = await api.audioInputTestStatus()
        if (cancelled) return
        setTestStatus(next)
        if (!next.running) await onRefresh()
      } catch (cause) {
        if (!cancelled) setError(cause instanceof Error ? cause.message : String(cause))
      }
    }, 250)
    return () => {
      cancelled = true
      window.clearInterval(timer)
    }
  }, [onRefresh, testStatus.running])

  async function start() {
    if (!selectedInput) return
    setBusy(true)
    setError('')
    try {
      const next = await api.startAudioInputTest({
        audio_input_id: selectedInput.id,
        duration_seconds: duration,
        threshold_dbfs: threshold,
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
      setTestStatus(await api.stopAudioInputTest())
      await onRefresh()
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause))
    } finally {
      setBusy(false)
    }
  }

  const progress = Math.round(testStatus.progress * 100)
  const currentThreshold = testStatus.running
    ? (testStatus.config.threshold_dbfs ?? threshold)
    : threshold
  const statusTone = testStatus.state === 'failed'
    ? 'error'
    : testStatus.signal_detected
      ? 'ok'
      : testStatus.running
        ? 'warn'
        : 'neutral'
  const stateLabels: Record<typeof testStatus.state, string> = {
    idle: '待机',
    starting: '启动中',
    running: '测试中',
    stopping: '停止中',
    stopped: '已停止',
    succeeded: '已完成',
    failed: '失败',
  }
  const signalLabel = testStatus.signal_detected
    ? '检测到输入'
    : testStatus.running
      ? '等待有效信号'
      : testStatus.state === 'succeeded'
        ? '未检测到信号'
        : stateLabels[testStatus.state]

  return (
    <div className="workspace speaker-workspace speaker-workspace--compact audio-input-test-workspace">
      <section className="panel audio-test-panel audio-input-test-panel">
        <PanelHeader
          title="音频设备测试"
          action={(
            <div className="audio-test-header-actions">
              {modeControl}
            </div>
          )}
        />

        {(error || testStatus.error) && <Notice tone="error">{error || testStatus.error}</Notice>}
        {resourceBusy && <Notice tone="error">音频资源正在被 {status.active_owner} 占用</Notice>}
        <div className="audio-test-body">
          <div className="audio-test-feedback">
            <div className="input-level-monitor">
              <div className="input-level-heading">
                <span><Mic2 size={20} />实时电平</span>
                <strong>{formatDbfs(testStatus.rms_dbfs)} dBFS</strong>
              </div>
              <div
                className={`input-level-meter ${testStatus.signal_detected ? 'has-signal' : ''}`}
                role="meter"
                aria-label="输入电平"
                aria-valuemin={-60}
                aria-valuemax={0}
                aria-valuenow={Math.max(testStatus.rms_dbfs, -60)}
              >
                <div className="input-level-fill" style={{ width: `${meterPosition(testStatus.rms_dbfs)}%` }} />
                <span className="input-level-threshold" style={{ left: `${meterPosition(currentThreshold)}%` }} />
                <span className="input-level-peak" style={{ left: `${meterPosition(testStatus.peak_dbfs)}%` }} />
              </div>
              <div className="input-level-scale" aria-hidden="true">
                <span>-60</span><span>-48</span><span>-36</span><span>-24</span><span>-12</span><span>0 dBFS</span>
              </div>
            </div>

            <div className="speaker-progress" aria-label={`测试进度 ${progress}%`}>
              <div style={{ width: `${progress}%` }} />
            </div>

            <div className="metrics-row speaker-metrics">
              <Metric label="采样率" value={testStatus.sample_rate || selectedInput?.default_sample_rate || 0} unit="Hz" tone="teal" />
              <Metric label="输入声道" value={testStatus.input_channels || selectedInput?.max_input_channels || 0} />
              <Metric label="峰值" value={formatDbfs(testStatus.peak_dbfs)} unit="dBFS" tone={testStatus.signal_detected ? 'teal' : undefined} />
              <Metric label="溢出" value={testStatus.overflows} tone={testStatus.overflows ? 'red' : undefined} />
            </div>
          </div>

          <aside className="audio-test-controls audio-input-test-controls" aria-label="输入测试参数">
            <div className="form-grid audio-test-form audio-input-test-form">
              <Field label="音频输入">
                <select value={inputId} onChange={(event) => setInputId(event.target.value)} disabled={testStatus.running}>
                  {!availableInputs.length && <option value="">无可用 Capture</option>}
                  {availableInputs.map((input) => (
                    <option value={input.id} key={input.id}>{input.name}</option>
                  ))}
                </select>
              </Field>
              <Field label={`检测阈值 ${threshold} dBFS`}>
                <input type="range" min="-80" max="-20" step="1" value={threshold} disabled={testStatus.running} onChange={(event) => setThreshold(Number(event.target.value))} />
              </Field>
              <Field label="持续时间">
                <Stepper value={duration} min={1} max={10} onChange={setDuration} label="输入测试持续秒数" />
              </Field>
              <div className="speaker-actions audio-test-action-row">
                <StatusPill tone={statusTone}>{signalLabel}</StatusPill>
                <button className={`primary-button ${testStatus.running ? 'danger-button' : ''}`} type="button" disabled={busy || (!testStatus.running && unavailable)} onClick={testStatus.running ? stop : start}>
                  {testStatus.running ? <CircleStop size={18} /> : <Play size={18} fill="currentColor" />}
                  {busy ? '处理中' : testStatus.running ? '立即停止' : '开始输入测试'}
                </button>
              </div>
            </div>
          </aside>
        </div>
      </section>
    </div>
  )
}
