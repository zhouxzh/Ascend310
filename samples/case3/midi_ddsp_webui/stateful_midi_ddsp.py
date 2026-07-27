from __future__ import annotations

from contextlib import contextmanager, nullcontext
from dataclasses import dataclass
import hashlib
import math
import time
from typing import Callable, Sequence

import numpy as np

from .model_bundle import RuntimeBundle


CONDITIONING_NAMES = (
    "volume",
    "vol_fluc",
    "vibrato",
    "brightness",
    "attack",
    "vol_peak_pos",
)
F0_BINS = 201


def array_sha256(value: np.ndarray) -> str:
    array = np.ascontiguousarray(value)
    digest = hashlib.sha256()
    digest.update(str(array.dtype).encode("ascii"))
    digest.update(np.asarray(array.shape, dtype=np.int64).tobytes())
    digest.update(array.tobytes())
    return digest.hexdigest()


def _padded_length(length: int, block: int) -> int:
    return max(block, math.ceil(length / block) * block)


def _pad_end(value: np.ndarray, length: int) -> np.ndarray:
    pad = length - value.shape[0]
    if pad <= 0:
        return value
    return np.pad(value, ((0, pad), *[(0, 0)] * (value.ndim - 1)))


def _pad_start(value: np.ndarray, length: int) -> tuple[np.ndarray, int]:
    pad = length - value.shape[0]
    if pad <= 0:
        return value, 0
    return np.pad(value, ((pad, 0), *[(0, 0)] * (value.ndim - 1))), pad


def _timed_infer(runner, feeds: dict[str, np.ndarray], timings: list[float]):
    started = time.perf_counter()
    result = runner.infer(feeds)
    timings.append((time.perf_counter() - started) * 1000.0)
    return result


@dataclass
class StatefulParameters:
    controls: np.ndarray
    f0_hz: np.ndarray
    f0_midi: np.ndarray
    amplitudes: np.ndarray
    harmonic_distribution: np.ndarray
    noise_magnitudes: np.ndarray
    sampled_bins: np.ndarray
    metrics: dict[str, object]


class StatefulMidiDdspInference:
    def __init__(
        self,
        bundle: RuntimeBundle,
        device_id: int = 0,
        seed: int = 20260724,
        progress: Callable[[str, int, int], None] | None = None,
    ) -> None:
        self.bundle = bundle
        self.device_id = int(device_id)
        self.seed = int(seed)
        self.progress = progress
        self.timings: dict[str, list[float]] = {}

    def _notify(self, stage: str, completed: int, total: int) -> None:
        if self.progress is not None:
            self.progress(stage, completed, total)

    def _context_pass(
        self,
        component_name: str,
        values: np.ndarray,
        state_width: int,
        *,
        reverse: bool,
        input_name: str,
    ) -> np.ndarray:
        block = self.bundle.expression_block if state_width == 128 else self.bundle.synthesis_block
        padded_length = _padded_length(len(values), block)
        if reverse:
            padded, trim = _pad_start(values, padded_length)
            starts = list(range(padded_length - block, -1, -block))
        else:
            padded = _pad_end(values, padded_length)
            trim = 0
            starts = list(range(0, padded_length, block))
        output_width = state_width
        contexts = np.zeros((padded_length, output_width), dtype=np.float32)
        state = np.zeros((1, state_width), dtype=np.float32)
        timings = self.timings.setdefault(component_name, [])
        with self.bundle.component(component_name).open(self.device_id) as runner:
            for item_index, start in enumerate(starts):
                result = _timed_infer(
                    runner,
                    {
                        input_name: padded[start : start + block][None, ...],
                        "state_in": state,
                    },
                    timings,
                )
                contexts[start : start + block] = result["context"][0]
                state = result["state_out"]
                self._notify(component_name, item_index + 1, len(starts))
        return contexts[trim : trim + len(values)]

    def expression_controls(
        self, tokens: Sequence[object], instrument_id: int
    ) -> np.ndarray:
        block = self.bundle.expression_block
        count = len(tokens)
        padded_length = _padded_length(count, block)
        pitch = np.zeros(padded_length, dtype=np.int64)
        length = np.zeros((padded_length, 1), dtype=np.float32)
        for index, token in enumerate(tokens):
            pitch[index] = int(getattr(token, "pitch"))
            length[index, 0] = float(getattr(token, "length_frames")) / 250.0
        instrument = np.asarray([instrument_id], dtype=np.int64)

        def expression_context(name: str, reverse: bool) -> np.ndarray:
            if reverse:
                padded_pitch, trim = _pad_start(pitch[:count, None], padded_length)
                padded_length_values, _ = _pad_start(length[:count], padded_length)
                padded_pitch = padded_pitch[:, 0]
                starts = list(range(padded_length - block, -1, -block))
            else:
                padded_pitch = pitch
                padded_length_values = length
                trim = 0
                starts = list(range(0, padded_length, block))
            context = np.zeros((padded_length, 128), dtype=np.float32)
            state = np.zeros((1, 128), dtype=np.float32)
            timings = self.timings.setdefault(name, [])
            with self.bundle.component(name).open(self.device_id) as runner:
                for item_index, start in enumerate(starts):
                    result = _timed_infer(
                        runner,
                        {
                            "note_pitch": padded_pitch[start : start + block][None, :],
                            "note_length": padded_length_values[
                                start : start + block
                            ][None, ...],
                            "instrument_id": instrument,
                            "state_in": state,
                        },
                        timings,
                    )
                    context[start : start + block] = result["context"][0]
                    state = result["state_out"]
                    self._notify(name, item_index + 1, len(starts))
            return context[trim : trim + count]

        forward_name = "midi_ddsp_v2_expression_context_forward_notes32"
        backward_name = "midi_ddsp_v2_expression_context_backward_notes32"
        forward = expression_context(forward_name, False)
        backward = expression_context(backward_name, True)
        context = np.concatenate([forward, backward], axis=-1)
        context = _pad_end(context, padded_length)

        name = "midi_ddsp_v2_expression_decode_notes32"
        controls = np.zeros((padded_length, 6), dtype=np.float32)
        previous = np.zeros((1, 6), dtype=np.float32)
        state1 = np.zeros((1, 128), dtype=np.float32)
        state2 = np.zeros((1, 128), dtype=np.float32)
        timings = self.timings.setdefault(name, [])
        with self.bundle.component(name).open(self.device_id) as runner:
            for index, start in enumerate(range(0, padded_length, block)):
                result = _timed_infer(
                    runner,
                    {
                        "context": context[start : start + block][None, ...],
                        "note_pitch": pitch[start : start + block][None, :],
                        "previous_controls": previous,
                        "state1_in": state1,
                        "state2_in": state2,
                    },
                    timings,
                )
                controls[start : start + block] = result["expression_controls"][0]
                previous = result["previous_controls_out"]
                state1 = result["state1_out"]
                state2 = result["state2_out"]
                self._notify(name, index + 1, padded_length // block)
        return np.clip(controls[:count], 0.0, 1.0)

    @staticmethod
    def relative_position(onsets: np.ndarray, q_pitch: np.ndarray) -> np.ndarray:
        positions = np.zeros((len(onsets), 1), dtype=np.float32)
        starts = np.flatnonzero(onsets)
        pitches = np.asarray(q_pitch).reshape(-1)
        for index, start in enumerate(starts):
            end = int(starts[index + 1]) if index + 1 < len(starts) else len(onsets)
            length = end - int(start)
            if length > 0 and pitches[start] > 0:
                positions[start:end, 0] = np.arange(1, length + 1) / length
        return positions

    def synthesis_parameters(self, features: object) -> tuple[np.ndarray, ...]:
        block = self.bundle.synthesis_block
        frame_count = int(getattr(features, "frames"))
        padded_length = _padded_length(frame_count, block)
        conditioning = _pad_end(
            np.asarray(getattr(features, "conditioning"), dtype=np.float32),
            padded_length,
        )
        q_pitch = _pad_end(
            np.asarray(getattr(features, "q_pitch"), dtype=np.float32), padded_length
        )
        onsets = _pad_end(
            np.asarray(getattr(features, "onsets"), dtype=np.int64)[:, None],
            padded_length,
        )[:, 0]
        offsets = _pad_end(
            np.asarray(getattr(features, "offsets"), dtype=np.int64)[:, None],
            padded_length,
        )[:, 0]
        relative = self.relative_position(onsets, q_pitch)
        instrument = np.asarray([int(getattr(features, "instrument_id"))], dtype=np.int64)

        precondition_name = "midi_ddsp_v2_synthesis_precondition_frames64"
        z_midi = np.zeros((padded_length, 320), dtype=np.float32)
        timings = self.timings.setdefault(precondition_name, [])
        with self.bundle.component(precondition_name).open(self.device_id) as runner:
            for index, start in enumerate(range(0, padded_length, block)):
                feeds = {
                    name: conditioning[start : start + block, channel][None, :, None]
                    for channel, name in enumerate(CONDITIONING_NAMES)
                }
                feeds.update(
                    {
                        "q_pitch": q_pitch[start : start + block][None, ...],
                        "onsets": onsets[start : start + block][None, :],
                        "offsets": offsets[start : start + block][None, :],
                        "relative_position": relative[start : start + block][None, ...],
                        "instrument_id": instrument,
                    }
                )
                result = _timed_infer(runner, feeds, timings)
                z_midi[start : start + block] = result["z_midi"][0]
                self._notify(precondition_name, index + 1, padded_length // block)
        z_midi[frame_count:] = 0.0

        forward = self._context_pass(
            "midi_ddsp_v2_synthesis_context_forward_frames64",
            z_midi[:frame_count],
            256,
            reverse=False,
            input_name="z_midi",
        )
        backward = self._context_pass(
            "midi_ddsp_v2_synthesis_context_backward_frames64",
            z_midi[:frame_count],
            256,
            reverse=True,
            input_name="z_midi",
        )
        context = _pad_end(np.concatenate([forward, backward], axis=-1), padded_length)

        rng = np.random.default_rng(self.seed)
        uniform = rng.uniform(
            np.finfo(np.float32).eps,
            1.0 - np.finfo(np.float32).eps,
            (padded_length, F0_BINS),
        ).astype(np.float32)
        gumbel = (-np.log(-np.log(uniform))).astype(np.float32)
        f0_hz = np.zeros((padded_length, 1), dtype=np.float32)
        f0_midi = np.zeros((padded_length, 1), dtype=np.float32)
        sampled_bins = np.zeros(padded_length, dtype=np.int64)
        previous = np.zeros((1, F0_BINS), dtype=np.float32)
        state1 = np.zeros((1, 256), dtype=np.float32)
        state2 = np.zeros((1, 256), dtype=np.float32)
        f0_name = "midi_ddsp_v2_synthesis_f0_decode_frames64"
        timings = self.timings.setdefault(f0_name, [])
        with self.bundle.component(f0_name).open(self.device_id) as runner:
            for index, start in enumerate(range(0, padded_length, block)):
                result = _timed_infer(
                    runner,
                    {
                        "context": context[start : start + block][None, ...],
                        "q_pitch": q_pitch[start : start + block][None, ...],
                        "gumbel": gumbel[start : start + block][None, ...],
                        "previous_f0": previous,
                        "state1_in": state1,
                        "state2_in": state2,
                    },
                    timings,
                )
                f0_hz[start : start + block] = result["f0_hz"][0]
                f0_midi[start : start + block] = result["f0_midi"][0]
                sampled_bins[start : start + block] = result["sampled_bins"][0]
                previous = result["previous_f0_out"]
                state1 = result["state1_out"]
                state2 = result["state2_out"]
                self._notify(f0_name, index + 1, padded_length // block)
        f0_hz[frame_count:] = 0.0
        f0_midi[frame_count:] = 0.0

        timbre_max_frames = self.bundle.timbre_max_frames
        if frame_count > timbre_max_frames:
            max_seconds = timbre_max_frames / 250.0
            raise ValueError(
                f"MIDI-DDSP timbre input is limited to {timbre_max_frames} "
                f"frames ({max_seconds:.3f} seconds)"
            )
        z_full = np.zeros((timbre_max_frames, 320), dtype=np.float32)
        f0_full = np.zeros((timbre_max_frames, 1), dtype=np.float32)
        z_full[:frame_count] = z_midi[:frame_count]
        f0_full[:frame_count] = f0_midi[:frame_count]
        timbre_name = f"midi_ddsp_v2_synthesis_timbre_frames{timbre_max_frames}"
        timings = self.timings.setdefault(timbre_name, [])
        with self.bundle.component(timbre_name).open(self.device_id) as runner:
            result = _timed_infer(
                runner,
                {
                    "z_midi": z_full[None, ...],
                    "f0_midi": f0_full[None, ...],
                    "valid_frames": np.asarray([frame_count], dtype=np.int64),
                },
                timings,
            )
        amplitudes = result["amplitudes"][0, :frame_count]
        harmonics = result["harmonic_distribution"][0, :frame_count]
        noise = result["noise_magnitudes"][0, :frame_count]
        self._notify(timbre_name, 1, 1)
        return (
            f0_hz[:frame_count],
            f0_midi[:frame_count],
            amplitudes,
            harmonics,
            noise,
            sampled_bins[:frame_count],
        )

    def run(self, tokens: Sequence[object], features_factory, instrument_id: int) -> StatefulParameters:
        runtime_session = getattr(self.bundle, "runtime_session", None)
        session = (
            runtime_session(self.device_id)
            if runtime_session is not None
            else nullcontext()
        )
        with session:
            controls = self.expression_controls(tokens, instrument_id)
            features = features_factory(tokens, controls, instrument_id)
            f0_hz, f0_midi, amplitudes, harmonics, noise, sampled = (
                self.synthesis_parameters(features)
            )
        metrics = {
            "architecture": "stateful-v2",
            "seed": self.seed,
            "component_timings_ms": {
                name: {
                    "count": len(values),
                    "mean": float(np.mean(values)) if values else 0.0,
                    "p95": float(np.percentile(values, 95)) if values else 0.0,
                    "max": float(np.max(values)) if values else 0.0,
                }
                for name, values in self.timings.items()
            },
            "tensor_sha256": {
                "expression_controls": array_sha256(controls),
                "f0_hz": array_sha256(f0_hz),
                "f0_midi": array_sha256(f0_midi),
                "amplitudes": array_sha256(amplitudes),
                "harmonic_distribution": array_sha256(harmonics),
                "noise_magnitudes": array_sha256(noise),
                "sampled_bins": array_sha256(sampled),
            },
        }
        return StatefulParameters(
            controls=controls,
            f0_hz=f0_hz,
            f0_midi=f0_midi,
            amplitudes=amplitudes,
            harmonic_distribution=harmonics,
            noise_magnitudes=noise,
            sampled_bins=sampled,
            metrics=metrics,
        )


class BatchedStatefulMidiDdspInference:
    """Run independent monophonic voices through one static-batch OM set."""

    def __init__(
        self,
        bundle: RuntimeBundle,
        voice_batch_size: int,
        device_id: int = 0,
        progress: Callable[[str, int, int], None] | None = None,
    ) -> None:
        if voice_batch_size not in bundle.voice_batch_sizes:
            raise ValueError(f"Bundle does not provide batch {voice_batch_size}")
        self.bundle = bundle
        self.voice_batch_size = int(voice_batch_size)
        self.device_id = int(device_id)
        self.progress = progress
        self.timings: dict[str, list[float]] = {}
        self.model_load_timings: dict[str, list[float]] = {}

    def _notify(self, stage: str, completed: int, total: int) -> None:
        if self.progress is not None:
            self.progress(stage, completed, total)

    @contextmanager
    def _runner(self, component_name: str):
        started = time.perf_counter()
        manager = self.bundle.component(
            component_name, self.voice_batch_size
        ).open(self.device_id)
        with manager as runner:
            self.model_load_timings.setdefault(component_name, []).append(
                (time.perf_counter() - started) * 1000.0
            )
            yield runner

    @staticmethod
    def _active_mask(block_counts: np.ndarray, step: int) -> np.ndarray:
        return step < block_counts

    @staticmethod
    def _update_rows(
        previous: np.ndarray, current: np.ndarray, active: np.ndarray
    ) -> np.ndarray:
        shape = (len(active),) + (1,) * (previous.ndim - 1)
        return np.where(active.reshape(shape), current, previous)

    def _context_pass(
        self,
        component_name: str,
        values: Sequence[np.ndarray],
        state_width: int,
        *,
        reverse: bool,
        input_name: str,
    ) -> list[np.ndarray]:
        block = (
            self.bundle.expression_block
            if state_width == 128
            else self.bundle.synthesis_block
        )
        lengths = np.asarray([len(value) for value in values], dtype=np.int64)
        padded_lengths = np.asarray(
            [_padded_length(int(length), block) for length in lengths], dtype=np.int64
        )
        block_counts = np.pad(
            padded_lengths // block,
            (0, self.voice_batch_size - len(values)),
        )
        max_blocks = int(block_counts.max(initial=1))
        batch_size = self.voice_batch_size
        value_width = int(values[0].shape[-1])
        contexts = [
            np.zeros((int(padded), state_width), dtype=np.float32)
            for padded in padded_lengths
        ]
        padded_values = []
        for value, padded_length in zip(values, padded_lengths.tolist()):
            if reverse:
                padded_value, _trim = _pad_start(value, int(padded_length))
            else:
                padded_value = _pad_end(value, int(padded_length))
            padded_values.append(padded_value)
        state = np.zeros((batch_size, state_width), dtype=np.float32)
        timings = self.timings.setdefault(component_name, [])
        with self._runner(component_name) as runner:
            for step in range(max_blocks):
                feeds = np.zeros((batch_size, block, value_width), dtype=np.float32)
                targets: list[int | None] = [None] * batch_size
                active = self._active_mask(block_counts, step)
                for row in np.flatnonzero(active):
                    padded_length = int(padded_lengths[row])
                    if reverse:
                        start = padded_length - (step + 1) * block
                    else:
                        start = step * block
                    feeds[row] = padded_values[row][start : start + block]
                    targets[row] = start
                result = _timed_infer(
                    runner,
                    {input_name: feeds, "state_in": state},
                    timings,
                )
                for row in np.flatnonzero(active):
                    start = int(targets[row])
                    contexts[row][start : start + block] = result["context"][row]
                state = self._update_rows(state, result["state_out"], active)
                self._notify(component_name, step + 1, max_blocks)
        output = []
        for context, length, padded_length in zip(
            contexts, lengths.tolist(), padded_lengths.tolist()
        ):
            trim = padded_length - length if reverse else 0
            output.append(context[trim : trim + length])
        return output

    def expression_controls(
        self,
        tokens_all: Sequence[Sequence[object]],
        instrument_ids: Sequence[int],
    ) -> list[np.ndarray]:
        real_count = len(tokens_all)
        if real_count <= 0 or real_count > self.voice_batch_size:
            raise ValueError("Invalid number of voices for the selected batch")
        if len(instrument_ids) != real_count:
            raise ValueError("instrument_ids must match tokens_all")
        block = self.bundle.expression_block
        counts = np.asarray([len(tokens) for tokens in tokens_all], dtype=np.int64)
        padded_lengths = np.asarray(
            [_padded_length(int(count), block) for count in counts], dtype=np.int64
        )
        block_counts = np.pad(
            padded_lengths // block,
            (0, self.voice_batch_size - real_count),
        )
        max_padded = int(padded_lengths.max(initial=block))
        max_blocks = max_padded // block
        pitch = np.zeros((self.voice_batch_size, max_padded), dtype=np.int64)
        length = np.zeros(
            (self.voice_batch_size, max_padded, 1), dtype=np.float32
        )
        instrument = np.zeros(self.voice_batch_size, dtype=np.int64)
        for row, (tokens, instrument_id) in enumerate(
            zip(tokens_all, instrument_ids)
        ):
            instrument[row] = int(instrument_id)
            for index, token in enumerate(tokens):
                pitch[row, index] = int(getattr(token, "pitch"))
                length[row, index, 0] = (
                    float(getattr(token, "length_frames")) / 250.0
                )

        def context_pass(name: str, reverse: bool) -> list[np.ndarray]:
            contexts = [
                np.zeros((int(padded), 128), dtype=np.float32)
                for padded in padded_lengths
            ]
            state = np.zeros((self.voice_batch_size, 128), dtype=np.float32)
            timings = self.timings.setdefault(name, [])
            with self._runner(name) as runner:
                for step in range(max_blocks):
                    feed_pitch = np.zeros(
                        (self.voice_batch_size, block), dtype=np.int64
                    )
                    feed_length = np.zeros(
                        (self.voice_batch_size, block, 1), dtype=np.float32
                    )
                    targets: list[int | None] = [None] * self.voice_batch_size
                    active = self._active_mask(block_counts, step)
                    for row in np.flatnonzero(active):
                        padded_length = int(padded_lengths[row])
                        count = int(counts[row])
                        if reverse:
                            start = padded_length - (step + 1) * block
                            trim = padded_length - count
                            row_pitch = np.pad(pitch[row, :count], (trim, 0))
                            row_length = np.pad(
                                length[row, :count], ((trim, 0), (0, 0))
                            )
                        else:
                            start = step * block
                            row_pitch = pitch[row, :padded_length]
                            row_length = length[row, :padded_length]
                        feed_pitch[row] = row_pitch[start : start + block]
                        feed_length[row] = row_length[start : start + block]
                        targets[row] = start
                    result = _timed_infer(
                        runner,
                        {
                            "note_pitch": feed_pitch,
                            "note_length": feed_length,
                            "instrument_id": instrument,
                            "state_in": state,
                        },
                        timings,
                    )
                    for row in np.flatnonzero(active):
                        start = int(targets[row])
                        contexts[row][start : start + block] = result["context"][row]
                    state = self._update_rows(state, result["state_out"], active)
                    self._notify(name, step + 1, max_blocks)
            output = []
            for row in range(real_count):
                trim = int(padded_lengths[row] - counts[row]) if reverse else 0
                output.append(
                    contexts[row][trim : trim + int(counts[row])]
                )
            return output

        forward_name = "midi_ddsp_v2_expression_context_forward_notes32"
        backward_name = "midi_ddsp_v2_expression_context_backward_notes32"
        forward = context_pass(forward_name, False)
        backward = context_pass(backward_name, True)
        context = np.zeros(
            (self.voice_batch_size, max_padded, 256), dtype=np.float32
        )
        for row in range(real_count):
            count = int(counts[row])
            context[row, :count] = np.concatenate(
                [forward[row], backward[row]], axis=-1
            )

        name = "midi_ddsp_v2_expression_decode_notes32"
        controls = np.zeros(
            (self.voice_batch_size, max_padded, 6), dtype=np.float32
        )
        previous = np.zeros((self.voice_batch_size, 6), dtype=np.float32)
        state1 = np.zeros((self.voice_batch_size, 128), dtype=np.float32)
        state2 = np.zeros((self.voice_batch_size, 128), dtype=np.float32)
        timings = self.timings.setdefault(name, [])
        with self._runner(name) as runner:
            for step in range(max_blocks):
                start = step * block
                active = self._active_mask(block_counts, step)
                result = _timed_infer(
                    runner,
                    {
                        "context": context[:, start : start + block],
                        "note_pitch": pitch[:, start : start + block],
                        "previous_controls": previous,
                        "state1_in": state1,
                        "state2_in": state2,
                    },
                    timings,
                )
                controls[:, start : start + block] = result[
                    "expression_controls"
                ]
                previous = self._update_rows(
                    previous, result["previous_controls_out"], active
                )
                state1 = self._update_rows(state1, result["state1_out"], active)
                state2 = self._update_rows(state2, result["state2_out"], active)
                self._notify(name, step + 1, max_blocks)
        return [
            np.clip(controls[row, : int(counts[row])], 0.0, 1.0)
            for row in range(real_count)
        ]

    def synthesis_parameters(
        self,
        features_all: Sequence[object],
        seeds: Sequence[int],
    ) -> list[tuple[np.ndarray, ...]]:
        real_count = len(features_all)
        if real_count <= 0 or real_count > self.voice_batch_size:
            raise ValueError("Invalid number of features for the selected batch")
        if len(seeds) != real_count:
            raise ValueError("seeds must match features_all")
        block = self.bundle.synthesis_block
        frame_counts = np.asarray(
            [int(getattr(features, "frames")) for features in features_all],
            dtype=np.int64,
        )
        if np.any(frame_counts > self.bundle.timbre_max_frames):
            raise ValueError(
                f"MIDI-DDSP timbre input is limited to {self.bundle.timbre_max_frames} frames"
            )
        padded_lengths = np.asarray(
            [_padded_length(int(count), block) for count in frame_counts],
            dtype=np.int64,
        )
        block_counts = np.pad(
            padded_lengths // block,
            (0, self.voice_batch_size - real_count),
        )
        max_padded = int(padded_lengths.max(initial=block))
        max_blocks = max_padded // block
        batch_size = self.voice_batch_size
        conditioning = np.zeros((batch_size, max_padded, 6), dtype=np.float32)
        q_pitch = np.zeros((batch_size, max_padded, 1), dtype=np.float32)
        onsets = np.zeros((batch_size, max_padded), dtype=np.int64)
        offsets = np.zeros((batch_size, max_padded), dtype=np.int64)
        relative = np.zeros((batch_size, max_padded, 1), dtype=np.float32)
        instrument = np.zeros(batch_size, dtype=np.int64)
        for row, features in enumerate(features_all):
            count = int(frame_counts[row])
            padded = int(padded_lengths[row])
            conditioning[row, :count] = np.asarray(
                getattr(features, "conditioning"), dtype=np.float32
            )
            q_pitch[row, :count] = np.asarray(
                getattr(features, "q_pitch"), dtype=np.float32
            )
            onsets[row, :count] = np.asarray(
                getattr(features, "onsets"), dtype=np.int64
            )
            offsets[row, :count] = np.asarray(
                getattr(features, "offsets"), dtype=np.int64
            )
            relative[row, :padded] = StatefulMidiDdspInference.relative_position(
                onsets[row, :padded], q_pitch[row, :padded]
            )
            instrument[row] = int(getattr(features, "instrument_id"))

        precondition_name = "midi_ddsp_v2_synthesis_precondition_frames64"
        z_midi = np.zeros((batch_size, max_padded, 320), dtype=np.float32)
        timings = self.timings.setdefault(precondition_name, [])
        with self._runner(precondition_name) as runner:
            for step in range(max_blocks):
                start = step * block
                feeds = {
                    name: conditioning[:, start : start + block, channel : channel + 1]
                    for channel, name in enumerate(CONDITIONING_NAMES)
                }
                feeds.update(
                    {
                        "q_pitch": q_pitch[:, start : start + block],
                        "onsets": onsets[:, start : start + block],
                        "offsets": offsets[:, start : start + block],
                        "relative_position": relative[:, start : start + block],
                        "instrument_id": instrument,
                    }
                )
                result = _timed_infer(runner, feeds, timings)
                z_midi[:, start : start + block] = result["z_midi"]
                self._notify(precondition_name, step + 1, max_blocks)
        for row in range(real_count):
            z_midi[row, int(frame_counts[row]) :] = 0.0

        values = [z_midi[row, : int(frame_counts[row])] for row in range(real_count)]
        forward = self._context_pass(
            "midi_ddsp_v2_synthesis_context_forward_frames64",
            values,
            256,
            reverse=False,
            input_name="z_midi",
        )
        backward = self._context_pass(
            "midi_ddsp_v2_synthesis_context_backward_frames64",
            values,
            256,
            reverse=True,
            input_name="z_midi",
        )
        context = np.zeros((batch_size, max_padded, 512), dtype=np.float32)
        for row in range(real_count):
            count = int(frame_counts[row])
            context[row, :count] = np.concatenate(
                [forward[row], backward[row]], axis=-1
            )

        gumbel = np.zeros((batch_size, max_padded, F0_BINS), dtype=np.float32)
        for row, seed in enumerate(seeds):
            rng = np.random.default_rng(int(seed))
            padded = int(padded_lengths[row])
            uniform = rng.uniform(
                np.finfo(np.float32).eps,
                1.0 - np.finfo(np.float32).eps,
                (padded, F0_BINS),
            ).astype(np.float32)
            gumbel[row, :padded] = (-np.log(-np.log(uniform))).astype(np.float32)
        f0_hz = np.zeros((batch_size, max_padded, 1), dtype=np.float32)
        f0_midi = np.zeros((batch_size, max_padded, 1), dtype=np.float32)
        sampled_bins = np.zeros((batch_size, max_padded), dtype=np.int64)
        previous = np.zeros((batch_size, F0_BINS), dtype=np.float32)
        state1 = np.zeros((batch_size, 256), dtype=np.float32)
        state2 = np.zeros((batch_size, 256), dtype=np.float32)
        f0_name = "midi_ddsp_v2_synthesis_f0_decode_frames64"
        timings = self.timings.setdefault(f0_name, [])
        with self._runner(f0_name) as runner:
            for step in range(max_blocks):
                start = step * block
                active = self._active_mask(block_counts, step)
                result = _timed_infer(
                    runner,
                    {
                        "context": context[:, start : start + block],
                        "q_pitch": q_pitch[:, start : start + block],
                        "gumbel": gumbel[:, start : start + block],
                        "previous_f0": previous,
                        "state1_in": state1,
                        "state2_in": state2,
                    },
                    timings,
                )
                f0_hz[:, start : start + block] = result["f0_hz"]
                f0_midi[:, start : start + block] = result["f0_midi"]
                sampled_bins[:, start : start + block] = result["sampled_bins"]
                previous = self._update_rows(
                    previous, result["previous_f0_out"], active
                )
                state1 = self._update_rows(state1, result["state1_out"], active)
                state2 = self._update_rows(state2, result["state2_out"], active)
                self._notify(f0_name, step + 1, max_blocks)
        for row in range(real_count):
            f0_hz[row, int(frame_counts[row]) :] = 0.0
            f0_midi[row, int(frame_counts[row]) :] = 0.0

        timbre_max = self.bundle.timbre_max_frames
        z_full = np.zeros((batch_size, timbre_max, 320), dtype=np.float32)
        f0_full = np.zeros((batch_size, timbre_max, 1), dtype=np.float32)
        for row in range(real_count):
            count = int(frame_counts[row])
            z_full[row, :count] = z_midi[row, :count]
            f0_full[row, :count] = f0_midi[row, :count]
        timbre_name = f"midi_ddsp_v2_synthesis_timbre_frames{timbre_max}"
        timings = self.timings.setdefault(timbre_name, [])
        with self._runner(timbre_name) as runner:
            result = _timed_infer(
                runner,
                {
                    "z_midi": z_full,
                    "f0_midi": f0_full,
                    "valid_frames": np.pad(
                        frame_counts,
                        (0, batch_size - real_count),
                    ).astype(np.int64),
                },
                timings,
            )
        self._notify(timbre_name, 1, 1)
        output = []
        for row in range(real_count):
            count = int(frame_counts[row])
            output.append(
                (
                    f0_hz[row, :count].copy(),
                    f0_midi[row, :count].copy(),
                    result["amplitudes"][row, :count].copy(),
                    result["harmonic_distribution"][row, :count].copy(),
                    result["noise_magnitudes"][row, :count].copy(),
                    sampled_bins[row, :count].copy(),
                )
            )
        return output

    def run(
        self,
        tokens_all: Sequence[Sequence[object]],
        features_factory,
        instrument_ids: Sequence[int],
        seeds: Sequence[int],
    ) -> list[StatefulParameters]:
        runtime_session = getattr(self.bundle, "runtime_session", None)
        session = (
            runtime_session(self.device_id)
            if runtime_session is not None
            else nullcontext()
        )
        with session:
            controls_all = self.expression_controls(tokens_all, instrument_ids)
            features_all = [
                features_factory(tokens, controls, instrument_id)
                for tokens, controls, instrument_id in zip(
                    tokens_all, controls_all, instrument_ids
                )
            ]
            synthesis_all = self.synthesis_parameters(features_all, seeds)

        component_metrics = {
            name: {
                "count": len(values),
                "mean": float(np.mean(values)) if values else 0.0,
                "p95": float(np.percentile(values, 95)) if values else 0.0,
                "max": float(np.max(values)) if values else 0.0,
            }
            for name, values in self.timings.items()
        }
        load_metrics = {
            name: float(np.sum(values))
            for name, values in self.model_load_timings.items()
        }
        output = []
        for row, (controls, synthesis, seed) in enumerate(
            zip(controls_all, synthesis_all, seeds)
        ):
            f0_hz, f0_midi, amplitudes, harmonics, noise, sampled = synthesis
            metrics = {
                "architecture": "stateful-v2-batched",
                "seed": int(seed),
                "voice_batch_size": self.voice_batch_size,
                "batch_member_index": row,
                "component_timings_ms": component_metrics,
                "model_load_timings_ms": load_metrics,
                "tensor_sha256": {
                    "expression_controls": array_sha256(controls),
                    "f0_hz": array_sha256(f0_hz),
                    "f0_midi": array_sha256(f0_midi),
                    "amplitudes": array_sha256(amplitudes),
                    "harmonic_distribution": array_sha256(harmonics),
                    "noise_magnitudes": array_sha256(noise),
                    "sampled_bins": array_sha256(sampled),
                },
            }
            output.append(
                StatefulParameters(
                    controls=controls,
                    f0_hz=f0_hz,
                    f0_midi=f0_midi,
                    amplitudes=amplitudes,
                    harmonic_distribution=harmonics,
                    noise_magnitudes=noise,
                    sampled_bins=sampled,
                    metrics=metrics,
                )
            )
        return output
