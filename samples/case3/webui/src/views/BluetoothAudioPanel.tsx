import { useCallback, useEffect, useMemo, useState } from 'react'
import { Bluetooth, BluetoothConnected, BluetoothSearching, Link2, RefreshCw, TriangleAlert, Unplug } from 'lucide-react'
import { api } from '../api'
import { Notice, PanelHeader, StatusPill } from '../components/ui'
import type { BluetoothAudioDevice, BluetoothAudioState } from '../types'

interface Props {
  onRefresh: () => Promise<void>
}

const EMPTY_STATE: BluetoothAudioState = {
  available: false,
  controller: null,
  devices: [],
  error: null,
}

function deviceLabel(device: BluetoothAudioDevice): string {
  return device.alias || device.name || device.address
}

function deviceStatusLabel(device: BluetoothAudioDevice): string {
  if (device.blocked) return 'Blocked'
  if (device.connected) return 'Connected'
  if (device.paired || device.trusted || device.bonded) return 'Paired'
  return device.is_audio ? 'Audio' : 'Found'
}

function deviceStatusTone(device: BluetoothAudioDevice): 'ok' | 'warn' | 'error' | 'neutral' {
  if (device.blocked) return 'error'
  if (device.connected) return 'ok'
  if (device.is_audio) return 'warn'
  return 'neutral'
}

function controllerSubtitle(state: BluetoothAudioState): string {
  if (state.error) return state.error
  if (!state.controller) return '未发现蓝牙控制器'
  const powered = state.controller.powered ? '已开启' : '未开启'
  return `${state.controller.name || state.controller.address} · ${powered}`
}

export default function BluetoothAudioPanel({ onRefresh }: Props) {
  const [state, setState] = useState<BluetoothAudioState>(EMPTY_STATE)
  const [loading, setLoading] = useState(false)
  const [scanning, setScanning] = useState(false)
  const [busyAddress, setBusyAddress] = useState('')
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const next = await api.bluetoothAudio()
      setState(next)
      if (next.error) setError(next.error)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  const visibleDevices = useMemo(() => {
    const audioDevices = state.devices.filter((device) => device.is_audio)
    return audioDevices.length ? audioDevices : state.devices
  }, [state.devices])

  async function scan() {
    setScanning(true)
    setError('')
    setMessage('')
    try {
      const next = await api.scanBluetoothAudio(8)
      setState(next)
      if (next.error) setError(next.error)
      await onRefresh()
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause))
    } finally {
      setScanning(false)
    }
  }

  async function connect(device: BluetoothAudioDevice) {
    setBusyAddress(device.address)
    setError('')
    setMessage('')
    try {
      const result = await api.connectBluetoothAudio({
        address: device.address,
        pair: !device.paired,
        trust: true,
      })
      const next = await api.bluetoothAudio()
      setState(next)
      await onRefresh()
      if (result.profile.error) {
        setError(`已连接，A2DP profile 未切换：${result.profile.error}`)
      } else {
        setMessage(`已连接 ${deviceLabel(result.device)}`)
      }
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause))
    } finally {
      setBusyAddress('')
    }
  }

  async function disconnect(device: BluetoothAudioDevice) {
    setBusyAddress(device.address)
    setError('')
    setMessage('')
    try {
      await api.disconnectBluetoothAudio(device.address)
      const next = await api.bluetoothAudio()
      setState(next)
      await onRefresh()
      setMessage(`已断开 ${deviceLabel(device)}`)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause))
    } finally {
      setBusyAddress('')
    }
  }

  const controllerPowered = state.controller?.powered === true
  const audioCount = state.devices.filter((device) => device.is_audio).length

  return (
    <section className="panel bluetooth-panel">
      <PanelHeader
        title="蓝牙音频"
        subtitle={controllerSubtitle(state)}
        action={(
          <div className="transport-actions">
            <button className="icon-button" title="刷新蓝牙设备" type="button" disabled={loading || scanning} onClick={load}>
              <RefreshCw size={18} className={loading ? 'spin' : ''} />
            </button>
            <button className="primary-button" title="扫描蓝牙设备" type="button" disabled={scanning} onClick={scan}>
              <BluetoothSearching size={18} />
              {scanning ? '扫描中' : '扫描'}
            </button>
          </div>
        )}
      />

      {error && <Notice tone="error">{error}</Notice>}
      {message && !error && <Notice tone="success">{message}</Notice>}

      <div className="bluetooth-summary">
        <div>
          <Bluetooth size={20} />
          <span>控制器</span>
          <strong>{controllerPowered ? 'Power On' : 'Power Off'}</strong>
        </div>
        <div>
          <BluetoothConnected size={20} />
          <span>音频设备</span>
          <strong>{audioCount}</strong>
        </div>
        <StatusPill tone={state.available && controllerPowered ? 'ok' : state.available ? 'warn' : 'neutral'}>
          {state.available ? 'Bluetooth' : 'Unavailable'}
        </StatusPill>
      </div>

      <div className="bluetooth-device-list">
        {visibleDevices.map((device) => {
          const busy = busyAddress === device.address
          return (
            <div className={`bluetooth-device-row ${device.connected ? 'is-connected' : ''}`} key={device.address}>
              <span className="bluetooth-device-icon">
                {device.connected ? <BluetoothConnected size={19} /> : <Bluetooth size={19} />}
              </span>
              <div>
                <strong>{deviceLabel(device)}</strong>
                <small>{device.address}{device.icon ? ` · ${device.icon}` : ''}</small>
              </div>
              <StatusPill tone={deviceStatusTone(device)}>{deviceStatusLabel(device)}</StatusPill>
              <button
                className="icon-button"
                title={device.connected ? '断开蓝牙音频' : '连接蓝牙音频'}
                type="button"
                disabled={busy || scanning || device.blocked}
                onClick={() => {
                  if (device.connected) void disconnect(device)
                  else void connect(device)
                }}
              >
                {busy ? <RefreshCw size={17} className="spin" /> : device.connected ? <Unplug size={17} /> : <Link2 size={17} />}
              </button>
            </div>
          )
        })}
        {!visibleDevices.length && !error && (
          <div className="empty-list">
            <TriangleAlert size={18} />
            未发现蓝牙音频设备
          </div>
        )}
      </div>
    </section>
  )
}
