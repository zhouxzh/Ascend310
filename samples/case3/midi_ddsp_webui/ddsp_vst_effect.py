from __future__ import annotations

from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass
import hashlib
import math
from pathlib import Path
import shutil
import subprocess
import threading
import time
from typing import Any

import numpy as np

from pyacl_ddsp import PyAclModelRunner
from realtime_ddsp import (
    DdspVstSettings,
    HarmonicSynthesizer,
    JuceFreeverb,
    ModelControls,
    NoiseSynthesizer,
    PyAclControlsModel,
    WindowedSincResampler,
)

from .core import ROOT, ResourceCoordinator
from .speaker import amplitude_to_dbfs


MODEL_SAMPLE_RATE = 16_000
MODEL_WINDOW_SIZE = 1024
MODEL_HOP_SIZE = 320
CAPTURE_SAMPLE_RATE = 48_000
OUTPUT_SAMPLE_RATE = 48_000
CAPTURE_CHANNELS = 2
OUTPUT_CHANNELS = 2
PULSE_LATENCY_MS = 20
SAFETY_OVERLOAD_FRAMES = 5
MODEL_FRAME_MS = MODEL_HOP_SIZE * 1000.0 / MODEL_SAMPLE_RATE
GATE_CALIBRATION_FRAMES = round(1_200.0 / MODEL_FRAME_MS)
GATE_CALIBRATION_MARGIN_DB = 8.0
GATE_CALIBRATED_MIN_DBFS = -60.0
GATE_CALIBRATED_MAX_DBFS = -24.0

FEATURE_OM_PATH = ROOT / "models" / "om" / "ddsp_vst_feature_mixed_float16.om"
FEATURE_OM_SHA256 = "a1973830eca98111642dcb331e0a1a163f7a664d871e6d15f40fdc70f9b98db4"
MODEL_MANIFEST_PATH = ROOT / "models" / "manifests" / "SHA256SUMS.txt"

FEATURE_INPUT_SHAPES = {"audio": (MODEL_WINDOW_SIZE,)}
FEATURE_OUTPUT_SHAPES = {
    "f0_scaled": (1,),
    "pw_scaled": (1,),
    "f0_hz": (1,),
    "pw_db": (1,),
}

PARAMETER_RANGES = {
    "transpose": (-24.0, 24.0),
    "input_pitch": (-0.5, 0.5),
    "input_gain": (-0.5, 0.5),
    "harmonic_gain": (0.0, 1.0),
    "noise_gain": (0.0, 1.0),
    "output_gain_db": (-60.0, 6.0),
    "reverb_size": (0.0, 1.0),
    "reverb_damping": (0.0, 1.0),
    "reverb_wet": (0.0, 1.0),
    "gate_threshold_dbfs": (-80.0, -20.0),
    "gate_hysteresis_db": (0.0, 18.0),
    "gate_hold_ms": (0.0, 1_000.0),
    "gate_attack_ms": (1.0, 200.0),
    "gate_release_ms": (20.0, 2_000.0),
}

DEFAULT_PARAMETERS = {
    "transpose": 0.0,
    "input_pitch": 0.0,
    "input_gain": 0.0,
    "harmonic_gain": 1.0,
    "noise_gain": 1.0,
    "output_gain_db": -18.0,
    "reverb_size": 0.4,
    "reverb_damping": 0.1,
    "reverb_wet": 0.0,
    "gate_threshold_dbfs": -40.0,
    "gate_hysteresis_db": 6.0,
    "gate_hold_ms": 160.0,
    "gate_attack_ms": 10.0,
    "gate_release_ms": 180.0,
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_parameters(values: Mapping[str, object]) -> dict[str, float]:
    unknown = sorted(set(values) - set(PARAMETER_RANGES))
    if unknown:
        raise ValueError(f"Unknown DDSP-VST Effect parameters: {unknown}")
    result: dict[str, float] = {}
    for name, raw in values.items():
        value = float(raw)
        minimum, maximum = PARAMETER_RANGES[name]
        if not math.isfinite(value) or value < minimum or value > maximum:
            raise ValueError(f"{name} must be between {minimum} and {maximum}")
        result[name] = value
    return result


def _manifest_hash(path: Path, manifest_path: Path) -> str:
    if not manifest_path.is_file():
        raise FileNotFoundError(f"Model manifest not found: {manifest_path}")
    expected_name = f"om/{path.name}"
    for line in manifest_path.read_text(encoding="utf-8").splitlines():
        parts = line.strip().split(maxsplit=1)
        if len(parts) == 2 and parts[1].replace("\\", "/") == expected_name:
            return parts[0].lower()
    raise ValueError(f"Model manifest does not contain {expected_name}")


def validate_runtime_models(
    feature_path: Path,
    control_path: Path,
    *,
    feature_sha256: str = FEATURE_OM_SHA256,
    manifest_path: Path = MODEL_MANIFEST_PATH,
) -> dict[str, str]:
    for label, path in (("Feature", feature_path), ("Control", control_path)):
        if path.suffix.lower() != ".om":
            raise ValueError(f"{label} backend must be an OM model")
        if not path.is_file():
            raise FileNotFoundError(f"{label} OM not found: {path}")
    actual_feature = sha256_file(feature_path)
    if actual_feature != feature_sha256.lower():
        raise ValueError("Feature OM SHA256 does not match the published release")
    expected_control = _manifest_hash(control_path, manifest_path)
    actual_control = sha256_file(control_path)
    if actual_control != expected_control:
        raise ValueError("Control OM SHA256 does not match the model manifest")
    return {"feature": actual_feature, "control": actual_control}


@dataclass(frozen=True)
class FeatureValues:
    f0_scaled: float
    pw_scaled: float
    f0_hz: float
    pw_db: float


class FeatureOmModel:
    backend_name = "acl/om"

    def __init__(
        self,
        model_path: Path,
        device_id: int = 0,
        *,
        runner_factory: Callable[..., Any] = PyAclModelRunner,
    ) -> None:
        self.runner = runner_factory(
            model_path,
            device_id=device_id,
            keep_runtime=True,
            input_shapes=FEATURE_INPUT_SHAPES,
            output_shapes=FEATURE_OUTPUT_SHAPES,
        )

    def predict(self, audio: np.ndarray) -> FeatureValues:
        values = self.runner.infer(
            {"audio": np.ascontiguousarray(audio, dtype=np.float32).reshape(MODEL_WINDOW_SIZE)}
        )
        return FeatureValues(
            f0_scaled=float(values["f0_scaled"][0]),
            pw_scaled=float(values["pw_scaled"][0]),
            f0_hz=float(values["f0_hz"][0]),
            pw_db=float(values["pw_db"][0]),
        )

    def close(self) -> None:
        self.runner.close()


class DdspVstEffectProcessor:
    def __init__(
        self,
        feature_model: Any,
        controls_model: Any,
        parameters: Mapping[str, object] | None = None,
    ) -> None:
        self.feature_model = feature_model
        self.controls_model = controls_model
        self.input_resampler = WindowedSincResampler(CAPTURE_SAMPLE_RATE, MODEL_SAMPLE_RATE)
        self.output_resampler = WindowedSincResampler(MODEL_SAMPLE_RATE, OUTPUT_SAMPLE_RATE)
        self.input_resampler.prepare(round(MODEL_HOP_SIZE * CAPTURE_SAMPLE_RATE / MODEL_SAMPLE_RATE))
        self.output_resampler.prepare(MODEL_HOP_SIZE)
        self.window = np.zeros(MODEL_WINDOW_SIZE, dtype=np.float32)
        self.state = np.zeros(512, dtype=np.float32)
        self.harmonic = HarmonicSynthesizer()
        self.noise = NoiseSynthesizer()
        self._parameter_lock = threading.Lock()
        self._metric_lock = threading.Lock()
        self._gate_lock = threading.Lock()
        self._parameters = dict(DEFAULT_PARAMETERS)
        if parameters:
            self._parameters.update(validate_parameters(parameters))
        self.reverb = JuceFreeverb(OUTPUT_SAMPLE_RATE, self._settings(self._parameters))
        self.feature_times_ms: deque[float] = deque(maxlen=1000)
        self.control_times_ms: deque[float] = deque(maxlen=1000)
        self.frames = 0
        self.input_rms_dbfs = -96.0
        self.input_peak_dbfs = -96.0
        self.output_rms_dbfs = -96.0
        self.output_peak_dbfs = -96.0
        self.f0_hz = 0.0
        self.pw_db = -96.0
        self.clipped_samples = 0
        self.overload_frames = 0
        self.safety_muted = False
        self.gate_open = False
        self.gate_gain = 0.0
        self.gate_hold_frames = 0
        self.gated_frames = 0
        self.noise_floor_dbfs = -96.0
        self.calibrating = False
        self.calibration_levels: list[float] = []
        self.calibration_progress = 0.0
        self._closed = False

    @staticmethod
    def _settings(parameters: Mapping[str, float]) -> DdspVstSettings:
        return DdspVstSettings(
            pitch_shift=parameters["transpose"],
            harmonic_gain=parameters["harmonic_gain"],
            noise_gain=parameters["noise_gain"],
            output_gain_db=parameters["output_gain_db"],
            input_pitch=parameters["input_pitch"],
            input_gain=parameters["input_gain"],
            reverb_size=parameters["reverb_size"],
            reverb_damping=parameters["reverb_damping"],
            reverb_wet=parameters["reverb_wet"],
        )

    @property
    def parameters(self) -> dict[str, float]:
        with self._parameter_lock:
            return dict(self._parameters)

    def update_parameters(self, values: Mapping[str, object]) -> dict[str, float]:
        validated = validate_parameters(values)
        with self._parameter_lock:
            self._parameters.update(validated)
            parameters = dict(self._parameters)
            self.reverb.update(self._settings(parameters))
        return parameters

    def begin_calibration(self) -> None:
        """Measure quiet-room input while keeping the synthesized output muted."""
        with self._gate_lock:
            self.calibrating = True
            self.calibration_levels = []
            self.calibration_progress = 0.0
            self.noise_floor_dbfs = -96.0
            self.gate_open = False
            self.gate_gain = 0.0
            self.gate_hold_frames = 0

    def _gate_envelope(
        self,
        input_rms_dbfs: float,
        parameters: Mapping[str, float],
    ) -> tuple[float, float]:
        threshold = float(parameters["gate_threshold_dbfs"])
        with self._gate_lock:
            if self.calibrating:
                self.calibration_levels.append(input_rms_dbfs)
                self.calibration_progress = min(
                    1.0,
                    len(self.calibration_levels) / GATE_CALIBRATION_FRAMES,
                )
                if len(self.calibration_levels) >= GATE_CALIBRATION_FRAMES:
                    self.noise_floor_dbfs = float(
                        np.quantile(self.calibration_levels, 0.9)
                    )
                    threshold = float(
                        np.clip(
                            self.noise_floor_dbfs + GATE_CALIBRATION_MARGIN_DB,
                            GATE_CALIBRATED_MIN_DBFS,
                            GATE_CALIBRATED_MAX_DBFS,
                        )
                    )
                    with self._parameter_lock:
                        self._parameters["gate_threshold_dbfs"] = threshold
                    self.calibrating = False
                    self.calibration_progress = 1.0

            hold_frames = max(
                0,
                round(float(parameters["gate_hold_ms"]) / MODEL_FRAME_MS),
            )
            close_threshold = threshold - float(parameters["gate_hysteresis_db"])
            if self.calibrating:
                self.gate_open = False
                self.gate_hold_frames = 0
            elif self.gate_open:
                if input_rms_dbfs >= close_threshold:
                    self.gate_hold_frames = hold_frames
                elif self.gate_hold_frames > 0:
                    self.gate_hold_frames -= 1
                else:
                    self.gate_open = False
            elif input_rms_dbfs >= threshold:
                self.gate_open = True
                self.gate_hold_frames = hold_frames

            previous_gain = self.gate_gain
            target_gain = 1.0 if self.gate_open and not self.calibrating else 0.0
            time_ms = float(
                parameters["gate_attack_ms"]
                if target_gain > previous_gain
                else parameters["gate_release_ms"]
            )
            coefficient = 1.0 - math.exp(-MODEL_FRAME_MS / max(1.0, time_ms))
            next_gain = previous_gain + coefficient * (target_gain - previous_gain)
            if abs(next_gain - target_gain) < 1e-4:
                next_gain = target_gain
            self.gate_gain = float(np.clip(next_gain, 0.0, 1.0))
            if not self.gate_open:
                self.gated_frames += 1
            return previous_gain, self.gate_gain

    @staticmethod
    def _normalized_pitch(frequency: float) -> float:
        frequency = float(np.clip(frequency, 8.18, 12_543.84))
        midi = 12.0 * (math.log2(frequency) - math.log2(440.0)) + 69.0
        return float(np.clip(midi / 127.0, 0.0, 1.0))

    @staticmethod
    def _level(samples: np.ndarray) -> tuple[float, float]:
        values = np.asarray(samples, dtype=np.float64).reshape(-1)
        if values.size == 0:
            return -96.0, -96.0
        rms = float(np.sqrt(np.mean(values * values)))
        peak = float(np.max(np.abs(values)))
        return amplitude_to_dbfs(rms), amplitude_to_dbfs(peak)

    def process_capture_block(self, block: np.ndarray) -> np.ndarray:
        if self._closed:
            raise RuntimeError("DDSP-VST Effect processor is closed")
        capture = np.asarray(block, dtype=np.float32)
        expected_frames = round(MODEL_HOP_SIZE * CAPTURE_SAMPLE_RATE / MODEL_SAMPLE_RATE)
        if capture.shape != (expected_frames, CAPTURE_CHANNELS):
            raise ValueError(
                f"Capture block must be {(expected_frames, CAPTURE_CHANNELS)}, got {capture.shape}"
            )
        mono = np.mean(capture, axis=1, dtype=np.float32)
        model_hop = self.input_resampler.process(mono)
        if model_hop.shape != (MODEL_HOP_SIZE,):
            raise AssertionError(f"Input resampler returned {model_hop.shape}")
        self.window[:-MODEL_HOP_SIZE] = self.window[MODEL_HOP_SIZE:]
        self.window[-MODEL_HOP_SIZE:] = model_hop

        input_rms, input_peak = self._level(capture)
        parameters = self.parameters
        gate_gain_start, gate_gain_end = self._gate_envelope(input_rms, parameters)

        feature_started = time.perf_counter()
        features = self.feature_model.predict(self.window)
        feature_ms = (time.perf_counter() - feature_started) * 1000.0
        shifted_hz = features.f0_hz * 2.0 ** (parameters["transpose"] / 12.0)
        f0_scaled = self._normalized_pitch(shifted_hz) - parameters["input_pitch"]
        pw_scaled = features.pw_scaled - parameters["input_gain"]
        control_started = time.perf_counter()
        controls, self.state = self.controls_model.predict_from_state(
            self.state, f0_scaled, pw_scaled
        )
        control_ms = (time.perf_counter() - control_started) * 1000.0
        if not isinstance(controls, ModelControls):
            controls = ModelControls(
                amplitude=float(controls.amplitude),
                harmonics=np.asarray(controls.harmonics, dtype=np.float32),
                noise_amps=np.asarray(controls.noise_amps, dtype=np.float32),
            )
        harmonic = self.harmonic.render(
            controls.amplitude * parameters["harmonic_gain"],
            controls.harmonics,
            shifted_hz,
        )
        noise = self.noise.render(controls.noise_amps * parameters["noise_gain"])
        output = self.output_resampler.process(harmonic + noise)
        gate_envelope = np.linspace(
            gate_gain_start,
            gate_gain_end,
            output.shape[0],
            endpoint=True,
            dtype=np.float32,
        )
        output *= gate_envelope
        output *= 10.0 ** (parameters["output_gain_db"] / 20.0)
        output = self.reverb.process(output)
        output = np.asarray(output, dtype=np.float32)
        output_rms, output_peak = self._level(output)
        clipped = int(np.count_nonzero(np.abs(output) > 1.0))
        with self._metric_lock:
            self.frames += 1
            self.feature_times_ms.append(feature_ms)
            self.control_times_ms.append(control_ms)
            self.input_rms_dbfs = input_rms
            self.input_peak_dbfs = input_peak
            self.output_rms_dbfs = output_rms
            self.output_peak_dbfs = output_peak
            self.f0_hz = shifted_hz
            self.pw_db = features.pw_db
            self.clipped_samples += clipped
            self.overload_frames = self.overload_frames + 1 if clipped else 0
            if self.overload_frames >= SAFETY_OVERLOAD_FRAMES:
                self.safety_muted = True
            safety_muted = self.safety_muted
        if safety_muted:
            output.fill(0.0)
        return output

    @staticmethod
    def _p95(values: deque[float]) -> float:
        return float(np.quantile(values, 0.95)) if values else 0.0

    def metrics(self) -> dict[str, object]:
        with self._gate_lock:
            gate_metrics = {
                "gate_open": self.gate_open,
                "gate_gain": self.gate_gain,
                "gate_threshold_dbfs": self._parameters["gate_threshold_dbfs"],
                "gate_close_threshold_dbfs": (
                    self._parameters["gate_threshold_dbfs"]
                    - self._parameters["gate_hysteresis_db"]
                ),
                "gate_hold_frames": self.gate_hold_frames,
                "gated_frames": self.gated_frames,
                "noise_floor_dbfs": self.noise_floor_dbfs,
                "calibrating": self.calibrating,
                "calibration_progress": self.calibration_progress,
            }
        with self._metric_lock:
            return {
                "frames": self.frames,
                "f0_hz": self.f0_hz,
                "pw_db": self.pw_db,
                "input_rms_dbfs": self.input_rms_dbfs,
                "input_peak_dbfs": self.input_peak_dbfs,
                "output_rms_dbfs": self.output_rms_dbfs,
                "output_peak_dbfs": self.output_peak_dbfs,
                "feature_ms": self.feature_times_ms[-1] if self.feature_times_ms else 0.0,
                "feature_p95_ms": self._p95(self.feature_times_ms),
                "control_ms": self.control_times_ms[-1] if self.control_times_ms else 0.0,
                "control_p95_ms": self._p95(self.control_times_ms),
                "clipped_samples": self.clipped_samples,
                "overload_frames": self.overload_frames,
                "safety_muted": self.safety_muted,
                **gate_metrics,
            }

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self.feature_model.close()
        self.controls_model.close()


class PulseDuplexEffect:
    def __init__(
        self,
        processor: DdspVstEffectProcessor,
        source_name: str,
        sink_name: str,
        input_device_name: str,
        output_device_name: str,
        terminal_callback: Callable[[BaseException | None], None],
        *,
        popen_factory: Callable[..., Any] = subprocess.Popen,
    ) -> None:
        self.processor = processor
        self.source_name = source_name
        self.sink_name = sink_name
        self.input_device_name = input_device_name
        self.output_device_name = output_device_name
        self.terminal_callback = terminal_callback
        self.popen_factory = popen_factory
        self.stop_event = threading.Event()
        self.ready_event = threading.Event()
        self.capture_process: Any | None = None
        self.playback_process: Any | None = None
        self.worker = threading.Thread(target=self._run, name="ddsp-vst-effect", daemon=True)
        self.error: BaseException | None = None
        self.captured_frames = 0
        self.played_frames = 0
        self.capture_overflows = 0
        self.playback_underruns = 0
        self.started_at = 0.0

    @staticmethod
    def _tool(name: str) -> str:
        path = shutil.which(name)
        if path is None:
            raise RuntimeError(f"{name} is required for DDSP-VST Effect")
        return path

    def start(self) -> None:
        parec = self._tool("parec")
        paplay = self._tool("paplay")
        self.capture_process = self.popen_factory(
            [
                parec,
                f"--device={self.source_name}",
                "--format=float32le",
                f"--rate={CAPTURE_SAMPLE_RATE}",
                f"--channels={CAPTURE_CHANNELS}",
                f"--latency-msec={PULSE_LATENCY_MS}",
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
        )
        self.playback_process = self.popen_factory(
            [
                paplay,
                "--playback",
                f"--device={self.sink_name}",
                "--client-name=MIDI-DDSP Studio",
                "--stream-name=DDSP-VST Effect",
                "--raw",
                "--format=float32le",
                f"--rate={OUTPUT_SAMPLE_RATE}",
                f"--channels={OUTPUT_CHANNELS}",
                f"--latency-msec={PULSE_LATENCY_MS}",
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            bufsize=0,
        )
        self.started_at = time.monotonic()
        self.worker.start()
        if not self.ready_event.wait(timeout=5.0):
            self.stop()
            raise RuntimeError("Timed out while opening DDSP-VST audio devices")
        if self.error is not None:
            self.stop()
            raise RuntimeError(str(self.error))

    @staticmethod
    def _process_error(process: Any, fallback: str) -> RuntimeError:
        detail = ""
        if (
            process is not None
            and process.stderr is not None
            and process.poll() is not None
        ):
            detail = process.stderr.read().decode("utf-8", errors="replace").strip()
        return RuntimeError(detail or fallback)

    def _read_exact(self, size: int) -> bytes:
        process = self.capture_process
        if process is None or process.stdout is None:
            raise RuntimeError("PulseAudio capture process is unavailable")
        parts = bytearray()
        while len(parts) < size and not self.stop_event.is_set():
            value = process.stdout.read(size - len(parts))
            if not value:
                raise self._process_error(process, "Selected capture device disconnected")
            parts.extend(value)
        return bytes(parts)

    def _write_all(self, payload: bytes) -> None:
        process = self.playback_process
        if process is None or process.stdin is None:
            raise RuntimeError("PulseAudio playback process is unavailable")
        remaining = memoryview(payload)
        while remaining and not self.stop_event.is_set():
            written = process.stdin.write(remaining)
            if written is None or written <= 0:
                self.playback_underruns += 1
                raise self._process_error(process, "Selected playback device disconnected")
            remaining = remaining[written:]

    def _run(self) -> None:
        terminal_error: BaseException | None = None
        try:
            for process, message in (
                (self.capture_process, "Unable to open selected capture device"),
                (self.playback_process, "Unable to open selected playback device"),
            ):
                if process is None or process.poll() is not None:
                    raise self._process_error(process, message)
            self.ready_event.set()
            capture_frames = round(MODEL_HOP_SIZE * CAPTURE_SAMPLE_RATE / MODEL_SAMPLE_RATE)
            capture_bytes = capture_frames * CAPTURE_CHANNELS * 4
            while not self.stop_event.is_set():
                payload = self._read_exact(capture_bytes)
                if self.stop_event.is_set():
                    break
                capture = np.frombuffer(payload, dtype="<f4").reshape(
                    capture_frames, CAPTURE_CHANNELS
                )
                output = self.processor.process_capture_block(capture)
                self._write_all(output[:, :2].astype("<f4", copy=False).tobytes())
                self.captured_frames += capture_frames
                self.played_frames += output.shape[0]
        except BaseException as exc:
            if not self.stop_event.is_set():
                terminal_error = exc
                self.error = exc
        finally:
            self.ready_event.set()
            self.stop_event.set()
            self._terminate_processes()
            self.terminal_callback(terminal_error)

    def _terminate_processes(self) -> None:
        for process in (self.capture_process, self.playback_process):
            if process is None:
                continue
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=1.0)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=1.0)

    def stop(self) -> None:
        self.stop_event.set()
        self._terminate_processes()
        if self.worker.ident is not None and self.worker is not threading.current_thread():
            self.worker.join(timeout=5.0)
            if self.worker.is_alive():
                raise RuntimeError("Timed out while stopping DDSP-VST Effect")

    def metrics(self) -> dict[str, object]:
        processing = self.processor.metrics()
        feature_ms = float(processing["feature_ms"])
        control_ms = float(processing["control_ms"])
        total_latency = (
            64.0
            + PULSE_LATENCY_MS * 2
            + self.processor.input_resampler.algorithmic_latency_seconds * 1000.0
            + self.processor.output_resampler.algorithmic_latency_seconds * 1000.0
            + feature_ms
            + control_ms
        )
        return {
            **processing,
            "captured_frames": self.captured_frames,
            "played_frames": self.played_frames,
            "capture_overflows": self.capture_overflows,
            "playback_underruns": self.playback_underruns,
            "queue_latency_ms": 0.0,
            "total_latency_ms": total_latency,
            "elapsed_seconds": max(0.0, time.monotonic() - self.started_at),
        }


class DdspVstEffectController:
    OWNER = "ddsp-vst-effect"

    def __init__(
        self,
        coordinator: ResourceCoordinator,
        *,
        feature_model_factory: Callable[..., Any] = FeatureOmModel,
        controls_model_factory: Callable[..., Any] = PyAclControlsModel,
        processor_factory: Callable[..., Any] = DdspVstEffectProcessor,
        session_factory: Callable[..., Any] = PulseDuplexEffect,
    ) -> None:
        self.coordinator = coordinator
        self.feature_model_factory = feature_model_factory
        self.controls_model_factory = controls_model_factory
        self.processor_factory = processor_factory
        self.session_factory = session_factory
        self._lock = threading.RLock()
        self._state = "stopped"
        self._error: str | None = None
        self._public_config: dict[str, object] = {}
        self._parameters = dict(DEFAULT_PARAMETERS)
        self._hashes: dict[str, str] = {}
        self._processor: Any | None = None
        self._session: Any | None = None
        self._owns_resource = False

    @property
    def running(self) -> bool:
        with self._lock:
            return self._state in {"starting", "running", "stopping"}

    def _release(self) -> None:
        with self._lock:
            if not self._owns_resource:
                return
            self._owns_resource = False
        self.coordinator.release(self.OWNER)

    def _terminal(self, error: BaseException | None) -> None:
        with self._lock:
            processor = self._processor
            if processor is not None:
                self._parameters.update(dict(processor.parameters))
            self._processor = None
            self._session = None
            if error is not None:
                self._state = "failed"
                self._error = str(error)
            elif self._state != "failed":
                self._state = "stopped"
                self._error = None
        if processor is not None:
            processor.close()
        self._release()

    def start(self, config: Mapping[str, object]) -> dict[str, object]:
        with self._lock:
            if self.running:
                raise RuntimeError("DDSP-VST Effect is already running")
        feature_path = Path(str(config["feature_model_path"])).resolve()
        control_path = Path(str(config["control_model_path"])).resolve()
        hashes = validate_runtime_models(feature_path, control_path)
        parameters = dict(DEFAULT_PARAMETERS)
        parameters.update(validate_parameters(config.get("parameters", {})))
        self.coordinator.acquire(self.OWNER)
        with self._lock:
            self._owns_resource = True
            self._state = "starting"
            self._error = None
        feature_model = controls_model = processor = session = None
        try:
            feature_model = self.feature_model_factory(
                feature_path, device_id=int(config.get("device_id", 0))
            )
            controls_model = self.controls_model_factory(
                control_path,
                device_id=int(config.get("device_id", 0)),
                keep_runtime=True,
            )
            if getattr(feature_model, "backend_name", None) != "acl/om":
                raise RuntimeError("Feature backend must report acl/om")
            if getattr(controls_model, "backend_name", None) != "om-pyacl":
                raise RuntimeError("Control backend must report om-pyacl")
            processor = self.processor_factory(feature_model, controls_model, parameters)
            begin_calibration = getattr(processor, "begin_calibration", None)
            if callable(begin_calibration):
                begin_calibration()
            session = self.session_factory(
                processor,
                source_name=str(config["pulse_source"]),
                sink_name=str(config["pulse_sink"]),
                input_device_name=str(config["input_device_name"]),
                output_device_name=str(config["output_device_name"]),
                terminal_callback=self._terminal,
            )
            with self._lock:
                self._processor = processor
                self._session = session
                self._parameters = parameters
                self._hashes = hashes
                self._public_config = {
                    key: config[key]
                    for key in ("model_id", "audio_input_id", "audio_output_id")
                }
                self._public_config.update(
                    {
                        "input_device_name": config["input_device_name"],
                        "output_device_name": config["output_device_name"],
                    }
                )
            session.start()
            with self._lock:
                self._state = "running"
            return self.status()
        except BaseException as exc:
            if session is not None:
                try:
                    session.stop()
                except BaseException:
                    pass
            if processor is not None:
                processor.close()
            else:
                if feature_model is not None:
                    feature_model.close()
                if controls_model is not None:
                    controls_model.close()
            with self._lock:
                self._processor = None
                self._session = None
                self._state = "failed"
                self._error = str(exc)
            self._release()
            raise

    def update_parameters(self, values: Mapping[str, object]) -> dict[str, object]:
        validated = validate_parameters(values)
        with self._lock:
            processor = self._processor
            self._parameters.update(validated)
        if processor is not None:
            processor.update_parameters(validated)
        return self.status()

    def calibrate(self) -> dict[str, object]:
        with self._lock:
            processor = self._processor
            if self._state != "running" or processor is None:
                raise RuntimeError("DDSP-VST Effect must be running before calibration")
        processor.begin_calibration()
        return self.status()

    def stop(self) -> dict[str, object]:
        with self._lock:
            session = self._session
            processor = self._processor
            if session is None:
                if self._state != "failed":
                    self._state = "stopped"
                return self.status()
            self._state = "stopping"
        session.stop()
        with self._lock:
            still_attached = self._session is session
        if still_attached:
            if processor is not None:
                processor.close()
            with self._lock:
                self._session = None
                self._processor = None
                self._state = "stopped"
                self._error = None
            self._release()
        return self.status()

    def status(self) -> dict[str, object]:
        with self._lock:
            state = self._state
            session = self._session
            processor = self._processor
            config = dict(self._public_config)
            parameters = (
                dict(processor.parameters)
                if processor is not None
                else dict(self._parameters)
            )
            hashes = dict(self._hashes)
            error = self._error
        metrics = session.metrics() if session is not None else {
            "frames": 0,
            "f0_hz": 0.0,
            "pw_db": -96.0,
            "input_rms_dbfs": -96.0,
            "input_peak_dbfs": -96.0,
            "output_rms_dbfs": -96.0,
            "output_peak_dbfs": -96.0,
            "feature_ms": 0.0,
            "feature_p95_ms": 0.0,
            "control_ms": 0.0,
            "control_p95_ms": 0.0,
            "queue_latency_ms": 0.0,
            "total_latency_ms": 0.0,
            "capture_overflows": 0,
            "playback_underruns": 0,
            "clipped_samples": 0,
            "safety_muted": False,
            "gate_open": False,
            "gate_gain": 0.0,
            "gate_threshold_dbfs": parameters["gate_threshold_dbfs"],
            "gate_close_threshold_dbfs": (
                parameters["gate_threshold_dbfs"] - parameters["gate_hysteresis_db"]
            ),
            "gate_hold_frames": 0,
            "gated_frames": 0,
            "noise_floor_dbfs": -96.0,
            "calibrating": False,
            "calibration_progress": 0.0,
        }
        return {
            "state": state,
            "running": state in {"starting", "running", "stopping"},
            "error": error,
            "backend": "acl/om",
            "feature_backend": "acl/om",
            "control_backend": "acl/om",
            "feature_model": FEATURE_OM_PATH.name,
            "config": config,
            "parameters": parameters,
            "hashes": hashes,
            "metrics": metrics,
        }
