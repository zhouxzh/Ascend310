#!/usr/bin/env python3
"""Real-time MIDI -> DDSP audio using ONNX Runtime or an Ascend OM model.

The control-model backend is isolated from MIDI handling and audio synthesis.
ONNX runs on the CPU; OM inference uses PyACL on an Ascend device.
Each active MIDI note owns an independent recurrent state and synth voice,
following the per-voice architecture used by ddsp-realtime.

Examples:
    python realtime_ddsp.py --demo --duration 2 --output violin_demo.wav
    python realtime_ddsp.py --midi-file test_violin.mid --output test_violin.wav
    python realtime_ddsp.py --play-midi test_violin.mid --prebuffer 6
    python realtime_ddsp.py --list-midi
    python realtime_ddsp.py --list-audio
    python realtime_ddsp.py --live --midi-port "Your MIDI Keyboard"
"""

from __future__ import annotations

import argparse
from collections import deque
from dataclasses import dataclass, replace
from collections.abc import Callable
import errno
import math
import os
from pathlib import Path
import queue
import re
import select
import sys
import threading
import time
from typing import Union
import wave

import numpy as np
from scipy.signal.windows import hann


ROOT_DIR = Path(__file__).resolve().parent
DEFAULT_MODEL = ROOT_DIR / "models" / "ddsp_vst" / "Violin.onnx"
MODEL_SAMPLE_RATE = 16_000
MODEL_HOP_SIZE = 320
MODEL_FRAME_RATE = MODEL_SAMPLE_RATE // MODEL_HOP_SIZE
NUM_HARMONICS = 60
NUM_NOISE_AMPS = 65


@dataclass(frozen=True)
class EnvelopeSettings:
    """ADSR settings for the MIDI loudness control signal."""

    # Match the DDSP-VST synth defaults. Short control envelopes are prone to
    # audible transients because this model is updated at 50 Hz.
    attack: float = 0.10
    decay: float = 0.0
    sustain: float = 1.0
    release: float = 1.20


DEFAULT_ENVELOPE = EnvelopeSettings()


@dataclass(frozen=True)
class DdspVstSettings:
    """Runtime controls exposed by the original DDSP-VST Synth plug-in."""

    pitch_shift: float = 0.0
    harmonic_gain: float = 1.0
    noise_gain: float = 1.0
    output_gain_db: float = 0.0
    velocity_curve: float = 1.0
    attack: float = DEFAULT_ENVELOPE.attack
    decay: float = DEFAULT_ENVELOPE.decay
    sustain: float = DEFAULT_ENVELOPE.sustain
    release: float = DEFAULT_ENVELOPE.release
    input_pitch: float = 0.0
    input_gain: float = 0.0
    reverb_size: float = 0.4
    reverb_damping: float = 0.1
    reverb_wet: float = 0.0

    @property
    def envelope(self) -> EnvelopeSettings:
        return EnvelopeSettings(
            attack=self.attack,
            decay=self.decay,
            sustain=self.sustain,
            release=self.release,
        )


DDSP_VST_PARAMETER_RANGES = {
    "pitch_shift": (-24.0, 24.0),
    "harmonic_gain": (0.0, 1.0),
    "noise_gain": (0.0, 1.0),
    "output_gain_db": (-60.0, 6.0),
    "velocity_curve": (0.25, 2.0),
    "attack": (0.01, 3.0),
    "decay": (0.0, 3.0),
    "sustain": (0.0, 1.0),
    "release": (0.01, 5.0),
    "input_pitch": (-0.5, 0.5),
    "input_gain": (-0.5, 0.5),
    "reverb_size": (0.0, 1.0),
    "reverb_damping": (0.0, 1.0),
    "reverb_wet": (0.0, 1.0),
}


def shape_midi_velocity(velocity: float, curve: float) -> float:
    """Apply a gamma curve while preserving silence and full-scale velocity."""
    normalized = float(np.clip(velocity, 0.0, 1.0))
    return normalized ** float(curve)


class Adsr:
    """Sample-based ADSR envelope used to form the DDSP loudness input."""

    def __init__(
        self,
        sample_rate: int,
        settings: EnvelopeSettings = DEFAULT_ENVELOPE,
    ) -> None:
        self.sample_rate = float(sample_rate)
        self.attack = max(settings.attack, 1e-5)
        self.decay = max(settings.decay, 1e-5)
        self.sustain = float(np.clip(settings.sustain, 0.0, 1.0))
        self.release = max(settings.release, 1e-5)
        self.level = 0.0
        self.phase = "idle"

    def note_on(self) -> None:
        self.phase = "attack"

    def note_off(self) -> None:
        if self.phase != "idle":
            self.phase = "release"

    def next_block(self, size: int) -> np.ndarray:
        values = np.empty(size, dtype=np.float32)
        for index in range(size):
            if self.phase == "attack":
                self.level += 1.0 / (self.attack * self.sample_rate)
                if self.level >= 1.0:
                    self.level = 1.0
                    self.phase = "decay"
            elif self.phase == "decay":
                self.level -= (1.0 - self.sustain) / (self.decay * self.sample_rate)
                if self.level <= self.sustain:
                    self.level = self.sustain
                    self.phase = "sustain"
            elif self.phase == "sustain":
                self.level = self.sustain
            elif self.phase == "release":
                self.level -= 1.0 / (self.release * self.sample_rate)
                if self.level <= 0.0:
                    self.level = 0.0
                    self.phase = "idle"
            else:
                self.level = 0.0
            values[index] = self.level
        return values


@dataclass
class MidiSnapshot:
    note: float = 0.0
    velocity: float = 0.0
    pitch_bend: int = 8192
    volume: float = 1.0
    expression: float = 1.0
    envelope: float = 0.0


class MidiState:
    """Thread-safe monophonic MIDI state with last-note priority."""

    def __init__(self, envelope: EnvelopeSettings = DEFAULT_ENVELOPE) -> None:
        self._lock = threading.Lock()
        self._snapshot = MidiSnapshot()
        self._held_notes: list[int] = []
        self._adsr = Adsr(MODEL_SAMPLE_RATE, envelope)

    @property
    def adsr(self) -> Adsr:
        return self._adsr

    def handle_message(self, message) -> None:
        message_type = getattr(message, "type", None)
        with self._lock:
            if message_type == "note_on" and getattr(message, "velocity", 0) > 0:
                note = int(message.note)
                self._held_notes = [n for n in self._held_notes if n != note]
                self._held_notes.append(note)
                self._snapshot.note = float(note)
                self._snapshot.velocity = float(message.velocity) / 127.0
                self._adsr.note_on()
            elif message_type in ("note_off", "note_on"):
                note = int(message.note)
                self._held_notes = [n for n in self._held_notes if n != note]
                if self._held_notes:
                    self._snapshot.note = float(self._held_notes[-1])
                    self._adsr.note_on()
                elif note == int(self._snapshot.note):
                    self._adsr.note_off()
            elif message_type == "pitchwheel":
                self._snapshot.pitch_bend = int(message.pitch) + 8192
            elif message_type == "control_change":
                if message.control == 7:
                    self._snapshot.volume = float(message.value) / 127.0
                elif message.control == 11:
                    self._snapshot.expression = float(message.value) / 127.0

    def note_on(self, note: int, velocity: int = 100) -> None:
        self.handle_message(
            type(
                "MidiMessage",
                (),
                {"type": "note_on", "note": int(note), "velocity": int(velocity)},
            )()
        )

    def note_off(self, note: int) -> None:
        self.handle_message(
            type(
                "MidiMessage",
                (),
                {"type": "note_off", "note": int(note), "velocity": 0},
            )()
        )

    def all_notes_off(self) -> None:
        with self._lock:
            self._held_notes.clear()
            self._adsr.note_off()

    def next_snapshot(self) -> MidiSnapshot:
        with self._lock:
            envelope = self._adsr.next_block(MODEL_HOP_SIZE)
            snapshot = MidiSnapshot(
                note=self._snapshot.note,
                velocity=self._snapshot.velocity,
                pitch_bend=self._snapshot.pitch_bend,
                volume=self._snapshot.volume,
                expression=self._snapshot.expression,
                envelope=float(envelope[-1]),
            )
            if self._adsr.phase == "idle" and not self._held_notes:
                snapshot.note = 0.0
                snapshot.velocity = 0.0
            return snapshot


@dataclass
class MidiVoiceSnapshot:
    """One frame of state for one MIDI note."""

    slot: int
    note: int
    velocity: float
    pitch_bend: int
    volume: float
    expression: float
    envelope: float
    finished: bool = False


@dataclass
class _MidiVoice:
    slot: int
    note: int
    velocity: float
    pitch_bend: int = 8192
    volume: float = 1.0
    expression: float = 1.0
    held: bool = True
    sustained: bool = False
    adsr: Adsr | None = None


class PolyphonicMidiState:
    """Thread-safe MIDI state with one ADSR per active note.

    This mirrors ddsp-realtime's voice model: MIDI events are collected on
    the input thread, while the render thread takes frame snapshots. Each
    note gets an independent envelope and ONNX recurrent state in the engine.
    """

    def __init__(
        self,
        max_voices: int = 8,
        envelope: EnvelopeSettings = DEFAULT_ENVELOPE,
    ) -> None:
        self._lock = threading.Lock()
        self.max_voices = max(1, int(max_voices))
        self.envelope = envelope
        self._voices: dict[int, _MidiVoice] = {}
        self._pitch_bend = 8192
        self._volume = 1.0
        self._expression = 1.0
        self._sustain = False

    def _next_free_slot(self) -> int:
        used_slots = {voice.slot for voice in self._voices.values()}
        for slot in range(self.max_voices):
            if slot not in used_slots:
                return slot
        raise RuntimeError("No MIDI voice slot is available")

    def _drop_oldest_voice(self) -> int:
        # Dict insertion order is the event order. Prefer stealing a released
        # voice so a held chord is preserved where possible.
        for note, voice in self._voices.items():
            if not voice.held:
                del self._voices[note]
                return voice.slot
        oldest_note = next(iter(self._voices))
        voice = self._voices.pop(oldest_note)
        return voice.slot

    def handle_message(self, message) -> None:
        message_type = getattr(message, "type", None)
        with self._lock:
            if message_type == "note_on" and getattr(message, "velocity", 0) > 0:
                note = int(message.note)
                if note in self._voices:
                    voice = self._voices.pop(note)
                    voice.velocity = float(message.velocity) / 127.0
                    voice.pitch_bend = self._pitch_bend
                    voice.volume = self._volume
                    voice.expression = self._expression
                    voice.held = True
                    voice.sustained = False
                    voice.adsr.note_on()
                    self._voices[note] = voice
                    return
                elif len(self._voices) >= self.max_voices:
                    slot = self._drop_oldest_voice()
                else:
                    slot = self._next_free_slot()
                voice = _MidiVoice(
                    slot=slot,
                    note=note,
                    velocity=float(message.velocity) / 127.0,
                    pitch_bend=self._pitch_bend,
                    volume=self._volume,
                    expression=self._expression,
                    adsr=Adsr(MODEL_SAMPLE_RATE, self.envelope),
                )
                voice.adsr.note_on()
                self._voices[note] = voice
            elif message_type in ("note_off", "note_on"):
                note = int(message.note)
                voice = self._voices.get(note)
                if voice is not None:
                    voice.held = False
                    voice.sustained = self._sustain
                    if not self._sustain:
                        voice.adsr.note_off()
            elif message_type == "pitchwheel":
                self._pitch_bend = int(np.clip(int(message.pitch) + 8192, 0, 16383))
                for voice in self._voices.values():
                    voice.pitch_bend = self._pitch_bend
            elif message_type == "control_change":
                if message.control == 7:
                    self._volume = float(message.value) / 127.0
                    for voice in self._voices.values():
                        voice.volume = self._volume
                elif message.control == 11:
                    self._expression = float(message.value) / 127.0
                    for voice in self._voices.values():
                        voice.expression = self._expression
                elif message.control == 64:
                    self._set_sustain_locked(int(message.value) >= 64)
                elif message.control in (120, 123):
                    self._all_notes_off_locked()

    @staticmethod
    def _validate_note(note: int) -> int:
        note = int(note)
        if not 0 <= note <= 127:
            raise ValueError("MIDI note must be in [0, 127]")
        return note

    @staticmethod
    def _validate_midi_value(value: int, name: str) -> int:
        value = int(value)
        if not 0 <= value <= 127:
            raise ValueError(f"{name} must be in [0, 127]")
        return value

    def note_on(self, note: int, velocity: int = 100) -> None:
        note = self._validate_note(note)
        velocity = self._validate_midi_value(velocity, "velocity")
        self.handle_message(
            type(
                "MidiMessage",
                (),
                {"type": "note_on", "note": note, "velocity": velocity},
            )()
        )

    def note_off(self, note: int) -> None:
        note = self._validate_note(note)
        self.handle_message(
            type(
                "MidiMessage",
                (),
                {"type": "note_off", "note": note, "velocity": 0},
            )()
        )

    def set_control(self, control: int, value: int) -> None:
        control = self._validate_midi_value(control, "control")
        value = self._validate_midi_value(value, "value")
        self.handle_message(
            type(
                "MidiMessage",
                (),
                {"type": "control_change", "control": control, "value": value},
            )()
        )

    def set_pitch_bend(self, value: int) -> None:
        value = int(value)
        if not -8192 <= value <= 8191:
            raise ValueError("pitch bend must be in [-8192, 8191]")
        self.handle_message(
            type("MidiMessage", (), {"type": "pitchwheel", "pitch": value})()
        )

    def _set_sustain_locked(self, enabled: bool) -> None:
        self._sustain = bool(enabled)
        if self._sustain:
            return
        for voice in self._voices.values():
            if voice.sustained and not voice.held:
                voice.sustained = False
                voice.adsr.note_off()

    def set_sustain(self, enabled: bool) -> None:
        with self._lock:
            self._set_sustain_locked(enabled)

    def _all_notes_off_locked(self) -> None:
        self._sustain = False
        for voice in self._voices.values():
            voice.held = False
            voice.sustained = False
            voice.adsr.note_off()

    def all_notes_off(self) -> None:
        with self._lock:
            self._all_notes_off_locked()

    def update_envelope(self, settings: EnvelopeSettings) -> None:
        """Apply new ADSR values to current and future voices."""
        with self._lock:
            self.envelope = settings
            for voice in self._voices.values():
                if voice.adsr is None:
                    continue
                voice.adsr.attack = max(settings.attack, 1e-5)
                voice.adsr.decay = max(settings.decay, 1e-5)
                voice.adsr.sustain = float(np.clip(settings.sustain, 0.0, 1.0))
                voice.adsr.release = max(settings.release, 1e-5)

    @property
    def active_notes(self) -> list[int]:
        with self._lock:
            return sorted(self._voices)

    def next_snapshots(self) -> list[MidiVoiceSnapshot]:
        snapshots: list[MidiVoiceSnapshot] = []
        with self._lock:
            finished: list[int] = []
            for note, voice in self._voices.items():
                envelope = voice.adsr.next_block(MODEL_HOP_SIZE)
                is_finished = voice.adsr.phase == "idle" and not voice.held
                snapshots.append(
                    MidiVoiceSnapshot(
                        slot=voice.slot,
                        note=note,
                        velocity=voice.velocity,
                        pitch_bend=voice.pitch_bend,
                        volume=voice.volume,
                        expression=voice.expression,
                        envelope=float(envelope[-1]),
                        finished=is_finished,
                    )
                )
                if is_finished:
                    finished.append(note)
            for note in finished:
                self._voices.pop(note, None)
        return snapshots


@dataclass
class ModelControls:
    amplitude: float
    harmonics: np.ndarray
    noise_amps: np.ndarray


class OnnxControlsModel:
    """Stateful ONNX Runtime wrapper for the exported DDSP-VST graph."""

    def __init__(self, model_path: Path) -> None:
        try:
            import onnxruntime as ort
        except ImportError as exc:
            raise RuntimeError(
                "ONNX Runtime is an optional local dependency and is not "
                "included in the Ascend board requirements.txt."
            ) from exc
        session_options = ort.SessionOptions()
        session_options.intra_op_num_threads = 1
        session_options.inter_op_num_threads = 1
        self.session = ort.InferenceSession(
            str(model_path),
            sess_options=session_options,
            providers=["CPUExecutionProvider"],
        )
        input_names = {item.name for item in self.session.get_inputs()}
        output_names = {item.name for item in self.session.get_outputs()}
        required_inputs = {"state", "f0_scaled", "pw_scaled"}
        required_outputs = {"amplitude", "harmonics", "noise_amps", "state_out"}
        if not required_inputs.issubset(input_names):
            raise ValueError(f"ONNX inputs missing: {required_inputs - input_names}")
        if not required_outputs.issubset(output_names):
            raise ValueError(f"ONNX outputs missing: {required_outputs - output_names}")
        self.state = np.zeros(512, dtype=np.float32)
        self.backend_name = "onnx"

    def reset(self) -> None:
        self.state.fill(0.0)

    def predict_from_state(
        self, state: np.ndarray, f0_scaled: float, pw_scaled: float
    ) -> tuple[ModelControls, np.ndarray]:
        values = self.session.run(
            None,
            {
                "state": np.asarray(state, dtype=np.float32).reshape(512),
                "f0_scaled": np.asarray([f0_scaled], dtype=np.float32),
                "pw_scaled": np.asarray([pw_scaled], dtype=np.float32),
            },
        )
        outputs = {
            item.name: value.reshape(-1)
            for item, value in zip(self.session.get_outputs(), values)
        }
        controls = ModelControls(
            amplitude=float(outputs["amplitude"][0]),
            harmonics=outputs["harmonics"].astype(np.float32, copy=False),
            noise_amps=outputs["noise_amps"].astype(np.float32, copy=False),
        )
        return controls, outputs["state_out"].astype(np.float32, copy=True)

    def predict(self, f0_scaled: float, pw_scaled: float) -> ModelControls:
        """Predict using the wrapper's private state (single-voice helper)."""
        controls, self.state = self.predict_from_state(
            self.state, f0_scaled, pw_scaled
        )
        return controls

    def close(self) -> None:
        """Match the explicit lifecycle exposed by the PyACL backend."""


class PyAclControlsModel:
    """Stateful adapter around the static DDSP PyACL model runner."""

    def __init__(
        self,
        model_path: Path,
        device_id: int = 0,
        *,
        keep_runtime: bool = False,
    ) -> None:
        from pyacl_ddsp import PyAclModelRunner

        self.runner = PyAclModelRunner(
            model_path,
            device_id=device_id,
            keep_runtime=keep_runtime,
        )
        self.state = np.zeros(512, dtype=np.float32)
        self.backend_name = "om-pyacl"

    def reset(self) -> None:
        self.state.fill(0.0)

    def predict_from_state(
        self, state: np.ndarray, f0_scaled: float, pw_scaled: float
    ) -> tuple[ModelControls, np.ndarray]:
        outputs = self.runner.infer(
            {
                "state": np.asarray(state, dtype=np.float32).reshape(512),
                "f0_scaled": np.asarray([f0_scaled], dtype=np.float32),
                "pw_scaled": np.asarray([pw_scaled], dtype=np.float32),
            }
        )
        controls = ModelControls(
            amplitude=float(outputs["amplitude"][0]),
            harmonics=outputs["harmonics"],
            noise_amps=outputs["noise_amps"],
        )
        return controls, outputs["state_out"]

    def predict(self, f0_scaled: float, pw_scaled: float) -> ModelControls:
        controls, self.state = self.predict_from_state(
            self.state, f0_scaled, pw_scaled
        )
        return controls

    def close(self) -> None:
        self.runner.close()


ControlsModel = Union[OnnxControlsModel, PyAclControlsModel]


def create_controls_model(
    model_path: Path,
    backend: str = "auto",
    device_id: int = 0,
    *,
    keep_acl_runtime: bool = False,
) -> ControlsModel:
    """Select a control-model backend explicitly or from the file extension."""
    suffix = model_path.suffix.lower()
    if backend == "auto":
        try:
            backend = {".onnx": "onnx", ".om": "om"}[suffix]
        except KeyError as exc:
            raise ValueError(
                "Cannot infer model backend; use an .onnx or .om model"
            ) from exc
    if backend == "onnx":
        if suffix != ".onnx":
            raise ValueError("The ONNX backend requires a .onnx model")
        return OnnxControlsModel(model_path)
    if backend == "om":
        if suffix != ".om":
            raise ValueError("The OM backend requires a .om model")
        return PyAclControlsModel(
            model_path,
            device_id=device_id,
            keep_runtime=keep_acl_runtime,
        )
    raise ValueError(f"Unsupported model backend: {backend}")


class HarmonicSynthesizer:
    def __init__(self, sample_rate: int = MODEL_SAMPLE_RATE, hop_size: int = MODEL_HOP_SIZE):
        self.sample_rate = float(sample_rate)
        self.hop_size = hop_size
        self.harmonic_numbers = np.arange(1, NUM_HARMONICS + 1, dtype=np.float32)
        self.previous_phase = 0.0
        self.previous_f0: float | None = None
        self.previous_amplitudes = np.zeros(NUM_HARMONICS, dtype=np.float32)

    def reset(self) -> None:
        self.previous_phase = 0.0
        self.previous_f0 = None
        self.previous_amplitudes.fill(0.0)

    def render(
        self, amplitude: float, harmonic_distribution: np.ndarray, f0_hz: float
    ) -> np.ndarray:
        if f0_hz <= 0.0 or amplitude <= 0.0:
            self.previous_f0 = 0.0
            self.previous_amplitudes.fill(0.0)
            return np.zeros(self.hop_size, dtype=np.float32)

        distribution = np.asarray(harmonic_distribution, dtype=np.float32).copy()
        frequencies = f0_hz * self.harmonic_numbers
        distribution[frequencies >= self.sample_rate / 2.0] = 0.0
        total = float(distribution.sum())
        if total <= 1e-8:
            return np.zeros(self.hop_size, dtype=np.float32)
        amplitudes = distribution / total * float(amplitude)

        previous_f0 = f0_hz if self.previous_f0 is None else self.previous_f0
        frequency_envelope = np.empty(self.hop_size, dtype=np.float32)
        midpoint = self.hop_size // 2
        frequency_envelope[:midpoint] = np.linspace(
            previous_f0, f0_hz, midpoint, endpoint=False, dtype=np.float32
        )
        frequency_envelope[midpoint:] = f0_hz

        amplitude_envelope = np.empty((self.hop_size, NUM_HARMONICS), dtype=np.float32)
        amplitude_envelope[:midpoint] = np.linspace(
            self.previous_amplitudes,
            amplitudes,
            midpoint,
            endpoint=False,
            axis=0,
            dtype=np.float32,
        )
        amplitude_envelope[midpoint:] = amplitudes

        phase_increments = frequency_envelope * (2.0 * math.pi / self.sample_rate)
        phases = np.cumsum(phase_increments, dtype=np.float32) + self.previous_phase
        self.previous_phase = float(phases[-1] % (2.0 * math.pi))
        self.previous_f0 = f0_hz
        self.previous_amplitudes = amplitudes
        return np.sum(
            np.sin(phases[:, None] * self.harmonic_numbers[None, :])
            * amplitude_envelope,
            axis=1,
            dtype=np.float32,
        )


class NoiseSynthesizer:
    def __init__(self, hop_size: int = MODEL_HOP_SIZE, seed: int = 42):
        self.hop_size = hop_size
        self.ir_size = (NUM_NOISE_AMPS - 1) * 2
        self.fft_size = 512
        self.rng = np.random.default_rng(seed)
        index = np.arange(self.ir_size, dtype=np.float32)
        window = 0.5 * (1.0 - np.cos(2.0 * math.pi * index / self.ir_size))
        self.zero_phase_window = np.roll(window, self.ir_size // 2)

    def reset(self) -> None:
        self.rng = np.random.default_rng(42)

    def render(self, magnitudes: np.ndarray) -> np.ndarray:
        spectrum = np.asarray(magnitudes, dtype=np.float32).astype(np.complex64)
        impulse = np.fft.irfft(spectrum, n=self.ir_size).astype(np.float32)
        impulse *= self.zero_phase_window
        impulse = np.roll(impulse, -self.ir_size // 2)

        padded_impulse = np.zeros(self.fft_size, dtype=np.float32)
        padded_impulse[: self.ir_size] = impulse
        white = self.rng.uniform(-1.0, 1.0, self.fft_size).astype(np.float32)
        filtered = np.fft.irfft(
            np.fft.rfft(white) * np.fft.rfft(padded_impulse),
            n=self.fft_size,
        ).astype(np.float32)
        delay = (self.ir_size - 1) // 2 - 1
        return filtered[delay : delay + self.hop_size]


class LinearResampler:
    """Legacy streaming linear resampler used by the MIDI-DDSP path."""

    def __init__(self, source_rate: int, target_rate: int):
        self.source_rate = source_rate
        self.target_rate = target_rate
        self.previous = 0.0

    def reset(self) -> None:
        self.previous = 0.0

    def process(self, block: np.ndarray) -> np.ndarray:
        if self.source_rate == self.target_rate:
            return np.asarray(block, dtype=np.float32).copy()
        block = np.asarray(block, dtype=np.float32)
        output_size = round(block.size * self.target_rate / self.source_rate)
        source = np.concatenate(
            [np.asarray([self.previous], dtype=np.float32), block]
        )
        positions = (
            np.arange(output_size, dtype=np.float64)
            * self.source_rate
            / self.target_rate
        )
        output = np.interp(positions, np.arange(source.size), source)
        self.previous = float(block[-1]) if block.size else self.previous
        return output.astype(np.float32)


class WindowedSincResampler:
    """Stateful 100-crossing Hann-windowed sinc resampler.

    The kernel and 100-sample source-domain latency follow JUCE's
    WindowedSincInterpolator, which is used by Magenta's DDSP-VST.  The delay
    makes the filter causal, so a complete output block can be produced without
    reading samples from the next DDSP control frame.
    """

    NUM_CROSSINGS = 100
    TABLE_POINTS_PER_CROSSING = 100
    HISTORY_SIZE = NUM_CROSSINGS * 2
    _lookup_table: np.ndarray | None = None

    def __init__(self, source_rate: int, target_rate: int):
        self.source_rate = int(source_rate)
        self.target_rate = int(target_rate)
        if self.source_rate <= 0 or self.target_rate <= 0:
            raise ValueError("Resampler sample rates must be positive")
        self.history = np.zeros(self.HISTORY_SIZE, dtype=np.float32)
        self._plans: dict[int, tuple[np.ndarray, np.ndarray]] = {}

    @classmethod
    def _table(cls) -> np.ndarray:
        if cls._lookup_table is None:
            table_size = cls.NUM_CROSSINGS * cls.TABLE_POINTS_PER_CROSSING
            positions = np.linspace(
                0.0, cls.NUM_CROSSINGS, table_size + 1, dtype=np.float64
            )
            window = hann(table_size * 2 + 1, sym=True)[table_size:]
            cls._lookup_table = (np.sinc(positions) * window).astype(np.float32)
        return cls._lookup_table

    @property
    def algorithmic_latency_seconds(self) -> float:
        return self.NUM_CROSSINGS / float(self.source_rate)

    def reset(self) -> None:
        self.history.fill(0.0)

    def prepare(self, input_size: int) -> None:
        """Precompute the fixed-rate phase kernels outside the render thread."""
        self._plan(int(input_size))

    def _plan(self, input_size: int) -> tuple[np.ndarray, np.ndarray]:
        cached = self._plans.get(input_size)
        if cached is not None:
            return cached

        output_size = round(input_size * self.target_rate / self.source_rate)
        ratio = self.source_rate / float(self.target_rate)
        positions = (
            np.arange(output_size, dtype=np.float64) * ratio - self.NUM_CROSSINGS
        )
        centers = np.floor(positions).astype(np.int64)
        offsets = np.arange(
            -self.NUM_CROSSINGS + 1,
            self.NUM_CROSSINGS + 1,
            dtype=np.int64,
        )
        source_indices = centers[:, None] + offsets[None, :]
        distances = positions[:, None] - source_indices

        table_positions = (
            np.abs(distances) * self.TABLE_POINTS_PER_CROSSING
        )
        lower = np.floor(table_positions).astype(np.int64)
        maximum = self.NUM_CROSSINGS * self.TABLE_POINTS_PER_CROSSING
        lower = np.clip(lower, 0, maximum)
        upper = np.minimum(lower + 1, maximum)
        fraction = table_positions - lower
        table = self._table()
        weights = table[lower] + fraction * (table[upper] - table[lower])
        weights[table_positions >= maximum] = 0.0

        indices = source_indices + self.HISTORY_SIZE
        plan = (
            indices.astype(np.int32, copy=False),
            weights.astype(np.float32, copy=False),
        )
        self._plans[input_size] = plan
        return plan

    def process(self, block: np.ndarray) -> np.ndarray:
        block = np.asarray(block, dtype=np.float32).reshape(-1)
        if block.size == 0:
            return np.zeros(0, dtype=np.float32)
        indices, weights = self._plan(block.size)
        source = np.concatenate((self.history, block))
        output = np.einsum(
            "ij,ij->i",
            source[indices],
            weights,
            optimize=True,
            dtype=np.float32,
        )
        self.history[:] = source[-self.HISTORY_SIZE :]
        return output.astype(np.float32, copy=False)


class _SmoothedValue:
    def __init__(self, value: float) -> None:
        self.current = float(value)
        self.target = float(value)
        self.step = 0.0
        self.remaining = 0

    def set_target(self, value: float, samples: int) -> None:
        self.target = float(value)
        self.remaining = max(0, int(samples))
        if self.remaining == 0:
            self.current = self.target
            self.step = 0.0
        else:
            self.step = (self.target - self.current) / self.remaining

    def next(self) -> float:
        if self.remaining > 0:
            self.current += self.step
            self.remaining -= 1
            if self.remaining == 0:
                self.current = self.target
        return self.current


class JuceFreeverb:
    """Stereo FreeVerb topology and parameter mapping used by JUCE Reverb."""

    COMB_TUNINGS = (1116, 1188, 1277, 1356, 1422, 1491, 1557, 1617)
    ALLPASS_TUNINGS = (556, 441, 341, 225)
    STEREO_SPREAD = 23

    def __init__(self, sample_rate: int, settings: DdspVstSettings) -> None:
        self.sample_rate = int(sample_rate)
        scale = self.sample_rate / 44_100.0
        self.comb_buffers = [
            [
                np.zeros(max(1, round((size + channel * self.STEREO_SPREAD) * scale)))
                for size in self.COMB_TUNINGS
            ]
            for channel in range(2)
        ]
        self.allpass_buffers = [
            [
                np.zeros(max(1, round((size + channel * self.STEREO_SPREAD) * scale)))
                for size in self.ALLPASS_TUNINGS
            ]
            for channel in range(2)
        ]
        self.comb_indices = [[0] * len(self.COMB_TUNINGS) for _ in range(2)]
        self.allpass_indices = [[0] * len(self.ALLPASS_TUNINGS) for _ in range(2)]
        self.comb_filter_store = [
            [0.0] * len(self.COMB_TUNINGS) for _ in range(2)
        ]
        self.room = _SmoothedValue(self._room(settings.reverb_size))
        self.damping = _SmoothedValue(self._damping(settings.reverb_damping))
        self.wet = _SmoothedValue(settings.reverb_wet)

    @staticmethod
    def _room(value: float) -> float:
        return float(value) * 0.28 + 0.7

    @staticmethod
    def _damping(value: float) -> float:
        return float(value) * 0.4

    def update(self, settings: DdspVstSettings) -> None:
        smoothing_samples = max(1, round(self.sample_rate * 0.01))
        self.room.set_target(self._room(settings.reverb_size), smoothing_samples)
        self.damping.set_target(
            self._damping(settings.reverb_damping), smoothing_samples
        )
        self.wet.set_target(settings.reverb_wet, smoothing_samples)

    def reset(self) -> None:
        for channels in (self.comb_buffers, self.allpass_buffers):
            for channel in channels:
                for buffer in channel:
                    buffer.fill(0.0)
        self.comb_indices = [[0] * len(self.COMB_TUNINGS) for _ in range(2)]
        self.allpass_indices = [[0] * len(self.ALLPASS_TUNINGS) for _ in range(2)]
        self.comb_filter_store = [
            [0.0] * len(self.COMB_TUNINGS) for _ in range(2)
        ]

    def process(self, dry: np.ndarray) -> np.ndarray:
        dry = np.asarray(dry, dtype=np.float32).reshape(-1)
        output = np.empty((dry.size, 2), dtype=np.float32)
        if self.wet.current <= 1e-7 and self.wet.target <= 1e-7:
            output[:, 0] = dry
            output[:, 1] = dry
            return output

        for sample_index, dry_sample in enumerate(dry):
            room = self.room.next()
            damping = self.damping.next()
            wet = self.wet.next() * 3.0
            filter_input = float(dry_sample) * 0.015
            channel_values = [0.0, 0.0]
            for channel in range(2):
                value = 0.0
                for comb_index, buffer in enumerate(self.comb_buffers[channel]):
                    index = self.comb_indices[channel][comb_index]
                    delayed = float(buffer[index])
                    filtered = delayed * (1.0 - damping) + (
                        self.comb_filter_store[channel][comb_index] * damping
                    )
                    self.comb_filter_store[channel][comb_index] = filtered
                    buffer[index] = filter_input + filtered * room
                    self.comb_indices[channel][comb_index] = (index + 1) % buffer.size
                    value += delayed
                for allpass_index, buffer in enumerate(
                    self.allpass_buffers[channel]
                ):
                    index = self.allpass_indices[channel][allpass_index]
                    delayed = float(buffer[index])
                    buffer[index] = value + delayed * 0.5
                    value = delayed - value
                    self.allpass_indices[channel][allpass_index] = (
                        index + 1
                    ) % buffer.size
                channel_values[channel] = value
            output[sample_index, 0] = dry_sample + wet * channel_values[0]
            output[sample_index, 1] = dry_sample + wet * channel_values[1]
        return output


class VoiceRenderer:
    """DDSP state and synthesizers for one active MIDI note."""

    def __init__(self, controls_model: ControlsModel) -> None:
        self.controls_model = controls_model
        self.state = np.zeros(512, dtype=np.float32)
        self.harmonic = HarmonicSynthesizer()
        self.noise = NoiseSynthesizer()

    def reset(self) -> None:
        self.state.fill(0.0)
        self.harmonic.reset()
        self.noise.reset()

    def render(
        self,
        snapshot: MidiVoiceSnapshot,
        settings: DdspVstSettings = DdspVstSettings(),
    ) -> np.ndarray:
        bend_semitones = (snapshot.pitch_bend - 8192.0) / 4096.0
        shifted_note = snapshot.note + bend_semitones + settings.pitch_shift
        f0_scaled = shifted_note / 127.0 - settings.input_pitch
        if snapshot.finished:
            _, self.state = self.controls_model.predict_from_state(
                self.state,
                f0_scaled,
                0.0,
            )
            self.harmonic.previous_amplitudes.fill(0.0)
            return np.zeros(MODEL_HOP_SIZE, dtype=np.float32)

        if snapshot.note <= 0 or snapshot.envelope <= 1e-5:
            _, self.state = self.controls_model.predict_from_state(
                self.state, 0.0, 0.0
            )
            self.harmonic.reset()
            self.noise.reset()
            return np.zeros(MODEL_HOP_SIZE, dtype=np.float32)

        shaped_velocity = shape_midi_velocity(
            snapshot.velocity,
            settings.velocity_curve,
        )
        pw_scaled = float(
            snapshot.envelope
            * shaped_velocity
            * snapshot.volume
            * snapshot.expression
            - settings.input_gain
        )
        controls, self.state = self.controls_model.predict_from_state(
            self.state, f0_scaled, pw_scaled
        )
        f0_hz = 440.0 * 2.0 ** ((shifted_note - 69.0) / 12.0)
        harmonic = self.harmonic.render(
            controls.amplitude * settings.harmonic_gain,
            controls.harmonics,
            f0_hz,
        )
        noise = self.noise.render(controls.noise_amps * settings.noise_gain)
        return (harmonic + noise).astype(np.float32, copy=False)


class PolyphonicGainSmoother:
    """Smooth 1/N voice normalization without delaying overload protection."""

    def __init__(self, release_seconds: float = 0.08) -> None:
        self.release_seconds = max(float(release_seconds), 1e-4)
        self.current_gain = 1.0
        self.active = False

    def reset(self) -> None:
        self.current_gain = 1.0
        self.active = False

    def process(self, mixed: np.ndarray, voice_count: int) -> np.ndarray:
        if voice_count <= 0:
            self.reset()
            return np.zeros_like(mixed)
        target_gain = 1.0 / float(voice_count)
        if not self.active:
            self.current_gain = target_gain
            self.active = True
            return (mixed * target_gain).astype(np.float32, copy=False)
        if target_gain < self.current_gain:
            next_gain = target_gain
        else:
            frame_seconds = MODEL_HOP_SIZE / MODEL_SAMPLE_RATE
            release_alpha = 1.0 - math.exp(-frame_seconds / self.release_seconds)
            next_gain = self.current_gain + release_alpha * (
                target_gain - self.current_gain
            )
        gain_envelope = np.linspace(
            self.current_gain,
            next_gain,
            mixed.size,
            endpoint=True,
            dtype=np.float32,
        )
        self.current_gain = next_gain
        return (mixed * gain_envelope).astype(np.float32, copy=False)


class RealtimeSynthEngine:
    def __init__(
        self,
        model_path: Path,
        output_sample_rate: int = 48_000,
        max_voices: int = 1,
        envelope: EnvelopeSettings = DEFAULT_ENVELOPE,
        output_gain_db: float = 0.0,
        backend: str = "auto",
        device_id: int = 0,
        keep_acl_runtime: bool = False,
    ):
        self.controls_model = create_controls_model(
            model_path,
            backend=backend,
            device_id=device_id,
            keep_acl_runtime=keep_acl_runtime,
        )
        print(
            f"[MODEL] backend={self.controls_model.backend_name}, "
            f"model={model_path}, device={device_id}"
        )
        self.midi = PolyphonicMidiState(max_voices=max_voices, envelope=envelope)
        self.max_voices = max(1, int(max_voices))
        self.voices = [
            VoiceRenderer(self.controls_model) for _ in range(self.max_voices)
        ]
        self.voice_gain = PolyphonicGainSmoother()
        self.resampler = WindowedSincResampler(MODEL_SAMPLE_RATE, output_sample_rate)
        self.resampler.prepare(MODEL_HOP_SIZE)
        self.output_sample_rate = output_sample_rate
        self._settings_lock = threading.Lock()
        self._output_stats_lock = threading.Lock()
        self.output_peak = 0.0
        self.clipped_samples = 0
        self.settings = DdspVstSettings(
            output_gain_db=float(output_gain_db),
            attack=envelope.attack,
            decay=envelope.decay,
            sustain=envelope.sustain,
            release=envelope.release,
        )
        self.reverb = JuceFreeverb(output_sample_rate, self.settings)

    @property
    def output_gain_db(self) -> float:
        with self._settings_lock:
            return self.settings.output_gain_db

    @property
    def output_gain(self) -> float:
        return 10.0 ** (self.output_gain_db / 20.0)

    def update_parameters(self, values: dict[str, float]) -> DdspVstSettings:
        unknown = set(values) - set(DDSP_VST_PARAMETER_RANGES)
        if unknown:
            raise ValueError(f"Unknown DDSP-VST parameters: {sorted(unknown)}")
        normalized: dict[str, float] = {}
        for name, value in values.items():
            value = float(value)
            minimum, maximum = DDSP_VST_PARAMETER_RANGES[name]
            if not math.isfinite(value) or not minimum <= value <= maximum:
                raise ValueError(f"{name} must be in [{minimum}, {maximum}]")
            normalized[name] = value
        with self._settings_lock:
            self.settings = replace(self.settings, **normalized)
            settings = self.settings
        if {"attack", "decay", "sustain", "release"} & set(normalized):
            self.midi.update_envelope(settings.envelope)
        if {"reverb_size", "reverb_damping", "reverb_wet"} & set(normalized):
            self.reverb.update(settings)
        return settings

    def reset(self) -> None:
        self.controls_model.reset()
        for voice in self.voices:
            voice.reset()
        self.voice_gain.reset()
        self.resampler.reset()
        self.reverb.reset()
        with self._output_stats_lock:
            self.output_peak = 0.0
            self.clipped_samples = 0

    def close(self) -> None:
        self.controls_model.close()

    def render_model_frame(self) -> np.ndarray:
        with self._settings_lock:
            settings = self.settings
        snapshots = self.midi.next_snapshots()
        if not snapshots:
            self.voice_gain.reset()
            return np.zeros(MODEL_HOP_SIZE, dtype=np.float32)

        mixed = np.zeros(MODEL_HOP_SIZE, dtype=np.float32)
        rendered_voices = 0
        for snapshot in snapshots:
            voice = self.voices[snapshot.slot]
            mixed += voice.render(snapshot, settings)
            if not snapshot.finished:
                rendered_voices += 1
        return self.voice_gain.process(mixed, rendered_voices)

    def render_output_block(self) -> np.ndarray:
        output = self.resampler.process(self.render_model_frame())
        gain = self.output_gain
        if gain != 1.0:
            output = output * gain
        output = self.reverb.process(output)
        output = np.asarray(output, dtype=np.float32)
        peak = float(np.max(np.abs(output), initial=0.0))
        clipped = int(np.count_nonzero(np.abs(output) > 1.0))
        with self._output_stats_lock:
            self.output_peak = max(self.output_peak, peak)
            self.clipped_samples += clipped
        return output.astype(np.float32, copy=False)


def write_wav(path: Path, samples: np.ndarray, sample_rate: int) -> None:
    samples = np.asarray(samples, dtype=np.float32)
    channels = 1 if samples.ndim == 1 else int(samples.shape[1])
    samples = np.clip(samples, -1.0, 1.0)
    pcm = (samples * 32767.0).astype("<i2").tobytes()
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(channels)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(pcm)


def render_demo(
    model_path: Path,
    output_path: Path,
    duration: float,
    sample_rate: int,
    envelope: EnvelopeSettings = DEFAULT_ENVELOPE,
    output_gain_db: float = 0.0,
    backend: str = "auto",
    device_id: int = 0,
) -> None:
    engine = RealtimeSynthEngine(
        model_path,
        sample_rate,
        envelope=envelope,
        output_gain_db=output_gain_db,
        backend=backend,
        device_id=device_id,
    )
    try:
        engine.midi.handle_message(
            type(
                "Message",
                (),
                {"type": "note_on", "note": 69, "velocity": 100},
            )()
        )
        blocks: list[np.ndarray] = []
        total_frames = max(1, math.ceil(duration * MODEL_FRAME_RATE))
        note_frames = max(1, int(total_frames * 0.72))
        for index in range(total_frames):
            if index == note_frames:
                engine.midi.handle_message(
                    type("Message", (), {"type": "note_off", "note": 69})()
                )
            blocks.append(engine.render_output_block())
    finally:
        engine.close()
    samples = np.concatenate(blocks)[: round(duration * sample_rate)]
    write_wav(output_path, samples, sample_rate)
    print(f"[DEMO] Wrote {output_path} ({samples.size} samples at {sample_rate} Hz)")


def render_midi_file(
    model_path: Path,
    midi_path: Path,
    output_path: Path,
    sample_rate: int,
    tail_seconds: float,
    max_voices: int,
    envelope: EnvelopeSettings = DEFAULT_ENVELOPE,
    output_gain_db: float = 0.0,
    backend: str = "auto",
    device_id: int = 0,
) -> None:
    """Render a MIDI file through the stateful, polyphonic DDSP model."""
    try:
        import mido
    except ImportError as exc:
        raise RuntimeError("MIDI file rendering requires mido.") from exc

    midi_file = mido.MidiFile(str(midi_path))
    events: list[tuple[float, object]] = []
    current_time = 0.0
    for message in midi_file:
        current_time += float(message.time)
        if not message.is_meta:
            events.append((current_time, message))

    engine = RealtimeSynthEngine(
        model_path,
        sample_rate,
        max_voices=max_voices,
        envelope=envelope,
        output_gain_db=output_gain_db,
        backend=backend,
        device_id=device_id,
    )
    try:
        total_duration = current_time + max(0.0, tail_seconds)
        total_frames = max(1, math.ceil(total_duration * MODEL_FRAME_RATE))
        event_index = 0
        blocks: list[np.ndarray] = []
        for frame_index in range(total_frames):
            frame_time = frame_index / MODEL_FRAME_RATE
            while (
                event_index < len(events)
                and events[event_index][0] <= frame_time + 1e-9
            ):
                engine.midi.handle_message(events[event_index][1])
                event_index += 1
            blocks.append(engine.render_output_block())
    finally:
        engine.close()

    samples = np.concatenate(blocks)
    expected_samples = round(total_duration * sample_rate)
    samples = samples[:expected_samples]
    write_wav(output_path, samples, sample_rate)
    print(
        f"[MIDI] Rendered {midi_path} -> {output_path} "
        f"({total_duration:.2f}s at {sample_rate} Hz)"
    )


class LivePlayer:
    def __init__(
        self,
        engine: RealtimeSynthEngine,
        prebuffer_blocks: int = 3,
        before_render: Callable[[int], None] | None = None,
        output_device: str | int | None = None,
        output_latency_seconds: float = 0.08,
        on_block: Callable[[np.ndarray], None] | None = None,
    ):
        self.engine = engine
        self.prebuffer_blocks = max(1, prebuffer_blocks)
        self.blocks: queue.Queue[np.ndarray] = queue.Queue(maxsize=self.prebuffer_blocks)
        self.stop_event = threading.Event()
        self.space_available = threading.Event()
        self.space_available.set()
        self.before_render = before_render
        self.on_block = on_block
        self.output_device = output_device
        self.output_latency_seconds = max(float(output_latency_seconds), 0.001)
        self.device_latency_seconds = self.output_latency_seconds
        self.sink_latency_seconds = 0.0
        self.pulse_buffer_latency_seconds = 0.0
        self._last_latency_query = 0.0
        self.output_channels = 1
        self.frame_period = MODEL_HOP_SIZE / MODEL_SAMPLE_RATE
        self._stats_lock = threading.Lock()
        self._worker_error: BaseException | None = None
        self.rendered_blocks = 0
        self.played_blocks = 0
        self.underruns = 0
        self.overruns = 0
        self.max_render_ms = 0.0
        self.render_times_ms: deque[float] = deque(maxlen=1000)
        self.worker = threading.Thread(target=self._render_loop, daemon=True)

    @property
    def buffered_blocks(self) -> int:
        return self.blocks.qsize()

    @property
    def queue_latency_seconds(self) -> float:
        return self.buffered_blocks * self.frame_period

    def _render_loop(self) -> None:
        try:
            while not self.stop_event.is_set():
                if self.blocks.full():
                    self.space_available.clear()
                    if self.blocks.full() and not self.stop_event.is_set():
                        self.space_available.wait(timeout=0.1)
                    continue

                with self._stats_lock:
                    frame_index = self.rendered_blocks
                if self.before_render is not None:
                    self.before_render(frame_index)
                started = time.monotonic()
                block = self.engine.render_output_block()
                elapsed_ms = (time.monotonic() - started) * 1000.0
                if self.on_block is not None:
                    self.on_block(block)
                try:
                    self.blocks.put_nowait(block)
                except queue.Full:
                    with self._stats_lock:
                        self.overruns += 1
                    continue
                with self._stats_lock:
                    self.rendered_blocks += 1
                    self.max_render_ms = max(self.max_render_ms, elapsed_ms)
                    self.render_times_ms.append(elapsed_ms)
        except BaseException as exc:
            with self._stats_lock:
                self._worker_error = exc
            self.stop_event.set()
            self.space_available.set()

    def _audio_callback(self, outdata, frames, _time_info, status) -> None:
        had_underrun = bool(getattr(status, "output_underflow", False))
        block_size = round(
            MODEL_HOP_SIZE * self.engine.output_sample_rate / MODEL_SAMPLE_RATE
        )
        if frames != block_size:
            outdata.fill(0.0)
            had_underrun = True
        else:
            try:
                block = self.blocks.get_nowait()
            except queue.Empty:
                outdata.fill(0.0)
                had_underrun = True
            else:
                self.space_available.set()
                block = np.asarray(block, dtype=np.float32)
                if block.ndim == 1:
                    outdata[:, :] = block[:, np.newaxis]
                elif self.output_channels == 1:
                    outdata[:, 0] = np.mean(block, axis=1)
                else:
                    outdata.fill(0.0)
                    channels = min(self.output_channels, block.shape[1])
                    outdata[:, :channels] = block[:, :channels]
                    if self.output_channels > channels:
                        outdata[:, channels:] = block[:, :1]
                with self._stats_lock:
                    self.played_blocks += 1
        if had_underrun:
            with self._stats_lock:
                self.underruns += 1

    def raise_worker_error(self) -> None:
        with self._stats_lock:
            worker_error = self._worker_error
        if worker_error is not None:
            raise RuntimeError("Realtime render worker failed") from worker_error

    def _stop_worker(self) -> None:
        self.stop_event.set()
        self.space_available.set()
        if self.worker.ident is not None:
            self.worker.join(timeout=2.0)

    def _select_output_channels(self, sd) -> tuple[int, str]:
        device = sd.query_devices(self.output_device, "output")
        max_channels = int(device["max_output_channels"])
        if max_channels <= 0:
            raise RuntimeError(f"Audio device has no output channels: {device['name']}")
        errors: list[str] = []
        channel_candidates = ([2] if max_channels >= 2 else []) + [1]
        channel_candidates.extend(range(3, max_channels + 1))
        for channels in channel_candidates:
            try:
                sd.check_output_settings(
                    device=self.output_device,
                    channels=channels,
                    samplerate=self.engine.output_sample_rate,
                    dtype="float32",
                )
            except Exception as exc:
                errors.append(f"{channels}: {exc}")
            else:
                return channels, str(device["name"])
        details = "; ".join(errors)
        raise RuntimeError(
            f"Audio device {device['name']} does not support "
            f"{self.engine.output_sample_rate} Hz output ({details})"
        )

    def start(self) -> None:
        try:
            import sounddevice as sd
        except ImportError as exc:
            raise RuntimeError(
                "Live audio requires sounddevice and python-rtmidi. "
                "Install requirements.txt."
            ) from exc
        self.output_channels, device_name = self._select_output_channels(sd)
        print(
            f"[AUDIO] device={device_name}, channels={self.output_channels}, "
            f"sample_rate={self.engine.output_sample_rate}, "
            f"latency={self.output_latency_seconds * 1000.0:.0f} ms, "
            f"gain={self.engine.output_gain_db:+.1f} dB"
        )
        self.worker.start()
        block_size = round(
            MODEL_HOP_SIZE * self.engine.output_sample_rate / MODEL_SAMPLE_RATE
        )
        deadline = time.monotonic() + max(
            1.0, self.prebuffer_blocks * self.frame_period * 4.0
        )
        while (
            self.blocks.qsize() < self.prebuffer_blocks
            and time.monotonic() < deadline
        ):
            try:
                self.raise_worker_error()
            except RuntimeError:
                self._stop_worker()
                raise
            time.sleep(0.001)
        if self.blocks.qsize() < self.prebuffer_blocks:
            self._stop_worker()
            raise RuntimeError(
                f"Could not prepare {self.prebuffer_blocks} realtime audio blocks"
            )

        try:
            self.stream = sd.OutputStream(
                samplerate=self.engine.output_sample_rate,
                blocksize=block_size,
                channels=self.output_channels,
                dtype="float32",
                device=self.output_device,
                latency=self.output_latency_seconds,
                callback=self._audio_callback,
            )
            self.stream.start()
            actual_latency = getattr(self.stream, "latency", self.output_latency_seconds)
            if isinstance(actual_latency, (tuple, list)):
                actual_latency = actual_latency[-1]
            self.device_latency_seconds = max(float(actual_latency), 0.0)
        except Exception:
            self._stop_worker()
            raise

    def stop(self) -> None:
        stream = getattr(self, "stream", None)
        self.stream = None
        if stream is not None:
            stream.stop()
            stream.close()
        self._stop_worker()
        with self._stats_lock:
            print(
                "[LIVE] rendered={0}, played={1}, underruns={2}, overruns={3}, "
                "max_render_ms={4:.2f}".format(
                    self.rendered_blocks,
                    self.played_blocks,
                    self.underruns,
                    self.overruns,
                    self.max_render_ms,
                )
            )


def play_midi_file(
    model_path: Path,
    midi_path: Path,
    sample_rate: int,
    prebuffer: int,
    max_voices: int,
    tail_seconds: float,
    output_device: str | int | None,
    envelope: EnvelopeSettings = DEFAULT_ENVELOPE,
    output_latency_seconds: float = 0.08,
    output_gain_db: float = 0.0,
    backend: str = "auto",
    device_id: int = 0,
) -> None:
    """Play a MIDI file through the realtime audio callback without writing WAV."""
    try:
        import mido
    except ImportError as exc:
        raise RuntimeError("MIDI playback requires mido and python-rtmidi.") from exc

    midi_file = mido.MidiFile(str(midi_path))
    events: list[tuple[float, object]] = []
    current_time = 0.0
    for message in midi_file:
        current_time += float(message.time)
        if not message.is_meta:
            events.append((current_time, message))

    engine = RealtimeSynthEngine(
        model_path,
        sample_rate,
        max_voices=max_voices,
        envelope=envelope,
        output_gain_db=output_gain_db,
        backend=backend,
        device_id=device_id,
    )
    event_index = 0

    def apply_events(frame_index: int) -> None:
        nonlocal event_index
        frame_time = frame_index / MODEL_FRAME_RATE
        while event_index < len(events) and events[event_index][0] <= frame_time + 1e-9:
            engine.midi.handle_message(events[event_index][1])
            event_index += 1

    player = LivePlayer(
        engine,
        prebuffer_blocks=prebuffer,
        before_render=apply_events,
        output_device=output_device,
        output_latency_seconds=output_latency_seconds,
    )
    total_duration = current_time + max(0.0, tail_seconds)
    total_frames = max(1, math.ceil(total_duration * MODEL_FRAME_RATE))
    try:
        player.start()
        print(
            f"[PLAY] Playing {midi_path} in realtime ({total_duration:.2f}s, "
            f"{sample_rate} Hz). Press Ctrl+C to stop."
        )
        deadline = time.monotonic() + total_duration + max(5.0, prebuffer * 2.0)
        try:
            while True:
                player.raise_worker_error()
                with player._stats_lock:
                    played_blocks = player.played_blocks
                if played_blocks >= total_frames:
                    break
                if time.monotonic() >= deadline:
                    raise RuntimeError(
                        "Realtime playback did not consume the expected audio blocks; "
                        "check the selected audio device and --sample-rate."
                    )
                time.sleep(0.01)
        except KeyboardInterrupt:
            print("[PLAY] Interrupted.")
    finally:
        player.stop()
        engine.close()


RAW_MIDI_PREFIX = "raw:"
RAW_MIDI_PATTERN = re.compile(r"midiC(?P<card>\d+)D(?P<device>\d+)")


def _midi_device_profile(name: str) -> dict[str, object]:
    normalized = " ".join(name.casefold().split())
    if "tiny" in normalized and (
        "midiplus" in normalized or "tiny midi" in normalized or normalized == "tiny"
    ):
        return {
            "manufacturer": "MIDIPLUS",
            "model": "TINY",
            "key_count": 32,
        }
    return {}


def _alsa_card_names() -> dict[int, str]:
    cards_path = Path("/proc/asound/cards")
    try:
        lines = cards_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return {}

    names: dict[int, str] = {}
    for index, line in enumerate(lines):
        match = re.match(r"^\s*(\d+)\s+\[[^]]+\]:.*?\s-\s(.+)$", line)
        if match is None:
            continue
        card = int(match.group(1))
        name = match.group(2).strip()
        if index + 1 < len(lines):
            description = lines[index + 1].strip().split(" at ", 1)[0].strip()
            if description:
                name = description
        names[card] = name
    return names


def _query_raw_midi_devices() -> list[dict[str, object]]:
    card_names = _alsa_card_names()
    devices: list[tuple[int, int, Path]] = []
    for path in Path("/dev/snd").glob("midiC*D*"):
        match = RAW_MIDI_PATTERN.fullmatch(path.name)
        if match is not None:
            devices.append((int(match.group("card")), int(match.group("device")), path))

    result: list[dict[str, object]] = []
    for index, (card, device, path) in enumerate(sorted(devices)):
        port = f"{RAW_MIDI_PREFIX}{path}"
        name = card_names.get(card, f"MIDI card {card}, device {device}")
        result.append(
            {
                "id": port,
                "index": index,
                "name": name,
                "port": port,
                "backend": "raw",
                **_midi_device_profile(name),
            }
        )
    return result


class RawMidiInput:
    """Read a Linux raw MIDI device and recover from USB re-enumeration."""

    RECONNECT_INTERVAL_SECONDS = 0.25

    def __init__(
        self,
        path: Path,
        callback: Callable[[object], None],
        device_name: str | None = None,
    ) -> None:
        try:
            import mido
        except ImportError as exc:
            raise RuntimeError("Raw MIDI input requires mido.") from exc

        self.path = path
        self.device_name = device_name
        self.callback = callback
        self._parser_factory = mido.Parser
        self.parser = self._parser_factory()
        self.stop_event = threading.Event()
        self._fd_lock = threading.Lock()
        self.fd: int | None = None
        self.reconnect_count = 0
        self.last_error: str | None = None
        if not self._open_available(initial=True):
            raise RuntimeError(
                f"Unable to open raw MIDI input {path}: "
                f"{self.last_error or 'device is unavailable'}"
            )
        self.worker = threading.Thread(target=self._read_loop, daemon=True)
        self.worker.start()

    @property
    def connected(self) -> bool:
        with self._fd_lock:
            return self.fd is not None

    def _candidate_paths(self) -> list[Path]:
        candidates = [self.path]
        if self.device_name:
            for device in _query_raw_midi_devices():
                if str(device.get("name")) != self.device_name:
                    continue
                port = str(device.get("port", ""))
                if port.startswith(RAW_MIDI_PREFIX):
                    candidate = Path(port[len(RAW_MIDI_PREFIX) :])
                    if candidate not in candidates:
                        candidates.append(candidate)
        return candidates

    def _open_available(self, initial: bool = False) -> bool:
        errors: list[str] = []
        for candidate in self._candidate_paths():
            try:
                fd = os.open(candidate, os.O_RDONLY | os.O_NONBLOCK)
            except OSError as exc:
                errors.append(f"{candidate}: {exc}")
                continue
            with self._fd_lock:
                if self.stop_event.is_set():
                    os.close(fd)
                    return False
                self.fd = fd
                self.path = candidate
                self.last_error = None
                if not initial:
                    self.reconnect_count += 1
            self.parser = self._parser_factory()
            return True
        with self._fd_lock:
            self.last_error = "; ".join(errors) or "device is unavailable"
        return False

    def _current_fd(self) -> int | None:
        with self._fd_lock:
            return self.fd

    def _fd_matches_path(self, fd: int) -> bool:
        try:
            opened = os.fstat(fd)
            current = os.stat(self.path)
        except OSError:
            return False
        return (opened.st_dev, opened.st_ino) == (current.st_dev, current.st_ino)

    def _disconnect(self, fd: int, reason: str) -> None:
        with self._fd_lock:
            if self.fd != fd:
                return
            self.fd = None
            self.last_error = reason
        try:
            os.close(fd)
        except OSError:
            pass
        self.parser = self._parser_factory()

    def _read_loop(self) -> None:
        while not self.stop_event.is_set():
            fd = self._current_fd()
            if fd is None:
                if self._open_available():
                    continue
                self.stop_event.wait(self.RECONNECT_INTERVAL_SECONDS)
                continue
            try:
                if not self._fd_matches_path(fd):
                    self._disconnect(fd, "MIDI device path was replaced")
                    continue
                readable, _, _ = select.select([fd], [], [], 0.1)
                if not readable:
                    continue
                data = os.read(fd, 256)
                if not data:
                    self._disconnect(fd, "MIDI device disconnected")
                    continue
                self.parser.feed(data)
                for message in self.parser:
                    self.callback(message)
            except OSError as exc:
                if self.stop_event.is_set():
                    return
                if exc.errno in {
                    errno.EBADF,
                    errno.ENODEV,
                    errno.ENOENT,
                    errno.ENXIO,
                    errno.EIO,
                }:
                    self._disconnect(fd, str(exc))
                    continue
                self.stop_event.wait(0.05)

    def close(self) -> None:
        self.stop_event.set()
        fd = self._current_fd()
        if fd is not None:
            self._disconnect(fd, "MIDI input closed")
        if self.worker.ident is not None:
            self.worker.join(timeout=1.0)


def open_midi_input(
    port: str | None,
    callback: Callable[[object], None],
) -> object:
    if port is not None and port.startswith(RAW_MIDI_PREFIX):
        device_name = next(
            (
                str(device["name"])
                for device in _query_raw_midi_devices()
                if str(device.get("port")) == port
            ),
            None,
        )
        return RawMidiInput(
            Path(port[len(RAW_MIDI_PREFIX) :]),
            callback,
            device_name=device_name,
        )

    if sys.platform.startswith("linux") and not Path("/dev/snd/seq").exists():
        devices = _query_raw_midi_devices()
        if not devices:
            raise RuntimeError(
                "Physical MIDI input is unavailable: ALSA sequencer device "
                "/dev/snd/seq is missing and no raw MIDI device was found"
            )
        if port is not None:
            raise RuntimeError(f"Unknown raw MIDI input: {port}")
        return RawMidiInput(
            Path(str(devices[0]["port"])[len(RAW_MIDI_PREFIX) :]), callback
        )

    try:
        import mido
    except ImportError as exc:
        raise RuntimeError("Physical MIDI input requires mido and python-rtmidi.") from exc
    try:
        return mido.open_input(port, callback=callback)
    except Exception as exc:
        raise RuntimeError(f"Unable to open MIDI input {port or 'default'}: {exc}") from exc


def query_midi_devices() -> list[dict[str, object]]:
    try:
        import mido
    except ImportError as exc:
        raise RuntimeError("Install mido and python-rtmidi first.") from exc

    if sys.platform.startswith("linux") and not Path("/dev/snd/seq").exists():
        devices = _query_raw_midi_devices()
        if devices:
            return devices
        raise RuntimeError(
            "Physical MIDI input is unavailable: ALSA sequencer device "
            "/dev/snd/seq is missing and no raw MIDI device was found"
        )

    try:
        names = mido.get_input_names()
    except Exception as exc:
        raise RuntimeError(f"Unable to enumerate MIDI input devices: {exc}") from exc

    return [
        {
            "id": str(index),
            "index": index,
            "name": name,
            "port": name,
            "backend": "rtmidi",
            **_midi_device_profile(name),
        }
        for index, name in enumerate(names)
    ]


def list_midi_devices() -> None:
    for device in query_midi_devices():
        index = device["index"]
        name = device["name"]
        print(f"{index}: {name}")


def query_audio_devices() -> list[dict[str, object]]:
    try:
        import sounddevice as sd
    except ImportError as exc:
        raise RuntimeError("Install requirements.txt first.") from exc
    result: list[dict[str, object]] = []
    for index, device in enumerate(sd.query_devices()):
        if device["max_output_channels"] > 0:
            host_api = sd.query_hostapis(device["hostapi"])["name"]
            result.append(
                {
                    "id": str(index),
                    "index": index,
                    "name": str(device["name"]),
                    "host_api": str(host_api),
                    "max_output_channels": int(device["max_output_channels"]),
                    "default_sample_rate": int(device["default_samplerate"]),
                }
            )
    return result


def list_audio_devices() -> None:
    for device in query_audio_devices():
        print(
            f"{device['index']}: {device['name']} "
            f"(api={device['host_api']}, channels={device['max_output_channels']}, "
            f"default_rate={device['default_sample_rate']})"
        )


def parse_audio_device(value: str | None) -> str | int | None:
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return value


def run_live(
    model_path: Path,
    midi_port: str | None,
    sample_rate: int,
    prebuffer: int,
    max_voices: int,
    output_device: str | int | None,
    envelope: EnvelopeSettings = DEFAULT_ENVELOPE,
    output_latency_seconds: float = 0.08,
    output_gain_db: float = 0.0,
    backend: str = "auto",
    device_id: int = 0,
) -> None:
    engine = RealtimeSynthEngine(
        model_path,
        sample_rate,
        max_voices=max_voices,
        envelope=envelope,
        output_gain_db=output_gain_db,
        backend=backend,
        device_id=device_id,
    )
    player = LivePlayer(
        engine,
        prebuffer,
        output_device=output_device,
        output_latency_seconds=output_latency_seconds,
    )
    port = None
    try:
        port = open_midi_input(midi_port, engine.midi.handle_message)
        player.start()
        print("[LIVE] Running. Press Ctrl+C to stop.")
        try:
            while True:
                player.raise_worker_error()
                time.sleep(0.1)
        except KeyboardInterrupt:
            pass
    finally:
        player.stop()
        if port is not None:
            port.close()
        engine.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Real-time DDSP-VST Synth using ONNX Runtime or Ascend PyACL"
    )
    parser.add_argument("--model", type=Path, default=DEFAULT_MODEL)
    parser.add_argument(
        "--backend",
        choices=("auto", "onnx", "om"),
        default="auto",
        help="Inference backend; auto selects it from the model extension",
    )
    parser.add_argument(
        "--device-id",
        type=int,
        default=0,
        help="Ascend device ID used by the OM backend (default: 0)",
    )
    parser.add_argument("--demo", action="store_true", help="Render a demo note to WAV")
    parser.add_argument("--live", action="store_true", help="Open MIDI and audio devices")
    parser.add_argument("--midi-file", type=Path, help="Render a MIDI file to WAV")
    parser.add_argument(
        "--play-midi", type=Path, help="Play a MIDI file through the realtime audio output"
    )
    parser.add_argument("--list-midi", action="store_true")
    parser.add_argument("--list-audio", action="store_true")
    parser.add_argument("--midi-port")
    parser.add_argument(
        "--audio-device",
        help="Output device name or numeric index; defaults to the system output",
    )
    parser.add_argument(
        "--audio-latency-ms",
        type=float,
        default=80.0,
        help="Audio-device output buffer target in milliseconds",
    )
    parser.add_argument("--duration", type=float, default=2.0)
    parser.add_argument("--output", type=Path, default=ROOT_DIR / "violin_demo.wav")
    parser.add_argument("--sample-rate", type=int, default=48_000)
    parser.add_argument("--prebuffer", type=int, default=3)
    parser.add_argument(
        "--output-gain-db",
        type=float,
        default=0.0,
        help="Software output gain in dB before final clipping (default: 0)",
    )
    parser.add_argument(
        "--max-voices",
        type=int,
        default=1,
        help="Maximum simultaneous notes; the Google Synth default is monophonic",
    )
    parser.add_argument("--attack", type=float, default=DEFAULT_ENVELOPE.attack)
    parser.add_argument("--decay", type=float, default=DEFAULT_ENVELOPE.decay)
    parser.add_argument("--sustain", type=float, default=DEFAULT_ENVELOPE.sustain)
    parser.add_argument("--release", type=float, default=DEFAULT_ENVELOPE.release)
    parser.add_argument(
        "--tail",
        type=float,
        default=1.4,
        help="Extra render/playback time after the MIDI events, in seconds",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.list_midi:
        list_midi_devices()
        return 0
    if args.list_audio:
        list_audio_devices()
        return 0
    if not args.model.exists():
        raise FileNotFoundError(f"Model not found: {args.model}")
    selected_modes = (
        int(args.demo)
        + int(args.live)
        + int(args.midi_file is not None)
        + int(args.play_midi is not None)
    )
    if selected_modes != 1:
        raise ValueError(
            "Choose exactly one of --demo, --live, --midi-file, or --play-midi"
        )
    if args.sample_rate <= 0 or args.duration <= 0:
        raise ValueError("Sample rate and duration must be positive")
    if args.max_voices <= 0:
        raise ValueError("--max-voices must be positive")
    if args.device_id < 0:
        raise ValueError("--device-id must be non-negative")
    if args.attack <= 0 or args.decay < 0 or args.release <= 0:
        raise ValueError("--attack and --release must be positive; --decay cannot be negative")
    if not 0.0 <= args.sustain <= 1.0:
        raise ValueError("--sustain must be between 0 and 1")
    if args.tail < 0:
        raise ValueError("--tail cannot be negative")
    if args.audio_latency_ms <= 0:
        raise ValueError("--audio-latency-ms must be positive")
    if not math.isfinite(args.output_gain_db) or not -60.0 <= args.output_gain_db <= 6.0:
        raise ValueError("--output-gain-db must be finite and between -60 and 6")
    audio_device = parse_audio_device(args.audio_device)
    audio_latency_seconds = args.audio_latency_ms / 1000.0
    envelope = EnvelopeSettings(
        attack=args.attack,
        decay=args.decay,
        sustain=args.sustain,
        release=args.release,
    )
    if args.demo:
        render_demo(
            args.model,
            args.output,
            args.duration,
            args.sample_rate,
            envelope,
            args.output_gain_db,
            args.backend,
            args.device_id,
        )
    elif args.live:
        run_live(
            args.model,
            args.midi_port,
            args.sample_rate,
            args.prebuffer,
            args.max_voices,
            audio_device,
            envelope,
            audio_latency_seconds,
            args.output_gain_db,
            args.backend,
            args.device_id,
        )
    elif args.midi_file is not None:
        if not args.midi_file.exists():
            raise FileNotFoundError(f"MIDI file not found: {args.midi_file}")
        render_midi_file(
            args.model,
            args.midi_file,
            args.output,
            args.sample_rate,
            args.tail,
            args.max_voices,
            envelope,
            args.output_gain_db,
            args.backend,
            args.device_id,
        )
    else:
        if not args.play_midi.exists():
            raise FileNotFoundError(f"MIDI file not found: {args.play_midi}")
        play_midi_file(
            args.model,
            args.play_midi,
            args.sample_rate,
            args.prebuffer,
            args.max_voices,
            args.tail,
            audio_device,
            envelope,
            audio_latency_seconds,
            args.output_gain_db,
            args.backend,
            args.device_id,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
