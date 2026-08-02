import { useState } from 'react'
import { Box, Cable, CheckCircle2, Cpu, Headphones, Mic2, RefreshCw, Server, TriangleAlert, Wifi, XCircle } from 'lucide-react'
import { formatBytes } from '../api'
import { audioDeviceLabel } from '../audio'
import { PanelHeader, StatusPill } from '../components/ui'
import type { AudioDevice, AudioInput, Catalog, MidiPort, SystemStatus } from '../types'
import BluetoothAudioPanel from './BluetoothAudioPanel'
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

export default function DevicesView({ status, catalog, speakerOutputs, audioInputs, midiPorts, audioError, audioInputError, midiError, onRefresh }: Props) {
  const [section, setSection] = useState<'overview' | 'audio' | 'runtime'>('audio')
  const captureInputs = audioInputs.filter((input) => input.type === 'capture' && input.available)
  const midiDdspComponentCount = catalog.midi_ddsp_bundles.reduce(
    (total, bundle) => total + Object.keys(bundle.components).length,
    0,
  )
  return (
    <div className="devices-workspace">
      <section className="panel device-overview">
        <PanelHeader title="系统与设备" subtitle={`${status.hostname} · ${status.primary_ip} · ${status.machine}`} action={<button className="icon-button" title="刷新设备" type="button" onClick={onRefresh}><RefreshCw size={18} /></button>} />
        <div className="device-summary-grid">
          <div className="device-summary"><span className="summary-icon"><Cpu size={22} /></span><div><small>ASCEND NPU</small><strong>{status.npu.available ? '310B4' : '不可用'}</strong></div><StatusPill tone={status.npu.health_alarm ? 'warn' : status.npu.available ? 'ok' : 'error'}>{status.npu.health_alarm ? 'Alarm' : status.npu.available ? 'Ready' : 'Offline'}</StatusPill></div>
          <div className="device-summary"><span className="summary-icon teal"><Wifi size={22} /></span><div><small>BOARD IP</small><strong>{status.primary_ip}</strong></div><StatusPill tone={status.primary_ip.startsWith('127.') ? 'warn' : 'ok'}>{status.primary_ip.startsWith('127.') ? 'Local' : 'LAN'}</StatusPill></div>
          <div className="device-summary"><span className="summary-icon teal"><Headphones size={22} /></span><div><small>AUDIO OUTPUT</small><strong>{speakerOutputs.length}</strong></div><StatusPill tone={speakerOutputs.length ? 'ok' : 'error'}>{speakerOutputs.length ? 'Ready' : 'Missing'}</StatusPill></div>
          <div className="device-summary"><span className="summary-icon teal"><Mic2 size={22} /></span><div><small>AUDIO INPUT</small><strong>{captureInputs.length}</strong></div><StatusPill tone={captureInputs.length ? 'ok' : 'warn'}>{captureInputs.length ? 'Capture' : 'No capture'}</StatusPill></div>
          <div className="device-summary"><span className="summary-icon amber"><Cable size={22} /></span><div><small>MIDI INPUT</small><strong>{midiPorts.length}</strong></div><StatusPill tone={midiPorts.length ? 'ok' : 'neutral'}>{midiPorts.length ? 'Connected' : 'None'}</StatusPill></div>
          <div className="device-summary"><span className="summary-icon graphite"><Box size={22} /></span><div><small>MODEL FILES</small><strong>{catalog.ddsp_vst_models.length + midiDdspComponentCount}</strong></div><StatusPill tone="ok">Indexed</StatusPill></div>
        </div>
      </section>

      <nav className="device-section-tabs" role="tablist" aria-label="设备页面分区">
        {([
          ['overview', '设备概览'],
          ['audio', '音频与 MIDI'],
          ['runtime', '运行环境'],
        ] as const).map(([id, label]) => (
          <button
            type="button"
            role="tab"
            aria-selected={section === id}
            aria-controls={`device-section-${id}`}
            className={section === id ? 'is-active' : ''}
            onClick={() => setSection(id)}
            key={id}
          >
            {label}
          </button>
        ))}
      </nav>

      {section === 'overview' && (
        <section id="device-section-overview" role="tabpanel" className="device-section-panel">
          <div className="device-overview-help">
            <strong>设备状态总览</strong>
            <span>选择“音频与 MIDI”配置扬声器、蓝牙及输入设备；运行依赖和模型状态集中在“运行环境”。</span>
          </div>
        </section>
      )}

      {section === 'audio' && <div id="device-section-audio" role="tabpanel" className="device-section-panel device-section-stack">
        <BluetoothAudioPanel onRefresh={onRefresh} />

        <SpeakerView status={status} audioDevices={speakerOutputs} onRefresh={onRefresh} />

      <section className="panel output-panel">
        <PanelHeader title="音频输入 / Effect 条件" action={<Mic2 size={18} />} />
        {audioInputError && <div className="inline-error"><TriangleAlert size={17} />{audioInputError}</div>}
        <div className="device-list">
          {audioInputs.map((input) => <div className="device-list-row" key={input.id}><div><strong>{input.name}</strong><small>{input.host_api}</small></div><span>{input.type}</span><StatusPill tone={input.available ? 'ok' : 'neutral'}>{input.available ? 'Capture' : 'Monitor'}</StatusPill></div>)}
          {!audioInputs.length && !audioInputError && <div className="empty-list">未发现音频输入</div>}
        </div>
        {!captureInputs.length && <div className="inline-warning"><TriangleAlert size={17} />DDSP-VST Effect 未就绪：无真实 capture source</div>}
      </section>

      <section className="panel output-panel">
        <PanelHeader title="音频输出" action={<Headphones size={18} />} />
        {audioError && <div className="inline-error"><TriangleAlert size={17} />{audioError}</div>}
        <div className="device-list">
          {speakerOutputs.map((device) => <div className="device-list-row" key={device.id}><div><strong>{audioDeviceLabel(device)}</strong><small>{device.host_api}</small></div><span>{device.max_output_channels} ch</span><span>{device.default_sample_rate / 1000} kHz</span></div>)}
          {!speakerOutputs.length && !audioError && <div className="empty-list">未发现音频输出</div>}
        </div>
      </section>

      <section className="panel output-panel">
        <PanelHeader title="MIDI 输入" action={<Cable size={18} />} />
        {midiError && <div className="inline-error"><TriangleAlert size={17} />{midiError}</div>}
        <div className="device-list">
          {midiPorts.map((port) => <div className="device-list-row" key={port.id}><div><strong>{port.name}</strong><small>Input port {port.index}</small></div><StatusPill tone="ok">Available</StatusPill></div>)}
          {!midiPorts.length && !midiError && <div className="empty-list">未连接实体 MIDI</div>}
        </div>
      </section>
      </div>}

      {section === 'runtime' && <div id="device-section-runtime" role="tabpanel" className="device-section-panel device-section-stack">
        <section className="panel dependency-panel">
        <PanelHeader title="运行依赖" action={<Server size={18} />} />
        <div className="dependency-grid">
          {Object.entries(status.dependencies).map(([name, found]) => (
            <div className="dependency-row" key={name}>
              {found ? <CheckCircle2 size={17} /> : <XCircle size={17} />}
              <span>{name}</span>
              <strong>{found ? 'FOUND' : 'MISSING'}</strong>
            </div>
          ))}
        </div>
      </section>

      <section className="panel model-inventory">
        <PanelHeader title="模型目录" action={<Box size={18} />} />
        <div className="inventory-table">
          {catalog.ddsp_vst_models.map((model) => (
            <div className="inventory-row" key={model.id}><strong>{model.name}</strong><span>{model.backend.toUpperCase()}</span><span>{model.precision}</span><span>{formatBytes(model.size_bytes)}</span></div>
          ))}
          {catalog.midi_ddsp_bundles.map((bundle) => (
            <div className="inventory-row" key={bundle.id}><strong>{bundle.name}</strong><span>BUNDLE</span><span>{bundle.precision}</span><span>{Object.keys(bundle.components).length} components</span></div>
          ))}
        </div>
      </section>

      <section className="panel npu-console">
        <PanelHeader title="NPU 状态" action={<Cpu size={18} />} />
        <pre>{status.npu.output || 'No NPU output.'}</pre>
      </section>
      </div>}
    </div>
  )
}
