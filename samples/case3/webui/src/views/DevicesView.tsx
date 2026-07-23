import { Box, Cable, CheckCircle2, Cpu, Headphones, RefreshCw, Server, TriangleAlert, XCircle } from 'lucide-react'
import { formatBytes } from '../api'
import { PanelHeader, StatusPill } from '../components/ui'
import type { AudioDevice, Catalog, MidiPort, SystemStatus } from '../types'

interface Props {
  status: SystemStatus
  catalog: Catalog
  audioDevices: AudioDevice[]
  midiPorts: MidiPort[]
  audioError: string | null
  midiError: string | null
  onRefresh: () => Promise<void>
}

export default function DevicesView({ status, catalog, audioDevices, midiPorts, audioError, midiError, onRefresh }: Props) {
  return (
    <div className="devices-workspace">
      <section className="panel device-overview">
        <PanelHeader title="系统与设备" subtitle={`${status.hostname} · ${status.machine}`} action={<button className="icon-button" title="刷新设备" type="button" onClick={onRefresh}><RefreshCw size={18} /></button>} />
        <div className="device-summary-grid">
          <div className="device-summary"><span className="summary-icon"><Cpu size={22} /></span><div><small>ASCEND NPU</small><strong>{status.npu.available ? '310B4' : '不可用'}</strong></div><StatusPill tone={status.npu.health_alarm ? 'warn' : status.npu.available ? 'ok' : 'error'}>{status.npu.health_alarm ? 'Alarm' : status.npu.available ? 'Ready' : 'Offline'}</StatusPill></div>
          <div className="device-summary"><span className="summary-icon teal"><Headphones size={22} /></span><div><small>AUDIO OUTPUT</small><strong>{audioDevices.length}</strong></div><StatusPill tone={audioDevices.length ? 'ok' : 'error'}>{audioDevices.length ? 'Ready' : 'Missing'}</StatusPill></div>
          <div className="device-summary"><span className="summary-icon amber"><Cable size={22} /></span><div><small>MIDI INPUT</small><strong>{midiPorts.length}</strong></div><StatusPill tone={midiPorts.length ? 'ok' : 'neutral'}>{midiPorts.length ? 'Connected' : 'None'}</StatusPill></div>
          <div className="device-summary"><span className="summary-icon graphite"><Box size={22} /></span><div><small>MODEL FILES</small><strong>{catalog.live_models.length + catalog.midi_ddsp_models.length}</strong></div><StatusPill tone="ok">Indexed</StatusPill></div>
        </div>
      </section>

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

      <section className="panel output-panel">
        <PanelHeader title="音频输出" action={<Headphones size={18} />} />
        {audioError && <div className="inline-error"><TriangleAlert size={17} />{audioError}</div>}
        <div className="device-list">
          {audioDevices.map((device) => <div className="device-list-row" key={device.id}><div><strong>{device.name}</strong><small>{device.host_api}</small></div><span>{device.max_output_channels} ch</span><span>{device.default_sample_rate / 1000} kHz</span></div>)}
          {!audioDevices.length && !audioError && <div className="empty-list">未发现音频输出</div>}
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

      <section className="panel model-inventory">
        <PanelHeader title="模型目录" action={<Box size={18} />} />
        <div className="inventory-table">
          {[...catalog.live_models, ...catalog.midi_ddsp_models].map((model) => (
            <div className="inventory-row" key={model.id}><strong>{model.name}</strong><span>{'backend' in model ? model.backend.toUpperCase() : model.component}</span><span>{model.precision}</span><span>{formatBytes(model.size_bytes)}</span></div>
          ))}
        </div>
      </section>

      <section className="panel npu-console">
        <PanelHeader title="NPU 状态" action={<Cpu size={18} />} />
        <pre>{status.npu.output || 'No NPU output.'}</pre>
      </section>
    </div>
  )
}
