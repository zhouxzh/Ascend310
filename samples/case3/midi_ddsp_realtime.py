#!/usr/bin/env python3
"""Real-time MIDI-file synthesis with the static MIDI-DDSP Ascend OMs.

The official MIDI-DDSP models are static sequence models. This program uses
the expression OM to prepare note controls, streams overlapping 64-frame
synthesis-parameter windows through the synthesis OM, and reproduces the
omitted DDSP oscillator, noise, and per-instrument Google reverb on the CPU.
It is intended for a monophonic MIDI file such as midi/ode-to-joy-violin.mid.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import queue
import shutil
import signal
import subprocess
import threading
import time
import wave

import numpy as np

from pyacl_midi_ddsp import (
    EXPRESSION_INPUTS,
    EXPRESSION_OUTPUTS,
    SYNTHESIS_INPUTS,
    SYNTHESIS_OUTPUTS,
    MidiDdspAclRunner,
)
from realtime_ddsp import LinearResampler
from midi_ddsp_webui.midi_analysis import (
    MidiAnalysis,
    MidiNote,
    MidiValidationError,
    analyze_midi,
)
from midi_ddsp_webui.model_bundle import load_runtime_bundle
from midi_ddsp_webui.stateful_midi_ddsp import StatefulMidiDdspInference


MODEL_SAMPLE_RATE = 16_000
MODEL_FRAME_RATE = 250
MODEL_FRAME_SIZE = MODEL_SAMPLE_RATE // MODEL_FRAME_RATE
EXPRESSION_LENGTH = 32
SYNTHESIS_LENGTH = 64
SYNTHESIS_HOP = 32
SYNTHESIS_BLOCK_SAMPLES = SYNTHESIS_HOP * MODEL_FRAME_SIZE
OFFICIAL_TAIL_SECONDS = 1.0
CONDITIONING_NAMES = (
    "volume",
    "vol_fluc",
    "vibrato",
    "brightness",
    "attack",
    "vol_peak_pos",
)


@dataclass(frozen=True)
class MidiToken:
    pitch: int
    length_frames: int


@dataclass(frozen=True)
class ParsedMidi:
    notes: list[MidiNote]
    source_track_count: int
    selected_track_index: int | None
    selected_track_name: str
    melody_extracted: bool
    max_polyphony: int = 1
    mode: str = "monophonic"


@dataclass
class FrameFeatures:
    conditioning: np.ndarray
    q_pitch: np.ndarray
    onsets: np.ndarray
    offsets: np.ndarray
    instrument_id: int

    @property
    def frames(self) -> int:
        return int(self.q_pitch.shape[0])


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _track_notes(midi, mido_module) -> list[tuple[int, str, list[MidiNote]]]:
    tempo_events: list[tuple[int, int]] = [(0, 500_000)]
    for track in midi.tracks:
        tick = 0
        for message in track:
            tick += int(message.time)
            if message.type == "set_tempo":
                tempo_events.append((tick, int(message.tempo)))
    tempo_events.sort(key=lambda item: item[0])
    normalized_tempos: list[tuple[int, int]] = []
    for tick, tempo in tempo_events:
        if normalized_tempos and normalized_tempos[-1][0] == tick:
            normalized_tempos[-1] = (tick, tempo)
        else:
            normalized_tempos.append((tick, tempo))

    def tick_to_seconds(target_tick: int) -> float:
        seconds = 0.0
        previous_tick = 0
        tempo = 500_000
        for tick, next_tempo in normalized_tempos:
            if tick > target_tick:
                break
            seconds += mido_module.tick2second(
                tick - previous_tick, midi.ticks_per_beat, tempo
            )
            previous_tick = tick
            tempo = next_tempo
        seconds += mido_module.tick2second(
            target_tick - previous_tick, midi.ticks_per_beat, tempo
        )
        return float(seconds)

    tracks: list[tuple[int, str, list[MidiNote]]] = []
    for track_index, track in enumerate(midi.tracks):
        tick = 0
        active: dict[tuple[int, int], list[tuple[int, int]]] = {}
        raw_notes: list[tuple[int, int, int, int]] = []
        for message in track:
            tick += int(message.time)
            if not hasattr(message, "channel") or int(message.channel) == 9:
                continue
            key = (int(message.channel), int(getattr(message, "note", -1)))
            if message.type == "note_on" and message.velocity > 0:
                active.setdefault(key, []).append((tick, int(message.velocity)))
            elif message.type in ("note_off", "note_on"):
                values = active.get(key)
                if values:
                    start_tick, velocity = values.pop(0)
                    raw_notes.append((start_tick, max(tick, start_tick + 1), key[1], velocity))
                    if not values:
                        active.pop(key, None)
        for (_channel, pitch), values in active.items():
            for start_tick, velocity in values:
                raw_notes.append((start_tick, max(tick, start_tick + 1), pitch, velocity))
        notes = [
            MidiNote(
                start=tick_to_seconds(start_tick),
                end=max(tick_to_seconds(end_tick), tick_to_seconds(start_tick) + 1e-3),
                pitch=pitch,
                velocity=velocity,
            )
            for start_tick, end_tick, pitch, velocity in raw_notes
        ]
        notes.sort(key=lambda note: (note.start, note.pitch, note.end))
        if notes:
            tracks.append((track_index, str(track.name or f"Track {track_index}"), notes))
    return tracks


def _is_monophonic(notes: list[MidiNote]) -> bool:
    previous_end = -1.0
    for note in notes:
        if note.start < previous_end - 1e-6:
            return False
        previous_end = max(previous_end, note.end)
    return True


def _highest_voice(notes: list[MidiNote]) -> list[MidiNote]:
    events: dict[float, list[tuple[bool, int, MidiNote]]] = {}
    for note_id, note in enumerate(notes):
        events.setdefault(note.start, []).append((True, note_id, note))
        events.setdefault(note.end, []).append((False, note_id, note))

    active: dict[int, MidiNote] = {}
    result: list[MidiNote] = []
    selected_id: int | None = None
    selected_note: MidiNote | None = None
    selected_start = 0.0
    for event_time in sorted(events):
        for is_start, note_id, _note in events[event_time]:
            if not is_start:
                active.pop(note_id, None)
        for is_start, note_id, note in events[event_time]:
            if is_start:
                active[note_id] = note
        next_id: int | None = None
        next_note: MidiNote | None = None
        if active:
            next_id, next_note = max(
                active.items(),
                key=lambda item: (
                    item[1].pitch,
                    item[1].start,
                    item[1].velocity,
                    item[0],
                ),
            )
        if next_id == selected_id:
            continue
        if selected_note is not None and event_time - selected_start >= 1e-3:
            result.append(
                MidiNote(
                    start=selected_start,
                    end=event_time,
                    pitch=selected_note.pitch,
                    velocity=selected_note.velocity,
                )
            )
        selected_id = next_id
        selected_note = next_note
        selected_start = event_time
    return result


def parse_midi_details(path: Path) -> ParsedMidi:
    analysis = analyze_midi(path)
    if not analysis.monophonic:
        raise MidiValidationError(
            "polyphonic_track",
            "MIDI-DDSP supports monophonic input; automatic highest-voice "
            "extraction has been removed because it discards harmony",
        )
    notes = sorted(
        (note for track in analysis.tracks for note in track.notes),
        key=lambda note: (note.start, note.pitch, note.end),
    )
    return ParsedMidi(
        notes=notes,
        source_track_count=len(analysis.tracks),
        selected_track_index=None,
        selected_track_name="",
        melody_extracted=False,
        max_polyphony=analysis.max_polyphony,
        mode=analysis.mode,
    )


def parse_midi(path: Path) -> list[MidiNote]:
    return parse_midi_details(path).notes


def build_tokens(
    notes: list[MidiNote], tail_frames: int = MODEL_FRAME_RATE
) -> list[MidiToken]:
    first_start = max(0, int(round(notes[0].start * MODEL_FRAME_RATE)))
    tokens: list[MidiToken] = [MidiToken(0, first_start)]
    previous_end = first_start
    for note in notes:
        start = int(round(note.start * MODEL_FRAME_RATE))
        end = max(start + 1, int(round(note.end * MODEL_FRAME_RATE)))
        if start > previous_end:
            tokens.append(MidiToken(0, start - previous_end))
        tokens.append(MidiToken(note.pitch, end - start))
        previous_end = end
    tokens.append(MidiToken(0, max(1, int(tail_frames))))
    return tokens


def expression_controls(
    runner: MidiDdspAclRunner,
    tokens: list[MidiToken],
    instrument_id: int,
) -> tuple[np.ndarray, int]:
    controls = np.zeros((len(tokens), 6), dtype=np.float32)
    inference_count = 0
    for start in range(0, len(tokens), EXPRESSION_LENGTH):
        chunk = tokens[start : start + EXPRESSION_LENGTH]
        pitch = np.zeros((1, EXPRESSION_LENGTH), dtype=np.int64)
        length = np.zeros((1, EXPRESSION_LENGTH, 1), dtype=np.float32)
        for index, token in enumerate(chunk):
            pitch[0, index] = token.pitch
            length[0, index, 0] = token.length_frames / MODEL_FRAME_RATE
        output = runner.infer(
            {
                "note_pitch": pitch,
                "note_length": length,
                "instrument_id": np.asarray([instrument_id], dtype=np.int64),
            }
        )
        count = len(chunk)
        controls[start : start + count] = np.clip(
            output["expression_controls"][0, :count], 0.0, 1.0
        )
        inference_count += 1
    return controls, inference_count


def build_frame_features(
    tokens: list[MidiToken], controls: np.ndarray, instrument_id: int
) -> FrameFeatures:
    total_frames = sum(token.length_frames for token in tokens)
    conditioning = np.zeros(
        (total_frames, len(CONDITIONING_NAMES)), dtype=np.float32
    )
    q_pitch = np.zeros((total_frames, 1), dtype=np.float32)
    onsets = np.zeros(total_frames, dtype=np.int64)
    offsets = np.zeros(total_frames, dtype=np.int64)
    cursor = 0
    for token, control in zip(tokens, controls):
        end = min(total_frames, cursor + token.length_frames)
        if end > cursor:
            # Official MIDI-DDSP writes through off + 1. The following token
            # overwrites that shared boundary, while the final slice clips at
            # total_frames.
            conditioning[cursor : min(total_frames, end + 1)] = control
            q_pitch[cursor : min(total_frames, end + 1), 0] = token.pitch
            onsets[cursor] = 1
            offsets[end - 1] = 1
        cursor = end
    return FrameFeatures(conditioning, q_pitch, onsets, offsets, instrument_id)


def exp_sigmoid(value: np.ndarray, *, bias: float = 0.0) -> np.ndarray:
    value = np.clip(np.asarray(value, dtype=np.float32) + bias, -30.0, 30.0)
    sigmoid = 1.0 / (1.0 + np.exp(-value))
    return (2.0 * sigmoid ** math.log(10.0) + 1e-7).astype(np.float32)


class StreamingFftReverb:
    """Uniform-partition convolution matching Google MIDI-DDSP ReverbModules."""

    def __init__(
        self,
        impulse_response: np.ndarray,
        block_size: int = SYNTHESIS_BLOCK_SAMPLES,
        *,
        sample_rate: int = MODEL_SAMPLE_RATE,
        instrument_id: int = 0,
        decay_start: int = 16_000,
        decay_exponent: float = 4.0,
    ) -> None:
        impulse_response = np.asarray(impulse_response, dtype=np.float32)
        if impulse_response.ndim != 1 or impulse_response.size < 2:
            raise ValueError("reverb impulse response must be a one-dimensional array")
        if block_size < 1:
            raise ValueError("reverb block size must be positive")
        self.block_size = int(block_size)
        self.sample_rate = int(sample_rate)
        self.instrument_id = int(instrument_id)
        self.decay_start = int(decay_start)
        self.decay_exponent = float(decay_exponent)
        self.ir_length = int(impulse_response.size)
        self.partition_count = math.ceil(self.ir_length / self.block_size)
        self.fft_size = self.block_size * 2
        padded = np.pad(
            impulse_response,
            (0, self.partition_count * self.block_size - self.ir_length),
        ).reshape(self.partition_count, self.block_size)
        self.ir_spectra = np.fft.rfft(padded, n=self.fft_size, axis=1)
        self.input_spectra = np.zeros_like(self.ir_spectra)
        self.overlap = np.zeros(self.block_size, dtype=np.float64)
        self.cursor = 0
        self.process_times_ms: list[float] = []

    @classmethod
    def from_asset(
        cls,
        path: Path,
        instrument_id: int,
        block_size: int = SYNTHESIS_BLOCK_SAMPLES,
    ) -> "StreamingFftReverb":
        if not path.is_file():
            raise FileNotFoundError(
                f"MIDI-DDSP reverb asset not found: {path}. "
                "Run tools/export_midi_ddsp_reverb.py locally and sync the output."
            )
        with np.load(path, allow_pickle=False) as data:
            impulse_responses = np.asarray(data["impulse_responses"], dtype=np.float32)
            sample_rate = int(data["sample_rate"])
            decay_start = int(data["decay_start"])
            decay_exponent = float(data["decay_exponent"])
            add_dry = bool(int(data["add_dry"]))
        if impulse_responses.ndim != 2:
            raise ValueError("reverb asset must contain [instrument, sample] IRs")
        if not 0 <= instrument_id < impulse_responses.shape[0]:
            raise ValueError(
                f"instrument id {instrument_id} is outside the reverb asset"
            )
        if sample_rate != MODEL_SAMPLE_RATE:
            raise ValueError(
                f"reverb sample rate {sample_rate} does not match {MODEL_SAMPLE_RATE}"
            )
        if not add_dry:
            raise ValueError("the runtime expects the original add_dry reverb contract")
        return cls(
            impulse_responses[instrument_id],
            block_size,
            sample_rate=sample_rate,
            instrument_id=instrument_id,
            decay_start=decay_start,
            decay_exponent=decay_exponent,
        )

    def process(self, dry: np.ndarray) -> np.ndarray:
        dry = np.asarray(dry, dtype=np.float32)
        if dry.shape != (self.block_size,):
            raise ValueError(
                f"reverb requires blocks of {self.block_size} samples, got {dry.shape}"
            )
        started = time.perf_counter()
        self.input_spectra[self.cursor] = np.fft.rfft(dry, n=self.fft_size)
        wet_spectrum = np.zeros(self.ir_spectra.shape[1], dtype=np.complex128)
        for partition in range(self.partition_count):
            source = (self.cursor - partition) % self.partition_count
            wet_spectrum += self.input_spectra[source] * self.ir_spectra[partition]
        convolved = np.fft.irfft(wet_spectrum, n=self.fft_size)
        wet = convolved[: self.block_size] + self.overlap
        self.overlap = convolved[self.block_size :].copy()
        self.cursor = (self.cursor + 1) % self.partition_count
        self.process_times_ms.append((time.perf_counter() - started) * 1000.0)
        # ddsp.effects.Reverb masks IR[0], convolves, then adds the dry signal.
        return (dry.astype(np.float64) + wet).astype(np.float32)


def _linear_frame_resample(values: np.ndarray, hop_size: int) -> np.ndarray:
    """Match DDSP's endpoint-holding linear frame upsampling."""
    values = np.asarray(values, dtype=np.float32)
    if values.ndim == 1:
        values = values[:, None]
    if values.shape[0] < 2:
        return np.repeat(values, hop_size, axis=0)
    intervals = values.shape[0] - 1
    positions = np.arange(intervals * hop_size, dtype=np.float64) / hop_size
    lower = np.floor(positions).astype(np.int64)
    fraction = (positions - lower)[:, None]
    return (
        values[lower].astype(np.float64) * (1.0 - fraction)
        + values[lower + 1].astype(np.float64) * fraction
    ).astype(np.float32)


def _window_frame_resample(values: np.ndarray, hop_size: int) -> np.ndarray:
    """Upsample controls with DDSP's overlapping periodic Hann windows."""
    values = np.asarray(values, dtype=np.float32)
    if values.ndim == 1:
        values = values[:, None]
    if values.shape[0] < 2:
        return np.repeat(values, hop_size, axis=0)
    frame_count = values.shape[0]
    window_length = hop_size * 2
    index = np.arange(window_length, dtype=np.float64)
    window = 0.5 - 0.5 * np.cos(2.0 * math.pi * index / window_length)
    output_length = (frame_count - 1) * hop_size + window_length
    output_channels = np.zeros(
        (output_length, values.shape[1]), dtype=np.float64
    )
    for frame_index, frame in enumerate(values.astype(np.float64)):
        start = frame_index * hop_size
        output_channels[start : start + window_length] += window[:, None] * frame
    return output_channels[hop_size:-hop_size].astype(np.float32)


class MidiDdspHarmonicSynthesizer:
    """Streaming harmonic bank with the original DDSP envelope semantics."""

    def __init__(self, sample_rate: int = MODEL_SAMPLE_RATE) -> None:
        self.sample_rate = float(sample_rate)
        self.phase = 0.0
        self.harmonic_numbers = np.arange(1, 61, dtype=np.float32)

    def render(
        self,
        f0_hz: np.ndarray,
        amplitudes: np.ndarray,
        harmonic_distribution: np.ndarray,
    ) -> np.ndarray:
        f0_hz = np.asarray(f0_hz, dtype=np.float32).reshape(-1)
        amplitudes = np.asarray(amplitudes, dtype=np.float32).reshape(-1)
        distribution = np.asarray(harmonic_distribution, dtype=np.float32).copy()
        if not (len(f0_hz) == len(amplitudes) == distribution.shape[0]):
            raise ValueError("harmonic controls must have the same frame count")
        if len(f0_hz) < 2:
            return np.zeros(0, dtype=np.float32)

        frequencies = f0_hz[:, None] * self.harmonic_numbers[None, :]
        distribution[frequencies >= self.sample_rate / 2.0] = 0.0
        totals = distribution.sum(axis=1, keepdims=True)
        distribution = np.divide(
            distribution,
            totals,
            out=np.zeros_like(distribution),
            where=totals > 1e-7,
        )
        harmonic_amplitudes = amplitudes[:, None] * distribution
        base_frequency = _linear_frame_resample(f0_hz, MODEL_FRAME_SIZE)[:, 0]
        amplitude_envelopes = _window_frame_resample(
            harmonic_amplitudes, MODEL_FRAME_SIZE
        )
        phase_increments = base_frequency * (2.0 * math.pi / self.sample_rate)
        phases = np.cumsum(phase_increments, dtype=np.float64) + self.phase
        if phases.size:
            self.phase = float(phases[-1] % (2.0 * math.pi))
        signal = np.sin(
            phases[:, None] * self.harmonic_numbers[None, :]
        ) * amplitude_envelopes
        return np.sum(signal, axis=1, dtype=np.float64).astype(np.float32)


class MidiDdspFilteredNoise:
    """DDSP frequency-sampling noise filter for one synthesis window."""

    def __init__(self, hop_size: int = MODEL_FRAME_SIZE, seed: int = 20260722) -> None:
        self.hop_size = int(hop_size)
        self.rng = np.random.default_rng(seed)
        self.ir_size = 128
        index = np.arange(self.ir_size, dtype=np.float64)
        periodic_hann = 0.5 - 0.5 * np.cos(2.0 * math.pi * index / self.ir_size)
        self.zero_phase_window = np.fft.fftshift(periodic_hann)
        self.delay_compensation = (self.ir_size - 1) // 2 - 1

    def render_window(
        self, magnitudes: np.ndarray, white_noise: np.ndarray | None = None
    ) -> np.ndarray:
        magnitudes = np.asarray(magnitudes, dtype=np.float32)
        if magnitudes.ndim != 2 or magnitudes.shape[1] != 65:
            raise ValueError("noise magnitudes must have shape [frames, 65]")
        frame_count = magnitudes.shape[0]
        sample_count = frame_count * self.hop_size
        if white_noise is None:
            white = self.rng.uniform(-1.0, 1.0, sample_count).astype(np.float32)
        else:
            white = np.asarray(white_noise, dtype=np.float32).reshape(-1)
            if white.size != sample_count:
                raise ValueError(
                    f"white noise has {white.size} samples, expected {sample_count}"
                )
        audio_frames = white.reshape(frame_count, self.hop_size)

        impulse = np.fft.irfft(magnitudes, n=self.ir_size, axis=1)
        impulse *= self.zero_phase_window[None, :]
        impulse = np.fft.fftshift(impulse, axes=1)
        fft_size = 256
        convolved = np.fft.irfft(
            np.fft.rfft(audio_frames, n=fft_size, axis=1)
            * np.fft.rfft(impulse, n=fft_size, axis=1),
            n=fft_size,
            axis=1,
        )
        overlap = np.zeros(
            (frame_count - 1) * self.hop_size + fft_size,
            dtype=np.float64,
        )
        for frame_index in range(frame_count):
            start = frame_index * self.hop_size
            overlap[start : start + fft_size] += convolved[frame_index]
        start = self.delay_compensation
        return overlap[start : start + sample_count].astype(np.float32)


class MidiDdspRenderer:
    def __init__(
        self,
        runner: MidiDdspAclRunner,
        features: FrameFeatures,
        reverb: StreamingFftReverb | None = None,
    ) -> None:
        self.runner = runner
        self.features = features
        self.reverb = reverb
        self.harmonic = MidiDdspHarmonicSynthesizer(MODEL_SAMPLE_RATE)
        self.noise = MidiDdspFilteredNoise(MODEL_FRAME_SIZE, seed=20260722)
        self.render_times_ms: list[float] = []
        self.dry_peak = 0.0
        self.reverberated_peak = 0.0
        self.dry_sum_squares = 0.0
        self.reverberated_sum_squares = 0.0
        self.metric_sample_count = 0

    @property
    def block_count(self) -> int:
        return math.ceil(self.features.frames / SYNTHESIS_HOP)

    def _window(self, start: int) -> dict[str, np.ndarray]:
        source_start = max(0, start - (SYNTHESIS_HOP - 1))
        emit_offset = start - source_start
        source_end = source_start + SYNTHESIS_LENGTH
        def copy_frame_values(values: np.ndarray, dtype: np.dtype) -> np.ndarray:
            result = np.zeros((1, SYNTHESIS_LENGTH, *values.shape[1:]), dtype=dtype)
            source = values[source_start:source_end]
            destination = max(0, -source_start)
            result[0, destination : destination + len(source)] = source
            return result

        conditioning = {
            name: copy_frame_values(
                self.features.conditioning[:, index : index + 1], np.float32
            )
            for index, name in enumerate(CONDITIONING_NAMES)
        }
        q_pitch = copy_frame_values(self.features.q_pitch, np.float32)
        onsets = copy_frame_values(self.features.onsets[:, None], np.int64)[:, :, 0]
        offsets = copy_frame_values(self.features.offsets[:, None], np.int64)[:, :, 0]
        feeds = {
            **conditioning,
            "q_pitch": q_pitch,
            "onsets": onsets,
            "offsets": offsets,
            "instrument_id": np.asarray([self.features.instrument_id], dtype=np.int64),
        }
        return {
            "feeds": feeds,
            "emit_offset": emit_offset,
            "source_start": source_start,
        }

    def render_block(self, block_index: int) -> np.ndarray:
        start = block_index * SYNTHESIS_HOP
        valid = min(SYNTHESIS_HOP, max(0, self.features.frames - start))
        if valid <= 0:
            return np.zeros(0, dtype=np.float32)
        started = time.perf_counter()
        window = self._window(start)
        outputs = self.runner.infer(window["feeds"])
        offset = window["emit_offset"]
        frame_end = min(SYNTHESIS_LENGTH, offset + valid + 1)
        f0 = outputs["f0_hz"][0, offset:frame_end, 0]
        amplitudes = exp_sigmoid(
            outputs["amplitudes"][0, offset:frame_end, 0]
        )
        harmonics = exp_sigmoid(
            outputs["harmonic_distribution"][0, offset:frame_end]
        )
        if len(f0) < valid + 1:
            f0 = np.pad(f0, (0, valid + 1 - len(f0)), mode="edge")
            amplitudes = np.pad(
                amplitudes, (0, valid + 1 - len(amplitudes)), mode="edge"
            )
            harmonics = np.pad(
                harmonics,
                ((0, valid + 1 - len(harmonics)), (0, 0)),
                mode="edge",
            )
        harmonic = self.harmonic.render(f0, amplitudes, harmonics)

        all_noise_magnitudes = exp_sigmoid(
            outputs["noise_magnitudes"][0], bias=-5.0
        )
        noise_window = self.noise.render_window(all_noise_magnitudes)
        noise_start = offset * MODEL_FRAME_SIZE
        noise_end = noise_start + valid * MODEL_FRAME_SIZE
        noise = noise_window[noise_start:noise_end]
        audio = harmonic[: valid * MODEL_FRAME_SIZE] + noise
        self.render_times_ms.append((time.perf_counter() - started) * 1000.0)
        return audio.astype(np.float32)

    def render_output_block(self, block_index: int) -> np.ndarray:
        block = self.render_block(block_index)
        if block.size < SYNTHESIS_BLOCK_SAMPLES:
            block = np.pad(block, (0, SYNTHESIS_BLOCK_SAMPLES - block.size))
        if block.size:
            self.dry_peak = max(self.dry_peak, float(np.max(np.abs(block))))
            self.dry_sum_squares += float(np.sum(block.astype(np.float64) ** 2))
        if self.reverb is not None:
            block = self.reverb.process(block)
        if block.size:
            self.reverberated_peak = max(
                self.reverberated_peak, float(np.max(np.abs(block)))
            )
            self.reverberated_sum_squares += float(
                np.sum(block.astype(np.float64) ** 2)
            )
            self.metric_sample_count += int(block.size)
        return block.astype(np.float32)


class RealtimeAudioPlayer:
    def __init__(
        self,
        renderer: MidiDdspRenderer,
        output_sample_rate: int,
        prebuffer: int,
        output_device: str | int | None,
        latency_ms: float,
        gain_db: float,
        capture: bool,
        pause_event: threading.Event | None = None,
    ) -> None:
        self.renderer = renderer
        self.output_sample_rate = output_sample_rate
        self.prebuffer = max(1, prebuffer)
        self.output_device = output_device
        self.latency_ms = latency_ms
        self.gain = 10.0 ** (gain_db / 20.0)
        self.capture = capture
        self.pause_event = pause_event or threading.Event()
        self.blocks: queue.Queue[np.ndarray] = queue.Queue(maxsize=self.prebuffer)
        self.stop_event = threading.Event()
        self.space_available = threading.Event()
        self.space_available.set()
        self.lock = threading.Lock()
        self.worker_error: BaseException | None = None
        self.rendered = 0
        self.played = 0
        self.underruns = 0
        self.overruns = 0
        self.captured: list[np.ndarray] = []
        self.preclip_peak = 0.0
        self.clipped_samples = 0
        self.worker = threading.Thread(target=self._render_loop, daemon=True)
        self.stream = None
        self.channels = 0

    def _render_loop(self) -> None:
        try:
            while self.rendered < self.renderer.block_count and not self.stop_event.is_set():
                if self.pause_event.is_set():
                    self.space_available.wait(timeout=0.05)
                    continue
                if self.blocks.full():
                    self.space_available.clear()
                    self.space_available.wait(timeout=0.1)
                    continue
                block = self.renderer.render_output_block(self.rendered)
                block = block * self.gain
                resampler = getattr(self, "resampler", None)
                if resampler is None:
                    resampler = LinearResampler(MODEL_SAMPLE_RATE, self.output_sample_rate)
                    self.resampler = resampler
                preclip = resampler.process(block)
                with self.lock:
                    if preclip.size:
                        self.preclip_peak = max(
                            self.preclip_peak, float(np.max(np.abs(preclip)))
                        )
                        self.clipped_samples += int(np.count_nonzero(np.abs(preclip) > 1.0))
                output = np.clip(preclip, -1.0, 1.0).astype(np.float32)
                self.blocks.put(output)
                with self.lock:
                    self.rendered += 1
        except BaseException as exc:
            with self.lock:
                self.worker_error = exc
            self.stop_event.set()
            self.space_available.set()

    def _audio_callback(self, outdata, frames, _time_info, status) -> None:
        if self.pause_event.is_set():
            outdata.fill(0.0)
            return
        underflow = bool(getattr(status, "output_underflow", False))
        try:
            block = self.blocks.get_nowait()
        except queue.Empty:
            outdata.fill(0.0)
            with self.lock:
                playback_complete = (
                    self.played >= self.renderer.block_count
                    and self.rendered >= self.renderer.block_count
                )
            underflow = underflow or not playback_complete
        else:
            self.space_available.set()
            if frames != len(block):
                outdata.fill(0.0)
                underflow = True
            else:
                outdata[:] = block[:, np.newaxis]
                if self.capture:
                    self.captured.append(block.copy())
                with self.lock:
                    self.played += 1
        if underflow:
            with self.lock:
                self.underruns += 1

    def _raise_worker_error(self) -> None:
        if self.worker_error is not None:
            raise RuntimeError("MIDI-DDSP realtime render worker failed") from self.worker_error

    def start(self) -> None:
        try:
            import sounddevice as sd
        except ImportError as exc:
            raise RuntimeError("sounddevice is required for realtime audio") from exc
        device = sd.query_devices(self.output_device, "output")
        max_channels = int(device["max_output_channels"])
        if max_channels < 1:
            raise RuntimeError(f"Audio device has no output channels: {device['name']}")
        self.channels = 2 if max_channels >= 2 else 1
        sd.check_output_settings(
            device=self.output_device,
            channels=self.channels,
            samplerate=self.output_sample_rate,
            dtype="float32",
        )
        self.resampler = LinearResampler(MODEL_SAMPLE_RATE, self.output_sample_rate)
        self.worker.start()
        required = min(self.prebuffer, self.renderer.block_count)
        deadline = time.monotonic() + max(10.0, required * 2.0)
        while self.blocks.qsize() < required and time.monotonic() < deadline:
            self._raise_worker_error()
            time.sleep(0.005)
        self._raise_worker_error()
        if self.blocks.qsize() < required:
            raise RuntimeError("Could not prepare the MIDI-DDSP audio prebuffer")
        blocksize = (
            SYNTHESIS_HOP
            * MODEL_FRAME_SIZE
            * self.output_sample_rate
            // MODEL_SAMPLE_RATE
        )
        self.stream = sd.OutputStream(
            samplerate=self.output_sample_rate,
            blocksize=blocksize,
            channels=self.channels,
            dtype="float32",
            device=self.output_device,
            latency=self.latency_ms / 1000.0,
            callback=self._audio_callback,
        )
        self.stream.start()
        print(
            f"[AUDIO] device={device['name']}, channels={self.channels}, "
            f"sample_rate={self.output_sample_rate}, block={blocksize}, "
            f"latency={self.latency_ms:.0f} ms"
        )

    def set_paused(self, paused: bool) -> None:
        if paused:
            self.pause_event.set()
        else:
            self.pause_event.clear()
            self.space_available.set()

    def snapshot(self) -> dict[str, object]:
        with self.lock:
            return {
                "rendered": self.rendered,
                "played": self.played,
                "total": self.renderer.block_count,
                "underruns": self.underruns,
                "overruns": self.overruns,
                "paused": self.pause_event.is_set(),
            }

    def wait(self, progress_callback=None) -> None:
        allowed_seconds = self.renderer.block_count * 0.004 * SYNTHESIS_HOP + 30.0
        active_seconds = 0.0
        previous = time.monotonic()
        last_progress = 0.0
        while True:
            self._raise_worker_error()
            with self.lock:
                if self.played >= self.renderer.block_count:
                    return
            now = time.monotonic()
            if not self.pause_event.is_set():
                active_seconds += now - previous
            previous = now
            if progress_callback is not None and now - last_progress >= 0.25:
                progress_callback(self.snapshot())
                last_progress = now
            if active_seconds > allowed_seconds:
                raise RuntimeError("MIDI-DDSP realtime playback timed out")
            time.sleep(0.01)

    def stop(self) -> None:
        self.stop_event.set()
        self.pause_event.clear()
        self.space_available.set()
        if self.stream is not None:
            self.stream.stop()
            self.stream.close()
            self.stream = None
        if self.worker.ident is not None:
            self.worker.join(timeout=10.0)
        print(
            f"[LIVE] rendered={self.rendered}, played={self.played}, "
            f"underruns={self.underruns}, overruns={self.overruns}"
        )


class PulseAudioPlayer:
    """Stream MIDI-DDSP blocks to one explicitly selected PulseAudio sink."""

    def __init__(
        self,
        renderer: MidiDdspRenderer,
        output_sample_rate: int,
        sink_name: str,
        device_name: str,
        latency_ms: float,
        gain_db: float,
        capture: bool,
        pause_event: threading.Event | None = None,
    ) -> None:
        self.renderer = renderer
        self.output_sample_rate = int(output_sample_rate)
        self.sink_name = sink_name
        self.device_name = device_name
        self.latency_ms = float(latency_ms)
        self.gain = 10.0 ** (gain_db / 20.0)
        self.capture = capture
        self.pause_event = pause_event or threading.Event()
        self.stop_event = threading.Event()
        self.completed_event = threading.Event()
        self.lock = threading.Lock()
        self.worker_error: BaseException | None = None
        self.rendered = 0
        self.played = 0
        self.underruns = 0
        self.overruns = 0
        self.captured: list[np.ndarray] = []
        self.preclip_peak = 0.0
        self.clipped_samples = 0
        self.process: subprocess.Popen[bytes] | None = None
        self.worker = threading.Thread(target=self._render_loop, daemon=True)

    def start(self) -> None:
        if shutil.which("paplay") is None:
            raise RuntimeError("paplay is required for a selected PulseAudio output")
        self.process = subprocess.Popen(
            [
                "paplay",
                "--playback",
                f"--device={self.sink_name}",
                "--client-name=MIDI-DDSP Studio",
                "--stream-name=MIDI-DDSP Player",
                "--raw",
                "--format=float32le",
                f"--rate={self.output_sample_rate}",
                "--channels=2",
                f"--latency-msec={round(self.latency_ms)}",
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        self.worker.start()
        time.sleep(0.02)
        self._raise_worker_error()
        if self.process.poll() is not None and not self.completed_event.is_set():
            raise RuntimeError("paplay failed to open the selected output")
        print(
            f"[AUDIO] device={self.device_name}, channels=2, "
            f"sample_rate={self.output_sample_rate}, backend=PulseAudio"
        )

    def _render_loop(self) -> None:
        process = self.process
        try:
            if process is None or process.stdin is None:
                raise RuntimeError("PulseAudio output process is not ready")
            resampler = LinearResampler(MODEL_SAMPLE_RATE, self.output_sample_rate)
            while self.rendered < self.renderer.block_count and not self.stop_event.is_set():
                if self.pause_event.is_set():
                    time.sleep(0.02)
                    continue
                block = self.renderer.render_output_block(self.rendered) * self.gain
                preclip = resampler.process(block)
                if preclip.size:
                    self.preclip_peak = max(
                        self.preclip_peak, float(np.max(np.abs(preclip)))
                    )
                    self.clipped_samples += int(
                        np.count_nonzero(np.abs(preclip) > 1.0)
                    )
                output = np.clip(preclip, -1.0, 1.0).astype(np.float32)
                stereo = np.repeat(output[:, None], 2, axis=1)
                process.stdin.write(stereo.astype("<f4", copy=False).tobytes())
                if self.capture:
                    self.captured.append(output.copy())
                with self.lock:
                    self.rendered += 1
                    self.played += 1
            process.stdin.close()
            return_code = process.wait(timeout=10.0)
            if return_code != 0 and not self.stop_event.is_set():
                error = (
                    process.stderr.read().decode("utf-8", errors="replace").strip()
                    if process.stderr is not None
                    else ""
                )
                raise RuntimeError(error or f"paplay exited with code {return_code}")
        except (BrokenPipeError, OSError) as exc:
            if not self.stop_event.is_set():
                self.worker_error = exc
        except BaseException as exc:
            self.worker_error = exc
        finally:
            self.completed_event.set()

    def _raise_worker_error(self) -> None:
        if self.worker_error is not None:
            raise RuntimeError("MIDI-DDSP PulseAudio worker failed") from self.worker_error

    def set_paused(self, paused: bool) -> None:
        if paused:
            self.pause_event.set()
        else:
            self.pause_event.clear()

    def snapshot(self) -> dict[str, object]:
        with self.lock:
            return {
                "rendered": self.rendered,
                "played": self.played,
                "total": self.renderer.block_count,
                "underruns": self.underruns,
                "overruns": self.overruns,
                "paused": self.pause_event.is_set(),
            }

    def wait(self, progress_callback=None) -> None:
        deadline = time.monotonic() + self.renderer.block_count * 0.128 + 30.0
        while not self.completed_event.wait(timeout=0.25):
            self._raise_worker_error()
            if progress_callback is not None:
                progress_callback(self.snapshot())
            if time.monotonic() > deadline:
                raise RuntimeError("MIDI-DDSP PulseAudio playback timed out")
        self._raise_worker_error()

    def stop(self) -> None:
        self.stop_event.set()
        self.pause_event.clear()
        process = self.process
        if process is not None and process.poll() is None:
            process.terminate()
        if self.worker.ident is not None:
            self.worker.join(timeout=5.0)
        self.process = None


def write_wav(path: Path, samples: np.ndarray, sample_rate: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pcm = np.clip(samples, -1.0, 1.0)
    pcm = (pcm * 32767.0).astype("<i2").tobytes()
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(pcm)


def read_wav(path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path), "rb") as handle:
        if handle.getnchannels() != 1 or handle.getsampwidth() != 2:
            raise ValueError(f"Unsupported cached WAV format: {path}")
        sample_rate = handle.getframerate()
        data = np.frombuffer(handle.readframes(handle.getnframes()), dtype="<i2")
    return (data.astype(np.float32) / 32767.0), sample_rate


def parse_audio_device(value: str | None) -> str | int | None:
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return value


def emit_web_event(enabled: bool, event: str, **payload: object) -> None:
    if not enabled:
        return
    message = {"event": event, "timestamp": time.time(), **payload}
    print("WEBUI_EVENT " + json.dumps(message, ensure_ascii=False), flush=True)


def _render_stateful_audio(
    parameters,
    reverb: StreamingFftReverb | None,
    seed: int,
    white_noise: np.ndarray | None = None,
) -> tuple[np.ndarray, dict[str, float]]:
    f0 = np.asarray(parameters.f0_hz, dtype=np.float32).reshape(-1)
    amplitudes = exp_sigmoid(parameters.amplitudes[:, 0])
    harmonics = exp_sigmoid(parameters.harmonic_distribution)
    noise_magnitudes = exp_sigmoid(parameters.noise_magnitudes, bias=-5.0)
    if f0.size == 0:
        return np.zeros(0, dtype=np.float32), {
            "dry_peak": 0.0,
            "dry_rms": 0.0,
            "reverberated_peak": 0.0,
            "reverberated_rms": 0.0,
        }

    harmonic_synth = MidiDdspHarmonicSynthesizer(MODEL_SAMPLE_RATE)
    harmonic = harmonic_synth.render(
        np.append(f0, f0[-1]),
        np.append(amplitudes, amplitudes[-1]),
        np.concatenate([harmonics, harmonics[-1:]], axis=0),
    )
    noise_synth = MidiDdspFilteredNoise(MODEL_FRAME_SIZE, seed=seed)
    noise = noise_synth.render_window(noise_magnitudes, white_noise=white_noise)
    sample_count = f0.size * MODEL_FRAME_SIZE
    dry = np.zeros(sample_count, dtype=np.float32)
    dry[: min(sample_count, harmonic.size)] += harmonic[:sample_count]
    dry[: min(sample_count, noise.size)] += noise[:sample_count]

    output = dry.copy()
    if reverb is not None:
        blocks = []
        for start in range(0, sample_count, reverb.block_size):
            valid = min(reverb.block_size, sample_count - start)
            block = np.zeros(reverb.block_size, dtype=np.float32)
            block[:valid] = dry[start : start + valid]
            blocks.append(reverb.process(block)[:valid])
        output = np.concatenate(blocks) if blocks else output
    metrics = {
        "dry_peak": float(np.max(np.abs(dry))) if dry.size else 0.0,
        "dry_rms": float(np.sqrt(np.mean(dry.astype(np.float64) ** 2))) if dry.size else 0.0,
        "reverberated_peak": float(np.max(np.abs(output))) if output.size else 0.0,
        "reverberated_rms": (
            float(np.sqrt(np.mean(output.astype(np.float64) ** 2)))
            if output.size
            else 0.0
        ),
    }
    return output.astype(np.float32), metrics


def _stateful_stage(component: str) -> str:
    if "expression" in component:
        return "expression"
    if "f0" in component or "context" in component or "precondition" in component:
        return "f0"
    if "timbre" in component:
        return "timbre"
    return component


def _stateful_timing_report(inference: StatefulMidiDdspInference) -> dict[str, object]:
    expression = [
        value
        for name, values in inference.timings.items()
        if "expression" in name
        for value in values
    ]
    synthesis = [
        value
        for name, values in inference.timings.items()
        if "synthesis" in name
        for value in values
    ]
    synthesis_array = np.asarray(synthesis, dtype=np.float64)
    return {
        "expression_inference_count": len(expression),
        "synthesis_block_count": len(synthesis),
        "synthesis_render_mean_ms": (
            float(synthesis_array.mean()) if synthesis_array.size else 0.0
        ),
        "synthesis_render_median_ms": (
            float(np.median(synthesis_array)) if synthesis_array.size else 0.0
        ),
        "synthesis_render_p95_ms": (
            float(np.quantile(synthesis_array, 0.95))
            if synthesis_array.size
            else 0.0
        ),
        "synthesis_render_max_ms": (
            float(synthesis_array.max()) if synthesis_array.size else 0.0
        ),
    }


def _boundary_continuity(samples: np.ndarray, block_size: int) -> dict[str, float]:
    values = np.asarray(samples, dtype=np.float32).reshape(-1)
    boundaries = np.arange(block_size, len(values), block_size, dtype=np.int64)
    if not boundaries.size:
        return {"count": 0, "mean_abs_jump": 0.0, "max_abs_jump": 0.0}
    jumps = np.abs(values[boundaries] - values[boundaries - 1]).astype(np.float64)
    return {
        "count": int(jumps.size),
        "mean_abs_jump": float(jumps.mean()),
        "max_abs_jump": float(jumps.max()),
    }


def _stem_seed(seed: int, track_index: int) -> int:
    return int(
        np.random.SeedSequence([int(seed), int(track_index)]).generate_state(
            1, dtype=np.uint32
        )[0]
    )


def _play_precomputed_audio(
    samples: np.ndarray,
    args: argparse.Namespace,
    pause_event: threading.Event,
) -> None:
    chunk_size = max(256, round(args.sample_rate * 0.05))
    total = math.ceil(len(samples) / chunk_size)
    if args.pulse_sink:
        if shutil.which("paplay") is None:
            raise RuntimeError("paplay is required for a selected PulseAudio output")
        process = subprocess.Popen(
            [
                "paplay",
                "--playback",
                f"--device={args.pulse_sink}",
                "--client-name=MIDI-DDSP Studio",
                "--stream-name=MIDI-DDSP Player",
                "--raw",
                "--format=float32le",
                f"--rate={args.sample_rate}",
                "--channels=2",
                f"--latency-msec={round(args.audio_latency_ms)}",
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        try:
            if process.stdin is None:
                raise RuntimeError("PulseAudio playback pipe is unavailable")
            for index, start in enumerate(range(0, len(samples), chunk_size)):
                while pause_event.is_set():
                    time.sleep(0.02)
                chunk = samples[start : start + chunk_size]
                stereo = np.repeat(chunk[:, None], 2, axis=1).astype("<f4", copy=False)
                process.stdin.write(stereo.tobytes())
                emit_web_event(
                    args.json_events,
                    "progress",
                    stage="playback",
                    rendered=total,
                    played=index + 1,
                    total=total,
                    paused=False,
                )
            process.stdin.close()
            return_code = process.wait(timeout=max(10.0, len(samples) / args.sample_rate + 10.0))
            if return_code != 0:
                error = (
                    process.stderr.read().decode("utf-8", errors="replace").strip()
                    if process.stderr is not None
                    else ""
                )
                raise RuntimeError(error or f"paplay exited with code {return_code}")
        finally:
            if process.poll() is None:
                process.terminate()
        return

    try:
        import sounddevice as sd
    except ImportError as exc:
        raise RuntimeError("sounddevice is required for audio playback") from exc
    device = parse_audio_device(args.audio_device)
    with sd.OutputStream(
        samplerate=args.sample_rate,
        channels=1,
        dtype="float32",
        device=device,
        latency=args.audio_latency_ms / 1000.0,
    ) as stream:
        for index, start in enumerate(range(0, len(samples), chunk_size)):
            while pause_event.is_set():
                time.sleep(0.02)
            chunk = samples[start : start + chunk_size]
            stream.write(chunk[:, None])
            emit_web_event(
                args.json_events,
                "progress",
                stage="playback",
                rendered=total,
                played=index + 1,
                total=total,
                paused=False,
            )


def _run_stateful(
    args: argparse.Namespace,
    parsed_midi: ParsedMidi,
    tokens: list[MidiToken],
    reverb: StreamingFftReverb | None,
    pause_event: threading.Event,
) -> int:
    bundle = load_runtime_bundle(args.model_bundle)
    cache_payload = {
        "midi_sha256": sha256_file(args.midi),
        "bundle_sha256": sha256_file(bundle.manifest_path),
        "instrument_id": args.instrument_id,
        "seed": args.seed,
        "tail_seconds": args.tail_seconds,
        "sample_rate": args.sample_rate,
        "output_gain_db": args.output_gain_db,
        "reverb_sha256": (
            sha256_file(args.reverb_ir) if reverb is not None else None
        ),
    }
    cache_key = hashlib.sha256(
        json.dumps(cache_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    cache_wav = args.cache_dir / f"{cache_key}.wav" if args.cache_dir else None
    cache_report = args.cache_dir / f"{cache_key}.json" if args.cache_dir else None
    if (
        cache_wav is not None
        and cache_report is not None
        and cache_wav.is_file()
        and cache_report.is_file()
    ):
        audio, sample_rate = read_wav(cache_wav)
        if sample_rate != args.sample_rate:
            raise ValueError("Cached MIDI-DDSP sample rate mismatch")
        if args.output is not None:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(cache_wav, args.output)
        report = json.loads(cache_report.read_text(encoding="utf-8"))
        report["cache_hit"] = True
        report["cache_key"] = cache_key
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        emit_web_event(
            args.json_events,
            "prepared",
            notes=report.get("notes", 0),
            frames=report.get("frames", 0),
            blocks=1,
            duration_seconds=report.get("duration_seconds", 0.0),
            source_track_count=report.get("source_track_count", 1),
            max_polyphony=report.get("max_polyphony", 1),
            midi_ddsp_mode=report.get("midi_ddsp_mode", "monophonic"),
            seed=args.seed,
            stage="cache",
            reverb_enabled=report.get("reverb_enabled", False),
        )
        if not args.render_only:
            _play_precomputed_audio(audio, args, pause_event)
        emit_web_event(args.json_events, "completed", report=str(args.report.resolve()))
        print(f"[CACHE] hit {cache_key}")
        return 0

    def progress(stage: str, completed: int, total: int) -> None:
        emit_web_event(
            args.json_events,
            "progress",
            stage=_stateful_stage(stage),
            component=stage,
            rendered=completed,
            played=completed,
            total=total,
            paused=False,
        )

    started = time.perf_counter()
    inference = StatefulMidiDdspInference(
        bundle,
        device_id=args.device_id,
        seed=args.seed,
        progress=progress,
    )
    parameters = inference.run(tokens, build_frame_features, args.instrument_id)
    features = build_frame_features(tokens, parameters.controls, args.instrument_id)
    emit_web_event(
        args.json_events,
        "prepared",
        notes=len(parsed_midi.notes),
        frames=features.frames,
        blocks=math.ceil(features.frames / bundle.synthesis_block),
        duration_seconds=features.frames / MODEL_FRAME_RATE,
        source_track_count=parsed_midi.source_track_count,
        selected_track_index=None,
        selected_track_name="",
        melody_extracted=False,
        max_polyphony=parsed_midi.max_polyphony,
        midi_ddsp_mode=parsed_midi.mode,
        seed=args.seed,
        stage="dsp",
        reverb_enabled=reverb is not None,
    )
    model_audio, signal_metrics = _render_stateful_audio(parameters, reverb, args.seed)
    resampler = LinearResampler(MODEL_SAMPLE_RATE, args.sample_rate)
    preclip = resampler.process(
        model_audio * 10.0 ** (args.output_gain_db / 20.0)
    )
    preclip_peak = float(np.max(np.abs(preclip))) if preclip.size else 0.0
    clipped_samples = int(np.count_nonzero(np.abs(preclip) > 1.0))
    audio = np.clip(preclip, -1.0, 1.0).astype(np.float32)
    if args.output is not None:
        write_wav(args.output, audio, args.sample_rate)
        print(f"[WAV] {args.output} samples={audio.size}")
    if not args.render_only:
        _play_precomputed_audio(audio, args, pause_event)

    reverb_ms = np.asarray(
        reverb.process_times_ms if reverb is not None else [], dtype=np.float64
    )
    report = {
        "midi": str(args.midi.resolve()),
        "midi_sha256": sha256_file(args.midi),
        "notes": len(parsed_midi.notes),
        "tokens": len(tokens),
        "frames": features.frames,
        "duration_seconds": features.frames / MODEL_FRAME_RATE,
        "source_track_count": parsed_midi.source_track_count,
        "max_polyphony": parsed_midi.max_polyphony,
        "midi_ddsp_mode": parsed_midi.mode,
        "instrument_id": args.instrument_id,
        "device_id": args.device_id,
        "seed": args.seed,
        "model_bundle_id": bundle.id,
        "model_bundle": str(bundle.manifest_path),
        "model_bundle_sha256": sha256_file(bundle.manifest_path),
        "source_commit": bundle.source_commit,
        "component_sha256": {
            name: component.sha256 for name, component in bundle.components.items()
        },
        "model_state_continuity": {
            "expression_context": True,
            "expression_decoder": True,
            "synthesis_context": True,
            "f0_decoder": True,
            "timbre_halo": bundle.timbre_halo,
        },
        **parameters.metrics,
        **_stateful_timing_report(inference),
        "inference_and_dsp_wall_seconds": time.perf_counter() - started,
        "audio_sample_rate": args.sample_rate,
        "output_gain_db": args.output_gain_db,
        "official_tail_seconds": OFFICIAL_TAIL_SECONDS,
        "additional_tail_seconds": args.tail_seconds,
        "reverb_enabled": reverb is not None,
        "reverb_ir": str(args.reverb_ir.resolve()) if reverb is not None else None,
        "reverb_ir_sha256": sha256_file(args.reverb_ir) if reverb is not None else None,
        "reverb_instrument_id": reverb.instrument_id if reverb is not None else None,
        "reverb_length_samples": reverb.ir_length if reverb is not None else 0,
        "reverb_process_mean_ms": float(reverb_ms.mean()) if reverb_ms.size else 0.0,
        "reverb_process_p95_ms": (
            float(np.quantile(reverb_ms, 0.95)) if reverb_ms.size else 0.0
        ),
        **signal_metrics,
        "preclip_peak": preclip_peak,
        "clipped_samples": clipped_samples,
        "render_only": args.render_only,
        "audio_samples_captured": int(audio.size),
        "audio_peak": float(np.max(np.abs(audio))) if audio.size else 0.0,
        "audio_rms": float(np.sqrt(np.mean(audio * audio))) if audio.size else 0.0,
        "underruns": 0,
        "overruns": 0,
        "cache_hit": False,
        "cache_key": cache_key,
        "boundary_continuity": _boundary_continuity(
            audio, bundle.synthesis_block * MODEL_FRAME_SIZE
        ),
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    if cache_wav is not None and cache_report is not None and args.output is not None:
        args.cache_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(args.output, cache_wav)
        shutil.copy2(args.report, cache_report)
    print(f"[REPORT] {args.report}")
    emit_web_event(args.json_events, "completed", report=str(args.report.resolve()))
    return 0


def _run_stateful_multitrack(
    args: argparse.Namespace,
    analysis: MidiAnalysis,
    pause_event: threading.Event,
) -> int:
    bundle = load_runtime_bundle(args.model_bundle)
    cache_payload = {
        "mode": "multitrack",
        "midi_sha256": sha256_file(args.midi),
        "bundle_sha256": sha256_file(bundle.manifest_path),
        "seed": args.seed,
        "tail_seconds": args.tail_seconds,
        "sample_rate": args.sample_rate,
        "output_gain_db": args.output_gain_db,
        "reverb_sha256": (
            None if args.disable_reverb else sha256_file(args.reverb_ir)
        ),
        "tracks": [
            {"index": track.index, "instrument_id": track.instrument_id}
            for track in analysis.tracks
        ],
    }
    cache_key = hashlib.sha256(
        json.dumps(cache_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    cache_wav = args.cache_dir / f"{cache_key}.wav" if args.cache_dir else None
    cache_report = args.cache_dir / f"{cache_key}.json" if args.cache_dir else None
    if (
        cache_wav is not None
        and cache_report is not None
        and cache_wav.is_file()
        and cache_report.is_file()
    ):
        audio, sample_rate = read_wav(cache_wav)
        if sample_rate != args.sample_rate:
            raise ValueError("Cached MIDI-DDSP sample rate mismatch")
        report = json.loads(cache_report.read_text(encoding="utf-8"))
        report["cache_hit"] = True
        report["cache_key"] = cache_key
        if args.output is not None:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(cache_wav, args.output)
            for stem in report.get("stems", []):
                track_index = int(stem["track_index"])
                source = args.cache_dir / f"{cache_key}.stem-{track_index:02d}.wav"
                if source.is_file():
                    shutil.copy2(
                        source,
                        args.output.parent / f"stem-track-{track_index:02d}.wav",
                    )
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        emit_web_event(
            args.json_events,
            "prepared",
            notes=analysis.note_count,
            frames=report.get("frames", 0),
            blocks=1,
            duration_seconds=report.get("duration_seconds", 0.0),
            source_track_count=len(analysis.tracks),
            max_polyphony=analysis.max_polyphony,
            midi_ddsp_mode="multitrack",
            seed=args.seed,
            stage="cache",
            reverb_enabled=not args.disable_reverb,
        )
        if not args.render_only:
            _play_precomputed_audio(audio, args, pause_event)
        emit_web_event(args.json_events, "completed", report=str(args.report.resolve()))
        print(f"[CACHE] hit {cache_key}")
        return 0

    started = time.perf_counter()
    gain = 10.0 ** (args.output_gain_db / 20.0)
    stem_audio: list[np.ndarray] = []
    stem_reports: list[dict[str, object]] = []
    all_expression_timings: list[float] = []
    all_synthesis_timings: list[float] = []
    total_frames = 0
    for stem_number, track in enumerate(analysis.tracks, start=1):
        if track.instrument_id is None:
            raise MidiValidationError(
                "unsupported_program",
                f"Track {track.index} cannot be mapped to a MIDI-DDSP instrument",
            )
        stem_seed = _stem_seed(args.seed, track.index)
        tokens = build_tokens(
            list(track.notes),
            round(MODEL_FRAME_RATE * (OFFICIAL_TAIL_SECONDS + args.tail_seconds)),
        )

        def progress(stage: str, completed: int, total: int) -> None:
            emit_web_event(
                args.json_events,
                "progress",
                stage=_stateful_stage(stage),
                component=stage,
                stem=stem_number,
                stem_count=len(analysis.tracks),
                rendered=completed,
                played=completed,
                total=total,
                paused=False,
            )

        inference = StatefulMidiDdspInference(
            bundle,
            device_id=args.device_id,
            seed=stem_seed,
            progress=progress,
        )
        parameters = inference.run(tokens, build_frame_features, track.instrument_id)
        features = build_frame_features(tokens, parameters.controls, track.instrument_id)
        total_frames = max(total_frames, features.frames)
        reverb = None
        if not args.disable_reverb:
            reverb = StreamingFftReverb.from_asset(args.reverb_ir, track.instrument_id)
        dsp_started = time.perf_counter()
        model_audio, signal_metrics = _render_stateful_audio(
            parameters, reverb, stem_seed
        )
        dsp_ms = (time.perf_counter() - dsp_started) * 1000.0
        resampled = LinearResampler(MODEL_SAMPLE_RATE, args.sample_rate).process(
            model_audio * gain
        )
        stem_audio.append(resampled.astype(np.float32))
        stem_preclip_peak = float(np.max(np.abs(resampled))) if resampled.size else 0.0
        stem_clipped = int(np.count_nonzero(np.abs(resampled) > 1.0))
        if args.output is not None:
            write_wav(
                args.output.parent / f"stem-track-{track.index:02d}.wav",
                resampled,
                args.sample_rate,
            )
        for name, values in inference.timings.items():
            destination = (
                all_expression_timings if "expression" in name else all_synthesis_timings
            )
            destination.extend(values)
        reverb_ms = np.asarray(
            reverb.process_times_ms if reverb is not None else [], dtype=np.float64
        )
        stem_reports.append(
            {
                "track_index": track.index,
                "track_name": track.name,
                "note_count": len(track.notes),
                "instrument_id": track.instrument_id,
                "programs": list(track.programs),
                "seed": stem_seed,
                "frames": features.frames,
                "duration_seconds": features.frames / MODEL_FRAME_RATE,
                "tensor_sha256": parameters.metrics["tensor_sha256"],
                "component_timings_ms": parameters.metrics["component_timings_ms"],
                "dsp_ms": dsp_ms,
                "reverb_process_mean_ms": (
                    float(reverb_ms.mean()) if reverb_ms.size else 0.0
                ),
                **signal_metrics,
                "preclip_peak": stem_preclip_peak,
                "clipped_samples": stem_clipped,
                "boundary_continuity": _boundary_continuity(
                    resampled,
                    bundle.synthesis_block
                    * MODEL_FRAME_SIZE
                    * args.sample_rate
                    // MODEL_SAMPLE_RATE,
                ),
                "artifact": f"stem-track-{track.index:02d}.wav",
            }
        )

    max_samples = max((len(stem) for stem in stem_audio), default=0)
    mixed = np.zeros(max_samples, dtype=np.float64)
    for stem in stem_audio:
        mixed[: len(stem)] += stem.astype(np.float64)
    preclip_peak = float(np.max(np.abs(mixed))) if mixed.size else 0.0
    clipped_samples = int(np.count_nonzero(np.abs(mixed) > 1.0))
    audio = np.clip(mixed, -1.0, 1.0).astype(np.float32)
    if args.output is not None:
        write_wav(args.output, audio, args.sample_rate)
        print(f"[WAV] {args.output} samples={audio.size}, stems={len(stem_audio)}")
    if not args.render_only:
        _play_precomputed_audio(audio, args, pause_event)

    synthesis_values = np.asarray(all_synthesis_timings, dtype=np.float64)
    report = {
        "midi": str(args.midi.resolve()),
        "midi_sha256": sha256_file(args.midi),
        "notes": analysis.note_count,
        "tokens": sum(len(build_tokens(list(track.notes), 0)) for track in analysis.tracks),
        "frames": total_frames,
        "duration_seconds": audio.size / args.sample_rate,
        "source_track_count": len(analysis.tracks),
        "stem_count": len(stem_reports),
        "stems": stem_reports,
        "max_polyphony": analysis.max_polyphony,
        "midi_ddsp_mode": "multitrack",
        "device_id": args.device_id,
        "seed": args.seed,
        "model_bundle_id": bundle.id,
        "model_bundle": str(bundle.manifest_path),
        "model_bundle_sha256": sha256_file(bundle.manifest_path),
        "source_commit": bundle.source_commit,
        "architecture": "stateful-v2",
        "component_sha256": {
            name: component.sha256 for name, component in bundle.components.items()
        },
        "model_state_continuity": {
            "expression_context": True,
            "expression_decoder": True,
            "synthesis_context": True,
            "f0_decoder": True,
            "timbre_halo": bundle.timbre_halo,
        },
        "expression_inference_count": len(all_expression_timings),
        "synthesis_block_count": len(all_synthesis_timings),
        "synthesis_render_mean_ms": (
            float(synthesis_values.mean()) if synthesis_values.size else 0.0
        ),
        "synthesis_render_median_ms": (
            float(np.median(synthesis_values)) if synthesis_values.size else 0.0
        ),
        "synthesis_render_p95_ms": (
            float(np.quantile(synthesis_values, 0.95))
            if synthesis_values.size
            else 0.0
        ),
        "synthesis_render_max_ms": (
            float(synthesis_values.max()) if synthesis_values.size else 0.0
        ),
        "inference_and_dsp_wall_seconds": time.perf_counter() - started,
        "audio_sample_rate": args.sample_rate,
        "output_gain_db": args.output_gain_db,
        "official_tail_seconds": OFFICIAL_TAIL_SECONDS,
        "additional_tail_seconds": args.tail_seconds,
        "reverb_enabled": not args.disable_reverb,
        "reverb_ir": str(args.reverb_ir.resolve()) if not args.disable_reverb else None,
        "reverb_ir_sha256": (
            sha256_file(args.reverb_ir) if not args.disable_reverb else None
        ),
        "preclip_peak": preclip_peak,
        "clipped_samples": clipped_samples,
        "audio_samples_captured": int(audio.size),
        "audio_peak": float(np.max(np.abs(audio))) if audio.size else 0.0,
        "audio_rms": float(np.sqrt(np.mean(audio * audio))) if audio.size else 0.0,
        "underruns": 0,
        "overruns": 0,
        "render_only": args.render_only,
        "cache_hit": False,
        "cache_key": cache_key,
        "boundary_continuity": _boundary_continuity(
            audio,
            bundle.synthesis_block
            * MODEL_FRAME_SIZE
            * args.sample_rate
            // MODEL_SAMPLE_RATE,
        ),
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    if cache_wav is not None and cache_report is not None and args.output is not None:
        args.cache_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(args.output, cache_wav)
        shutil.copy2(args.report, cache_report)
        for stem in stem_reports:
            track_index = int(stem["track_index"])
            shutil.copy2(
                args.output.parent / f"stem-track-{track_index:02d}.wav",
                args.cache_dir / f"{cache_key}.stem-{track_index:02d}.wav",
            )
    print(f"[REPORT] {args.report}")
    emit_web_event(args.json_events, "completed", report=str(args.report.resolve()))
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    root = Path(__file__).resolve().parent
    parser.add_argument("--midi", type=Path, required=True)
    parser.add_argument(
        "--model-bundle",
        type=Path,
        help="Runtime manifest for a stateful-v2 MIDI-DDSP model bundle",
    )
    parser.add_argument(
        "--expression-om",
        type=Path,
        default=root / "models/om/midi_ddsp_expression_notes32_mixed_float16.om",
    )
    parser.add_argument(
        "--synthesis-om",
        type=Path,
        default=root / "models/om/midi_ddsp_synthesis_params_frames64_mixed_float16.om",
    )
    parser.add_argument("--instrument-id", type=int, default=0)
    parser.add_argument("--seed", type=int, default=20260724)
    parser.add_argument("--cache-dir", type=Path)
    parser.add_argument("--device-id", type=int, default=0)
    parser.add_argument("--audio-device")
    parser.add_argument("--pulse-sink")
    parser.add_argument("--pulse-device-name", default="PulseAudio")
    parser.add_argument("--sample-rate", type=int, default=48_000)
    parser.add_argument("--prebuffer", type=int, default=2)
    parser.add_argument("--audio-latency-ms", type=float, default=80.0)
    parser.add_argument("--output-gain-db", type=float, default=0.0)
    parser.add_argument(
        "--tail-seconds",
        type=float,
        default=2.0,
        help="Additional silence after the original one-second MIDI-DDSP tail",
    )
    parser.add_argument(
        "--reverb-ir",
        type=Path,
        default=root / "models/om/midi_ddsp_reverb_ir.npz",
    )
    parser.add_argument("--disable-reverb", action="store_true")
    parser.add_argument("--render-only", action="store_true")
    parser.add_argument(
        "--web-control",
        action="store_true",
        help="Enable SIGUSR1 pause and SIGUSR2 resume controls on Linux",
    )
    parser.add_argument(
        "--json-events",
        action="store_true",
        help="Emit machine-readable WEBUI_EVENT lines",
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--report",
        type=Path,
        default=root / "reports/midi_ddsp_realtime.json",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    pause_event = threading.Event()
    if args.web_control and hasattr(signal, "SIGUSR1"):
        signal.signal(signal.SIGUSR1, lambda *_args: pause_event.set())
        signal.signal(signal.SIGUSR2, lambda *_args: pause_event.clear())
    emit_web_event(
        args.json_events,
        "starting",
        pid=os.getpid(),
        mode="render" if args.render_only else "play",
    )
    if not args.midi.is_file():
        raise FileNotFoundError(args.midi)
    if args.instrument_id < 0 or args.instrument_id >= 13:
        raise ValueError("--instrument-id must be in [0, 12]")
    midi_analysis = analyze_midi(args.midi)
    if args.model_bundle is not None and midi_analysis.mode == "multitrack":
        return _run_stateful_multitrack(args, midi_analysis, pause_event)
    parsed_midi = parse_midi_details(args.midi)
    notes = parsed_midi.notes
    if parsed_midi.melody_extracted:
        track_label = (
            f"track {parsed_midi.selected_track_index}: {parsed_midi.selected_track_name}"
            if parsed_midi.selected_track_index is not None
            else parsed_midi.selected_track_name
        )
        print(f"[MIDI] extracted monophonic melody using {track_label}")
    tokens = build_tokens(
        notes,
        round(MODEL_FRAME_RATE * (OFFICIAL_TAIL_SECONDS + args.tail_seconds)),
    )
    reverb = None
    if not args.disable_reverb:
        reverb = StreamingFftReverb.from_asset(args.reverb_ir, args.instrument_id)
        print(
            f"[REVERB] Google MIDI-DDSP IR instrument={args.instrument_id}, "
            f"samples={reverb.ir_length}, partitions={reverb.partition_count}"
        )
    if args.model_bundle is not None:
        return _run_stateful(args, parsed_midi, tokens, reverb, pause_event)
    expression_runner: MidiDdspAclRunner | None = None
    synthesis_runner: MidiDdspAclRunner | None = None
    player: RealtimeAudioPlayer | PulseAudioPlayer | None = None
    capture: list[np.ndarray] = []
    preclip_peak = 0.0
    clipped_samples = 0
    try:
        expression_runner = MidiDdspAclRunner(
            args.expression_om,
            EXPRESSION_INPUTS,
            EXPRESSION_OUTPUTS,
            args.device_id,
        )
        synthesis_runner = MidiDdspAclRunner(
            args.synthesis_om,
            SYNTHESIS_INPUTS,
            SYNTHESIS_OUTPUTS,
            args.device_id,
        )
        expression_started = time.perf_counter()
        controls, expression_count = expression_controls(
            expression_runner, tokens, args.instrument_id
        )
        expression_elapsed = time.perf_counter() - expression_started
        features = build_frame_features(tokens, controls, args.instrument_id)
        renderer = MidiDdspRenderer(synthesis_runner, features, reverb=reverb)
        renderer.noise = MidiDdspFilteredNoise(MODEL_FRAME_SIZE, seed=args.seed)
        emit_web_event(
            args.json_events,
            "prepared",
            notes=len(notes),
            frames=features.frames,
            blocks=renderer.block_count,
            duration_seconds=features.frames / MODEL_FRAME_RATE,
            source_track_count=parsed_midi.source_track_count,
            selected_track_index=parsed_midi.selected_track_index,
            selected_track_name=parsed_midi.selected_track_name,
            melody_extracted=parsed_midi.melody_extracted,
            max_polyphony=parsed_midi.max_polyphony,
            midi_ddsp_mode=parsed_midi.mode,
            seed=args.seed,
            reverb_enabled=reverb is not None,
        )
        realtime_started = time.perf_counter()
        if args.render_only:
            resampler = LinearResampler(MODEL_SAMPLE_RATE, args.sample_rate)
            for index in range(renderer.block_count):
                block = renderer.render_output_block(index) * 10.0 ** (
                    args.output_gain_db / 20.0
                )
                preclip = resampler.process(block)
                if preclip.size:
                    preclip_peak = max(preclip_peak, float(np.max(np.abs(preclip))))
                    clipped_samples += int(np.count_nonzero(np.abs(preclip) > 1.0))
                capture.append(np.clip(preclip, -1.0, 1.0))
                if index == renderer.block_count - 1 or index % max(
                    1, renderer.block_count // 20
                ) == 0:
                    emit_web_event(
                        args.json_events,
                        "progress",
                        rendered=index + 1,
                        played=index + 1,
                        total=renderer.block_count,
                        paused=False,
                    )
            print(f"[RENDER] blocks={renderer.block_count}, frames={features.frames}")
        else:
            if args.pulse_sink:
                player = PulseAudioPlayer(
                    renderer,
                    args.sample_rate,
                    args.pulse_sink,
                    args.pulse_device_name,
                    args.audio_latency_ms,
                    args.output_gain_db,
                    capture=args.output is not None,
                    pause_event=pause_event,
                )
            else:
                player = RealtimeAudioPlayer(
                    renderer,
                    args.sample_rate,
                    args.prebuffer,
                    parse_audio_device(args.audio_device),
                    args.audio_latency_ms,
                    args.output_gain_db,
                    capture=args.output is not None,
                    pause_event=pause_event,
                )
            player.start()
            print(
                f"[PLAY] {args.midi} ({features.frames / MODEL_FRAME_RATE:.2f}s, "
                f"MIDI-DDSP OMs, instrument_id={args.instrument_id})"
            )
            try:
                player.wait(
                    lambda snapshot: emit_web_event(
                        args.json_events, "progress", **snapshot
                    )
                )
            except KeyboardInterrupt:
                print("[PLAY] Interrupted.")
            finally:
                player.stop()
            capture = player.captured
            preclip_peak = player.preclip_peak
            clipped_samples = player.clipped_samples
        realtime_elapsed = time.perf_counter() - realtime_started
    finally:
        if synthesis_runner is not None:
            synthesis_runner.close(suppress_errors=True)
        if expression_runner is not None:
            expression_runner.close(suppress_errors=True)

    audio = np.concatenate(capture) if capture else np.zeros(0, dtype=np.float32)
    if args.output is not None and audio.size:
        write_wav(args.output, audio, args.sample_rate)
        print(f"[WAV] {args.output} samples={audio.size}")
    render_ms = np.asarray(renderer.render_times_ms, dtype=np.float64)
    reverb_ms = np.asarray(
        reverb.process_times_ms if reverb is not None else [], dtype=np.float64
    )
    report = {
        "midi": str(args.midi.resolve()),
        "notes": len(notes),
        "tokens": len(tokens),
        "frames": features.frames,
        "duration_seconds": features.frames / MODEL_FRAME_RATE,
        "source_track_count": parsed_midi.source_track_count,
        "selected_track_index": parsed_midi.selected_track_index,
        "selected_track_name": parsed_midi.selected_track_name,
        "melody_extracted": parsed_midi.melody_extracted,
        "max_polyphony": parsed_midi.max_polyphony,
        "midi_ddsp_mode": parsed_midi.mode,
        "instrument_id": args.instrument_id,
        "seed": args.seed,
        "architecture": "legacy-static-v1",
        "device_id": args.device_id,
        "expression_om": str(args.expression_om.resolve()),
        "expression_om_sha256": sha256_file(args.expression_om),
        "synthesis_om": str(args.synthesis_om.resolve()),
        "synthesis_om_sha256": sha256_file(args.synthesis_om),
        "expression_inference_count": expression_count,
        "expression_elapsed_seconds": expression_elapsed,
        "synthesis_block_count": renderer.block_count,
        "synthesis_render_mean_ms": float(render_ms.mean()) if render_ms.size else 0.0,
        "synthesis_render_median_ms": float(np.median(render_ms)) if render_ms.size else 0.0,
        "synthesis_render_p95_ms": float(np.quantile(render_ms, 0.95)) if render_ms.size else 0.0,
        "synthesis_render_max_ms": float(render_ms.max()) if render_ms.size else 0.0,
        "realtime_wall_seconds": realtime_elapsed,
        "audio_sample_rate": args.sample_rate,
        "output_gain_db": args.output_gain_db,
        "official_tail_seconds": OFFICIAL_TAIL_SECONDS,
        "additional_tail_seconds": args.tail_seconds,
        "reverb_enabled": reverb is not None,
        "reverb_ir": str(args.reverb_ir.resolve()) if reverb is not None else None,
        "reverb_ir_sha256": sha256_file(args.reverb_ir) if reverb is not None else None,
        "reverb_instrument_id": reverb.instrument_id if reverb is not None else None,
        "reverb_length_samples": reverb.ir_length if reverb is not None else 0,
        "reverb_decay_start": reverb.decay_start if reverb is not None else 0,
        "reverb_decay_exponent": reverb.decay_exponent if reverb is not None else 0.0,
        "reverb_process_mean_ms": float(reverb_ms.mean()) if reverb_ms.size else 0.0,
        "reverb_process_p95_ms": (
            float(np.quantile(reverb_ms, 0.95)) if reverb_ms.size else 0.0
        ),
        "dry_peak": renderer.dry_peak,
        "dry_rms": (
            math.sqrt(renderer.dry_sum_squares / renderer.metric_sample_count)
            if renderer.metric_sample_count
            else 0.0
        ),
        "reverberated_peak": renderer.reverberated_peak,
        "reverberated_rms": (
            math.sqrt(
                renderer.reverberated_sum_squares / renderer.metric_sample_count
            )
            if renderer.metric_sample_count
            else 0.0
        ),
        "preclip_peak": preclip_peak,
        "clipped_samples": clipped_samples,
        "render_only": args.render_only,
        "audio_samples_captured": int(audio.size),
        "audio_peak": float(np.max(np.abs(audio))) if audio.size else 0.0,
        "audio_rms": float(np.sqrt(np.mean(audio * audio))) if audio.size else 0.0,
        "underruns": player.underruns if player is not None else 0,
        "overruns": player.overruns if player is not None else 0,
        "played_blocks": player.played if player is not None else renderer.block_count,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"[REPORT] {args.report}")
    emit_web_event(args.json_events, "completed", report=str(args.report.resolve()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
