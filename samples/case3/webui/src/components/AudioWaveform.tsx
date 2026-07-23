import { useEffect, useState } from 'react'
import { artifactUrl } from '../api'
import type { Artifact } from '../types'

export default function AudioWaveform({ artifact }: { artifact?: Artifact }) {
  const [levels, setLevels] = useState<number[]>([])

  useEffect(() => {
    if (!artifact) {
      setLevels([])
      return
    }
    let cancelled = false
    const load = async () => {
      const response = await fetch(artifactUrl(artifact.id))
      const bytes = await response.arrayBuffer()
      const context = new AudioContext()
      try {
        const buffer = await context.decodeAudioData(bytes.slice(0))
        const samples = buffer.getChannelData(0)
        const bins = 96
        const stride = Math.max(1, Math.floor(samples.length / bins))
        const next = Array.from({ length: bins }, (_, index) => {
          const start = index * stride
          const end = Math.min(samples.length, start + stride)
          let peak = 0
          for (let cursor = start; cursor < end; cursor += 1) peak = Math.max(peak, Math.abs(samples[cursor]))
          return peak
        })
        const maximum = Math.max(...next, 0.001)
        if (!cancelled) setLevels(next.map((value) => value / maximum))
      } finally {
        await context.close()
      }
    }
    load().catch(() => !cancelled && setLevels([]))
    return () => {
      cancelled = true
    }
  }, [artifact])

  return (
    <div className="audio-preview">
      <div className="waveform" aria-label="音频波形">
        {(levels.length ? levels : Array.from({ length: 96 }, () => 0.05)).map((level, index) => (
          <span key={index} style={{ height: `${Math.max(5, level * 100)}%` }} />
        ))}
      </div>
      {artifact && <audio controls src={artifactUrl(artifact.id)} preload="metadata" />}
    </div>
  )
}
