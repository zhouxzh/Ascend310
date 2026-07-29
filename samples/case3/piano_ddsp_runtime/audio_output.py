"""Bounded realtime audio output, recording, and latency inspection."""

from __future__ import annotations

import os
from pathlib import Path
import queue
import re
import shutil
import subprocess
import threading
import time
import wave
from typing import Callable

import numpy as np

from .metrics import RuntimeMetrics


_PULSE_LATENCY_RE = re.compile(r"(Buffer|Sink) Latency:\s*(\d+) usec")


def _stereo_to_mono_s16(stereo: np.ndarray) -> bytes:
    samples = np.asarray(stereo, dtype=np.float32)
    mono = np.mean(samples, axis=1, dtype=np.float32)
    return np.rint(np.clip(mono, -1.0, 1.0) * 32767.0).astype(
        "<i2", copy=False
    ).tobytes()


def _configure_alsa_output_route(
    card: int,
    route_device_id: int,
    playback_level: int,
) -> None:
    if shutil.which("amixer") is None:
        raise RuntimeError("amixer is unavailable for the selected onboard output")
    for control, value in (
        ("Playback", playback_level),
        ("Deviceid", route_device_id),
    ):
        result = subprocess.run(
            ["amixer", "-c", str(card), "set", control, str(value)],
            text=True,
            capture_output=True,
            timeout=5.0,
            check=False,
        )
        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip()
            raise RuntimeError(
                detail or f"Unable to set ALSA control {control}={value}"
            )


class WavRecorder:
    def __init__(self) -> None:
        self._wave: wave.Wave_write | None = None
        self._path: Path | None = None
        self._lock = threading.Lock()

    @property
    def active(self) -> bool:
        with self._lock:
            return self._wave is not None

    @property
    def path(self) -> Path | None:
        with self._lock:
            return self._path

    def start(self, path: Path, sample_rate: int) -> Path:
        path = Path(path).resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            if self._wave is not None:
                raise RuntimeError("A Piano-DDSP recording is already active")
            handle = wave.open(str(path), "wb")
            handle.setnchannels(2)
            handle.setsampwidth(2)
            handle.setframerate(int(sample_rate))
            self._wave = handle
            self._path = path
        return path

    def write(self, stereo: np.ndarray) -> None:
        pcm = np.clip(np.asarray(stereo, dtype=np.float32), -1.0, 1.0)
        payload = np.rint(pcm * 32767.0).astype("<i2", copy=False).tobytes()
        with self._lock:
            if self._wave is not None:
                self._wave.writeframesraw(payload)

    def stop(self) -> Path | None:
        with self._lock:
            handle, path = self._wave, self._path
            self._wave = None
            self._path = None
        if handle is not None:
            handle.close()
        return path


class BoundedAudioOutput:
    """Consume stereo float32 blocks without allowing producer backpressure growth."""

    def __init__(
        self,
        sample_rate: int,
        block_samples: int,
        capacity: int,
        prebuffer: int,
        latency_ms: float,
        metrics: RuntimeMetrics,
        *,
        backend: str = "pulse",
        sink_name: str | None = None,
        device: str | int | None = None,
        alsa_device: str | None = None,
        alsa_card: int | None = None,
        alsa_route_device_id: int | None = None,
        alsa_playback_level: int = 10,
        on_played: Callable[[np.ndarray], None] | None = None,
    ) -> None:
        if capacity < 2 or not 1 <= prebuffer < capacity:
            raise ValueError("Audio queue requires 1 <= prebuffer < capacity")
        self.sample_rate = int(sample_rate)
        self.block_samples = int(block_samples)
        self.latency_ms = float(latency_ms)
        self.prebuffer = int(prebuffer)
        self.metrics = metrics
        self.backend = backend
        self.sink_name = sink_name
        self.device = device
        self.alsa_device = alsa_device
        self.alsa_card = alsa_card
        self.alsa_route_device_id = alsa_route_device_id
        self.alsa_playback_level = int(alsa_playback_level)
        self.on_played = on_played
        self.queue: queue.Queue[np.ndarray] = queue.Queue(maxsize=capacity)
        self.stop_event = threading.Event()
        self.ready = threading.Event()
        self.error: BaseException | None = None
        self.device_latency_ms = self.latency_ms
        self.sink_latency_ms = 0.0
        self.pulse_buffer_latency_ms = 0.0
        self.process: subprocess.Popen[bytes] | None = None
        self.stream = None
        self.worker = threading.Thread(target=self._write_loop, name="piano-audio", daemon=True)

    @property
    def buffered_blocks(self) -> int:
        return self.queue.qsize()

    def start(self) -> None:
        if self.backend == "pulse":
            if shutil.which("paplay") is None:
                raise RuntimeError("paplay is unavailable for the selected PulseAudio output")
            command = [
                "paplay",
                "--playback",
                "--client-name=Piano-DDSP",
                "--stream-name=Piano-DDSP Realtime",
                "--raw",
                "--format=float32le",
                f"--rate={self.sample_rate}",
                "--channels=2",
                f"--latency-msec={round(self.latency_ms)}",
            ]
            if self.sink_name:
                command.insert(2, f"--device={self.sink_name}")
            self.process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                bufsize=0,
            )
        elif self.backend == "alsa_mono":
            if shutil.which("aplay") is None:
                raise RuntimeError("aplay is unavailable for the onboard headset output")
            _configure_alsa_output_route(
                int(self.alsa_card or 0),
                int(self.alsa_route_device_id or 2),
                self.alsa_playback_level,
            )
            self.process = subprocess.Popen(
                [
                    "aplay",
                    "--quiet",
                    "-D",
                    self.alsa_device or "hw:ascend310b",
                    "-t",
                    "raw",
                    "-f",
                    "S16_LE",
                    "-r",
                    str(self.sample_rate),
                    "-c",
                    "1",
                ],
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                bufsize=0,
            )
            self.device_latency_ms = max(self.device_latency_ms, 100.0)
        elif self.backend == "portaudio":
            if self.alsa_route_device_id is not None:
                _configure_alsa_output_route(
                    int(self.alsa_card or 0),
                    int(self.alsa_route_device_id),
                    self.alsa_playback_level,
                )
            try:
                import sounddevice as sd
            except ImportError as exc:
                raise RuntimeError("sounddevice is unavailable") from exc
            self.stream = sd.RawOutputStream(
                samplerate=self.sample_rate,
                blocksize=self.block_samples,
                channels=2,
                dtype="float32",
                device=self.device,
                latency=self.latency_ms / 1000.0,
            )
            self.stream.start()
            latency = getattr(self.stream, "latency", None)
            if latency:
                self.device_latency_ms = float(latency) * 1000.0
        else:
            raise ValueError(f"Unsupported audio backend: {self.backend}")
        self.worker.start()

    def submit(self, stereo: np.ndarray) -> bool:
        block = np.ascontiguousarray(stereo, dtype=np.float32)
        if block.shape != (self.block_samples, 2):
            raise ValueError(f"Expected audio block {(self.block_samples, 2)}, got {block.shape}")
        try:
            self.queue.put(block, timeout=self.block_samples / self.sample_rate)
            if self.queue.qsize() >= self.prebuffer:
                self.ready.set()
            return True
        except queue.Full:
            self.metrics.increment("overruns")
            return False

    def clear(self) -> None:
        while True:
            try:
                self.queue.get_nowait()
            except queue.Empty:
                return

    def _write_loop(self) -> None:
        self.ready.wait(timeout=1.0)
        silence = np.zeros((self.block_samples, 2), dtype=np.float32)
        timeout = self.block_samples / self.sample_rate
        try:
            while not self.stop_event.is_set():
                try:
                    block = self.queue.get(timeout=timeout)
                except queue.Empty:
                    self.metrics.increment("underruns")
                    block = silence
                started = time.perf_counter()
                if self.backend in {"pulse", "alsa_mono"}:
                    if self.process is None or self.process.stdin is None:
                        raise RuntimeError("Subprocess audio output is not open")
                    payload = (
                        _stereo_to_mono_s16(block)
                        if self.backend == "alsa_mono"
                        else block.astype("<f4", copy=False).tobytes()
                    )
                    self.process.stdin.write(payload)
                else:
                    if self.stream is None:
                        raise RuntimeError("PortAudio output is not open")
                    self.stream.write(block.tobytes())
                self.metrics.add("write_ms", (time.perf_counter() - started) * 1000.0)
                self.metrics.increment("played_blocks")
                if self.on_played is not None:
                    self.on_played(block)
                if self.backend in {"pulse", "alsa_mono"} and self.process is not None and self.process.poll() is not None:
                    error = self.process.stderr.read().decode("utf-8", errors="replace").strip()
                    raise RuntimeError(error or "paplay exited")
        except BaseException as exc:
            if not self.stop_event.is_set():
                self.error = exc
            self.stop_event.set()

    def refresh_latencies(self) -> None:
        if self.backend != "pulse" or self.process is None or shutil.which("pactl") is None:
            return
        try:
            result = subprocess.run(
                ["pactl", "list", "sink-inputs"], capture_output=True, text=True, timeout=1, check=False
            )
        except (OSError, subprocess.TimeoutExpired):
            return
        marker = f'application.process.id = "{self.process.pid}"'
        for section in re.split(r"(?=Sink Input #)", result.stdout):
            if marker not in section:
                continue
            values = {name: int(value) / 1000.0 for name, value in _PULSE_LATENCY_RE.findall(section)}
            self.pulse_buffer_latency_ms = values.get("Buffer", self.pulse_buffer_latency_ms)
            self.sink_latency_ms = values.get("Sink", self.sink_latency_ms)
            return

    def close(self) -> None:
        self.stop_event.set()
        self.ready.set()
        process = self.process
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                process.kill()
        if self.worker.ident is not None:
            self.worker.join(timeout=2.0)
        if self.stream is not None:
            try:
                self.stream.stop()
                self.stream.close()
            finally:
                self.stream = None
        if process is not None:
            if process.stdin is not None:
                try:
                    process.stdin.close()
                except OSError:
                    pass
            try:
                process.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2.0)
            self.process = None
