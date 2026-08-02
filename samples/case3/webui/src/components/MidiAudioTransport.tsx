import { Pause, Play, Repeat2, Square } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { artifactUrl } from '../api'
import type { Artifact } from '../types'

interface Props {
  artifact?: Artifact
  onPlayingChange: (playing: boolean) => void
  onProgress: (progress: number | null) => void
}

function formatTime(value: number): string {
  const seconds = Math.max(0, Math.floor(Number.isFinite(value) ? value : 0))
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`
}

export default function MidiAudioTransport({ artifact, onPlayingChange, onProgress }: Props) {
  const audioRef = useRef<HTMLAudioElement>(null)
  const [playing, setPlaying] = useState(false)
  const [ready, setReady] = useState(false)
  const [looping, setLooping] = useState(false)
  const [currentTime, setCurrentTime] = useState(0)
  const [duration, setDuration] = useState(0)
  const [error, setError] = useState('')

  useEffect(() => {
    setPlaying(false)
    setReady(false)
    setCurrentTime(0)
    setDuration(0)
    setError('')
    onPlayingChange(false)
    onProgress(artifact ? 0 : null)
  }, [artifact, onPlayingChange, onProgress])

  function syncTime(audio: HTMLAudioElement) {
    const nextDuration = Number.isFinite(audio.duration) ? audio.duration : 0
    setCurrentTime(audio.currentTime)
    setDuration(nextDuration)
    onProgress(nextDuration > 0 ? Math.min(1, audio.currentTime / nextDuration) : 0)
  }

  async function togglePlayback() {
    const audio = audioRef.current
    if (!audio) return
    if (playing) {
      audio.pause()
      return
    }
    setError('')
    try {
      await audio.play()
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '浏览器无法播放该音频')
    }
  }

  function stopPlayback() {
    const audio = audioRef.current
    if (!audio) return
    audio.pause()
    audio.currentTime = 0
    syncTime(audio)
  }

  function seekPlayback(value: number) {
    const audio = audioRef.current
    if (!audio || !Number.isFinite(value)) return
    audio.currentTime = Math.max(0, Math.min(duration, value))
    syncTime(audio)
  }

  function toggleLoop() {
    const audio = audioRef.current
    if (!audio) return
    const next = !looping
    audio.loop = next
    setLooping(next)
  }

  return (
    <div className="midi-roll-transport" aria-label="浏览器音频播放" aria-busy={artifact && !ready ? 'true' : 'false'}>
      <audio
        ref={audioRef}
        className="midi-roll-audio"
        src={artifact ? artifactUrl(artifact.id) : undefined}
        preload="metadata"
        onLoadedMetadata={(event) => {
          setReady(true)
          syncTime(event.currentTarget)
        }}
        onCanPlay={() => setReady(true)}
        onTimeUpdate={(event) => syncTime(event.currentTarget)}
        onPlay={() => {
          setPlaying(true)
          onPlayingChange(true)
        }}
        onPause={() => {
          setPlaying(false)
          onPlayingChange(false)
        }}
        onEnded={(event) => {
          setPlaying(false)
          syncTime(event.currentTarget)
          onPlayingChange(false)
        }}
        onError={() => {
          setReady(false)
          setError('浏览器无法解码该音频')
        }}
      />
      <button
        type="button"
        className="icon-button"
        title={playing ? '暂停浏览器播放' : '浏览器播放'}
        aria-label={playing ? '暂停浏览器播放' : '浏览器播放'}
        onClick={togglePlayback}
        disabled={!artifact || !ready}
      >
        {playing ? <Pause size={18} fill="currentColor" /> : <Play size={18} fill="currentColor" />}
      </button>
      <button
        type="button"
        className="icon-button"
        title="停止浏览器播放"
        aria-label="停止浏览器播放"
        onClick={stopPlayback}
        disabled={!artifact || currentTime <= 0}
      >
        <Square size={16} fill="currentColor" />
      </button>
      <button
        type="button"
        className={`icon-button ${looping ? 'is-active' : ''}`}
        title={looping ? '关闭循环播放' : '循环播放'}
        aria-label={looping ? '关闭循环播放' : '循环播放'}
        aria-pressed={looping}
        onClick={toggleLoop}
        disabled={!artifact || !ready}
      >
        <Repeat2 size={18} />
      </button>
      <input
        className="midi-roll-seek"
        type="range"
        aria-label="浏览器播放位置"
        aria-valuetext={`${formatTime(currentTime)} / ${duration > 0 ? formatTime(duration) : '--:--'}`}
        min={0}
        max={duration || 0}
        step={0.01}
        value={Math.min(currentTime, duration || 0)}
        onChange={(event) => seekPlayback(Number(event.target.value))}
        disabled={!artifact || !ready || duration <= 0}
      />
      <output className="midi-roll-transport-time" aria-live="off">
        {formatTime(currentTime)} / {duration > 0 ? formatTime(duration) : '--:--'}
      </output>
      {artifact && !ready && !error && <span className="midi-roll-transport-status">载入中</span>}
      {error && <span className="midi-roll-transport-error" title={error}>播放失败</span>}
    </div>
  )
}
