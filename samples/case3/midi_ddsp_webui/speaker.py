from __future__ import annotations

import json
import math
import re
import shutil
import subprocess
import threading
import time
from typing import Any, Callable

import numpy as np

from .core import ResourceCoordinator


CHANNEL_MODES = {"left", "both", "right"}
SAMPLE_SPEC_PATTERN = re.compile(r"(?P<channels>\d+)ch\s+(?P<rate>\d+)Hz")


def query_audio_inputs(
    portaudio_query: Callable[[], Any] | None = None,
) -> list[dict[str, object]]:
    """Return capture sources and monitor sources without treating them alike."""
    if shutil.which("pactl") is not None:
        try:
            result = subprocess.run(
                ["pactl", "--format=json", "list", "sources"],
                text=True,
                capture_output=True,
                timeout=5.0,
                check=False,
            )
            if result.returncode == 0:
                sources = json.loads(result.stdout)
                if not isinstance(sources, list):
                    raise TypeError("PulseAudio source response is not a list")
                inputs = []
                for source in sources:
                    if not isinstance(source, dict):
                        continue
                    name = str(source.get("name", "")).strip()
                    if not name:
                        continue
                    properties = source.get("properties", {})
                    if not isinstance(properties, dict):
                        properties = {}
                    source_type = (
                        "monitor"
                        if name.endswith(".monitor")
                        or str(properties.get("device.class", "")).lower() == "monitor"
                        else "capture"
                    )
                    match = SAMPLE_SPEC_PATTERN.search(
                        str(source.get("sample_specification", ""))
                    )
                    state = str(source.get("state", "UNKNOWN")).lower()
                    inputs.append(
                        {
                            "id": f"pulse:{name}",
                            "index": int(source.get("index", len(inputs))),
                            "name": str(
                                source.get("description")
                                or properties.get("device.description")
                                or name
                            ),
                            "source_name": name,
                            "host_api": "PulseAudio",
                            "backend": "pulse",
                            "type": source_type,
                            "max_input_channels": (
                                int(match.group("channels")) if match else 1
                            ),
                            "default_sample_rate": (
                                int(match.group("rate")) if match else 44_100
                            ),
                            "state": state,
                            "available": source_type == "capture"
                            and state not in {"unavailable", "failed"},
                        }
                    )
                return inputs
        except (
            AttributeError,
            OSError,
            ValueError,
            subprocess.TimeoutExpired,
            json.JSONDecodeError,
            TypeError,
        ):
            pass

    if portaudio_query is None:
        try:
            import sounddevice as sd
        except ImportError as exc:
            raise RuntimeError("Install requirements.txt first.") from exc
        portaudio_query = sd.query_devices
    inputs = []
    for index, device in enumerate(portaudio_query()):
        channels = int(device.get("max_input_channels", 0))
        if channels <= 0:
            continue
        name = str(device.get("name", f"Input {index}"))
        source_type = "monitor" if "monitor" in name.lower() else "capture"
        inputs.append(
            {
                "id": str(index),
                "index": index,
                "name": name,
                "host_api": str(device.get("host_api", "PortAudio")),
                "backend": "portaudio",
                "type": source_type,
                "max_input_channels": channels,
                "default_sample_rate": int(device.get("default_samplerate", 0)),
                "state": "available",
                "available": source_type == "capture",
            }
        )
    return inputs


def query_speaker_outputs(
    portaudio_query: Callable[[], list[dict[str, object]]] | None = None,
) -> list[dict[str, object]]:
    if shutil.which("pactl") is not None and shutil.which("paplay") is not None:
        try:
            sinks_result = subprocess.run(
                ["pactl", "--format=json", "list", "sinks"],
                text=True,
                capture_output=True,
                timeout=5.0,
                check=False,
            )
            default_result = subprocess.run(
                ["pactl", "get-default-sink"],
                text=True,
                capture_output=True,
                timeout=5.0,
                check=False,
            )
            if sinks_result.returncode == 0:
                sinks = json.loads(sinks_result.stdout)
                default_sink = (
                    default_result.stdout.strip()
                    if default_result.returncode == 0
                    else ""
                )
                if not isinstance(sinks, list):
                    raise TypeError("PulseAudio sink response is not a list")
                outputs = []
                for sink in sinks:
                    if not isinstance(sink, dict):
                        continue
                    sink_name = str(sink.get("name", "")).strip()
                    if not sink_name:
                        continue
                    match = SAMPLE_SPEC_PATTERN.search(
                        str(sink.get("sample_specification", ""))
                    )
                    channels = int(match.group("channels")) if match else 2
                    sample_rate = int(match.group("rate")) if match else 44_100
                    properties = sink.get("properties", {})
                    if not isinstance(properties, dict):
                        properties = {}
                    description = str(
                        sink.get("description")
                        or properties.get("device.description")
                        or sink_name
                    )
                    outputs.append(
                        {
                            "id": f"pulse:{sink_name}",
                            "index": int(sink.get("index", len(outputs))),
                            "name": description,
                            "host_api": "PulseAudio",
                            "backend": "pulse",
                            "sink_name": sink_name,
                            "max_output_channels": channels,
                            "default_sample_rate": sample_rate,
                            "is_default": sink_name == default_sink,
                            "state": str(sink.get("state", "UNKNOWN")).lower(),
                        }
                    )
                if outputs:
                    return outputs
        except (
            AttributeError,
            OSError,
            ValueError,
            subprocess.TimeoutExpired,
            json.JSONDecodeError,
            TypeError,
        ):
            pass

    if portaudio_query is None:
        from realtime_ddsp import query_audio_devices

        portaudio_query = query_audio_devices
    outputs = []
    for device in portaudio_query():
        outputs.append(
            {
                **device,
                "backend": "portaudio",
                "is_default": str(device.get("name", "")).lower() in {"default", "pulse"},
                "state": "available",
            }
        )
    return outputs


def build_test_signal(
    sample_rate: int,
    duration_seconds: float,
    frequency_hz: float,
    level_db: float,
    channels: int,
    channel_mode: str,
) -> np.ndarray:
    if channel_mode not in CHANNEL_MODES:
        raise ValueError(f"Unknown speaker channel mode: {channel_mode}")
    if channels not in {1, 2}:
        raise ValueError("Speaker test supports one or two output channels")
    if channel_mode == "right" and channels < 2:
        raise ValueError("Right-channel test requires a stereo output device")

    frames = max(1, round(sample_rate * duration_seconds))
    phase = np.arange(frames, dtype=np.float64) / float(sample_rate)
    amplitude = 10.0 ** (level_db / 20.0)
    tone = (np.sin(2.0 * math.pi * frequency_hz * phase) * amplitude).astype(
        np.float32
    )

    fade_frames = min(round(sample_rate * 0.02), frames // 2)
    if fade_frames > 0:
        ramp = np.linspace(0.0, 1.0, fade_frames, endpoint=False, dtype=np.float32)
        tone[:fade_frames] *= ramp
        tone[-fade_frames:] *= ramp[::-1]

    output = np.zeros((frames, channels), dtype=np.float32)
    if channels == 1:
        output[:, 0] = tone
    elif channel_mode == "left":
        output[:, 0] = tone
    elif channel_mode == "right":
        output[:, 1] = tone
    else:
        output[:, 0] = tone
        output[:, 1] = tone
    return output


class SpeakerTestController:
    OWNER = "speaker-test"

    def __init__(
        self,
        coordinator: ResourceCoordinator,
        sounddevice_module: Any | None = None,
        popen_factory: Callable[..., Any] | None = None,
    ) -> None:
        self.coordinator = coordinator
        self._sounddevice_module = sounddevice_module
        self._popen_factory = popen_factory or subprocess.Popen
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._ready_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._process: Any | None = None
        self._state = "idle"
        self._error: str | None = None
        self._config: dict[str, object] = {}
        self._device_name = ""
        self._sample_rate = 0
        self._output_channels = 0
        self._played_frames = 0
        self._total_frames = 0
        self._underruns = 0
        self._started_at = 0.0

    @property
    def running(self) -> bool:
        with self._lock:
            return self._state in {"starting", "running", "stopping"}

    def _sounddevice(self) -> Any:
        if self._sounddevice_module is not None:
            return self._sounddevice_module
        try:
            import sounddevice as sd
        except ImportError as exc:
            raise RuntimeError("Install requirements.txt first.") from exc
        return sd

    def status(self) -> dict[str, object]:
        with self._lock:
            state = self._state
            config = dict(self._config)
            played_frames = self._played_frames
            total_frames = self._total_frames
            sample_rate = self._sample_rate
            started_at = self._started_at
            payload = {
                "running": state in {"starting", "running", "stopping"},
                "state": state,
                "error": self._error,
                "device_name": self._device_name,
                "sample_rate": sample_rate,
                "output_channels": self._output_channels,
                "played_frames": played_frames,
                "total_frames": total_frames,
                "underruns": self._underruns,
                "config": config,
            }
        progress = played_frames / total_frames if total_frames else 0.0
        elapsed = time.monotonic() - started_at if started_at else 0.0
        duration = total_frames / sample_rate if sample_rate else 0.0
        payload.update(
            {
                "progress": min(max(progress, 0.0), 1.0),
                "elapsed_seconds": min(elapsed, duration) if duration else 0.0,
                "remaining_seconds": max(duration - elapsed, 0.0),
            }
        )
        return payload

    def start(self, config: dict[str, object]) -> dict[str, object]:
        self.coordinator.acquire(self.OWNER)
        self._stop_event = threading.Event()
        self._ready_event = threading.Event()
        with self._lock:
            self._state = "starting"
            self._error = None
            self._config = dict(config)
            self._device_name = str(config.get("device_name", ""))
            self._sample_rate = 0
            self._output_channels = 0
            self._played_frames = 0
            self._total_frames = 0
            self._underruns = 0
            self._started_at = time.monotonic()
        thread = threading.Thread(
            target=self._run,
            daemon=True,
            name="speaker-test",
        )
        self._thread = thread
        thread.start()
        if not self._ready_event.wait(timeout=10.0):
            self._stop_event.set()
            raise RuntimeError("Timed out while opening the audio output device")
        with self._lock:
            error = self._error
        if error is not None:
            thread.join(timeout=1.0)
            raise RuntimeError(error)
        return self.status()

    def stop(self) -> dict[str, object]:
        with self._lock:
            thread = self._thread
            process = self._process
            if self._state in {"starting", "running"}:
                self._state = "stopping"
        if thread is None or not thread.is_alive():
            return self.status()
        self._stop_event.set()
        if process is not None and process.poll() is None:
            process.terminate()
        thread.join(timeout=5.0)
        if thread.is_alive():
            raise RuntimeError("Timed out while stopping the speaker test")
        return self.status()

    def _run(self) -> None:
        try:
            if self._config.get("audio_backend") == "pulse":
                self._run_pulse()
            else:
                self._run_portaudio()
        except Exception as exc:
            with self._lock:
                if self._stop_event.is_set():
                    self._state = "stopped"
                    self._error = None
                else:
                    self._state = "failed"
                    self._error = str(exc)
            self._ready_event.set()
        finally:
            with self._lock:
                self._process = None
            self.coordinator.release(self.OWNER)

    def _run_portaudio(self) -> None:
        sd = self._sounddevice()
        device_id = int(str(self._config["audio_device_id"]))
        channel_mode = str(self._config["channel_mode"])
        device = sd.query_devices(device_id, "output")
        max_channels = int(device["max_output_channels"])
        if max_channels <= 0:
            raise RuntimeError("Selected device has no output channels")
        output_channels = 2 if max_channels >= 2 else 1
        sample_rate = round(float(device["default_samplerate"])) or 48_000
        signal = build_test_signal(
            sample_rate=sample_rate,
            duration_seconds=float(self._config["duration_seconds"]),
            frequency_hz=float(self._config["frequency_hz"]),
            level_db=float(self._config["level_db"]),
            channels=output_channels,
            channel_mode=channel_mode,
        )
        sd.check_output_settings(
            device=device_id,
            samplerate=sample_rate,
            channels=output_channels,
            dtype="float32",
        )
        with sd.OutputStream(
            device=device_id,
            samplerate=sample_rate,
            channels=output_channels,
            dtype="float32",
            latency="high",
        ) as stream:
            with self._lock:
                self._device_name = str(device["name"])
                self._sample_rate = sample_rate
                self._output_channels = output_channels
                self._total_frames = len(signal)
                self._state = "running"
            self._ready_event.set()
            block_frames = 1024
            for offset in range(0, len(signal), block_frames):
                if self._stop_event.is_set():
                    break
                block = signal[offset : offset + block_frames]
                underflowed = bool(stream.write(block))
                with self._lock:
                    self._played_frames += len(block)
                    if underflowed:
                        self._underruns += 1
            with self._lock:
                self._state = (
                    "stopped" if self._stop_event.is_set() else "succeeded"
                )

    def _run_pulse(self) -> None:
        sink_name = str(self._config["pulse_sink"])
        channel_mode = str(self._config["channel_mode"])
        max_channels = int(self._config.get("max_output_channels", 2))
        output_channels = 2 if max_channels >= 2 else 1
        sample_rate = int(self._config.get("default_sample_rate", 44_100))
        signal = build_test_signal(
            sample_rate=sample_rate,
            duration_seconds=float(self._config["duration_seconds"]),
            frequency_hz=float(self._config["frequency_hz"]),
            level_db=float(self._config["level_db"]),
            channels=output_channels,
            channel_mode=channel_mode,
        )
        process = self._popen_factory(
            [
                "paplay",
                "--playback",
                f"--device={sink_name}",
                "--client-name=MIDI-DDSP Studio",
                "--stream-name=Speaker Test",
                "--raw",
                "--format=float32le",
                f"--rate={sample_rate}",
                f"--channels={output_channels}",
                "--latency-msec=100",
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        if process.stdin is None:
            raise RuntimeError("Unable to open paplay input stream")
        with self._lock:
            self._process = process
            self._device_name = str(self._config.get("device_name", sink_name))
            self._sample_rate = sample_rate
            self._output_channels = output_channels
            self._total_frames = len(signal)
            self._state = "running"
        self._ready_event.set()

        block_frames = 1024
        for offset in range(0, len(signal), block_frames):
            if self._stop_event.is_set():
                break
            block = signal[offset : offset + block_frames]
            process.stdin.write(block.astype("<f4", copy=False).tobytes())
            with self._lock:
                self._played_frames += len(block)
        process.stdin.close()
        return_code = process.wait(timeout=5.0)
        if self._stop_event.is_set():
            with self._lock:
                self._state = "stopped"
            return
        if return_code != 0:
            error = process.stderr.read().decode("utf-8", errors="replace").strip()
            raise RuntimeError(error or f"paplay exited with code {return_code}")
        with self._lock:
            self._state = "succeeded"
