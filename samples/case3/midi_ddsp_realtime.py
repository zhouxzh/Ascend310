#!/usr/bin/env python3
"""Real-time MIDI-file synthesis with the static MIDI-DDSP Ascend OMs.

The official MIDI-DDSP models are monophonic sequence models. This program
partitions polyphonic MIDI tracks into monophonic voices, renders every voice
with the stateful Ascend OMs, and reproduces the omitted DDSP oscillator,
noise, per-instrument Google reverb, and stem mixing on the CPU.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import pickle
import queue
import shutil
import signal
import subprocess
import sys
import tempfile
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
    MidiVoice,
    MidiValidationError,
    VOICE_SEPARATION_INFO,
    analyze_midi,
    analyze_midi_voices,
    split_midi_voices,
)
from midi_ddsp_webui.model_bundle import load_runtime_bundle
from midi_ddsp_webui.stateful_midi_ddsp import (
    BatchedStatefulMidiDdspInference,
    StatefulMidiDdspInference,
)


MODEL_SAMPLE_RATE = 16_000
MODEL_FRAME_RATE = 250
MODEL_FRAME_SIZE = MODEL_SAMPLE_RATE // MODEL_FRAME_RATE
EXPRESSION_LENGTH = 32
SYNTHESIS_LENGTH = 64
SYNTHESIS_HOP = 32
SYNTHESIS_BLOCK_SAMPLES = SYNTHESIS_HOP * MODEL_FRAME_SIZE
OFFICIAL_TAIL_SECONDS = 1.0
MIX_TARGET_PEAK = 0.95
BATCH_WORKER_EVENT = "MIDI_DDSP_BATCH_EVENT "
CONDITIONING_NAMES = (
    "volume",
    "vol_fluc",
    "vibrato",
    "brightness",
    "attack",
    "vol_peak_pos",
)

RENDER_STAGE_RANGES = {
    "preparing": (0.00, 0.01),
    "loading_models": (0.01, 0.04),
    "expression": (0.04, 0.10),
    "pitch_context": (0.10, 0.65),
    "timbre": (0.65, 0.72),
    "dsp_reverb": (0.72, 0.93),
    "mixing": (0.93, 0.96),
    "writing_cache": (0.96, 1.00),
}


class RenderProgress:
    """Rate-limited render progress with an independent heartbeat."""

    def __init__(self, enabled: bool, playback_expected: bool) -> None:
        self.enabled = bool(enabled)
        self.playback_expected = bool(playback_expected)
        self.started = time.monotonic()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._stage = "preparing"
        self._stage_progress = 0.0
        self._overall_progress = 0.0
        self._completed = 0
        self._total = 1
        self._batch_index: int | None = None
        self._batch_count: int | None = None
        self._component: str | None = None
        self._activity: str | None = None
        self._last_progress_emit = 0.0
        self._heartbeat = threading.Thread(
            target=self._heartbeat_loop,
            name="midi-ddsp-heartbeat",
            daemon=True,
        )
        if self.enabled:
            self._heartbeat.start()

    def __enter__(self) -> "RenderProgress":
        self.update("preparing", 0.0, force=True)
        return self

    def __exit__(self, *_exc_info: object) -> None:
        self.close()

    def _render_overall(self, stage: str, stage_progress: float) -> float:
        start, end = RENDER_STAGE_RANGES.get(stage, (0.0, 1.0))
        render_progress = start + (end - start) * stage_progress
        return render_progress * (0.9 if self.playback_expected else 1.0)

    def _payload_locked(self) -> dict[str, object]:
        elapsed = max(0.0, time.monotonic() - self.started)
        overall = self._overall_progress
        eta = elapsed * (1.0 - overall) / overall if overall >= 0.01 else None
        return {
            "stage": self._stage,
            "stage_progress": self._stage_progress,
            "overall_progress": overall,
            "completed": self._completed,
            "total": self._total,
            "voice_batch_index": self._batch_index,
            "voice_batch_count": self._batch_count,
            "component": self._component,
            "activity": self._activity,
            "elapsed_seconds": elapsed,
            "eta_seconds": eta,
            "heartbeat_at": time.time(),
            "paused": False,
        }

    def update(
        self,
        stage: str,
        stage_progress: float,
        *,
        completed: int = 0,
        total: int = 1,
        batch_index: int | None = None,
        batch_count: int | None = None,
        component: str | None = None,
        activity: str | None = None,
        render_overall: float | None = None,
        force: bool = False,
    ) -> None:
        stage_progress = min(1.0, max(0.0, float(stage_progress)))
        with self._lock:
            self._stage = stage
            self._stage_progress = stage_progress
            self._completed = max(0, int(completed))
            self._total = max(1, int(total))
            self._batch_index = batch_index
            self._batch_count = batch_count
            self._component = component
            self._activity = activity
            if stage == "playback":
                overall = 0.9 + 0.1 * stage_progress
            elif render_overall is not None:
                overall = min(1.0, max(0.0, float(render_overall))) * (
                    0.9 if self.playback_expected else 1.0
                )
            else:
                overall = self._render_overall(stage, stage_progress)
            self._overall_progress = max(self._overall_progress, overall)
            now = time.monotonic()
            if self.enabled and (
                force or now - self._last_progress_emit >= 1.0 or overall >= 1.0
            ):
                self._last_progress_emit = now
                emit_web_event(True, "progress", **self._payload_locked())

    def _heartbeat_loop(self) -> None:
        while not self._stop.wait(1.0):
            with self._lock:
                emit_web_event(True, "heartbeat", **self._payload_locked())

    def close(self) -> None:
        self._stop.set()
        if self.enabled and self._heartbeat.is_alive():
            self._heartbeat.join(timeout=2.0)


def _component_progress(component: str, completed: int, total: int) -> tuple[str, float]:
    fraction = min(1.0, max(0.0, completed / max(1, total)))
    if "expression_context_forward" in component:
        return "expression", 0.30 * fraction
    if "expression_context_backward" in component:
        return "expression", 0.30 + 0.30 * fraction
    if "expression_decode" in component:
        return "expression", 0.60 + 0.40 * fraction
    if "synthesis_precondition" in component:
        return "pitch_context", 0.08 * fraction
    if "synthesis_context_forward" in component:
        return "pitch_context", 0.08 + 0.20 * fraction
    if "synthesis_context_backward" in component:
        return "pitch_context", 0.28 + 0.20 * fraction
    if "synthesis_f0_decode" in component:
        return "pitch_context", 0.48 + 0.52 * fraction
    if "synthesis_timbre" in component:
        return "timbre", fraction
    return "loading_models", fraction


def plan_voice_batches(
    voice_count: int,
    available_batch_sizes: tuple[int, ...],
    requested_batch_size: int | None = None,
) -> list[tuple[int, int, int]]:
    """Return (start, stop, static_batch_size) groups in source order."""
    if voice_count <= 0:
        return []
    available = tuple(sorted(set(int(value) for value in available_batch_sizes)))
    if not available or available[0] <= 0:
        raise ValueError("At least one positive voice batch size is required")
    if requested_batch_size is not None and requested_batch_size not in available:
        raise ValueError(
            f"Requested voice batch {requested_batch_size} is unavailable; "
            f"available={available}"
        )
    groups: list[tuple[int, int, int]] = []
    start = 0
    while start < voice_count:
        remaining = voice_count - start
        if requested_batch_size is not None:
            batch_size = requested_batch_size
        elif remaining > available[-1]:
            batch_size = available[-1]
        else:
            batch_size = next(value for value in available if value >= remaining)
        stop = min(voice_count, start + batch_size)
        groups.append((start, stop, batch_size))
        start = stop
    return groups


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


def _should_read_cache(
    force_render: bool,
    cache_wav: Path | None,
    cache_report: Path | None,
) -> bool:
    return bool(
        not force_render
        and cache_wav is not None
        and cache_report is not None
        and cache_wav.is_file()
        and cache_report.is_file()
    )


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
    sample_index = np.arange(intervals * hop_size, dtype=np.int64)
    lower = sample_index // hop_size
    fraction = (sample_index % hop_size).astype(np.float32) / np.float32(hop_size)
    return (
        values[lower]
        + (values[lower + 1] - values[lower]) * fraction[:, None]
    ).astype(np.float32)


def _angular_cumsum(
    angular_frequency: np.ndarray, chunk_size: int = 1000
) -> np.ndarray:
    """Match DDSP's float32, per-oscillator chunked phase accumulation."""
    values = np.asarray(angular_frequency, dtype=np.float32)
    remainder = values.shape[0] % chunk_size
    if remainder:
        values = np.pad(values, ((0, chunk_size - remainder), (0, 0)))
    chunks = values.reshape(-1, chunk_size, values.shape[-1])
    phase = np.cumsum(chunks, axis=1, dtype=np.float32)
    two_pi = np.float32(2.0 * math.pi)
    offsets = np.mod(phase[:, -1:, :], two_pi)
    offsets = np.concatenate([np.zeros_like(offsets[:1]), offsets], axis=0)[:-1]
    offsets = np.mod(np.cumsum(offsets, axis=0, dtype=np.float32), two_pi)
    phase = np.mod(phase + offsets, two_pi).reshape(-1, values.shape[-1])
    if remainder:
        phase = phase[: -(chunk_size - remainder)]
    return phase.astype(np.float32, copy=False)


def _angular_cumsum_partition(
    angular_frequency: np.ndarray,
    cumulative_offsets: np.ndarray,
    chunk_size: int = 1000,
) -> tuple[np.ndarray, np.ndarray]:
    """Continue DDSP's float32 phase groups without resetting their offsets."""
    values = np.asarray(angular_frequency, dtype=np.float32)
    remainder = values.shape[0] % chunk_size
    if remainder:
        values = np.pad(values, ((0, chunk_size - remainder), (0, 0)))
    chunks = values.reshape(-1, chunk_size, values.shape[-1])
    local_phase = np.cumsum(chunks, axis=1, dtype=np.float32)
    two_pi = np.float32(2.0 * math.pi)
    end_offsets = np.mod(local_phase[:, -1, :], two_pi)
    group_offsets = np.empty_like(end_offsets)
    running = np.asarray(cumulative_offsets, dtype=np.float32).copy()
    for group_index, end_offset in enumerate(end_offsets):
        group_offsets[group_index] = np.mod(running, two_pi)
        running = np.add(running, end_offset, dtype=np.float32)
    phase = np.mod(local_phase + group_offsets[:, None, :], two_pi).reshape(
        -1, values.shape[-1]
    )
    if remainder:
        phase = phase[: -(chunk_size - remainder)]
    return phase.astype(np.float32, copy=False), running


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

    def __init__(
        self,
        sample_rate: int = MODEL_SAMPLE_RATE,
        render_chunk_frames: int = 125,
    ) -> None:
        self.sample_rate = float(sample_rate)
        self.phase = 0.0
        self.harmonic_numbers = np.arange(1, 61, dtype=np.float32)
        self.render_chunk_frames = max(1, int(render_chunk_frames))
        if self.render_chunk_frames * MODEL_FRAME_SIZE % 1000:
            raise ValueError("harmonic render chunks must align to 1000-sample phase groups")

    def render(
        self,
        f0_hz: np.ndarray,
        amplitudes: np.ndarray,
        harmonic_distribution: np.ndarray,
    ) -> np.ndarray:
        f0_hz = np.asarray(f0_hz, dtype=np.float32).reshape(-1)
        amplitudes = np.asarray(amplitudes, dtype=np.float32).reshape(-1)
        distribution = np.asarray(harmonic_distribution, dtype=np.float32)
        if not (len(f0_hz) == len(amplitudes) == distribution.shape[0]):
            raise ValueError("harmonic controls must have the same frame count")
        if len(f0_hz) < 2:
            return np.zeros(0, dtype=np.float32)

        interval_count = len(f0_hz) - 1
        output = np.empty(interval_count * MODEL_FRAME_SIZE, dtype=np.float32)
        two_pi = np.float32(2.0 * math.pi)
        initial_phase = (
            np.float32(self.phase) * self.harmonic_numbers
            if self.phase
            else np.zeros_like(self.harmonic_numbers)
        )
        cumulative_offsets = np.zeros_like(self.harmonic_numbers)
        final_phase = initial_phase
        for frame_start in range(0, interval_count, self.render_chunk_frames):
            frame_stop = min(interval_count, frame_start + self.render_chunk_frames)
            source = slice(frame_start, frame_stop + 1)
            frequencies = (
                f0_hz[source, None] * self.harmonic_numbers[None, :]
            )
            chunk_distribution = distribution[source].copy()
            chunk_distribution[frequencies >= self.sample_rate / 2.0] = 0.0
            totals = chunk_distribution.sum(axis=1, keepdims=True)
            chunk_distribution = np.divide(
                chunk_distribution,
                totals,
                out=np.zeros_like(chunk_distribution),
                where=totals > 1e-7,
            )
            harmonic_amplitudes = (
                amplitudes[source, None] * chunk_distribution
            )
            frequency_envelopes = _linear_frame_resample(
                frequencies, MODEL_FRAME_SIZE
            )
            amplitude_envelopes = _window_frame_resample(
                harmonic_amplitudes, MODEL_FRAME_SIZE
            )
            phase_increments = (
                frequency_envelopes * two_pi
            ) / np.float32(self.sample_rate)
            phases, cumulative_offsets = _angular_cumsum_partition(
                phase_increments, cumulative_offsets
            )
            phases = np.mod(phases + initial_phase[None, :], two_pi)
            final_phase = phases[-1]
            sample_start = frame_start * MODEL_FRAME_SIZE
            sample_stop = frame_stop * MODEL_FRAME_SIZE
            output[sample_start:sample_stop] = np.sum(
                np.sin(phases) * amplitude_envelopes,
                axis=1,
                dtype=np.float32,
            )
        self.phase = float(final_phase[0] % two_pi)
        return output


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
    tail_samples: int = 0,
    cancel_event: threading.Event | None = None,
) -> tuple[np.ndarray, dict[str, float]]:
    if tail_samples < 0:
        raise ValueError("tail_samples must be non-negative")
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
    if cancel_event is not None and cancel_event.is_set():
        raise InterruptedError("MIDI-DDSP render cancelled")

    harmonic_synth = MidiDdspHarmonicSynthesizer(MODEL_SAMPLE_RATE)
    harmonic = harmonic_synth.render(
        np.append(f0, f0[-1]),
        np.append(amplitudes, amplitudes[-1]),
        np.concatenate([harmonics, harmonics[-1:]], axis=0),
    )
    noise_synth = MidiDdspFilteredNoise(MODEL_FRAME_SIZE, seed=seed)
    noise = noise_synth.render_window(noise_magnitudes, white_noise=white_noise)
    if cancel_event is not None and cancel_event.is_set():
        raise InterruptedError("MIDI-DDSP render cancelled")
    sample_count = f0.size * MODEL_FRAME_SIZE
    dry = np.zeros(sample_count, dtype=np.float32)
    dry[: min(sample_count, harmonic.size)] += harmonic[:sample_count]
    dry[: min(sample_count, noise.size)] += noise[:sample_count]
    if tail_samples:
        dry = np.pad(dry, (0, tail_samples))

    output = dry.copy()
    if reverb is not None:
        blocks = []
        for start in range(0, len(dry), reverb.block_size):
            if cancel_event is not None and cancel_event.is_set():
                raise InterruptedError("MIDI-DDSP render cancelled")
            valid = min(reverb.block_size, len(dry) - start)
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


def _stem_seed(seed: int, track_index: int, voice_index: int = 0) -> int:
    entropy = [int(seed), int(track_index)]
    if voice_index:
        entropy.append(int(voice_index))
    return int(
        np.random.SeedSequence(entropy).generate_state(
            1, dtype=np.uint32
        )[0]
    )


def _voice_seed(seed: int, voice: MidiVoice) -> int:
    """Derive a stable seed without collisions between channel/program groups."""
    voice_digest = hashlib.sha256(voice.id.encode("utf-8")).digest()
    return int(
        np.random.SeedSequence(
            [int(seed), int.from_bytes(voice_digest[:4], "little")]
        ).generate_state(1, dtype=np.uint32)[0]
    )


def _configured_voice_instruments(
    args: argparse.Namespace,
    voices: tuple[MidiVoice, ...],
) -> dict[str, int]:
    cached = getattr(args, "voice_instrument_map", None)
    if cached is not None:
        return dict(cached)

    voice_analysis = analyze_midi_voices(args.midi)
    current_analysis_id = str(voice_analysis["analysis_id"])
    requested_analysis_id = getattr(args, "voice_analysis_id", None)
    if requested_analysis_id and requested_analysis_id != current_analysis_id:
        raise MidiValidationError(
            "voice_analysis_stale",
            "The MIDI file changed after voice analysis; analyze it again.",
        )

    raw_mapping = getattr(args, "voice_instruments_json", None)
    if raw_mapping is None:
        if not 0 <= int(args.instrument_id) < 13:
            raise ValueError("--instrument-id must be in [0, 12]")
        mapping = {voice.id: int(args.instrument_id) for voice in voices}
        source = "global_fallback"
    else:
        try:
            parsed = json.loads(raw_mapping)
        except json.JSONDecodeError as exc:
            raise ValueError("--voice-instruments-json must be valid JSON") from exc
        if not isinstance(parsed, dict):
            raise ValueError("--voice-instruments-json must contain an object")
        expected_ids = {voice.id for voice in voices}
        supplied_ids = set(parsed)
        if supplied_ids != expected_ids:
            missing = sorted(expected_ids - supplied_ids)
            extra = sorted(supplied_ids - expected_ids)
            raise MidiValidationError(
                "voice_assignment_mismatch",
                f"Voice assignment mismatch; missing={missing}, extra={extra}",
            )
        mapping = {}
        for voice_id, instrument_id in parsed.items():
            if isinstance(instrument_id, bool) or not isinstance(instrument_id, int):
                raise ValueError(
                    f"Instrument for {voice_id} must be an integer in [0, 12]"
                )
            if not 0 <= instrument_id < 13:
                raise ValueError(
                    f"Instrument for {voice_id} must be an integer in [0, 12]"
                )
            mapping[str(voice_id)] = int(instrument_id)
        source = "voice_assignment"

    args.voice_instrument_map = mapping
    args.resolved_voice_analysis_id = current_analysis_id
    args.voice_instrument_source = source
    return dict(mapping)


def _play_precomputed_audio(
    samples: np.ndarray,
    args: argparse.Namespace,
    pause_event: threading.Event,
    progress: RenderProgress | None = None,
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
                if progress is not None:
                    progress.update(
                        "playback",
                        (index + 1) / max(1, total),
                        completed=index + 1,
                        total=total,
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
            if progress is not None:
                progress.update(
                    "playback",
                    (index + 1) / max(1, total),
                    completed=index + 1,
                    total=total,
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
        "voice_separation_commit": VOICE_SEPARATION_INFO["commit"],
        "voice_analysis_id": getattr(args, "resolved_voice_analysis_id", None),
        "voice_instruments": getattr(args, "voice_instrument_map", None),
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
    force_render = bool(getattr(args, "force_render", False))
    cache_overwritten = bool(
        force_render
        and cache_wav is not None
        and cache_report is not None
        and cache_wav.is_file()
        and cache_report.is_file()
    )
    with RenderProgress(args.json_events, not args.render_only) as progress:
        if _should_read_cache(force_render, cache_wav, cache_report):
            cache_started = time.perf_counter()
            progress.update(
                "writing_cache", 0.2, activity="reading_cache", force=True
            )
            audio, sample_rate = read_wav(cache_wav)
            if sample_rate != args.sample_rate:
                raise ValueError("Cached MIDI-DDSP sample rate mismatch")
            if args.output is not None:
                args.output.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(cache_wav, args.output)
            report = json.loads(cache_report.read_text(encoding="utf-8"))
            report["cache_hit"] = True
            report["cache_key"] = cache_key
            report["cache_read_wall_seconds"] = time.perf_counter() - cache_started
            report["playback_wall_seconds"] = 0.0
            report["total_wall_seconds"] = report["cache_read_wall_seconds"]
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(
                json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            progress.update(
                "writing_cache", 1.0, activity="reading_cache", force=True
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
                stage="writing_cache",
                cache_hit=True,
                reverb_enabled=report.get("reverb_enabled", False),
            )
            emit_web_event(
                args.json_events,
                "rendered",
                report=str(args.report.resolve()),
                output=str(args.output.resolve()) if args.output is not None else None,
                cache_hit=True,
            )
            if not args.render_only:
                playback_started = time.perf_counter()
                _play_precomputed_audio(audio, args, pause_event, progress)
                report["playback_wall_seconds"] = (
                    time.perf_counter() - playback_started
                )
                report["total_wall_seconds"] = (
                    report["cache_read_wall_seconds"]
                    + report["playback_wall_seconds"]
                )
                args.report.write_text(
                    json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
            emit_web_event(
                args.json_events, "completed", report=str(args.report.resolve())
            )
            print(f"[CACHE] hit {cache_key}")
            return 0

        render_started = time.perf_counter()
        expected_frames = sum(token.length_frames for token in tokens)
        emit_web_event(
            args.json_events,
            "prepared",
            notes=len(parsed_midi.notes),
            frames=expected_frames,
            blocks=math.ceil(expected_frames / bundle.synthesis_block),
            duration_seconds=expected_frames / MODEL_FRAME_RATE + args.tail_seconds,
            source_track_count=parsed_midi.source_track_count,
            selected_track_index=None,
            selected_track_name="",
            melody_extracted=False,
            max_polyphony=parsed_midi.max_polyphony,
            midi_ddsp_mode=parsed_midi.mode,
            voice_count=1,
            voice_batch_count=1,
            seed=args.seed,
            stage="preparing",
            cache_hit=False,
            reverb_enabled=reverb is not None,
        )

        def inference_progress(component: str, completed: int, total: int) -> None:
            stage, stage_progress = _component_progress(component, completed, total)
            progress.update(
                stage,
                stage_progress,
                completed=completed,
                total=total,
                batch_index=1,
                batch_count=1,
                component=component,
            )

        progress.update("loading_models", 0.0, force=True)
        inference = BatchedStatefulMidiDdspInference(
            bundle,
            1,
            device_id=args.device_id,
            progress=inference_progress,
        )
        parameters = inference.run(
            [tokens], build_frame_features, [args.instrument_id], [args.seed]
        )[0]
        features = build_frame_features(
            tokens, parameters.controls, args.instrument_id
        )
        progress.update("timbre", 1.0, force=True)
        progress.update("dsp_reverb", 0.0, completed=0, total=1, force=True)
        dsp_started = time.perf_counter()
        model_audio, signal_metrics = _render_stateful_audio(
            parameters,
            reverb,
            args.seed,
            tail_samples=round(args.tail_seconds * MODEL_SAMPLE_RATE),
        )
        dsp_seconds = time.perf_counter() - dsp_started
        progress.update("dsp_reverb", 1.0, completed=1, total=1, force=True)
        resample_started = time.perf_counter()
        preclip = LinearResampler(MODEL_SAMPLE_RATE, args.sample_rate).process(
            model_audio * 10.0 ** (args.output_gain_db / 20.0)
        )
        resampling_seconds = time.perf_counter() - resample_started
        preclip_peak = float(np.max(np.abs(preclip))) if preclip.size else 0.0
        clipped_samples = int(np.count_nonzero(np.abs(preclip) > 1.0))
        audio = np.clip(preclip, -1.0, 1.0).astype(np.float32)
        progress.update("mixing", 1.0, force=True)
        progress.update("writing_cache", 0.0, force=True)
        write_disk_seconds = 0.0
        if args.output is not None:
            write_started = time.perf_counter()
            write_wav(args.output, audio, args.sample_rate)
            write_disk_seconds += time.perf_counter() - write_started
            print(f"[WAV] {args.output} samples={audio.size}")

        reverb_ms = np.asarray(
            reverb.process_times_ms if reverb is not None else [], dtype=np.float64
        )
        model_load_seconds = sum(
            sum(values) for values in inference.model_load_timings.values()
        ) / 1000.0
        npu_seconds = sum(sum(values) for values in inference.timings.values()) / 1000.0
        report = {
            "midi": str(args.midi.resolve()),
            "midi_sha256": sha256_file(args.midi),
            "notes": len(parsed_midi.notes),
            "tokens": len(tokens),
            "frames": features.frames,
            "duration_seconds": audio.size / args.sample_rate,
            "source_track_count": parsed_midi.source_track_count,
            "max_polyphony": parsed_midi.max_polyphony,
            "midi_ddsp_mode": parsed_midi.mode,
            "instrument_id": args.instrument_id,
            "instrument_ids": [args.instrument_id],
            "instrument_mode": (
                "per_voice"
                if getattr(args, "voice_instrument_source", "global_fallback")
                == "voice_assignment"
                else "global_fallback"
            ),
            "voice_analysis_id": getattr(args, "resolved_voice_analysis_id", None),
            "voice_instruments": getattr(args, "voice_instrument_map", None),
            "voice_separation": dict(VOICE_SEPARATION_INFO),
            "device_id": args.device_id,
            "seed": args.seed,
            "model_bundle_id": bundle.id,
            "model_bundle": str(bundle.manifest_path),
            "model_bundle_sha256": sha256_file(bundle.manifest_path),
            "source_commit": bundle.source_commit,
            "component_sha256": {
                component.export_name: component.sha256
                for component in bundle.component_sets[1].values()
            },
            "model_state_continuity": {
                "expression_context": True,
                "expression_decoder": True,
                "synthesis_context": True,
                "f0_decoder": True,
                "per_voice_state": True,
                "per_voice_valid_frames": True,
                "per_voice_random_seed": True,
                "timbre_global_normalization": True,
                "timbre_max_frames": bundle.timbre_max_frames,
            },
            **parameters.metrics,
            **_stateful_timing_report(inference),
            "model_load_seconds": model_load_seconds,
            "npu_inference_seconds": npu_seconds,
            "dsp_seconds": dsp_seconds,
            "resampling_seconds": resampling_seconds,
            "mixing_seconds": 0.0,
            "write_disk_seconds": write_disk_seconds,
            "render_wall_seconds": 0.0,
            "playback_wall_seconds": 0.0,
            "total_wall_seconds": 0.0,
            "realtime_factor": 0.0,
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
            "force_render": force_render,
            "cache_overwritten": cache_overwritten,
            "cache_key": cache_key,
            "boundary_continuity": _boundary_continuity(
                audio, bundle.synthesis_block * MODEL_FRAME_SIZE
            ),
        }
        args.report.parent.mkdir(parents=True, exist_ok=True)
        report_write_started = time.perf_counter()
        args.report.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        write_disk_seconds += time.perf_counter() - report_write_started
        if cache_wav is not None and cache_report is not None and args.output is not None:
            args.cache_dir.mkdir(parents=True, exist_ok=True)
            cache_write_started = time.perf_counter()
            shutil.copy2(args.output, cache_wav)
            write_disk_seconds += time.perf_counter() - cache_write_started
        render_wall_seconds = time.perf_counter() - render_started
        report["write_disk_seconds"] = write_disk_seconds
        report["render_wall_seconds"] = render_wall_seconds
        report["inference_and_dsp_wall_seconds"] = render_wall_seconds
        report["total_wall_seconds"] = render_wall_seconds
        report["realtime_factor"] = (
            render_wall_seconds / report["duration_seconds"]
            if report["duration_seconds"]
            else 0.0
        )
        args.report.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        if cache_report is not None and cache_wav is not None and cache_wav.is_file():
            shutil.copy2(args.report, cache_report)
        progress.update("writing_cache", 1.0, force=True)
        emit_web_event(
            args.json_events,
            "rendered",
            report=str(args.report.resolve()),
            output=str(args.output.resolve()) if args.output is not None else None,
            cache_hit=False,
        )
        if not args.render_only:
            playback_started = time.perf_counter()
            _play_precomputed_audio(audio, args, pause_event, progress)
            report["playback_wall_seconds"] = time.perf_counter() - playback_started
            report["total_wall_seconds"] = (
                report["render_wall_seconds"] + report["playback_wall_seconds"]
            )
            args.report.write_text(
                json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            if cache_report is not None and cache_wav is not None and cache_wav.is_file():
                shutil.copy2(args.report, cache_report)
        print(f"[REPORT] {args.report}")
        emit_web_event(args.json_events, "completed", report=str(args.report.resolve()))
        return 0


def _run_stateful_batch_worker(args: argparse.Namespace) -> int:
    analysis = analyze_midi(args.midi)
    voices = split_midi_voices(analysis)
    voice_instruments = _configured_voice_instruments(args, voices)
    start = int(args.batch_worker_start)
    stop = int(args.batch_worker_stop)
    if not (0 <= start < stop <= len(voices)):
        raise ValueError("Invalid isolated voice batch range")
    if args.model_bundle is None or args.batch_worker_result is None:
        raise ValueError("Isolated voice batch requires a bundle and result path")

    selected = voices[start:stop]
    tokens_all = [
        build_tokens(
            list(voice.notes),
            round(MODEL_FRAME_RATE * OFFICIAL_TAIL_SECONDS),
        )
        for voice in selected
    ]
    seeds = [
        _voice_seed(args.seed, voice)
        for voice in selected
    ]
    bundle = load_runtime_bundle(args.model_bundle)
    # Keep ACL/GE initialized across all component runners, then release it only
    # after the isolated result has been flushed and fsynced.
    args._batch_runtime_guard = bundle.runtime_session(args.device_id)
    batch_size = int(args.voice_batch_size)
    last_progress = 0.0

    def worker_progress(component: str, completed: int, total: int) -> None:
        nonlocal last_progress
        now = time.monotonic()
        if completed < total and now - last_progress < 0.25:
            return
        last_progress = now
        print(
            BATCH_WORKER_EVENT
            + json.dumps(
                {
                    "component": component,
                    "completed": completed,
                    "total": total,
                },
                separators=(",", ":"),
            ),
            flush=True,
        )

    inference = BatchedStatefulMidiDdspInference(
        bundle,
        batch_size,
        device_id=args.device_id,
        progress=worker_progress,
    )
    started = time.perf_counter()
    parameters = inference.run(
        tokens_all,
        build_frame_features,
        [voice_instruments[voice.id] for voice in selected],
        seeds,
    )
    payload = {
        "parameters": parameters,
        "component_timings": inference.timings,
        "model_load_timings": inference.model_load_timings,
        "wall_seconds": time.perf_counter() - started,
    }
    args.batch_worker_result.parent.mkdir(parents=True, exist_ok=True)
    with args.batch_worker_result.open("wb") as handle:
        pickle.dump(payload, handle, protocol=pickle.HIGHEST_PROTOCOL)
        handle.flush()
        os.fsync(handle.fileno())
    args._batch_runtime_guard.close(suppress_errors=True)
    args._batch_runtime_guard = None
    return 0


def _run_isolated_voice_batch(
    args: argparse.Namespace,
    start: int,
    stop: int,
    batch_size: int,
    result_path: Path,
    progress_callback,
) -> dict[str, object]:
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--midi",
        str(args.midi),
        "--model-bundle",
        str(args.model_bundle),
        "--instrument-id",
        str(args.instrument_id),
        "--seed",
        str(args.seed),
        "--device-id",
        str(args.device_id),
        "--voice-batch-size",
        str(batch_size),
        "--batch-worker-start",
        str(start),
        "--batch-worker-stop",
        str(stop),
        "--batch-worker-result",
        str(result_path),
    ]
    if getattr(args, "voice_analysis_id", None):
        command.extend(["--voice-analysis-id", str(args.voice_analysis_id)])
    if getattr(args, "voice_instruments_json", None):
        command.extend(
            ["--voice-instruments-json", str(args.voice_instruments_json)]
        )
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    try:
        assert process.stdout is not None
        for raw_line in process.stdout:
            line = raw_line.rstrip("\n")
            if line.startswith(BATCH_WORKER_EVENT):
                event = json.loads(line[len(BATCH_WORKER_EVENT) :])
                progress_callback(
                    str(event["component"]),
                    int(event["completed"]),
                    int(event["total"]),
                )
            elif line:
                print(f"[BATCH WORKER] {line}", flush=True)
        exit_code = process.wait()
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
    cleanup_segfault = exit_code == -11 and result_path.is_file()
    if exit_code != 0 and not cleanup_segfault:
        raise RuntimeError(f"Isolated voice batch failed with exit code {exit_code}")
    if not result_path.is_file():
        raise RuntimeError("Isolated voice batch did not write its result")
    try:
        with result_path.open("rb") as handle:
            payload = pickle.load(handle)
    except BaseException as exc:
        raise RuntimeError("Isolated voice batch wrote an invalid result") from exc
    required = {"parameters", "component_timings", "model_load_timings", "wall_seconds"}
    if not isinstance(payload, dict) or not required.issubset(payload):
        raise RuntimeError("Isolated voice batch result is incomplete")
    result_path.unlink()
    if cleanup_segfault:
        print(
            "[BATCH WORKER] accepted fsynced result after CANN cleanup exit -11",
            flush=True,
        )
    return payload


def _run_stateful_multivoice(
    args: argparse.Namespace,
    analysis: MidiAnalysis,
    pause_event: threading.Event,
) -> int:
    bundle = load_runtime_bundle(args.model_bundle)
    voices = split_midi_voices(analysis)
    voice_instruments = _configured_voice_instruments(args, voices)
    instrument_ids = [voice_instruments[voice.id] for voice in voices]
    requested_batch_size = (
        None if args.voice_batch_size == "auto" else int(args.voice_batch_size)
    )
    voice_batches = plan_voice_batches(
        len(voices), bundle.voice_batch_sizes, requested_batch_size
    )
    cache_payload = {
        "mode": "multivoice-v3-per-voice-instruments",
        "render_only": args.render_only,
        "midi_sha256": sha256_file(args.midi),
        "bundle_sha256": sha256_file(bundle.manifest_path),
        "seed": args.seed,
        "tail_seconds": args.tail_seconds,
        "sample_rate": args.sample_rate,
        "output_gain_db": args.output_gain_db,
        "reverb_sha256": (
            None if args.disable_reverb else sha256_file(args.reverb_ir)
        ),
        "voice_separation_commit": VOICE_SEPARATION_INFO["commit"],
        "voice_analysis_id": args.resolved_voice_analysis_id,
        "voices": [
            {
                "voice_id": voice.id,
                "track_index": voice.source_track_index,
                "channel": voice.channel,
                "program": voice.program,
                "voice_index": voice.voice_index,
                "instrument_id": voice_instruments[voice.id],
            }
            for voice in voices
        ],
    }
    cache_key = hashlib.sha256(
        json.dumps(cache_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    cache_wav = args.cache_dir / f"{cache_key}.wav" if args.cache_dir else None
    cache_report = args.cache_dir / f"{cache_key}.json" if args.cache_dir else None
    force_render = bool(getattr(args, "force_render", False))
    cache_overwritten = bool(
        force_render
        and cache_wav is not None
        and cache_report is not None
        and cache_wav.is_file()
        and cache_report.is_file()
    )
    with RenderProgress(args.json_events, not args.render_only) as progress:
        if _should_read_cache(force_render, cache_wav, cache_report):
            cache_started = time.perf_counter()
            progress.update(
                "writing_cache", 0.2, activity="reading_cache", force=True
            )
            audio, sample_rate = read_wav(cache_wav)
            if sample_rate != args.sample_rate:
                raise ValueError("Cached MIDI-DDSP sample rate mismatch")
            report = json.loads(cache_report.read_text(encoding="utf-8"))
            report["cache_hit"] = True
            report["cache_key"] = cache_key
            if args.output is not None:
                args.output.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(cache_wav, args.output)
                if args.render_only:
                    for stem in report.get("stems", []):
                        artifact = stem.get("artifact")
                        if not artifact:
                            continue
                        source = args.cache_dir / f"{cache_key}.{artifact}"
                        if source.is_file():
                            shutil.copy2(source, args.output.parent / str(artifact))
            report["cache_read_wall_seconds"] = time.perf_counter() - cache_started
            report["playback_wall_seconds"] = 0.0
            report["total_wall_seconds"] = report["cache_read_wall_seconds"]
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(
                json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            progress.update(
                "writing_cache", 1.0, activity="reading_cache", force=True
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
                midi_ddsp_mode=analysis.mode,
                seed=args.seed,
                stage="writing_cache",
                cache_hit=True,
                reverb_enabled=not args.disable_reverb,
            )
            emit_web_event(
                args.json_events,
                "rendered",
                report=str(args.report.resolve()),
                output=str(args.output.resolve()) if args.output is not None else None,
                cache_hit=True,
            )
            if not args.render_only:
                playback_started = time.perf_counter()
                _play_precomputed_audio(audio, args, pause_event, progress)
                report["playback_wall_seconds"] = (
                    time.perf_counter() - playback_started
                )
                report["total_wall_seconds"] = (
                    report["cache_read_wall_seconds"]
                    + report["playback_wall_seconds"]
                )
                args.report.write_text(
                    json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
            emit_web_event(
                args.json_events, "completed", report=str(args.report.resolve())
            )
            print(f"[CACHE] hit {cache_key}")
            return 0

        render_started = time.perf_counter()
        progress.update(
            "preparing",
            1.0,
            completed=len(voices),
            total=len(voices),
            activity="partitioning_voices",
            force=True,
        )
        emit_web_event(
            args.json_events,
            "prepared",
            notes=analysis.note_count,
            frames=0,
            blocks=0,
            duration_seconds=analysis.duration_seconds + OFFICIAL_TAIL_SECONDS,
            source_track_count=len(analysis.tracks),
            max_polyphony=analysis.max_polyphony,
            midi_ddsp_mode=analysis.mode,
            voice_count=len(voices),
            voice_batch_count=len(voice_batches),
            seed=args.seed,
            stage="preparing",
            cache_hit=False,
            reverb_enabled=not args.disable_reverb,
        )

        gain = 10.0 ** (args.output_gain_db / 20.0)
        tokens_all = [
            build_tokens(
                list(voice.notes),
                round(MODEL_FRAME_RATE * OFFICIAL_TAIL_SECONDS),
            )
            for voice in voices
        ]
        seeds = [
            _voice_seed(args.seed, voice)
            for voice in voices
        ]
        parameters_all: list[object | None] = [None] * len(voices)
        all_expression_timings: list[float] = []
        all_synthesis_timings: list[float] = []
        component_timings: dict[str, list[float]] = {}
        model_load_timings: dict[str, list[float]] = {}
        batch_reports: list[dict[str, object]] = []
        args.report.parent.mkdir(parents=True, exist_ok=True)
        worker_temp = tempfile.TemporaryDirectory(
            prefix="midi-ddsp-batches-", dir=args.report.parent
        )
        try:
            for batch_number, (start, stop, batch_size) in enumerate(
                voice_batches, start=1
            ):
                progress.update(
                    "loading_models",
                    (batch_number - 1) / max(1, len(voice_batches)),
                    completed=batch_number - 1,
                    total=len(voice_batches),
                    batch_index=batch_number,
                    batch_count=len(voice_batches),
                    activity=f"batch_{batch_size}",
                    force=True,
                )

                def batch_progress(
                    component: str,
                    completed: int,
                    total: int,
                    *,
                    current_batch: int = batch_number,
                ) -> None:
                    stage, local_progress = _component_progress(
                        component, completed, total
                    )
                    npu_stage_progress = {
                        "expression": 0.10 * local_progress,
                        "pitch_context": 0.10 + 0.80 * local_progress,
                        "timbre": 0.90 + 0.10 * local_progress,
                    }.get(stage, local_progress)
                    grouped_progress = (
                        current_batch - 1 + local_progress
                    ) / max(1, len(voice_batches))
                    grouped_npu_progress = (
                        current_batch - 1 + npu_stage_progress
                    ) / max(1, len(voice_batches))
                    progress.update(
                        stage,
                        grouped_progress,
                        completed=completed,
                        total=total,
                        batch_index=current_batch,
                        batch_count=len(voice_batches),
                        component=component,
                        render_overall=0.04 + 0.68 * grouped_npu_progress,
                    )

                batch_payload = _run_isolated_voice_batch(
                    args,
                    start,
                    stop,
                    batch_size,
                    Path(worker_temp.name) / f"batch-{batch_number}.pkl",
                    batch_progress,
                )
                batch_parameters = batch_payload["parameters"]
                batch_wall_seconds = float(batch_payload["wall_seconds"])
                parameters_all[start:stop] = batch_parameters
                for name, values in batch_payload["component_timings"].items():
                    component_timings.setdefault(name, []).extend(values)
                    destination = (
                        all_expression_timings
                        if "expression" in name
                        else all_synthesis_timings
                    )
                    destination.extend(values)
                for name, values in batch_payload["model_load_timings"].items():
                    model_load_timings.setdefault(name, []).extend(values)
                batch_reports.append(
                    {
                        "batch_index": batch_number,
                        "voice_start": start,
                        "voice_stop": stop,
                        "voice_count": stop - start,
                        "static_batch_size": batch_size,
                        "wall_seconds": batch_wall_seconds,
                    }
                )
        finally:
            worker_temp.cleanup()

        if any(parameters is None for parameters in parameters_all):
            raise RuntimeError("MIDI-DDSP batch inference did not produce every voice")
        progress.update("timbre", 1.0, force=True)

        features_all = [
            build_frame_features(tokens, parameters.controls, instrument_id)
            for tokens, parameters, instrument_id in zip(
                tokens_all, parameters_all, instrument_ids
            )
        ]
        total_frames = max((features.frames for features in features_all), default=0)
        stem_audio: list[np.ndarray | None] = [None] * len(voices)
        stem_reports: list[dict[str, object] | None] = [None] * len(voices)
        write_disk_seconds = 0.0
        dsp_cancel = threading.Event()

        def render_voice(index: int) -> tuple[int, np.ndarray, dict[str, object], float]:
            voice = voices[index]
            parameters = parameters_all[index]
            features = features_all[index]
            stem_seed = seeds[index]
            instrument_id = instrument_ids[index]
            reverb = (
                None
                if args.disable_reverb
                else StreamingFftReverb.from_asset(args.reverb_ir, instrument_id)
            )
            dsp_started = time.perf_counter()
            model_audio, signal_metrics = _render_stateful_audio(
                parameters,
                reverb,
                stem_seed,
                tail_samples=round(args.tail_seconds * MODEL_SAMPLE_RATE),
                cancel_event=dsp_cancel,
            )
            dsp_seconds = time.perf_counter() - dsp_started
            resample_started = time.perf_counter()
            resampled = LinearResampler(MODEL_SAMPLE_RATE, args.sample_rate).process(
                model_audio * gain
            ).astype(np.float32)
            resampling_seconds = time.perf_counter() - resample_started
            artifact = f"stem-{voice.id}.wav"
            stem_write_seconds = 0.0
            if args.render_only and args.output is not None:
                write_started = time.perf_counter()
                write_wav(args.output.parent / artifact, resampled, args.sample_rate)
                stem_write_seconds = time.perf_counter() - write_started
            reverb_ms = np.asarray(
                reverb.process_times_ms if reverb is not None else [],
                dtype=np.float64,
            )
            stem_preclip_peak = (
                float(np.max(np.abs(resampled))) if resampled.size else 0.0
            )
            stem_report = {
                "voice_id": voice.id,
                "group_id": voice.source_group_id,
                "track_index": voice.source_track_index,
                "track_name": voice.source_track_name,
                "channel": voice.channel + 1,
                "program": voice.program,
                "voice_index": voice.voice_index,
                "note_count": len(voice.notes),
                "instrument_id": instrument_id,
                "instrument_source": args.voice_instrument_source,
                "programs": list(voice.programs),
                "seed": stem_seed,
                "frames": features.frames,
                "duration_seconds": len(resampled) / args.sample_rate,
                "voice_batch_size": parameters.metrics["voice_batch_size"],
                "batch_member_index": parameters.metrics["batch_member_index"],
                "tensor_sha256": parameters.metrics["tensor_sha256"],
                "dsp_seconds": dsp_seconds,
                "resampling_seconds": resampling_seconds,
                "write_disk_seconds": stem_write_seconds,
                "reverb_process_mean_ms": (
                    float(reverb_ms.mean()) if reverb_ms.size else 0.0
                ),
                **signal_metrics,
                "preclip_peak": stem_preclip_peak,
                "clipped_samples": int(np.count_nonzero(np.abs(resampled) > 1.0)),
                "boundary_continuity": _boundary_continuity(
                    resampled,
                    bundle.synthesis_block
                    * MODEL_FRAME_SIZE
                    * args.sample_rate
                    // MODEL_SAMPLE_RATE,
                ),
                "artifact": artifact if args.render_only else None,
            }
            return index, resampled, stem_report, stem_write_seconds

        detected_cpus = os.cpu_count() or 1
        if hasattr(os, "sched_getaffinity"):
            detected_cpus = len(os.sched_getaffinity(0))
        dsp_workers = min(
            len(voices),
            max(1, args.dsp_workers if args.dsp_workers > 0 else min(3, detected_cpus)),
        )
        progress.update(
            "dsp_reverb",
            0.0,
            completed=0,
            total=len(voices),
            activity=f"workers_{dsp_workers}",
            force=True,
        )
        futures = []
        with ThreadPoolExecutor(
            max_workers=dsp_workers, thread_name_prefix="midi-ddsp-dsp"
        ) as executor:
            futures = [
                executor.submit(render_voice, index)
                for index in range(len(voices))
            ]
            try:
                for completed_count, future in enumerate(
                    as_completed(futures), start=1
                ):
                    index, resampled, stem_report, stem_write_seconds = future.result()
                    stem_audio[index] = resampled
                    stem_reports[index] = stem_report
                    write_disk_seconds += stem_write_seconds
                    progress.update(
                        "dsp_reverb",
                        completed_count / max(1, len(voices)),
                        completed=completed_count,
                        total=len(voices),
                    )
            except BaseException:
                dsp_cancel.set()
                for future in futures:
                    future.cancel()
                raise

        if any(stem is None for stem in stem_audio) or any(
            stem is None for stem in stem_reports
        ):
            raise RuntimeError("MIDI-DDSP DSP did not produce every voice")
        ordered_audio = [stem for stem in stem_audio if stem is not None]
        ordered_reports = [stem for stem in stem_reports if stem is not None]
        progress.update("mixing", 0.0, force=True)
        mix_started = time.perf_counter()
        max_samples = max((len(stem) for stem in ordered_audio), default=0)
        mixed = np.zeros(max_samples, dtype=np.float64)
        for stem in ordered_audio:
            mixed[: len(stem)] += stem.astype(np.float64)
        preclip_peak = float(np.max(np.abs(mixed))) if mixed.size else 0.0
        overload_samples = int(np.count_nonzero(np.abs(mixed) > 1.0))
        mix_gain = (
            MIX_TARGET_PEAK / preclip_peak if preclip_peak > MIX_TARGET_PEAK else 1.0
        )
        protected = mixed * mix_gain
        clipped_samples = int(np.count_nonzero(np.abs(protected) > 1.0))
        audio = np.clip(protected, -1.0, 1.0).astype(np.float32)
        mixing_seconds = time.perf_counter() - mix_started
        progress.update("mixing", 1.0, force=True)

        progress.update("writing_cache", 0.0, force=True)
        if args.output is not None:
            write_started = time.perf_counter()
            write_wav(args.output, audio, args.sample_rate)
            write_disk_seconds += time.perf_counter() - write_started
            print(f"[WAV] {args.output} samples={audio.size}, voices={len(voices)}")

        synthesis_values = np.asarray(all_synthesis_timings, dtype=np.float64)
        expression_values = np.asarray(all_expression_timings, dtype=np.float64)
        model_load_seconds = sum(
            sum(values) for values in model_load_timings.values()
        ) / 1000.0
        npu_seconds = (
            float(expression_values.sum()) + float(synthesis_values.sum())
        ) / 1000.0
        dsp_seconds = sum(float(stem["dsp_seconds"]) for stem in ordered_reports)
        resampling_seconds = sum(
            float(stem["resampling_seconds"]) for stem in ordered_reports
        )
        selected_components = {
            component.export_name: component.sha256
            for _start, _stop, batch_size in voice_batches
            for component in bundle.component_sets[batch_size].values()
        }
        report = {
            "midi": str(args.midi.resolve()),
            "midi_sha256": sha256_file(args.midi),
            "notes": analysis.note_count,
            "tokens": sum(len(tokens) for tokens in tokens_all),
            "frames": total_frames,
            "duration_seconds": audio.size / args.sample_rate,
            "source_track_count": len(analysis.tracks),
            "voice_count": len(voices),
            "instrument_id": args.instrument_id,
            "instrument_ids": sorted(set(instrument_ids)),
            "instrument_mode": (
                "per_voice"
                if args.voice_instrument_source == "voice_assignment"
                else "global_fallback"
            ),
            "voice_analysis_id": args.resolved_voice_analysis_id,
            "voice_instruments": voice_instruments,
            "voice_separation": dict(VOICE_SEPARATION_INFO),
            "stem_count": len(ordered_reports) if args.render_only else 0,
            "stems": ordered_reports,
            "max_polyphony": analysis.max_polyphony,
            "midi_ddsp_mode": analysis.mode,
            "device_id": args.device_id,
            "seed": args.seed,
            "model_bundle_id": bundle.id,
            "model_bundle": str(bundle.manifest_path),
            "model_bundle_sha256": sha256_file(bundle.manifest_path),
            "source_commit": bundle.source_commit,
            "architecture": "stateful-v2-batched",
            "voice_batch_sizes_available": list(bundle.voice_batch_sizes),
            "voice_batch_sizes_used": [group[2] for group in voice_batches],
            "voice_batches": batch_reports,
            "dsp_workers": dsp_workers,
            "component_sha256": selected_components,
            "model_state_continuity": {
                "expression_context": True,
                "expression_decoder": True,
                "synthesis_context": True,
                "f0_decoder": True,
                "per_voice_state": True,
                "per_voice_valid_frames": True,
                "per_voice_random_seed": True,
                "timbre_global_normalization": True,
                "timbre_max_frames": bundle.timbre_max_frames,
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
            "component_timings_ms": {
                name: {
                    "count": len(values),
                    "mean": float(np.mean(values)) if values else 0.0,
                    "p95": float(np.percentile(values, 95)) if values else 0.0,
                    "max": float(np.max(values)) if values else 0.0,
                }
                for name, values in component_timings.items()
            },
            "model_load_timings_ms": {
                name: float(np.sum(values))
                for name, values in model_load_timings.items()
            },
            "model_load_seconds": model_load_seconds,
            "npu_inference_seconds": npu_seconds,
            "dsp_seconds": dsp_seconds,
            "resampling_seconds": resampling_seconds,
            "mixing_seconds": mixing_seconds,
            "write_disk_seconds": write_disk_seconds,
            "render_wall_seconds": 0.0,
            "playback_wall_seconds": 0.0,
            "total_wall_seconds": 0.0,
            "realtime_factor": 0.0,
            "audio_sample_rate": args.sample_rate,
            "output_gain_db": args.output_gain_db,
            "official_tail_seconds": OFFICIAL_TAIL_SECONDS,
            "additional_tail_seconds": args.tail_seconds,
            "reverb_enabled": not args.disable_reverb,
            "reverb_ir": (
                str(args.reverb_ir.resolve()) if not args.disable_reverb else None
            ),
            "reverb_ir_sha256": (
                sha256_file(args.reverb_ir) if not args.disable_reverb else None
            ),
            "preclip_peak": preclip_peak,
            "overload_samples": overload_samples,
            "mix_gain": mix_gain,
            "mix_gain_db": 20.0 * math.log10(mix_gain),
            "peak_protection_enabled": mix_gain < 1.0,
            "mix_target_peak": MIX_TARGET_PEAK,
            "clipped_samples": clipped_samples,
            "audio_samples_captured": int(audio.size),
            "audio_peak": float(np.max(np.abs(audio))) if audio.size else 0.0,
            "audio_rms": float(np.sqrt(np.mean(audio * audio))) if audio.size else 0.0,
            "underruns": 0,
            "overruns": 0,
            "render_only": args.render_only,
            "cache_hit": False,
            "force_render": force_render,
            "cache_overwritten": cache_overwritten,
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
        report_write_started = time.perf_counter()
        args.report.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        write_disk_seconds += time.perf_counter() - report_write_started
        if cache_wav is not None and cache_report is not None and args.output is not None:
            args.cache_dir.mkdir(parents=True, exist_ok=True)
            cache_write_started = time.perf_counter()
            shutil.copy2(args.output, cache_wav)
            if args.render_only:
                for stem in ordered_reports:
                    artifact = stem.get("artifact")
                    if artifact:
                        shutil.copy2(
                            args.output.parent / str(artifact),
                            args.cache_dir / f"{cache_key}.{artifact}",
                        )
            write_disk_seconds += time.perf_counter() - cache_write_started

        render_wall_seconds = time.perf_counter() - render_started
        report["write_disk_seconds"] = write_disk_seconds
        report["render_wall_seconds"] = render_wall_seconds
        report["inference_and_dsp_wall_seconds"] = render_wall_seconds
        report["total_wall_seconds"] = render_wall_seconds
        report["realtime_factor"] = (
            render_wall_seconds / report["duration_seconds"]
            if report["duration_seconds"]
            else 0.0
        )
        args.report.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        if cache_report is not None and cache_wav is not None and cache_wav.is_file():
            shutil.copy2(args.report, cache_report)
        progress.update("writing_cache", 1.0, force=True)
        print(f"[REPORT] {args.report}")
        emit_web_event(
            args.json_events,
            "rendered",
            report=str(args.report.resolve()),
            output=str(args.output.resolve()) if args.output is not None else None,
            cache_hit=False,
        )

        if not args.render_only:
            playback_started = time.perf_counter()
            _play_precomputed_audio(audio, args, pause_event, progress)
            report["playback_wall_seconds"] = time.perf_counter() - playback_started
            report["total_wall_seconds"] = (
                report["render_wall_seconds"] + report["playback_wall_seconds"]
            )
            args.report.write_text(
                json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            if cache_report is not None and cache_wav is not None and cache_wav.is_file():
                shutil.copy2(args.report, cache_report)
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
    parser.add_argument("--voice-analysis-id")
    parser.add_argument("--voice-instruments-json")
    parser.add_argument("--seed", type=int, default=20260724)
    parser.add_argument(
        "--voice-batch-size",
        choices=("auto", "1", "2", "4", "8"),
        default="auto",
        help="Static voice batch override; auto selects the smallest fitting bundle",
    )
    parser.add_argument(
        "--dsp-workers",
        type=int,
        default=0,
        help="CPU DSP workers; 0 selects up to three workers automatically",
    )
    parser.add_argument("--cache-dir", type=Path)
    parser.add_argument(
        "--force-render",
        action="store_true",
        help="Skip cache reads and overwrite the matching cache after rendering",
    )
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
    parser.add_argument("--batch-worker-start", type=int, help=argparse.SUPPRESS)
    parser.add_argument("--batch-worker-stop", type=int, help=argparse.SUPPRESS)
    parser.add_argument("--batch-worker-result", type=Path, help=argparse.SUPPRESS)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.batch_worker_result is not None:
        return _run_stateful_batch_worker(args)
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
    midi_voices = split_midi_voices(midi_analysis)
    voice_instruments = _configured_voice_instruments(args, midi_voices)
    if len(midi_voices) == 1 and args.voice_instrument_source == "voice_assignment":
        args.instrument_id = voice_instruments[midi_voices[0].id]
    if args.model_bundle is not None and midi_analysis.mode in {
        "multitrack",
        "polyphonic",
    }:
        return _run_stateful_multivoice(args, midi_analysis, pause_event)
    parsed_midi = parse_midi_details(args.midi)
    notes = parsed_midi.notes
    if parsed_midi.melody_extracted:
        track_label = (
            f"track {parsed_midi.selected_track_index}: {parsed_midi.selected_track_name}"
            if parsed_midi.selected_track_index is not None
            else parsed_midi.selected_track_name
        )
        print(f"[MIDI] extracted monophonic melody using {track_label}")
    inference_tail_seconds = (
        OFFICIAL_TAIL_SECONDS
        if args.model_bundle is not None
        else OFFICIAL_TAIL_SECONDS + args.tail_seconds
    )
    tokens = build_tokens(
        notes,
        round(MODEL_FRAME_RATE * inference_tail_seconds),
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
        "instrument_ids": [args.instrument_id],
        "instrument_mode": (
            "per_voice"
            if getattr(args, "voice_instrument_source", "global_fallback")
            == "voice_assignment"
            else "global_fallback"
        ),
        "voice_analysis_id": getattr(args, "resolved_voice_analysis_id", None),
        "voice_instruments": getattr(args, "voice_instrument_map", None),
        "voice_separation": dict(VOICE_SEPARATION_INFO),
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
