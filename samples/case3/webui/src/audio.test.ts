import { describe, expect, it } from 'vitest'
import { audioDeviceLabel } from './audio'
import type { AudioDevice } from './types'

function output(values: Partial<AudioDevice>): AudioDevice {
  return {
    id: 'output',
    index: 0,
    name: 'EDIFIER M16 Pro',
    host_api: 'Audio',
    max_output_channels: 2,
    default_sample_rate: 48000,
    ...values,
  }
}

describe('audio device labels', () => {
  it('keeps one physical name while identifying backend routes', () => {
    expect(audioDeviceLabel(output({ backend: 'pulse' }))).toBe(
      'EDIFIER M16 Pro（PulseAudio）',
    )
    expect(audioDeviceLabel(output({ backend: 'portaudio', is_default: true }))).toBe(
      'EDIFIER M16 Pro（直连，默认）',
    )
  })

  it('labels the onboard compatibility route consistently', () => {
    expect(audioDeviceLabel(output({
      id: 'alsa:onboard-headset',
      name: '板载 3.5 mm',
      backend: 'alsa_mono',
      is_mono: true,
      is_default: true,
    }))).toBe('板载 3.5 mm（单声道，默认）')
  })
})
