"""Stateful realtime Piano-DDSP inference and host DSP engine."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import threading
import time
from typing import Any, Callable

import numpy as np

from realtime_ddsp import open_midi_input

from .acl_model import PianoAclModel
from .audio_output import BoundedAudioOutput, WavRecorder
from .bundle import PianoBundle, PianoModelAsset
from .harmonic import HarmonicSynthesizer
from .metrics import RuntimeMetrics
from .midi_state import LiveMidiState, PIANO_MIDI_MAX, PIANO_MIDI_MIN
from .noise import NoiseSynthesizer
from .reverb import StreamingReverb
from .resampler import PianoSincResampler
from .scheduler import MidiScheduler, MidiTimeline, load_midi_timeline


MODEL_RATE = 16_000
FRAME_RATE = 250
SAMPLES_PER_FRAME = 64
CONTROL_OUTPUT_NAMES = (
    "amplitudes",
    "harmonic_distribution",
    "inharmonicity",
    "f0_hz",
    "noise_magnitudes",
)
RUNTIME_OUTPUT_NAMES = set(CONTROL_OUTPUT_NAMES) | {
    "next_context_state",
    "next_monophonic_state",
}
RUNTIME_METRIC_CAPACITY = 2_048
LATENCY_PROFILES = {
    "low": {"frames": 4, "prebuffer": 1, "latency_ms": 15.0, "capacity": 2},
    "balanced": {"frames": 8, "prebuffer": 2, "latency_ms": 20.0, "capacity": 4},
    "safe": {"frames": 16, "prebuffer": 2, "latency_ms": 40.0, "capacity": 3},
}


@dataclass
class PlayerState:
    timeline: MidiTimeline | None = None
    path: str | None = None
    state: str = "empty"
    position_seconds: float = 0.0
    started_ns: int = 0
    tempo: float = 1.0
    loop: bool = False


class PianoDdspEngine:
    def __init__(
        self,
        bundle: PianoBundle,
        model_id: str = "gru_ir_96_64",
        *,
        piano_year: int = 2018,
        output_sample_rate: int = 48_000,
        latency_profile: str = "balanced",
        seed: int = 0,
        velocity_curve: float = 1.0,
        transpose: int = 0,
        output_gain_db: float = 0.0,
        reverb_mix: float = 1.0,
        midi_port: str | None = None,
        audio_backend: str = "pulse",
        pulse_sink: str | None = None,
        audio_device: str | int | None = None,
        is_bluetooth: bool = False,
        alsa_device: str | None = None,
        alsa_card: int | None = None,
        alsa_route_device_id: int | None = None,
        alsa_playback_level: int | None = None,
        device_id: int = 0,
        recorder_root: Path | None = None,
        monitor_callback: Callable[[np.ndarray], None] | None = None,
        note_listener: Callable[[int, bool], None] | None = None,
        model_factory: Callable[[Path, dict[str, Any], int], Any] = PianoAclModel,
    ) -> None:
        if latency_profile not in LATENCY_PROFILES:
            raise ValueError(f"Unknown latency profile: {latency_profile}")
        if is_bluetooth and latency_profile == "low":
            raise ValueError("Bluetooth audio does not support the low latency profile")
        if audio_backend == "alsa_mono" and latency_profile == "low":
            raise ValueError("Onboard mono audio does not support the low latency profile")
        if model_id not in bundle.models:
            raise KeyError(f"Model {model_id!r} is not in bundle {bundle.id!r}")
        if not bundle.models[model_id].validation_passed:
            raise RuntimeError(
                f"Model {model_id!r} has not passed the 10,000-frame OM validation"
            )
        self.bundle = bundle
        self.model_id = model_id
        self.piano_year = int(piano_year)
        self.output_sample_rate = int(output_sample_rate)
        self.latency_profile = latency_profile
        self.seed = int(seed)
        self.velocity_curve = float(velocity_curve)
        self.transpose = int(transpose)
        self.output_gain_db = float(output_gain_db)
        self.reverb_mix = float(reverb_mix)
        self.midi_port = midi_port
        self.audio_backend = audio_backend
        self.pulse_sink = pulse_sink
        self.audio_device = audio_device
        self.is_bluetooth = bool(is_bluetooth)
        self.alsa_device = alsa_device
        self.alsa_card = alsa_card
        self.alsa_route_device_id = alsa_route_device_id
        self.alsa_playback_level = int(alsa_playback_level or 10)
        self.device_id = int(device_id)
        self.recorder_root = Path(recorder_root or "reports/webui/piano-ddsp").resolve()
        self.monitor_callback = monitor_callback
        self.model_factory = model_factory
        self.metrics = RuntimeMetrics(RUNTIME_METRIC_CAPACITY)
        self.midi = LiveMidiState(note_listener=note_listener)
        self.scheduler = MidiScheduler(self.midi)
        self.player = PlayerState()
        self.recorder = WavRecorder()
        self.model: Any = None
        self.asset: PianoModelAsset | None = None
        self.harmonic: HarmonicSynthesizer | None = None
        self.noise: NoiseSynthesizer | None = None
        self.reverb: StreamingReverb | None = None
        self.resampler: PianoSincResampler | None = None
        self._control_buffers: dict[str, np.ndarray] = {}
        self.audio: BoundedAudioOutput | None = None
        self.midi_input: Any = None
        self.context_state = np.zeros((1, 1, 64), dtype=np.float32)
        self.monophonic_state = np.zeros((1, 16, 192), dtype=np.float32)
        self.extended_pitch = np.zeros((16,), dtype=np.float32)
        self.release_counter = np.zeros((16,), dtype=np.int16)
        self.voice_gain = np.zeros((16,), dtype=np.float32)
        self._gain_current = np.float32(10.0 ** (self.output_gain_db / 20.0))
        self._running = False
        self._state = "stopped"
        self._error: str | None = None
        self._stop = threading.Event()
        self._render_thread: threading.Thread | None = None
        self._lock = threading.RLock()
        self._fade_samples = 0
        self._fade_total = 0
        self._reset_after_fade = False
        self._input_frozen = False
        self._pending_note_times: list[int] = []

    @property
    def block_frames(self) -> int:
        return int(LATENCY_PROFILES[self.latency_profile]["frames"])

    @property
    def block_samples(self) -> int:
        return self.block_frames * SAMPLES_PER_FRAME

    def start(self) -> None:
        with self._lock:
            if self._running:
                return
            self._state = "starting"
            self._load_model(self.bundle.models[self.model_id])
            profile = dict(LATENCY_PROFILES[self.latency_profile])
            latency_ms = float(profile["latency_ms"])
            prebuffer = int(profile["prebuffer"])
            if self.is_bluetooth:
                latency_ms = 220.0 if self.latency_profile == "balanced" else 300.0
                prebuffer = max(prebuffer, 2 if self.latency_profile == "balanced" else 3)
            output_block = round(self.block_samples * self.output_sample_rate / MODEL_RATE)
            self.audio = BoundedAudioOutput(
                self.output_sample_rate,
                output_block,
                max(int(profile["capacity"]), prebuffer + 1),
                prebuffer,
                latency_ms,
                self.metrics,
                backend=self.audio_backend,
                sink_name=self.pulse_sink,
                device=self.audio_device,
                alsa_device=self.alsa_device,
                alsa_card=self.alsa_card,
                alsa_route_device_id=self.alsa_route_device_id,
                alsa_playback_level=self.alsa_playback_level,
                on_played=self._on_played,
            )
            if self.midi_port:
                self.midi_input = open_midi_input(self.midi_port, self._hardware_message)
            self._prime_audio(self.audio)
            self.audio.ready.clear()
            self.audio.start()
            self._stop.clear()
            self._running = True
            self._state = "running"
            self._render_thread = threading.Thread(
                target=self._render_loop, name="piano-render", daemon=True
            )
            self._render_thread.start()
            self.audio.ready.set()

    def _prime_audio(self, audio: BoundedAudioOutput) -> None:
        """Warm inference/DSP, then queue a clean initial timeline before playback."""
        for _ in range(2):
            self.render_block()
        self._reset_states(reset_midi=False)
        for _ in range(audio.capacity):
            if not audio.submit(self.render_block()):
                raise RuntimeError("Unable to prefill the realtime audio queue")
        self.metrics.reset()

    def _load_model(self, asset: PianoModelAsset) -> None:
        if self.piano_year not in asset.piano_years:
            raise ValueError(f"Piano year {self.piano_year} is unavailable for {asset.model_id}")
        self.asset = asset
        self.model = self.model_factory(asset.om_path, asset.metadata, self.device_id)
        self.context_state.fill(0.0)
        self.monophonic_state.fill(0.0)
        zero = self._model_inputs(
            np.zeros((16, 2), dtype=np.float32), np.zeros(4, dtype=np.float32)
        )
        warm = self.model.infer(zero)
        reverb_name = str(asset.metadata.get("reverb_output", "reverb_ir"))
        condition = warm[reverb_name]
        harmonics = int(asset.metadata["n_harmonics"])
        bands = int(asset.metadata["n_noise_bands"])
        substrings = int(asset.metadata.get("n_substrings", 1))
        self.harmonic = HarmonicSynthesizer(16, harmonics, substrings)
        self.noise = NoiseSynthesizer(16, bands, seed=self.seed)
        self.reverb = StreamingReverb(
            asset.metadata, condition, self.block_samples, self.reverb_mix
        )
        self.resampler = PianoSincResampler(MODEL_RATE, self.output_sample_rate)
        self.resampler.prepare(self.block_samples)
        output_shapes = dict(asset.metadata["outputs"])
        self._control_buffers = {
            name: np.empty(
                (self.block_frames, *tuple(output_shapes[name])[2:]),
                dtype=np.float32,
            )
            for name in CONTROL_OUTPUT_NAMES
        }
        self._reset_states(reset_midi=False)

    def _model_inputs(self, conditioning: np.ndarray, pedal: np.ndarray) -> dict[str, np.ndarray]:
        assert self.asset is not None
        piano_index = self.asset.piano_years.index(self.piano_year)
        return {
            "conditioning": np.asarray(conditioning, dtype=np.float32).reshape(1, 1, 16, 2),
            "pedal": np.asarray(pedal, dtype=np.float32).reshape(1, 1, 4),
            "piano_model": np.asarray([piano_index], dtype=np.int32),
            "extended_pitch": self.extended_pitch.reshape(1, 1, 16, 1).copy(),
            "context_state": self.context_state,
            "monophonic_state": self.monophonic_state,
        }

    def _shape_conditioning(self, values: np.ndarray) -> np.ndarray:
        shaped = values.copy()
        active = shaped[:, 0] > 0
        shaped[active, 0] += self.transpose
        valid = (shaped[:, 0] >= PIANO_MIDI_MIN) & (shaped[:, 0] <= PIANO_MIDI_MAX)
        shaped[~valid, :] = 0.0
        shaped[:, 1] = np.power(np.clip(shaped[:, 1], 0.0, 1.0), self.velocity_curve)
        return shaped

    def _update_extended_pitch(self, conditioning: np.ndarray) -> None:
        pitches = conditioning[:, 0]
        active = pitches > 0
        self.extended_pitch[active] = pitches[active]
        self.release_counter[active] = 251
        released = ~active & (self.release_counter > 0)
        self.release_counter[released] -= 1
        expired = ~active & (self.release_counter <= 0)
        self.extended_pitch[expired] = 0.0

    def _voice_envelopes(self, gates: np.ndarray) -> np.ndarray:
        samples = self.block_samples
        result = np.empty((16, samples), dtype=np.float32)
        release_samples = round(0.060 * MODEL_RATE)
        decrement = np.float32(1.0 / release_samples) * np.arange(
            1, SAMPLES_PER_FRAME + 1, dtype=np.float32
        )
        current = self.voice_gain.copy()
        for frame in range(self.block_frames):
            active = np.asarray(gates[frame], dtype=bool)
            released = np.maximum(current[:, None] - decrement[None, :], 0.0)
            values = np.where(active[:, None], np.float32(1.0), released)
            start = frame * SAMPLES_PER_FRAME
            result[:, start : start + SAMPLES_PER_FRAME] = values
            current = values[:, -1]
        self.voice_gain[:] = current
        return result

    def render_block(self, first_frame_ns: int | None = None) -> np.ndarray:
        started = time.perf_counter()
        with self._lock:
            if self.model is None or self.harmonic is None or self.noise is None:
                raise RuntimeError("Piano-DDSP model is not loaded")
            first_frame_ns = time.monotonic_ns() if first_frame_ns is None else first_frame_ns
            self._update_player(first_frame_ns)
            conditions, pedals, gates = self.scheduler.render_conditions(
                self.block_frames, first_frame_ns
            )
            controls = self._control_buffers
            npu_timings: list[float] = []
            npu_started = time.perf_counter()
            for frame in range(self.block_frames):
                shaped = self._shape_conditioning(conditions[frame])
                self._update_extended_pitch(shaped)
                inputs = self._model_inputs(shaped, pedals[frame])
                if isinstance(self.model, PianoAclModel):
                    output = self.model.infer(
                        inputs,
                        output_names=RUNTIME_OUTPUT_NAMES,
                        output_targets={name: controls[name][frame] for name in CONTROL_OUTPUT_NAMES},
                        copy_outputs=False,
                        validate_tensors=False,
                    )
                else:
                    output = self.model.infer(inputs)
                self.context_state = output["next_context_state"]
                self.monophonic_state = output["next_monophonic_state"]
                if not isinstance(self.model, PianoAclModel):
                    for name in CONTROL_OUTPUT_NAMES:
                        controls[name][frame] = output[name][0, 0]
                npu_timings.append((time.perf_counter() - npu_started) * 1000.0)
                npu_started = time.perf_counter()
            self.metrics.add_many("npu_ms", npu_timings)
            finite_tensors = tuple(controls.values()) + (
                self.context_state,
                self.monophonic_state,
            )
            if not all(np.all(np.isfinite(value)) for value in finite_tensors):
                raise RuntimeError("Piano-DDSP runtime tensor contains NaN or Inf")
            envelopes = self._voice_envelopes(gates)
            dsp_started = time.perf_counter()
            dry = self.harmonic.render(
                controls["amplitudes"],
                controls["harmonic_distribution"],
                controls["inharmonicity"],
                controls["f0_hz"],
                envelopes,
            )
            dry += self.noise.render(controls["noise_magnitudes"], envelopes)
            wet = self.reverb.process(dry) if self.reverb is not None else dry
            target_gain = np.float32(10.0 ** (self.output_gain_db / 20.0))
            gain = np.linspace(self._gain_current, target_gain, wet.size, dtype=np.float32)
            self._gain_current = target_gain
            wet *= gain
            if self._fade_samples > 0:
                count = min(wet.size, self._fade_samples)
                begin = self._fade_samples / max(1, self._fade_total)
                end = (self._fade_samples - count) / max(1, self._fade_total)
                fade = np.linspace(begin, end, count, endpoint=False, dtype=np.float32)
                wet[:count] *= fade
                if count < wet.size:
                    wet[count:] = 0.0
                self._fade_samples -= count
                if self._fade_samples <= 0 and self._reset_after_fade:
                    self._reset_states(reset_midi=False)
                    self._reset_after_fade = False
            elif self._state == "switching":
                wet.fill(0.0)
            clipped = int(np.count_nonzero(np.abs(wet) > 1.0))
            if clipped:
                self.metrics.increment("clipped_samples", clipped)
            wet = np.clip(wet, -1.0, 1.0)
            assert self.resampler is not None
            output = self.resampler.process(wet)
            stereo = np.repeat(output[:, None], 2, axis=1)
            self.metrics.add("dsp_ms", (time.perf_counter() - dsp_started) * 1000.0)
            self.metrics.add("block_ms", (time.perf_counter() - started) * 1000.0)
            self.metrics.increment("rendered_blocks")
            if np.any(np.abs(stereo) > 1e-6) and self._pending_note_times:
                now = time.monotonic_ns()
                for timestamp in self._pending_note_times:
                    self.metrics.add("midi_to_pcm_ms", (now - timestamp) / 1e6)
                self._pending_note_times.clear()
            return stereo.astype(np.float32, copy=False)

    def _render_loop(self) -> None:
        try:
            while not self._stop.is_set():
                audio = self.audio
                if audio is None:
                    break
                if audio.error is not None:
                    raise RuntimeError("Audio output failed") from audio.error
                block = self.render_block()
                audio.submit(block)
                if self._state == "stopping" and self._fade_samples <= 0:
                    self._stop.set()
        except BaseException as exc:
            self._error = str(exc)
            self._state = "failed"
            self._stop.set()

    def _on_played(self, block: np.ndarray) -> None:
        self.recorder.write(block)
        if self.monitor_callback is not None:
            self.monitor_callback(block)

    def _hardware_message(self, message: object) -> None:
        kind = str(getattr(message, "type", ""))
        if kind == "note_on":
            velocity = int(getattr(message, "velocity", 0))
            note = int(getattr(message, "note", 0))
            self.note("hardware", note, velocity, velocity > 0)
        elif kind == "note_off":
            self.note("hardware", int(getattr(message, "note", 0)), 0, False)
        elif kind == "control_change":
            self.control_change(
                "hardware", int(getattr(message, "control", 0)), int(getattr(message, "value", 0))
            )

    def note(self, source: str, note: int, velocity: int, on: bool) -> None:
        if self._input_frozen:
            return
        now = time.monotonic_ns()
        self.scheduler.push(source, "note_on" if on and velocity > 0 else "note_off", note, velocity, now)
        if on and velocity > 0:
            self._pending_note_times.append(now)

    def control_change(self, source: str, controller: int, value: int) -> None:
        if self._input_frozen:
            return
        if controller in {120, 123}:
            self.all_notes_off()
            return
        self.scheduler.push(source, "control_change", controller, value)

    def release_source(self, source: str) -> None:
        self.scheduler.cancel_source(source)

    def update_parameters(self, values: dict[str, object]) -> None:
        allowed = {"velocity_curve", "transpose", "output_gain_db", "reverb_mix", "pedal"}
        unknown = set(values) - allowed
        if unknown:
            raise ValueError(f"Unsupported Piano-DDSP parameters: {sorted(unknown)}")
        with self._lock:
            if "velocity_curve" in values:
                value = float(values["velocity_curve"])
                if not 0.25 <= value <= 2.0:
                    raise ValueError("velocity_curve must be between 0.25 and 2")
                self.velocity_curve = value
            if "transpose" in values:
                value = int(values["transpose"])
                if not -24 <= value <= 24:
                    raise ValueError("transpose must be between -24 and 24")
                self.transpose = value
            if "output_gain_db" in values:
                value = float(values["output_gain_db"])
                if not -60.0 <= value <= 6.0:
                    raise ValueError("output_gain_db must be between -60 and 6")
                self.output_gain_db = value
            if "reverb_mix" in values:
                value = float(values["reverb_mix"])
                if not 0.0 <= value <= 1.0:
                    raise ValueError("reverb_mix must be between 0 and 1")
                self.reverb_mix = value
                if self.reverb is not None:
                    self.reverb.mix = value
            if "pedal" in values:
                self.control_change("parameters", 64, 127 if bool(values["pedal"]) else 0)

    def panic(self) -> None:
        with self._lock:
            self.scheduler.clear()
            self.midi.panic()
            self.player = PlayerState()
            self._reset_states(reset_midi=False)
            if self.audio is not None:
                self.audio.clear()

    def all_notes_off(self) -> None:
        """Release all sources through a bounded 120 ms global fade."""
        with self._lock:
            self.scheduler.clear()
            self.midi.panic()
            self.player = PlayerState()
            self._fade_total = round(0.120 * MODEL_RATE)
            self._fade_samples = self._fade_total
            self._reset_after_fade = True

    def _reset_states(self, *, reset_midi: bool) -> None:
        self.context_state.fill(0.0)
        self.monophonic_state.fill(0.0)
        self.extended_pitch.fill(0.0)
        self.release_counter.fill(0)
        self.voice_gain.fill(0.0)
        if self.harmonic is not None:
            self.harmonic.reset()
        if self.noise is not None:
            self.noise.reset()
        if self.resampler is not None:
            self.resampler.reset()
        if self.reverb is not None:
            self.reverb.reset()
        if reset_midi:
            self.midi.panic()

    def switch(self, model_id: str | None = None, piano_year: int | None = None) -> None:
        target = model_id or self.model_id
        year = self.piano_year if piano_year is None else int(piano_year)
        with self._lock:
            if target not in self.bundle.models:
                raise KeyError(target)
            if not self.bundle.models[target].validation_passed:
                raise RuntimeError(
                    f"Model {target!r} has not passed the 10,000-frame OM validation"
                )
            self._state = "switching"
            self._input_frozen = True
            self.scheduler.clear()
            self.midi.panic()
            self._fade_total = round(0.120 * MODEL_RATE)
            self._fade_samples = self._fade_total

        deadline = time.monotonic() + 1.0
        while self._fade_samples > 0 and not self._stop.is_set() and time.monotonic() < deadline:
            time.sleep(0.005)

        with self._lock:
            old_model = self.model
            self.model = None
            try:
                self.model_id = target
                self.piano_year = year
                self._load_model(self.bundle.models[target])
                if old_model is not None:
                    old_model.close()
                if self.audio is not None:
                    self.audio.clear()
                self._state = "running"
                self._error = None
            except BaseException as exc:
                if self.model is not None:
                    self.model.close(suppress_errors=True)
                    self.model = None
                if old_model is not None:
                    old_model.close(suppress_errors=True)
                self._error = str(exc)
                self._state = "failed"
                self._stop.set()
                raise
            finally:
                self._fade_samples = 0
                self._input_frozen = False

    def load_player(self, path: Path) -> dict[str, object]:
        timeline = load_midi_timeline(Path(path))
        self.scheduler.cancel_source("midi-file")
        self.player = PlayerState(timeline=timeline, path=str(Path(path).resolve()), state="loaded")
        return self.player_status()

    def player_command(self, action: str, **values: object) -> dict[str, object]:
        player = self.player
        if action == "load":
            return self.load_player(Path(str(values["path"])))
        if player.timeline is None:
            raise RuntimeError("No MIDI file is loaded")
        if action == "play":
            now = time.monotonic_ns()
            player.started_ns = now
            player.state = "playing"
            self.scheduler.schedule_timeline(
                "midi-file", player.timeline, now, player.position_seconds, player.tempo
            )
        elif action == "pause":
            self._sync_player_position(time.monotonic_ns())
            player.state = "paused"
            self.scheduler.cancel_source("midi-file")
        elif action == "stop":
            self.scheduler.cancel_source("midi-file")
            player.position_seconds = 0.0
            player.state = "loaded"
        elif action == "seek":
            position = float(values.get("position_seconds", 0.0))
            player.position_seconds = min(player.timeline.duration_seconds, max(0.0, position))
            if player.state == "playing":
                player.started_ns = time.monotonic_ns()
                self.scheduler.schedule_timeline(
                    "midi-file", player.timeline, player.started_ns, player.position_seconds, player.tempo
                )
        elif action == "loop":
            player.loop = bool(values.get("enabled", False))
        elif action == "tempo":
            tempo = float(values.get("value", 1.0))
            if not 0.5 <= tempo <= 2.0:
                raise ValueError("tempo must be between 0.5 and 2")
            self._sync_player_position(time.monotonic_ns())
            player.tempo = tempo
            if player.state == "playing":
                player.started_ns = time.monotonic_ns()
                self.scheduler.schedule_timeline(
                    "midi-file", player.timeline, player.started_ns, player.position_seconds, player.tempo
                )
        else:
            raise ValueError(f"Unknown player action: {action}")
        return self.player_status()

    def _sync_player_position(self, now_ns: int) -> None:
        if self.player.state == "playing":
            elapsed = (now_ns - self.player.started_ns) / 1e9 * self.player.tempo
            self.player.position_seconds += max(0.0, elapsed)
            self.player.started_ns = now_ns

    def _update_player(self, now_ns: int) -> None:
        player = self.player
        if player.state != "playing" or player.timeline is None:
            return
        position = player.position_seconds + (now_ns - player.started_ns) / 1e9 * player.tempo
        if position < player.timeline.duration_seconds:
            return
        if player.loop:
            player.position_seconds = 0.0
            player.started_ns = now_ns
            self.scheduler.schedule_timeline("midi-file", player.timeline, now_ns, 0.0, player.tempo)
        else:
            player.position_seconds = player.timeline.duration_seconds
            player.state = "loaded"
            self.scheduler.cancel_source("midi-file")

    def player_status(self) -> dict[str, object]:
        player = self.player
        position = player.position_seconds
        if player.state == "playing":
            position += (time.monotonic_ns() - player.started_ns) / 1e9 * player.tempo
        return {
            "state": player.state,
            "path": player.path,
            "position_seconds": position,
            "duration_seconds": player.timeline.duration_seconds if player.timeline else 0.0,
            "tempo": player.tempo,
            "loop": player.loop,
        }

    def start_recording(self, recording_id: str) -> str:
        safe = "".join(char for char in recording_id if char.isalnum() or char in "-_")
        if not safe:
            raise ValueError("Invalid recording id")
        return str(self.recorder.start(self.recorder_root / f"{safe}.wav", self.output_sample_rate))

    def stop_recording(self) -> str | None:
        path = self.recorder.stop()
        return str(path) if path else None

    def status(self) -> dict[str, object]:
        audio = self.audio
        midi_connected = bool(getattr(self.midi_input, "connected", self.midi_input is not None))
        metrics = self.metrics.snapshot(
            buffered_blocks=audio.buffered_blocks if audio else 0,
            block_duration_ms=self.block_frames * 4.0,
            device_latency_ms=audio.device_latency_ms if audio else 0.0,
            sink_latency_ms=(audio.sink_latency_ms + audio.pulse_buffer_latency_ms) if audio else 0.0,
            resampler_latency_ms=(
                self.resampler.algorithmic_latency_seconds * 1000.0 if self.resampler else 0.0
            ),
        )
        snapshot = self.midi.snapshot()
        return {
            "state": self._state,
            "running": self._running and not self._stop.is_set(),
            "error": self._error,
            "config": {
                "bundle_id": self.bundle.id,
                "model_id": self.model_id,
                "piano_year": self.piano_year,
                "latency_profile": self.latency_profile,
                "seed": self.seed,
                "output_sample_rate": self.output_sample_rate,
                "velocity_curve": self.velocity_curve,
                "transpose": self.transpose,
                "output_gain_db": self.output_gain_db,
                "reverb_mix": self.reverb_mix,
            },
            "midi": {
                "port": self.midi_port,
                "connected": midi_connected,
                "reconnects": int(getattr(self.midi_input, "reconnect_count", 0)),
                "error": getattr(self.midi_input, "last_error", None),
                "active_notes": list(snapshot.active_notes),
                "slot_notes": list(snapshot.slot_notes),
                "pedal": list(snapshot.pedal),
                "voice_steals": snapshot.voice_steals,
                "last_velocity": snapshot.last_velocity,
            },
            "audio": {
                "backend": self.audio_backend,
                "sink": self.pulse_sink,
                "device_lost": bool(audio and audio.error),
                "error": str(audio.error) if audio and audio.error else None,
            },
            "player": self.player_status(),
            "recording": {
                "active": self.recorder.active,
                "path": str(self.recorder.path) if self.recorder.path else None,
            },
            "metrics": metrics,
            "heartbeat_ns": time.monotonic_ns(),
        }

    def stop(self, graceful: bool = True) -> None:
        with self._lock:
            if not self._running and self._state == "stopped":
                return
            self._state = "stopping"
            if graceful and self._render_thread is not None and self._render_thread.is_alive():
                self._fade_total = round(0.120 * MODEL_RATE)
                self._fade_samples = self._fade_total
            else:
                self._stop.set()
        thread = self._render_thread
        if graceful and thread is not None:
            thread.join(timeout=2.0)
        self._stop.set()
        if thread is not None and thread.is_alive():
            thread.join(timeout=1.0)
        self.recorder.stop()
        if self.midi_input is not None:
            try:
                self.midi_input.close()
            finally:
                self.midi_input = None
        if self.audio is not None:
            self.audio.close()
            self.audio = None
        if self.model is not None:
            self.model.close(suppress_errors=True)
            self.model = None
        self._running = False
        self._state = "stopped" if self._state != "failed" else "failed"
