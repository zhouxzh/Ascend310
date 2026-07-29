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
  const bluetooth = isBluetoothOutput(device)
  if (bluetooth) flags.push('蓝牙')
  else if (device.backend === 'pulse') flags.push('PulseAudio')
  else if (device.backend === 'portaudio') flags.push('直连')
  if (device.is_mono) flags.push('单声道')
  if (device.is_default) flags.push('默认')
  return flags.length ? `${device.name}（${flags.join('，')}）` : device.name
}
