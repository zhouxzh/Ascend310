"""Safe ALSA enumeration and board-owned PCM capture/playback.

No shell is used. The only subprocesses are fixed ``arecord`` and ``aplay``
argument lists with agent-enumerated device IDs. Captured PCM stays in memory
until ASR consumes it and is never exposed by the HTTP service.
"""

from __future__ import annotations

import asyncio
import contextlib
import re
import shutil
import subprocess
import sys
import threading
from array import array
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .config import VoiceSettings
from .protocol import SynthesizedAudio, VoiceDevice
from .vad import EnergyVad, VadSettings, VadState


_PCM_LINE = re.compile(
    r"^(?P<card>\d+)-(?P<device>\d+):\s*(?P<name>.*?)\s*:\s*.*?\s*:\s*(?P<direction>playback|capture)\s+\d+\s*$"
)


class AudioDeviceError(RuntimeError):
    """A local ALSA device, command, or PCM contract failed."""


@dataclass(frozen=True)
class AlsaPcmDevice:
    """One PCM node as reported by ``/proc/asound/pcm``."""

    card: int
    device: int
    name: str
    direction: Literal["capture", "playback"]

    @property
    def hw_name(self) -> str:
        return f"hw:{self.card},{self.device}"

    @property
    def public_id(self) -> str:
        return f"alsa-{self.direction}-{self.card}-{self.device}"

    def as_voice_device(self) -> VoiceDevice:
        return VoiceDevice(
            id=self.public_id,
            label=f"{self.name} ({self.hw_name})",
            direction=self.direction,
            backend_id=self.hw_name,
        )


def enumerate_alsa_devices(proc_root: Path = Path("/proc/asound")) -> list[AlsaPcmDevice]:
    """Parse available capture/playback PCM devices without opening them."""

    try:
        lines = (proc_root / "pcm").read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:
        raise AudioDeviceError(f"cannot read {proc_root / 'pcm'}") from exc
    devices: list[AlsaPcmDevice] = []
    for line in lines:
        match = _PCM_LINE.match(line.strip())
        if match is None:
            continue
        devices.append(
            AlsaPcmDevice(
                card=int(match.group("card")),
                device=int(match.group("device")),
                name=match.group("name").strip() or "unnamed",
                direction=match.group("direction"),
            )
        )
    return devices


def discover_voice_devices(proc_root: Path = Path("/proc/asound")) -> list[VoiceDevice]:
    """Return public descriptors, never exposing a caller-selected ALSA path."""

    return [device.as_voice_device() for device in enumerate_alsa_devices(proc_root)]


def downmix_pcm16le(pcm: bytes, channels: int) -> bytes:
    """Return mono S16_LE PCM without retaining the source recording.

    The initial ASR/VAD contract is mono, while USB microphones commonly expose
    only stereo capture.  Downmixing happens frame-by-frame in memory.
    """

    if channels not in {1, 2} or not pcm or len(pcm) % (2 * channels):
        raise AudioDeviceError("capture PCM does not match its declared channel count")
    if channels == 1:
        return pcm
    values = array("h")
    values.frombytes(pcm)
    if sys.byteorder != "little":
        values.byteswap()
    mono = array("h")
    for index in range(0, len(values), 2):
        mixed = (int(values[index]) + int(values[index + 1])) // 2
        mono.append(max(-32768, min(32767, mixed)))
    if sys.byteorder != "little":
        mono.byteswap()
    return mono.tobytes()


async def _stop_process(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    process.terminate()
    try:
        await asyncio.wait_for(process.wait(), timeout=1.5)
    except asyncio.TimeoutError:
        process.kill()
        try:
            await asyncio.wait_for(process.wait(), timeout=1.5)
        except asyncio.TimeoutError as exc:
            raise AudioDeviceError("arecord did not exit after termination") from exc


class AlsaAudioIO:
    """One half-duplex ALSA endpoint with explicit agent-selected devices."""

    def __init__(self, settings: VoiceSettings, *, capture_device: str, playback_device: str):
        self.settings = settings
        self.capture_device = capture_device
        self.playback_device = playback_device
        self._operation_lock = threading.Lock()

    def capture_command(self, channels: int = 1) -> tuple[str, ...]:
        if channels not in {1, 2}:
            raise AudioDeviceError("capture channels must be mono or stereo")
        return (
            "arecord", "--quiet", "--format=S16_LE",
            f"--rate={self.settings.capture_sample_rate_hz}",
            f"--channels={channels}", "--file-type=raw",
            f"--device={self.capture_device}",
        )

    def playback_command(self, sample_rate_hz: int) -> tuple[str, ...]:
        playback_device = (
            "plughw:" + self.playback_device[3:]
            if self.playback_device.startswith("hw:")
            else self.playback_device
        )
        return (
            "aplay", "--quiet", "--format=S16_LE",
            f"--rate={sample_rate_hz}", f"--channels={self.settings.channels}",
            "--file-type=raw", f"--device={playback_device}",
        )

    def pulse_playback_command(self, sample_rate_hz: int) -> tuple[str, ...]:
        """Final fallback route through the desktop PulseAudio server."""

        return (
            "aplay", "--quiet", "--format=S16_LE",
            f"--rate={sample_rate_hz}", f"--channels={self.settings.channels}",
            "--file-type=raw", "--device=pulse",
        )

    def suspended_playback_command(self, sample_rate_hz: int) -> tuple[str, ...]:
        """Temporarily suspend PulseAudio and play directly to the selected PCM."""

        return ("pasuspender", "--", *self.playback_command(sample_rate_hz))

    @staticmethod
    def _require_binary(name: str) -> None:
        if shutil.which(name) is None:
            raise AudioDeviceError(f"required ALSA command is unavailable: {name}")

    async def capture_utterance(self, *, max_duration_seconds: float) -> bytes:
        """Capture one VAD-bounded signed-16-bit mono utterance in memory."""

        if not 1.0 <= max_duration_seconds <= self.settings.max_capture_seconds:
            raise AudioDeviceError("capture duration exceeds the configured voice-agent bound")
        self._require_binary("arecord")
        # Most UVC microphones expose only two channels. Users may force mono
        # or stereo after inspecting their device; auto defaults to stereo and
        # immediately downmixes to the fixed mono ASR contract.
        capture_channels = self.settings.capture_device_channels or 2
        with self._operation_lock:
            process = await asyncio.create_subprocess_exec(
                *self.capture_command(capture_channels), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            try:
                if process.stdout is None:
                    raise AudioDeviceError("arecord stdout was not available")
                vad = EnergyVad(VadSettings(
                    sample_rate_hz=self.settings.capture_sample_rate_hz,
                    frame_ms=self.settings.frame_ms,
                    speech_rms_threshold=self.settings.speech_rms_threshold,
                    start_speech_frames=self.settings.start_speech_frames,
                    stop_silence_ms=self.settings.stop_silence_ms,
                    max_duration_seconds=max_duration_seconds,
                ))
                pre_roll: list[bytes] = []
                utterance = bytearray()
                deadline = asyncio.get_running_loop().time() + max_duration_seconds
                while True:
                    remaining = deadline - asyncio.get_running_loop().time()
                    if remaining <= 0:
                        raise AudioDeviceError("arecord capture exceeded its time limit")
                    try:
                        raw_frame = await asyncio.wait_for(
                            process.stdout.readexactly(
                                vad.settings.frame_bytes * capture_channels
                            ),
                            timeout=remaining,
                        )
                    except asyncio.TimeoutError as exc:
                        raise AudioDeviceError("arecord capture timed out") from exc
                    frame = downmix_pcm16le(raw_frame, capture_channels)
                    decision = vad.feed(frame)
                    if decision.state == VadState.WAITING:
                        pre_roll.append(frame)
                        if len(pre_roll) > self.settings.start_speech_frames:
                            del pre_roll[0]
                    elif decision.started:
                        utterance.extend(b"".join(pre_roll))
                        pre_roll.clear()
                    else:
                        utterance.extend(frame)
                    if len(utterance) > self.settings.max_capture_bytes:
                        raise AudioDeviceError("captured PCM exceeded the configured memory bound")
                    if decision.complete:
                        if not utterance:
                            raise AudioDeviceError("capture ended before voice activity was detected")
                        return bytes(utterance)
            except asyncio.IncompleteReadError as exc:
                detail = ""
                if process.stderr is not None:
                    detail = (await process.stderr.read()).decode("utf-8", errors="replace").strip().split("\n")[0]
                raise AudioDeviceError(f"arecord ended before a complete VAD frame{': ' + detail if detail else ''}") from exc
            finally:
                with contextlib.suppress(ProcessLookupError):
                    await _stop_process(process)

    async def play_pcm(self, audio: SynthesizedAudio) -> None:
        """Play finite signed-16-bit mono PCM and wait for local completion."""

        if audio.channels != self.settings.channels or audio.sample_width_bytes != self.settings.sample_width_bytes:
            raise AudioDeviceError("TTS PCM does not match the mono signed-16-bit playback contract")
        if not 8_000 <= audio.sample_rate_hz <= 48_000 or not audio.pcm or len(audio.pcm) % 2:
            raise AudioDeviceError("TTS PCM violates the playback contract")
        if len(audio.pcm) > self.settings.max_capture_bytes * 4:
            raise AudioDeviceError("TTS PCM exceeded the configured playback memory bound")
        self._require_binary("aplay")

        async def run(command: tuple[str, ...]) -> tuple[int, str]:
            def execute() -> tuple[int, str]:
                try:
                    completed = subprocess.run(
                        command,
                        input=audio.pcm,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.PIPE,
                        timeout=max(10.0, len(audio.pcm) / (audio.sample_rate_hz * 2) + 8.0),
                        check=False,
                    )
                except (OSError, subprocess.TimeoutExpired) as exc:
                    return 1, str(exc)
                detail = completed.stderr.decode("utf-8", errors="replace").strip().split("\n")[0]
                return completed.returncode, detail

            return await asyncio.to_thread(execute)

        with self._operation_lock:
            returncode, detail = await run(self.playback_command(audio.sample_rate_hz))
            if returncode != 0 and "busy" in detail.lower():
                if shutil.which("pasuspender") is not None:
                    returncode, detail = await run(self.suspended_playback_command(audio.sample_rate_hz))
                if returncode != 0:
                    returncode, detail = await run(self.pulse_playback_command(audio.sample_rate_hz))
            if returncode != 0:
                raise AudioDeviceError(
                    f"aplay exited with status {returncode}{': ' + detail if detail else ''}"
                )


__all__ = ["AlsaAudioIO", "AlsaPcmDevice", "AudioDeviceError", "discover_voice_devices", "enumerate_alsa_devices"]
