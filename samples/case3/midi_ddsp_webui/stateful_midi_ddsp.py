from __future__ import annotations

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

        halo = self.bundle.timbre_halo
        window = block + 2 * halo
        z_with_halo = np.pad(z_midi, ((halo, halo), (0, 0)))
        f0_with_halo = np.pad(f0_midi, ((halo, halo), (0, 0)))
        amplitudes = np.zeros((padded_length, 1), dtype=np.float32)
        harmonics = np.zeros((padded_length, 60), dtype=np.float32)
        noise = np.zeros((padded_length, 65), dtype=np.float32)
        timbre_name = f"midi_ddsp_v2_synthesis_timbre_frames{window}"
        timings = self.timings.setdefault(timbre_name, [])
        with self.bundle.component(timbre_name).open(self.device_id) as runner:
            for index, start in enumerate(range(0, padded_length, block)):
                result = _timed_infer(
                    runner,
                    {
                        "z_midi": z_with_halo[start : start + window][None, ...],
                        "f0_midi": f0_with_halo[start : start + window][None, ...],
                    },
                    timings,
                )
                core = slice(halo, halo + block)
                amplitudes[start : start + block] = result["amplitudes"][0, core]
                harmonics[start : start + block] = result[
                    "harmonic_distribution"
                ][0, core]
                noise[start : start + block] = result["noise_magnitudes"][0, core]
                self._notify(timbre_name, index + 1, padded_length // block)
        return (
            f0_hz[:frame_count],
            f0_midi[:frame_count],
            amplitudes[:frame_count],
            harmonics[:frame_count],
            noise[:frame_count],
            sampled_bins[:frame_count],
        )

    def run(self, tokens: Sequence[object], features_factory, instrument_id: int) -> StatefulParameters:
        controls = self.expression_controls(tokens, instrument_id)
        features = features_factory(tokens, controls, instrument_id)
        f0_hz, f0_midi, amplitudes, harmonics, noise, sampled = self.synthesis_parameters(
            features
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
