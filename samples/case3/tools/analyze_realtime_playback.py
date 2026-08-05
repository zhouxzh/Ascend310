#!/usr/bin/env python3
"""Capture and analyze the exact audio blocks sent to the output callback."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import sys
import time

import mido
import numpy as np


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from realtime_ddsp import (  # noqa: E402
    DEFAULT_MODEL,
    MODEL_FRAME_RATE,
    MODEL_HOP_SIZE,
    MODEL_SAMPLE_RATE,
    LivePlayer,
    RealtimeSynthEngine,
    parse_audio_device,
    write_wav,
)


class CapturingLivePlayer(LivePlayer):
    """Live player with a preallocated, non-blocking software output tap."""

    def __init__(self, *args, capture_capacity: int, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.capture_block_size = round(
            MODEL_HOP_SIZE * self.engine.output_sample_rate / MODEL_SAMPLE_RATE
        )
        self.capture_blocks = np.zeros(
            (capture_capacity, self.capture_block_size), dtype=np.float32
        )
        self.callback_times = np.zeros(capture_capacity, dtype=np.float64)
        self.callback_frames = np.zeros(capture_capacity, dtype=np.int32)
        self.callback_queue_depth = np.zeros(capture_capacity, dtype=np.int32)
        self.callback_status_underflow = np.zeros(capture_capacity, dtype=np.bool_)
        self.callback_had_audio = np.zeros(capture_capacity, dtype=np.bool_)
        self.callback_render_index = np.full(capture_capacity, -1, dtype=np.int32)
        self.capture_count = 0
        self.capture_overflow = 0

    def _audio_callback(self, outdata, frames, time_info, status) -> None:
        callback_time = time.perf_counter()
        queue_depth = self.blocks.qsize()
        with self._stats_lock:
            played_before = self.played_blocks
        super()._audio_callback(outdata, frames, time_info, status)
        with self._stats_lock:
            had_audio = self.played_blocks > played_before

        index = self.capture_count
        self.capture_count += 1
        if index >= self.capture_blocks.shape[0]:
            self.capture_overflow += 1
            return

        self.callback_times[index] = callback_time
        self.callback_frames[index] = frames
        self.callback_queue_depth[index] = queue_depth
        self.callback_status_underflow[index] = bool(
            getattr(status, "output_underflow", False)
        )
        self.callback_had_audio[index] = had_audio
        if had_audio:
            self.callback_render_index[index] = played_before
        copy_size = min(frames, self.capture_block_size, outdata.shape[0])
        if copy_size > 0:
            np.copyto(self.capture_blocks[index, :copy_size], outdata[:copy_size, 0])

    def captured_data(self) -> tuple[np.ndarray, dict[str, np.ndarray]]:
        count = min(self.capture_count, self.capture_blocks.shape[0])
        metadata = {
            "times": self.callback_times[:count].copy(),
            "frames": self.callback_frames[:count].copy(),
            "queue_depth": self.callback_queue_depth[:count].copy(),
            "status_underflow": self.callback_status_underflow[:count].copy(),
            "had_audio": self.callback_had_audio[:count].copy(),
            "render_index": self.callback_render_index[:count].copy(),
        }
        return self.capture_blocks[:count].copy(), metadata


def load_midi_events(midi_path: Path) -> tuple[list[tuple[float, object]], float]:
    midi_file = mido.MidiFile(str(midi_path))
    events: list[tuple[float, object]] = []
    current_time = 0.0
    for message in midi_file:
        current_time += float(message.time)
        if not message.is_meta:
            events.append((current_time, message))
    return events, current_time


def load_model_metadata(model_path: Path) -> dict[str, object]:
    metadata_path = model_path.parent / "metadata.json"
    if not metadata_path.is_file():
        return {}
    try:
        all_metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    instrument = model_path.stem.split("_force_fp16")[0].split("_mixed_float16")[0]
    candidate = all_metadata.get(instrument, {})
    return candidate if isinstance(candidate, dict) else {}


def _summary(values: np.ndarray) -> dict[str, float]:
    if values.size == 0:
        return {"min": 0.0, "mean": 0.0, "p95": 0.0, "p99": 0.0, "max": 0.0}
    return {
        "min": float(np.min(values)),
        "mean": float(np.mean(values)),
        "p95": float(np.percentile(values, 95)),
        "p99": float(np.percentile(values, 99)),
        "max": float(np.max(values)),
    }


def find_boundary_discontinuities(
    blocks: np.ndarray,
    minimum_jump: float = 0.005,
    local_ratio: float = 4.0,
) -> tuple[np.ndarray, np.ndarray, list[dict[str, float | int]]]:
    if blocks.shape[0] < 2 or blocks.shape[1] < 2:
        return np.zeros(0), np.zeros(0), []
    samples = blocks.reshape(-1)
    differences = np.abs(np.diff(samples))
    block_size = blocks.shape[1]
    boundary_indices = np.arange(1, blocks.shape[0]) * block_size - 1
    boundary_jumps = differences[boundary_indices]
    scores = np.zeros_like(boundary_jumps)
    discontinuities: list[dict[str, float | int]] = []

    for block_index, (difference_index, jump) in enumerate(
        zip(boundary_indices, boundary_jumps), start=1
    ):
        start = max(0, difference_index - 64)
        stop = min(differences.size, difference_index + 65)
        local = np.concatenate(
            [differences[start:difference_index], differences[difference_index + 1 : stop]]
        )
        local_reference = float(np.percentile(local, 95)) if local.size else 0.0
        score = float(jump) / max(local_reference, 1e-6)
        scores[block_index - 1] = score
        if float(jump) >= minimum_jump and score >= local_ratio:
            discontinuities.append(
                {
                    "block": block_index,
                    "jump": float(jump),
                    "local_p95": local_reference,
                    "ratio": score,
                }
            )
    return boundary_jumps, scores, discontinuities


def analyze_capture(
    blocks: np.ndarray,
    metadata: dict[str, np.ndarray],
    sample_rate: int,
    capture_overflow: int = 0,
    model_metadata: dict[str, object] | None = None,
) -> dict[str, object]:
    block_count = int(blocks.shape[0])
    block_size = int(blocks.shape[1]) if blocks.ndim == 2 else 0
    samples = blocks.reshape(-1) if block_count else np.zeros(0, dtype=np.float32)
    block_rms = (
        np.sqrt(np.mean(np.square(blocks, dtype=np.float64), axis=1))
        if block_count
        else np.zeros(0)
    )
    block_peak = (
        np.max(np.abs(blocks), axis=1) if block_count else np.zeros(0)
    )
    boundary_jumps, boundary_scores, discontinuities = find_boundary_discontinuities(
        blocks
    )

    callback_times = metadata["times"]
    callback_intervals_ms = np.diff(callback_times) * 1000.0
    expected_interval_ms = block_size / sample_rate * 1000.0 if sample_rate else 0.0
    late_callbacks = (
        callback_intervals_ms > expected_interval_ms * 1.5
        if callback_intervals_ms.size
        else np.zeros(0, dtype=np.bool_)
    )
    frame_mismatches = metadata["frames"] != block_size
    transport_silence = np.logical_not(metadata["had_audio"])
    held_voice_count = metadata.get(
        "held_voice_count", np.zeros(block_count, dtype=np.int32)
    )
    note_on_count = metadata.get(
        "note_on_count", np.zeros(block_count, dtype=np.int32)
    )
    total_voice_count = metadata.get(
        "total_voice_count", np.zeros(block_count, dtype=np.int32)
    )
    minimum_midi_note = metadata.get(
        "minimum_midi_note", np.full(block_count, -1, dtype=np.int32)
    )
    maximum_midi_note = metadata.get(
        "maximum_midi_note", np.full(block_count, -1, dtype=np.int32)
    )
    active_silent = np.logical_and(block_peak <= 1e-6, held_voice_count > 0)
    active_near_silent = np.logical_and(block_rms <= 1e-4, held_voice_count > 0)
    # DDSP-VST's default 100 ms attack intentionally begins near silence. Do
    # not flag those control frames as a synthesis dropout.
    attack_grace_blocks = max(1, math.ceil(sample_rate * 0.10 / max(block_size, 1)))
    in_attack = np.zeros(block_count, dtype=np.bool_)
    remaining_attack_blocks = 0
    for index, count in enumerate(note_on_count):
        if count > 0:
            remaining_attack_blocks = attack_grace_blocks
        if remaining_attack_blocks > 0:
            in_attack[index] = True
            remaining_attack_blocks -= 1
    active_near_silent = np.logical_and(active_near_silent, np.logical_not(in_attack))
    pitch_min = None
    pitch_max = None
    if model_metadata:
        pitch_min = model_metadata.get("mean_min_pitch_note")
        pitch_max = model_metadata.get("mean_max_pitch_note")
    out_of_range = np.zeros(block_count, dtype=np.bool_)
    if pitch_min is not None and pitch_max is not None:
        out_of_range = np.logical_and(
            held_voice_count > 0,
            np.logical_or(
                minimum_midi_note < float(pitch_min),
                maximum_midi_note > float(pitch_max),
            ),
        )

    active_dropout_details: list[dict[str, float | int | bool]] = []
    for index in np.flatnonzero(active_near_silent)[:100]:
        active_dropout_details.append(
            {
                "block": int(index),
                "time_seconds": float(index * block_size / sample_rate),
                "rms": float(block_rms[index]),
                "peak": float(block_peak[index]),
                "held_voices": int(held_voice_count[index]),
                "total_voices": int(total_voice_count[index]),
                "minimum_midi_note": int(minimum_midi_note[index]),
                "maximum_midi_note": int(maximum_midi_note[index]),
                "outside_model_pitch_range": bool(out_of_range[index]),
            }
        )
    exact_repeats = 0
    if block_count > 1:
        repeated = np.max(np.abs(np.diff(blocks, axis=0)), axis=1) <= 1e-8
        non_silent = np.logical_and(block_peak[:-1] > 1e-6, block_peak[1:] > 1e-6)
        exact_repeats = int(np.count_nonzero(np.logical_and(repeated, non_silent)))

    signal_rms = float(np.sqrt(np.mean(np.square(samples, dtype=np.float64)))) if samples.size else 0.0
    result: dict[str, object] = {
        "capture": {
            "blocks": block_count,
            "block_size": block_size,
            "samples": int(samples.size),
            "duration_seconds": float(samples.size / sample_rate) if sample_rate else 0.0,
            "overflow_callbacks": int(capture_overflow),
        },
        "transport": {
            "expected_callback_interval_ms": expected_interval_ms,
            "callback_interval_ms": _summary(callback_intervals_ms),
            "late_callbacks": int(np.count_nonzero(late_callbacks)),
            "late_callback_details": [
                {
                    "after_block": int(index),
                    "interval_ms": float(callback_intervals_ms[index]),
                }
                for index in np.flatnonzero(late_callbacks)[:100]
            ],
            "status_underflows": int(np.count_nonzero(metadata["status_underflow"])),
            "callbacks_without_audio": int(np.count_nonzero(transport_silence)),
            "frame_size_mismatches": int(np.count_nonzero(frame_mismatches)),
            "minimum_queue_depth": int(np.min(metadata["queue_depth"])) if block_count else 0,
        },
        "signal": {
            "peak": float(np.max(np.abs(samples))) if samples.size else 0.0,
            "rms": signal_rms,
            "silent_blocks": int(np.count_nonzero(block_peak <= 1e-6)),
            "silent_blocks_with_held_notes": int(np.count_nonzero(active_silent)),
            "near_silent_blocks_with_held_notes": int(
                np.count_nonzero(active_near_silent)
            ),
            "attack_grace_blocks": attack_grace_blocks,
            "active_blocks_outside_model_pitch_range": int(
                np.count_nonzero(out_of_range)
            ),
            "near_silent_blocks_outside_model_pitch_range": int(
                np.count_nonzero(np.logical_and(active_near_silent, out_of_range))
            ),
            "active_dropout_details": active_dropout_details,
            "exact_repeated_non_silent_blocks": exact_repeats,
            "block_rms": _summary(block_rms),
            "boundary_jump": _summary(boundary_jumps),
            "boundary_score": _summary(boundary_scores),
            "discontinuity_count": len(discontinuities),
            "discontinuities": discontinuities[:100],
        },
    }

    transport_problem = any(
        (
            result["transport"]["status_underflows"],
            result["transport"]["callbacks_without_audio"],
            result["transport"]["frame_size_mismatches"],
            capture_overflow,
        )
    )
    model_range_dropout = bool(np.any(np.logical_and(active_near_silent, out_of_range)))
    if transport_problem:
        conclusion = "transport_discontinuity_detected"
    elif model_range_dropout:
        conclusion = "model_range_dropout_detected"
    elif np.any(active_near_silent):
        conclusion = "synthesis_activity_dropout_detected"
    elif discontinuities:
        conclusion = "synthesis_boundary_discontinuity_detected"
    elif np.any(late_callbacks):
        conclusion = "callback_timing_jitter_detected"
    else:
        conclusion = "callback_signal_continuous"
    result["conclusion"] = conclusion
    return result


def run_capture(args: argparse.Namespace) -> tuple[CapturingLivePlayer, dict[str, object]]:
    events, midi_duration = load_midi_events(args.midi)
    model_metadata = load_model_metadata(args.model)
    engine = RealtimeSynthEngine(
        args.model,
        args.sample_rate,
        max_voices=args.max_voices,
    )
    event_index = 0
    timeout_seconds = max(5.0, args.prebuffer * 2.0)
    frame_period = MODEL_HOP_SIZE / MODEL_SAMPLE_RATE
    total_duration = midi_duration + max(0.0, args.tail)
    total_frames = max(1, math.ceil(total_duration * MODEL_FRAME_RATE))
    capture_capacity = (
        total_frames
        + math.ceil(timeout_seconds / frame_period)
        + args.prebuffer
        + 32
    )
    render_total_voices = np.zeros(capture_capacity, dtype=np.int32)
    render_held_voices = np.zeros(capture_capacity, dtype=np.int32)
    render_note_ons = np.zeros(capture_capacity, dtype=np.int32)
    render_minimum_note = np.full(capture_capacity, -1, dtype=np.int32)
    render_maximum_note = np.full(capture_capacity, -1, dtype=np.int32)

    def apply_events(frame_index: int) -> None:
        nonlocal event_index
        frame_time = frame_index / MODEL_FRAME_RATE
        while event_index < len(events) and events[event_index][0] <= frame_time + 1e-9:
            message = events[event_index][1]
            if (
                getattr(message, "type", None) == "note_on"
                and getattr(message, "velocity", 0) > 0
                and frame_index < capture_capacity
            ):
                render_note_ons[frame_index] += 1
            engine.midi.handle_message(message)
            event_index += 1
        if frame_index < capture_capacity:
            voices = list(engine.midi._voices.values())
            held_notes = [voice.note for voice in voices if voice.held]
            render_total_voices[frame_index] = len(voices)
            render_held_voices[frame_index] = len(held_notes)
            if held_notes:
                render_minimum_note[frame_index] = min(held_notes)
                render_maximum_note[frame_index] = max(held_notes)
    player = CapturingLivePlayer(
        engine,
        prebuffer_blocks=args.prebuffer,
        before_render=apply_events,
        output_device=parse_audio_device(args.audio_device),
        output_latency_seconds=args.audio_latency_ms / 1000.0,
        capture_capacity=capture_capacity,
    )
    player.start()
    print(
        f"[CAPTURE] Playing {args.midi} ({total_duration:.2f}s); "
        "recording callback output in memory"
    )
    deadline = time.monotonic() + total_duration + timeout_seconds
    try:
        while True:
            player.raise_worker_error()
            with player._stats_lock:
                played_blocks = player.played_blocks
            if played_blocks >= total_frames:
                break
            if time.monotonic() >= deadline:
                raise RuntimeError("Realtime capture timed out before all blocks played")
            time.sleep(0.01)
    except KeyboardInterrupt:
        print("[CAPTURE] Interrupted; analyzing captured prefix")
    finally:
        player.stop()

    blocks, metadata = player.captured_data()
    render_indices = metadata["render_index"]
    valid_render_indices = np.logical_and(
        render_indices >= 0, render_indices < capture_capacity
    )
    for key, source in (
        ("total_voice_count", render_total_voices),
        ("held_voice_count", render_held_voices),
        ("note_on_count", render_note_ons),
        ("minimum_midi_note", render_minimum_note),
        ("maximum_midi_note", render_maximum_note),
    ):
        default = -1 if "midi_note" in key else 0
        values = np.full(render_indices.shape, default, dtype=np.int32)
        values[valid_render_indices] = source[render_indices[valid_render_indices]]
        metadata[key] = values
    report = analyze_capture(
        blocks,
        metadata,
        args.sample_rate,
        capture_overflow=player.capture_overflow,
        model_metadata=model_metadata,
    )
    report["model_metadata"] = model_metadata
    note_on_values = [
        int(message.note)
        for _, message in events
        if getattr(message, "type", None) == "note_on"
        and getattr(message, "velocity", 0) > 0
    ]
    model_pitch_min = model_metadata.get("mean_min_pitch_note")
    model_pitch_max = model_metadata.get("mean_max_pitch_note")
    out_of_range_note_ons = 0
    if model_pitch_min is not None and model_pitch_max is not None:
        out_of_range_note_ons = sum(
            note < float(model_pitch_min) or note > float(model_pitch_max)
            for note in note_on_values
        )
    report["midi_analysis"] = {
        "note_on_events": len(note_on_values),
        "minimum_midi_note": min(note_on_values) if note_on_values else None,
        "maximum_midi_note": max(note_on_values) if note_on_values else None,
        "note_on_events_outside_model_pitch_range": out_of_range_note_ons,
    }
    report["configuration"] = {
        "midi": str(args.midi.resolve()),
        "model": str(args.model.resolve()),
        "audio_device": args.audio_device,
        "audio_latency_ms": args.audio_latency_ms,
        "sample_rate": args.sample_rate,
        "prebuffer": args.prebuffer,
        "max_voices": args.max_voices,
        "tail": args.tail,
    }
    report["runtime"] = {
        "rendered_blocks": player.rendered_blocks,
        "played_blocks": player.played_blocks,
        "underruns": player.underruns,
        "overruns": player.overruns,
        "max_render_ms": player.max_render_ms,
        "output_channels": player.output_channels,
    }

    args.wav.parent.mkdir(parents=True, exist_ok=True)
    write_wav(args.wav, blocks.reshape(-1), args.sample_rate)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return player, report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--midi", type=Path, required=True)
    parser.add_argument("--model", type=Path, default=DEFAULT_MODEL)
    parser.add_argument("--audio-device")
    parser.add_argument("--audio-latency-ms", type=float, default=80.0)
    parser.add_argument("--sample-rate", type=int, default=48_000)
    parser.add_argument("--prebuffer", type=int, default=8)
    parser.add_argument("--max-voices", type=int, default=8)
    parser.add_argument("--tail", type=float, default=0.8)
    parser.add_argument(
        "--wav",
        type=Path,
        default=ROOT_DIR / "reports" / "realtime_capture.wav",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=ROOT_DIR / "reports" / "realtime_capture.json",
    )
    args = parser.parse_args()
    if not args.midi.exists():
        parser.error(f"MIDI file not found: {args.midi}")
    if not args.model.exists():
        parser.error(f"ONNX model not found: {args.model}")
    if (
        args.sample_rate <= 0
        or args.prebuffer <= 0
        or args.max_voices <= 0
        or args.audio_latency_ms <= 0
    ):
        parser.error(
            "sample rate, prebuffer, max voices, and audio latency must be positive"
        )
    return args


def main() -> int:
    args = parse_args()
    _, report = run_capture(args)
    transport = report["transport"]
    signal = report["signal"]
    print(f"[CAPTURE] WAV: {args.wav}")
    print(f"[CAPTURE] JSON: {args.report}")
    print(
        "[ANALYSIS] conclusion={0}, underflows={1}, empty_callbacks={2}, "
        "late_callbacks={3}, boundary_discontinuities={4}, active_near_silence={5}, "
        "out_of_range_blocks={6}, max_boundary_jump={7:.6f}".format(
            report["conclusion"],
            transport["status_underflows"],
            transport["callbacks_without_audio"],
            transport["late_callbacks"],
            signal["discontinuity_count"],
            signal["near_silent_blocks_with_held_notes"],
            signal["active_blocks_outside_model_pitch_range"],
            signal["boundary_jump"]["max"],
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
