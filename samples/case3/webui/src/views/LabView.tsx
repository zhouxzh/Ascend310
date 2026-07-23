import { useMemo, useState } from 'react'
import { Activity, BarChart3, Download, FlaskConical, Play, TerminalSquare } from 'lucide-react'
import { Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { api, artifactUrl } from '../api'
import { Metric, Notice, PanelHeader, StatusPill } from '../components/ui'
import type { BenchmarkRow, BenchmarkSummary, Job } from '../types'

interface Props {
  jobs: Job[]
  summary: BenchmarkSummary | null
  onRefresh: () => Promise<void>
}

export default function LabView({ jobs, summary, onRefresh }: Props) {
  const [busy, setBusy] = useState<'runtime' | 'benchmark' | null>(null)
  const [error, setError] = useState('')
  const labJobs = jobs.filter((job) => job.kind === 'runtime-validation' || job.kind === 'benchmark-smoke')
  const latest = labJobs[0]
  const rows = useMemo<BenchmarkRow[]>(() => {
    if (summary?.format !== 'json' || typeof summary.data === 'string') return []
    return summary.data.rows ?? []
  }, [summary])

  async function run(kind: 'runtime' | 'benchmark') {
    setBusy(kind)
    setError('')
    try {
      if (kind === 'runtime') await api.runRuntime()
      else await api.runBenchmark()
      await onRefresh()
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause))
    } finally {
      setBusy(null)
    }
  }

  return (
    <div className="workspace lab-workspace">
      <section className="panel lab-main">
        <PanelHeader title="模型实验" subtitle="Ascend OM · FP16 / Mixed Precision" action={<FlaskConical size={20} />} />
        {error && <Notice tone="error">{error}</Notice>}

        <div className="test-actions">
          <button className="test-action" type="button" disabled={Boolean(busy)} onClick={() => run('runtime')}>
            <span className="test-icon"><Activity size={22} /></span>
            <span><strong>OM 运行验证</strong><small>4 models · 1 inference</small></span>
            <Play size={18} fill="currentColor" />
          </button>
          <button className="test-action" type="button" disabled={Boolean(busy)} onClick={() => run('benchmark')}>
            <span className="test-icon amber"><BarChart3 size={22} /></span>
            <span><strong>短基准测试</strong><small>2 warmup · 5 loops</small></span>
            <Play size={18} fill="currentColor" />
          </button>
        </div>

        <div className="chart-section">
          <div className="section-heading">
            <div><span>SPEED COMPARISON</span><h3>NPU 与端到端耗时</h3></div>
            {summary && <StatusPill tone="ok">已加载</StatusPill>}
          </div>
          {rows.length ? (
            <div className="benchmark-chart">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={rows} margin={{ top: 12, right: 10, left: -12, bottom: 8 }}>
                  <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#dfe3e2" />
                  <XAxis dataKey={(row: BenchmarkRow) => `${row.component === 'expression' ? 'Expression' : 'Synthesis'} · ${row.precision === 'force_fp16' ? 'FP16' : 'Mixed'}`} tick={{ fontSize: 11, fill: '#66706e' }} axisLine={false} tickLine={false} />
                  <YAxis unit=" ms" tick={{ fontSize: 11, fill: '#66706e' }} axisLine={false} tickLine={false} />
                  <Tooltip cursor={{ fill: '#edf0ef' }} contentStyle={{ borderRadius: 6, border: '1px solid #d8dddb' }} />
                  <Legend iconType="circle" wrapperStyle={{ fontSize: 12 }} />
                  <Bar dataKey="npu_median_ms" name="NPU median" fill="#087f73" radius={[3, 3, 0, 0]} isAnimationActive={false} />
                  <Bar dataKey="end_to_end_median_ms" name="End-to-end median" fill="#d08a19" radius={[3, 3, 0, 0]} isAnimationActive={false} />
                </BarChart>
              </ResponsiveContainer>
            </div>
          ) : (
            <div className="empty-chart"><BarChart3 size={28} /><span>暂无结构化基准数据</span></div>
          )}
        </div>

        <div className="metrics-row lab-metrics">
          <Metric label="实验记录" value={labJobs.length} />
          <Metric label="最近状态" value={latest?.state ?? '—'} tone={latest?.state === 'failed' ? 'red' : latest?.state === 'succeeded' ? 'teal' : undefined} />
          <Metric label="进度" value={Math.round((latest?.progress ?? 0) * 100)} unit="%" />
          <Metric label="退出码" value={latest?.exit_code ?? '—'} />
        </div>
      </section>

      <aside className="panel history-panel">
        <PanelHeader title="实验记录" action={<TerminalSquare size={18} />} />
        <div className="job-list">
          {labJobs.length === 0 && <div className="empty-list">暂无实验记录</div>}
          {labJobs.map((job) => (
            <article className="job-row" key={job.id}>
              <div className="job-row-head">
                <div><strong>{job.kind === 'runtime-validation' ? 'OM 运行验证' : '短基准测试'}</strong><small>{new Date(job.created_at).toLocaleString()}</small></div>
                <StatusPill tone={job.state === 'succeeded' ? 'ok' : job.state === 'failed' ? 'error' : 'warn'}>{job.state}</StatusPill>
              </div>
              <div className="progress-track"><span style={{ width: `${job.progress * 100}%` }} /></div>
              <code>{job.message || 'Waiting for output...'}</code>
              {job.artifacts.length > 0 && <div className="artifact-list">{job.artifacts.slice(0, 4).map((artifact) => <a href={artifactUrl(artifact.id)} title={`下载 ${artifact.name}`} key={artifact.id}><Download size={14} />{artifact.name}</a>)}</div>}
            </article>
          ))}
        </div>
      </aside>
    </div>
  )
}
