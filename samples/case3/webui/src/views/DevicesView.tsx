import { useState } from 'react'
import {
  Activity,
  Box,
  CheckCircle2,
  Cpu,
  Server,
  ShieldCheck,
  TriangleAlert,
  XCircle,
} from 'lucide-react'
import { formatBytes } from '../api'
import { audioDeviceLabel } from '../audio'
import { PanelHeader, Segmented, StatusPill } from '../components/ui'
import { modelPrecisionNameZh, timbreNameZh } from '../timbres'
import type { AudioDevice, AudioInput, Catalog, MidiPort, SystemStatus } from '../types'
import BluetoothAudioPanel from './BluetoothAudioPanel'
import AudioInputTestView from './AudioInputTestView'
import SpeakerView from './SpeakerView'

interface Props {
  status: SystemStatus
  catalog: Catalog
  speakerOutputs: AudioDevice[]
  audioInputs: AudioInput[]
  midiPorts: MidiPort[]
  audioError: string | null
  audioInputError: string | null
  midiError: string | null
  onRefresh: () => Promise<void>
}

type DeviceSection = 'overview' | 'audio' | 'runtime'
type DeviceIoSection = 'output' | 'input' | 'midi'
type AudioTestMode = 'output' | 'input'
type StatusTone = 'ok' | 'warn' | 'error' | 'neutral'

function formatSampleRate(sampleRate: number): string {
  if (!sampleRate) {
    return '未知'
  }
  return `${Number.isInteger(sampleRate / 1000) ? sampleRate / 1000 : (sampleRate / 1000).toFixed(1)} kHz`
}

function toneClass(tone: StatusTone): string {
  return `tone-${tone}`
}

export default function DevicesView({
  status,
  catalog,
  speakerOutputs,
  audioInputs,
  midiPorts,
  audioError,
  audioInputError,
  midiError,
  onRefresh,
}: Props) {
  const [section, setSection] = useState<DeviceSection>('overview')
  const [ioSection, setIoSection] = useState<DeviceIoSection>('output')
  const [audioTestMode, setAudioTestMode] = useState<AudioTestMode>('output')
  const captureInputs = audioInputs.filter((input) => input.type === 'capture' && input.available)
  const monitorInputs = audioInputs.filter((input) => input.type === 'monitor')
  const midiDdspComponentCount = catalog.midi_ddsp_bundles.reduce(
    (total, bundle) => total + Object.keys(bundle.components).length,
    0,
  )
  const modelAssetCount = catalog.ddsp_vst_models.length
    + midiDdspComponentCount
    + catalog.midi_ddsp_reverb_assets.length
  const dependencyEntries = Object.entries(status.dependencies)
  const missingDependencies = dependencyEntries.filter(([, found]) => !found)
  const dependencyTone: StatusTone = dependencyEntries.length === 0
    ? 'neutral'
    : missingDependencies.length
      ? 'error'
      : 'ok'
  const npuTone: StatusTone = status.npu.available
    ? status.npu.health_alarm ? 'warn' : 'ok'
    : 'error'
  const captureTone: StatusTone = audioInputError ? 'error' : captureInputs.length ? 'ok' : 'warn'
  const modelTone: StatusTone = modelAssetCount ? 'ok' : 'error'
  const realtimeTone: StatusTone = status.realtime?.running ? 'ok' : status.active_owner ? 'warn' : 'neutral'
  const performanceTone: StatusTone = status.npu.available && speakerOutputs.length && modelAssetCount
    ? realtimeTone === 'warn' ? 'warn' : 'ok'
    : 'error'
  const audioTabTone: StatusTone = audioError || audioInputError
    ? 'warn'
    : speakerOutputs.length && captureInputs.length
      ? 'ok'
      : 'warn'

  const tabs: { id: DeviceSection; label: string; badge: string; tone: StatusTone }[] = [
    { id: 'overview', label: '设备概览', badge: status.npu.available ? '板端在线' : '待检查', tone: npuTone },
    { id: 'audio', label: '音频设备', badge: `${speakerOutputs.length} 输出 / ${captureInputs.length} 输入`, tone: audioTabTone },
    { id: 'runtime', label: '运行环境', badge: missingDependencies.length ? `${missingDependencies.length} 缺失` : '依赖通过', tone: dependencyTone },
  ]
  const ioOptions: { value: DeviceIoSection; label: string }[] = [
    { value: 'output', label: `输出 ${speakerOutputs.length}` },
    { value: 'input', label: `输入 ${audioInputs.length}` },
    { value: 'midi', label: `MIDI ${midiPorts.length}` },
  ]
  const audioTestOptions: { value: AudioTestMode; label: string }[] = [
    { value: 'output', label: '输出测试' },
    { value: 'input', label: '输入测试' },
  ]
  const selectIoSection = (next: DeviceIoSection) => {
    setIoSection(next)
    if (next === 'output' || next === 'input') setAudioTestMode(next)
  }
  const selectAudioTestMode = (next: AudioTestMode) => {
    setAudioTestMode(next)
    setIoSection(next)
  }
  const audioTestModeControl = (
    <div className="audio-test-mode-control" role="group" aria-label="音频测试类型">
      <Segmented value={audioTestMode} options={audioTestOptions} onChange={selectAudioTestMode} />
    </div>
  )

  const readinessCards = [
    {
      title: '触控与 MIDI 演奏',
      icon: <ShieldCheck size={22} />,
      tone: performanceTone,
      status: status.realtime?.running ? '演奏中' : status.active_owner ? '资源占用' : '待启动',
      details: [
        ['NPU', status.npu.available ? status.npu.health_alarm ? '可见，Health Alarm' : '可见' : '未发现'],
        ['输出', speakerOutputs.length ? `${speakerOutputs.length} 个可选` : '缺失'],
        ['会话', status.active_owner || '空闲'],
      ],
      hint: '演奏前确认 NPU、音频输出和 MIDI 控制器状态。',
    },
    {
      title: 'DDSP-VST Effect',
      icon: <Activity size={22} />,
      tone: captureTone,
      status: captureInputs.length ? '输入就绪' : '缺少 Capture',
      details: [
        ['Capture', `${captureInputs.length}`],
        ['Monitor', `${monitorInputs.length}`],
        ['错误', audioInputError || '无'],
      ],
      hint: 'Effect 需要可用的 Capture 输入，Monitor 不能替代实体输入。',
    },
    {
      title: 'MIDI-DDSP 渲染',
      icon: <Box size={22} />,
      tone: modelTone,
      status: modelAssetCount ? '模型已索引' : '模型缺失',
      details: [
        ['DDSP-VST', `${catalog.ddsp_vst_models.length}`],
        ['MIDI-DDSP', `${catalog.midi_ddsp_bundles.length} bundle / ${midiDdspComponentCount} component`],
        ['Reverb', `${catalog.midi_ddsp_reverb_assets.length}`],
      ],
      hint: '模型资产完整时，可以创建新的离线渲染任务。',
    },
    {
      title: '运行环境',
      icon: <Server size={22} />,
      tone: dependencyTone,
      status: dependencyEntries.length ? missingDependencies.length ? '依赖缺失' : '依赖通过' : '未上报',
      details: [
        ['Python', status.python],
        ['依赖', dependencyEntries.length ? `${dependencyEntries.length - missingDependencies.length}/${dependencyEntries.length}` : '未上报'],
        ['任务', `${status.job_count}`],
      ],
      hint: '依赖状态来自当前 Python 环境的启动检查。',
    },
  ]

  return (
    <div className="devices-workspace">
      <nav className="device-section-tabs" role="tablist" aria-label="设备页面分区">
        {tabs.map((tab) => (
          <button
            type="button"
            role="tab"
            id={`device-tab-${tab.id}`}
            aria-selected={section === tab.id}
            aria-controls={`device-section-${tab.id}`}
            className={section === tab.id ? 'is-active' : ''}
            onClick={() => setSection(tab.id)}
            key={tab.id}
          >
            <span>{tab.label}</span>
            <StatusPill tone={tab.tone}>{tab.badge}</StatusPill>
          </button>
        ))}
      </nav>

      {section === 'overview' && (
        <section
          id="device-section-overview"
          role="tabpanel"
          aria-labelledby="device-tab-overview"
          className="device-section-panel device-overview-dashboard"
        >
          <section className="device-board-status" aria-labelledby="device-board-status-title">
            <div className="device-board-status-heading">
              <span className="summary-icon"><Server size={22} /></span>
              <div>
                <h2 id="device-board-status-title">开发板基本状态</h2>
                <p>{status.is_ascend_board ? 'Ascend 310B 开发板' : '当前环境未识别为 Ascend 开发板'}</p>
              </div>
              <StatusPill tone={npuTone}>{status.npu.available ? '板端在线' : '待检查'}</StatusPill>
            </div>
            <dl className="device-board-facts">
              <div><dt>主机</dt><dd>{status.hostname}</dd></div>
              <div><dt>网络</dt><dd>{status.primary_ip}</dd></div>
              <div><dt>平台</dt><dd title={status.platform}>{status.machine} · {status.platform}</dd></div>
              <div>
                <dt>NPU</dt>
                <dd>{status.npu.available ? status.npu.health_alarm ? '已识别 · Health Alarm' : '已识别 · Ready' : '未识别'}</dd>
              </div>
            </dl>
          </section>

          <div className="device-readiness-grid">
            {readinessCards.map((card) => (
              <article className={`device-readiness-card ${toneClass(card.tone)}`} key={card.title}>
                <div className="device-readiness-heading">
                  <span className="summary-icon">{card.icon}</span>
                  <h3>{card.title}</h3>
                  <StatusPill tone={card.tone}>{card.status}</StatusPill>
                </div>
                <dl>
                  {card.details.map(([label, value]) => (
                    <div key={label}>
                      <dt>{label}</dt>
                      <dd>{value}</dd>
                    </div>
                  ))}
                </dl>
                <p className="device-readiness-hint">{card.hint}</p>
              </article>
            ))}
          </div>

          {status.npu.health_alarm && status.npu.available && (
            <div className="device-state-note tone-warn">
              <TriangleAlert size={18} />
              <span>NPU 已可见，但 npu-smi 报 Health Alarm；当前页面只提示风险，实际推理结果仍以运行任务为准。</span>
            </div>
          )}
        </section>
      )}

      {section === 'audio' && (
        <div
          id="device-section-audio"
          role="tabpanel"
          aria-labelledby="device-tab-audio"
          className="device-section-panel device-audio-layout"
        >
          <div className="device-audio-sidebar">
            <BluetoothAudioPanel onRefresh={onRefresh} />

            <section className="panel device-io-panel">
              <PanelHeader
                title="接口状态"
                subtitle={ioSection === 'output'
                  ? `${speakerOutputs.length} 个音频输出`
                  : ioSection === 'input'
                    ? `${captureInputs.length} 路实体输入 · ${monitorInputs.length} 路监听源`
                    : `${midiPorts.length} 个 MIDI 输入`}
                action={(
                  <div role="group" aria-label="接口类型">
                    <Segmented value={ioSection} options={ioOptions} onChange={selectIoSection} />
                  </div>
                )}
              />

              {ioSection === 'output' && (
                <div className="device-io-content">
                  {audioError && <div className="inline-error"><TriangleAlert size={17} />{audioError}</div>}
                  <div className="device-list">
                    {speakerOutputs.map((device) => (
                      <div className={`device-list-row ${device.warning ? 'has-warning' : 'is-available'}`} key={device.id}>
                        <div>
                          <strong>{audioDeviceLabel(device)}</strong>
                          <small>
                            {device.host_api}{device.state ? ` · ${device.state}` : ''}
                            {typeof device.system_volume_percent === 'number'
                              ? ` · 系统音量 ${device.system_muted ? '静音' : `${Math.round(device.system_volume_percent)}%`}`
                              : ''}
                          </small>
                        </div>
                        <span>{device.max_output_channels} ch</span>
                        <span>{formatSampleRate(device.default_sample_rate)}</span>
                        <StatusPill tone={device.warning ? 'warn' : 'ok'}>{device.is_default ? '默认输出' : device.backend || '音频输出'}</StatusPill>
                      </div>
                    ))}
                    {!speakerOutputs.length && !audioError && <div className="empty-list">未发现音频输出</div>}
                  </div>
                </div>
              )}

              {ioSection === 'input' && (
                <div className="device-io-content">
                  {audioInputError && <div className="inline-error"><TriangleAlert size={17} />{audioInputError}</div>}
                  <div className="device-list">
                    {audioInputs.map((input) => (
                      <div className={`device-list-row ${input.available ? 'is-available' : 'is-muted'}`} key={input.id}>
                        <div>
                          <strong>{input.name}</strong>
                          <small>{input.host_api} · {input.state || 'unknown'}</small>
                        </div>
                        <span>{input.max_input_channels} ch</span>
                        <span>{formatSampleRate(input.default_sample_rate)}</span>
                        <StatusPill tone={input.available ? 'ok' : 'neutral'}>{input.type === 'capture' ? '实体输入' : '监听源'}</StatusPill>
                      </div>
                    ))}
                    {!audioInputs.length && !audioInputError && <div className="empty-list">未发现音频输入</div>}
                  </div>
                  {!captureInputs.length && (
                    <div className="inline-warning">
                      <TriangleAlert size={17} />
                      DDSP-VST Effect 未就绪：无真实 capture source
                    </div>
                  )}
                </div>
              )}

              {ioSection === 'midi' && (
                <div className="device-io-content">
                  {midiError && <div className="inline-error"><TriangleAlert size={17} />{midiError}</div>}
                  <div className="device-list">
                    {midiPorts.map((port) => (
                      <div className="device-list-row is-available" key={port.id}>
                        <div>
                          <strong>{port.name}</strong>
                          <small>{port.manufacturer || port.backend || 'MIDI'} · 输入端口 {port.index}</small>
                        </div>
                        {port.key_count && <span>{port.key_count} 键</span>}
                        {port.model && <span>{port.model}</span>}
                        <StatusPill tone="ok">可用</StatusPill>
                      </div>
                    ))}
                    {!midiPorts.length && !midiError && <div className="empty-list">未连接实体 MIDI</div>}
                  </div>
                </div>
              )}
            </section>
          </div>

          {ioSection === 'midi' ? (
            <section className="panel midi-device-status-panel" aria-label="MIDI 输入状态">
              <PanelHeader
                title="MIDI 输入状态"
                subtitle={`${midiPorts.length} 个输入端口`}
                action={<StatusPill tone={midiError ? 'error' : midiPorts.length ? 'ok' : 'neutral'}>{midiError ? '检查失败' : midiPorts.length ? '已连接' : '未连接'}</StatusPill>}
              />
              {midiError && <div className="inline-error"><TriangleAlert size={17} />{midiError}</div>}
              <div className="midi-device-status-list">
                {midiPorts.map((port) => (
                  <article className="midi-device-status-card" key={port.id}>
                    <span className="summary-icon"><Activity size={22} /></span>
                    <div className="midi-device-status-heading">
                      <strong>{port.name}</strong>
                      <span>{port.manufacturer || port.backend || 'MIDI'}</span>
                    </div>
                    <dl>
                      <div><dt>琴键</dt><dd>{port.key_count ? `${port.key_count} 键` : '未上报'}</dd></div>
                      <div><dt>型号</dt><dd>{port.model || '未上报'}</dd></div>
                      <div><dt>输入端口</dt><dd>{port.index}</dd></div>
                      <div><dt>状态</dt><dd>可用</dd></div>
                    </dl>
                  </article>
                ))}
                {!midiPorts.length && !midiError && <div className="empty-list">未连接实体 MIDI 控制器</div>}
              </div>
            </section>
          ) : audioTestMode === 'output' ? (
            <SpeakerView
              status={status}
              audioDevices={speakerOutputs}
              onRefresh={onRefresh}
              modeControl={audioTestModeControl}
            />
          ) : (
            <AudioInputTestView
              status={status}
              audioInputs={audioInputs}
              onRefresh={onRefresh}
              modeControl={audioTestModeControl}
            />
          )}
        </div>
      )}

      {section === 'runtime' && (
        <div
          id="device-section-runtime"
          role="tabpanel"
          aria-labelledby="device-tab-runtime"
          className="device-section-panel device-runtime-layout"
        >
          <section className="runtime-summary-grid" aria-label="运行环境状态摘要">
            <article className="runtime-summary-card tone-neutral">
              <span className="summary-icon"><Server size={21} /></span>
              <div><small>PYTHON</small><strong>{status.python}</strong><span>当前服务进程</span></div>
              <StatusPill tone="neutral">运行中</StatusPill>
            </article>
            <article className={`runtime-summary-card ${toneClass(dependencyTone)}`}>
              <span className="summary-icon"><CheckCircle2 size={21} /></span>
              <div>
                <small>运行依赖</small>
                <strong>{dependencyEntries.length - missingDependencies.length}/{dependencyEntries.length}</strong>
                <span>{missingDependencies.length ? `${missingDependencies.length} 项缺失` : '检查通过'}</span>
              </div>
              <StatusPill tone={dependencyTone}>{missingDependencies.length ? '缺失' : '就绪'}</StatusPill>
            </article>
            <article className={`runtime-summary-card ${toneClass(modelTone)}`}>
              <span className="summary-icon"><Box size={21} /></span>
              <div><small>模型资产</small><strong>{modelAssetCount}</strong><span>已发布文件</span></div>
              <StatusPill tone={modelTone}>{modelAssetCount ? '已索引' : '缺失'}</StatusPill>
            </article>
            <article className={`runtime-summary-card ${toneClass(npuTone)}`}>
              <span className="summary-icon"><Cpu size={21} /></span>
              <div>
                <small>NPU</small>
                <strong>{status.npu.available ? '已识别' : '未识别'}</strong>
                <span>{status.npu.available ? status.npu.health_alarm ? 'Health Alarm' : 'Ready' : '等待设备'}</span>
              </div>
              <StatusPill tone={npuTone}>{status.npu.health_alarm ? '告警' : status.npu.available ? '就绪' : '离线'}</StatusPill>
            </article>
          </section>

          {status.npu.available && status.npu.health_alarm && (
            <div className="runtime-health-note tone-warn">
              <TriangleAlert size={19} />
              <div>
                <strong>NPU 可见，但健康状态为 Health Alarm</strong>
                <span>该状态作为风险提示，不直接判定推理失败；实际可用性以真实推理任务为准。</span>
              </div>
            </div>
          )}
          {!status.npu.available && (
            <div className="runtime-health-note tone-error">
              <XCircle size={19} />
              <div><strong>未识别到 NPU</strong><span>请在开发板上检查 CANN 环境和设备可见性。</span></div>
            </div>
          )}

          <div className="runtime-detail-grid">
            <section className="panel dependency-panel">
              <PanelHeader
                title="运行依赖"
                subtitle={dependencyEntries.length ? `${dependencyEntries.length - missingDependencies.length}/${dependencyEntries.length} 已找到` : '等待状态上报'}
                action={<Server size={18} />}
              />
              <div className="dependency-grid">
                {dependencyEntries.map(([name, found]) => (
                  <div className={`dependency-row ${found ? 'is-found' : 'is-missing'}`} key={name}>
                    {found ? <CheckCircle2 size={17} /> : <XCircle size={17} />}
                    <span>{name}</span>
                    <strong>{found ? '已找到' : '缺失'}</strong>
                  </div>
                ))}
                {!dependencyEntries.length && <div className="empty-list">运行依赖尚未上报</div>}
              </div>
              <div className="runtime-python-path">
                <span>Python 可执行文件</span>
                <code title={status.python_executable}>{status.python_executable}</code>
              </div>
            </section>

            <section className="panel model-inventory">
              <PanelHeader title="模型资产" subtitle={`${modelAssetCount} 个发布文件`} action={<Box size={18} />} />
              <div className="model-kind-summary">
                <div><span>DDSP-VST</span><strong>{catalog.ddsp_vst_models.length}</strong><small>OM 模型</small></div>
                <div><span>MIDI-DDSP</span><strong>{catalog.midi_ddsp_bundles.length}</strong><small>{midiDdspComponentCount} 个组件</small></div>
                <div><span>混响资产</span><strong>{catalog.midi_ddsp_reverb_assets.length}</strong><small>已发布</small></div>
              </div>
              <div className="inventory-table">
                {catalog.ddsp_vst_models.map((model) => (
                  <div className="inventory-row" key={model.id}>
                    <strong title={model.name}>{timbreNameZh(model.instrument)}</strong>
                    <span>{model.backend.toUpperCase()}</span>
                    <span>{modelPrecisionNameZh(model.precision)}</span>
                    <span>{formatBytes(model.size_bytes)}</span>
                  </div>
                ))}
                {catalog.midi_ddsp_bundles.map((bundle) => (
                  <div className="inventory-row" key={bundle.id}>
                    <strong>{bundle.name}</strong>
                    <span>BUNDLE</span>
                    <span>{bundle.precision}</span>
                    <span>{Object.keys(bundle.components).length} components</span>
                  </div>
                ))}
                {catalog.midi_ddsp_reverb_assets.map((asset) => (
                  <div className="inventory-row" key={asset.id}>
                    <strong>{asset.name}</strong>
                    <span>REVERB</span>
                    <span>{asset.instrument_count} instruments</span>
                    <span>{formatBytes(asset.size_bytes)}</span>
                  </div>
                ))}
                {!modelAssetCount && <div className="empty-list">未发现发布模型资产</div>}
              </div>
            </section>
          </div>
        </div>
      )}
    </div>
  )
}
