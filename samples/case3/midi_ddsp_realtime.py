#!/usr/bin/env python3
"""Real-time MIDI-file synthesis with the static MIDI-DDSP Ascend OMs.

The official MIDI-DDSP models are static sequence models. This program uses
the expression OM to prepare note controls, then streams overlapping 64-frame
synthesis-parameter windows through the synthesis OM and renders the omitted
DDSP oscillator/noise DSP on the CPU. It is intended for a monophonic MIDI
file such as midi/ode-to-joy-violin.mid.
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
import signal
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
from realtime_ddsp import HarmonicSynthesizer, LinearResampler, NoiseSynthesizer


MODEL_SAMPLE_RATE = 16_000
MODEL_FRAME_RATE = 250
MODEL_FRAME_SIZE = MODEL_SAMPLE_RATE // MODEL_FRAME_RATE
EXPRESSION_LENGTH = 32
SYNTHESIS_LENGTH = 64
SYNTHESIS_HOP = 32
CONDITIONING_NAMES = (
    "volume",
    "vol_fluc",
    "vibrato",
    "brightness",
    "attack",
    "vol_peak_pos",
)


@dataclass(frozen=True)
class MidiNote:
    start: float
    end: float
    pitch: int
    velocity: int


@dataclass(frozen=True)
class MidiToken:
    pitch: int
    length_frames: int


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


def parse_midi(path: Path) -> list[MidiNote]:
    try:
        import mido
    except ImportError as exc:
        raise RuntimeError("mido is required for MIDI-file playback") from exc

    current_time = 0.0
    active: dict[int, list[tuple[float, int]]] = {}
    notes: list[MidiNote] = []
    for message in mido.MidiFile(str(path)):
        current_time += float(message.time)
        if message.type == "note_on" and message.velocity > 0:
            active.setdefault(int(message.note), []).append(
                (current_time, int(message.velocity))
            )
        elif message.type in ("note_off", "note_on"):
            values = active.get(int(message.note))
            if values:
                start, velocity = values.pop(0)
                notes.append(
                    MidiNote(
                        start=start,
                        end=max(current_time, start + 1e-3),
                        pitch=int(message.note),
                        velocity=velocity,
                    )
                )
                if not values:
                    active.pop(int(message.note), None)
    for pitch, values in active.items():
        for start, velocity in values:
            notes.append(
                MidiNote(
                    start=start,
                    end=max(current_time, start + 1e-3),
                    pitch=pitch,
                    velocity=velocity,
                )
            )
    notes.sort(key=lambda note: (note.start, note.pitch, note.end))
    if not notes:
        raise ValueError(f"No note events found in {path}")
    previous_end = -1.0
    for note in notes:
        if note.start < previous_end - 1e-6:
            raise ValueError(
                "MIDI-DDSP realtime test accepts one monophonic line; overlapping "
                f"notes found near {note.start:.3f}s (use a monophonic MIDI file)"
            )
        previous_end = max(previous_end, note.end)
    return notes


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
            conditioning[cursor:end] = control
            q_pitch[cursor:end, 0] = token.pitch
            if token.pitch:
                onsets[cursor] = 1
                offsets[end - 1] = 1
        cursor = end
    return FrameFeatures(conditioning, q_pitch, onsets, offsets, instrument_id)


def exp_sigmoid(value: np.ndarray, *, bias: float = 0.0) -> np.ndarray:
    value = np.clip(np.asarray(value, dtype=np.float32) + bias, -30.0, 30.0)
    sigmoid = 1.0 / (1.0 + np.exp(-value))
    return (2.0 * sigmoid ** math.log(10.0) + 1e-7).astype(np.float32)


class MidiDdspRenderer:
    def __init__(self, runner: MidiDdspAclRunner, features: FrameFeatures) -> None:
        self.runner = runner
        self.features = features
        self.harmonic = HarmonicSynthesizer(
            sample_rate=MODEL_SAMPLE_RATE, hop_size=MODEL_FRAME_SIZE
        )
        self.noise = NoiseSynthesizer(hop_size=MODEL_FRAME_SIZE, seed=20260722)
        self.render_times_ms: list[float] = []

    @property
    def block_count(self) -> int:
        return math.ceil(self.features.frames / SYNTHESIS_HOP)

    def _window(self, start: int) -> dict[str, np.ndarray]:
        if start == 0:
            source_start = 0
            emit_offset = 0
        else:
            source_start = start - SYNTHESIS_HOP
            emit_offset = SYNTHESIS_HOP
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
        return {"feeds": feeds, "emit_offset": emit_offset}

    def render_block(self, block_index: int) -> np.ndarray:
        start = block_index * SYNTHESIS_HOP
        valid = min(SYNTHESIS_HOP, max(0, self.features.frames - start))
        if valid <= 0:
            return np.zeros(0, dtype=np.float32)
        started = time.perf_counter()
        window = self._window(start)
        outputs = self.runner.infer(window["feeds"])
        offset = window["emit_offset"]
        f0 = outputs["f0_hz"][0, offset : offset + valid, 0]
        amplitudes = exp_sigmoid(
            outputs["amplitudes"][0, offset : offset + valid, 0]
        )
        harmonics = exp_sigmoid(
            outputs["harmonic_distribution"][0, offset : offset + valid]
        )
        noise_magnitudes = exp_sigmoid(
            outputs["noise_magnitudes"][0, offset : offset + valid], bias=-5.0
        )
        audio = np.zeros(valid * MODEL_FRAME_SIZE, dtype=np.float32)
        for index in range(valid):
            block_start = index * MODEL_FRAME_SIZE
            block_end = block_start + MODEL_FRAME_SIZE
            if self.features.q_pitch[start + index, 0] <= 0.0:
                self.harmonic.reset()
                continue
            harmonic = self.harmonic.render(
                float(amplitudes[index]), harmonics[index], float(f0[index])
            )
            noise = self.noise.render(noise_magnitudes[index])
            audio[block_start:block_end] = harmonic + noise
        self.render_times_ms.append((time.perf_counter() - started) * 1000.0)
        return np.clip(audio, -1.0, 1.0).astype(np.float32)


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
                block = self.renderer.render_block(self.rendered)
                block = block * self.gain
                source_samples = SYNTHESIS_HOP * MODEL_FRAME_SIZE
                if block.size < source_samples:
                    block = np.pad(block, (0, source_samples - block.size))
                resampler = getattr(self, "resampler", None)
                if resampler is None:
                    resampler = LinearResampler(MODEL_SAMPLE_RATE, self.output_sample_rate)
                    self.resampler = resampler
                output = np.clip(resampler.process(block), -1.0, 1.0).astype(np.float32)
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


def write_wav(path: Path, samples: np.ndarray, sample_rate: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pcm = np.clip(samples, -1.0, 1.0)
    pcm = (pcm * 32767.0).astype("<i2").tobytes()
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(pcm)


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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    root = Path(__file__).resolve().parent
    parser.add_argument("--midi", type=Path, required=True)
    parser.add_argument(
        "--expression-om",
        type=Path,
        default=root / "models/midi_ddsp/om/ascend8t2/midi_ddsp_expression_notes32_mixed_float16.om",
    )
    parser.add_argument(
        "--synthesis-om",
        type=Path,
        default=root / "models/midi_ddsp/om/ascend8t2/midi_ddsp_synthesis_params_frames64_mixed_float16.om",
    )
    parser.add_argument("--instrument-id", type=int, default=0)
    parser.add_argument("--device-id", type=int, default=0)
    parser.add_argument("--audio-device")
    parser.add_argument("--sample-rate", type=int, default=48_000)
    parser.add_argument("--prebuffer", type=int, default=2)
    parser.add_argument("--audio-latency-ms", type=float, default=80.0)
    parser.add_argument("--output-gain-db", type=float, default=24.0)
    parser.add_argument("--tail-seconds", type=float, default=0.5)
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
    if args.instrument_id < 0 or args.instrument_id >= 20:
        raise ValueError("--instrument-id must be in [0, 19]")
    notes = parse_midi(args.midi)
    tokens = build_tokens(notes, round((MODEL_FRAME_RATE * args.tail_seconds) + MODEL_FRAME_RATE))
    expression_runner: MidiDdspAclRunner | None = None
    synthesis_runner: MidiDdspAclRunner | None = None
    player: RealtimeAudioPlayer | None = None
    capture: list[np.ndarray] = []
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
        renderer = MidiDdspRenderer(synthesis_runner, features)
        emit_web_event(
            args.json_events,
            "prepared",
            notes=len(notes),
            frames=features.frames,
            blocks=renderer.block_count,
            duration_seconds=features.frames / MODEL_FRAME_RATE,
        )
        realtime_started = time.perf_counter()
        if args.render_only:
            resampler = LinearResampler(MODEL_SAMPLE_RATE, args.sample_rate)
            for index in range(renderer.block_count):
                block = renderer.render_block(index) * 10.0 ** (args.output_gain_db / 20.0)
                source_samples = SYNTHESIS_HOP * MODEL_FRAME_SIZE
                if block.size < source_samples:
                    block = np.pad(block, (0, source_samples - block.size))
                capture.append(np.clip(resampler.process(block), -1.0, 1.0))
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
    report = {
        "midi": str(args.midi.resolve()),
        "notes": len(notes),
        "tokens": len(tokens),
        "frames": features.frames,
        "duration_seconds": features.frames / MODEL_FRAME_RATE,
        "instrument_id": args.instrument_id,
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
