import type { AudioDevice } from './types'

export function isBluetoothOutput(device: AudioDevice): boolean {
  return Boolean(
    device.is_bluetooth
    || device.id.toLowerCase().includes('bluez_')
    || device.sink_name?.toLowerCase().startsWith('bluez_')
    || device.name.toLowerCase().includes('bluetooth'),
  )
}

export function audioDeviceLabel(device: AudioDevice): string {
  const flags = []
  if (isBluetoothOutput(device)) flags.push('蓝牙')
  if (device.is_default) flags.push('默认')
  return flags.length ? `${device.name}（${flags.join('，')}）` : device.name
}
