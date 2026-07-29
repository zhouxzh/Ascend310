"""Generate deterministic Piano-DDSP ONNX, state, DSP, NPZ, and WAV references."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import subprocess
import sys
import time
import wave

import numpy as np


SOURCE_COMMIT = "1f7cf65ff9c58968bc3b605ee571db928d1ac37a"
CONTROL_NAMES = (
    "amplitudes",
    "harmonic_distribution",
    "inharmonicity",
    "f0_hz",
    "noise_magnitudes",
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def require_clean_source(path: Path) -> None:
    head = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    if head != SOURCE_COMMIT:
        raise RuntimeError(f"Source HEAD is {head}, expected {SOURCE_COMMIT}")
    status = subprocess.run(
        ["git", "-C", str(path), "status", "--porcelain"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    if status.strip():
        raise RuntimeError("Reference generation requires a clean source worktree")


def fixed_events(frame_count: int) -> list[tuple[int, str, int, int]]:
    events: list[tuple[int, str, int, int]] = [
        (0, "cc", 65, 32),
        (1, "cc", 66, 64),
        (2, "cc", 67, 96),
        (10, "note_on", 60, 90),
        (28, "note_on", 60, 112),
        (48, "note_off", 60, 0),
        (90, "cc", 64, 127),
    ]
    chord = (36, 40, 43, 47, 48, 52, 55, 59, 60, 64, 67, 71, 72, 76, 79, 83)
    for pitch in chord:
        events.append((100, "note_on", pitch, 48 + (pitch % 48)))
        events.append((170, "note_off", pitch, 0))
    events.extend([(185, "note_on", 88, 120), (205, "note_off", 88, 0), (240, "cc", 64, 0)])
    for start in range(300, frame_count - 180, 320):
        root = 36 + (start // 320) % 24
        pitches = [min(108, root + offset) for offset in (0, 4, 7, 12, 16, 19)]
        for index, pitch in enumerate(pitches):
            on = start + index * 3
            events.append((on, "note_on", pitch, 55 + (index * 11) % 70))
            events.append((on + 45 + index, "note_off", pitch, 0))
        repeat_pitch = min(108, root + 24)
        for offset, velocity in ((80, 45), (88, 80), (96, 118)):
            events.append((start + offset, "note_on", repeat_pitch, velocity))
            events.append((start + offset + 5, "note_off", repeat_pitch, 0))
        events.extend(
            [
                (start + 120, "cc", 64, 127),
                (start + 125, "note_on", min(108, root + 31), 100),
                (start + 150, "note_off", min(108, root + 31), 0),
                (start + 175, "cc", 64, 0),
            ]
        )
    return sorted((event for event in events if event[0] < frame_count), key=lambda item: item[0])


def build_conditioning(frame_count: int, events: list[tuple[int, str, int, int]], state_type: type):
    state = state_type(16)
    conditioning = np.zeros((frame_count, 16, 2), dtype=np.float32)
    pedal = np.zeros((frame_count, 4), dtype=np.float32)
    gates = np.zeros((frame_count, 16), dtype=np.bool_)
    cursor = 0
    for frame in range(frame_count):
        while cursor < len(events) and events[cursor][0] == frame:
            _, kind, data1, data2 = events[cursor]
            if kind == "note_on":
                state.note_on(data1, data2)
            elif kind == "note_off":
                state.note_off(data1)
            else:
                state.control_change(data1, data2)
            cursor += 1
        condition, pedals, gate = state.render_block(1)
        conditioning[frame] = condition[0]
        pedal[frame] = pedals[0]
        gates[frame] = gate
    return conditioning, pedal, gates


def write_midi(path: Path, events: list[tuple[int, str, int, int]]) -> None:
    import mido

    midi = mido.MidiFile(type=0, ticks_per_beat=500)
    track = mido.MidiTrack()
    track.append(mido.MetaMessage("set_tempo", tempo=500_000, time=0))
    previous_tick = 0
    for frame, kind, data1, data2 in events:
        tick = frame * 4
        delta = tick - previous_tick
        previous_tick = tick
        if kind == "note_on":
            track.append(mido.Message("note_on", note=data1, velocity=data2, time=delta))
        elif kind == "note_off":
            track.append(mido.Message("note_off", note=data1, velocity=0, time=delta))
        else:
            track.append(mido.Message("control_change", control=data1, value=data2, time=delta))
    midi.tracks.append(track)
    midi.save(path)


def voice_envelopes(gates: np.ndarray, samples_per_frame: int = 64) -> np.ndarray:
    frames, voices = gates.shape
    samples = frames * samples_per_frame
    result = np.empty((voices, samples), dtype=np.float32)
    gain = np.zeros(voices, dtype=np.float32)
    step = np.float32(1.0 / round(0.060 * 16_000))
    expanded = np.repeat(gates, samples_per_frame, axis=0)
    for voice in range(voices):
        for index in range(samples):
            gain[voice] = 1.0 if expanded[index, voice] else max(0.0, gain[voice] - step)
            result[voice, index] = gain[voice]
    return result


def snr_db(reference: np.ndarray, actual: np.ndarray) -> float:
    error = np.asarray(reference, dtype=np.float64) - np.asarray(actual, dtype=np.float64)
    signal_power = float(np.sum(np.square(reference, dtype=np.float64)))
    error_power = float(np.sum(np.square(error, dtype=np.float64)))
    return 10.0 * math.log10(max(signal_power, 1e-30) / max(error_power, 1e-30))


def write_wav(path: Path, audio: np.ndarray, sample_rate: int = 16_000) -> None:
    audio = np.asarray(audio, dtype=np.float32)
    pcm = np.rint(np.clip(audio, -1.0, 1.0) * 32767.0).astype("<i2")
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        output.writeframes(pcm.tobytes())


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument(
        "--model", type=Path, default=Path("models/piano_ddsp/model-suite-v1.0.0/ddsp_piano_paper_ir.onnx")
    )
    parser.add_argument(
        "--metadata", type=Path, default=Path("models/piano_ddsp/model-suite-v1.0.0/ddsp_piano_paper_ir.json")
    )
    parser.add_argument(
        "--output-dir", type=Path, default=Path("models/piano_ddsp/references/model-suite-v1.0.0/paper_ir")
    )
    parser.add_argument("--frames", type=int, default=10_000)
    parser.add_argument("--audio-frames", type=int, default=2_048)
    parser.add_argument("--block-frames", type=int, default=8)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--piano-model", type=int, default=9)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.frames < 10_000:
        raise ValueError("At least 10,000 frames are required for the release reference")
    if args.block_frames <= 0 or args.frames % args.block_frames:
        raise ValueError("frames must be divisible by block-frames")
    if not 0 < args.audio_frames <= args.frames or args.audio_frames % args.block_frames:
        raise ValueError("audio-frames must be positive, bounded by frames, and block aligned")
    source_root = args.source_root.resolve()
    require_clean_source(source_root)
    sys.path.insert(0, str(source_root))
    case_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(case_root))

    import onnxruntime as ort
    import torch
    from torch.nn import functional as functional
    from ddsp_piano.deployment import extend_pitch_for_release
    from ddsp_piano.ddsp_pytorch.core import frequency_impulse_response, get_fft_size, scale_function
    from ddsp_piano.realtime import LiveMidiState, StreamingReverb as ReferenceReverb
    from scripts.render_onnx import _StreamingDrySynthesizer
    from piano_ddsp_runtime.harmonic import HarmonicSynthesizer
    from piano_ddsp_runtime.noise import NoiseSynthesizer
    from piano_ddsp_runtime.reverb import StreamingReverb as NumpyReverb

    model_path = args.model.resolve()
    metadata_path = args.metadata.resolve()
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    events = fixed_events(args.frames)
    (output_dir / "events.json").write_text(json.dumps(events, indent=2) + "\n", encoding="utf-8")
    write_midi(output_dir / "reference.mid", events)
    conditioning, pedal, gates = build_conditioning(args.frames, events, LiveMidiState)

    session = ort.InferenceSession(str(model_path), providers=["CPUExecutionProvider"])
    reverb_name = str(metadata.get("reverb_output", "reverb_ir"))
    output_names = list(CONTROL_NAMES) + [reverb_name, "next_context_state", "next_monophonic_state"]
    context_in = np.empty((args.frames, 64), dtype=np.float32)
    context_out = np.empty_like(context_in)
    monophonic_in = np.empty((args.frames, 16, 192), dtype=np.float32)
    monophonic_out = np.empty_like(monophonic_in)
    extended = np.empty((args.frames, 16, 1), dtype=np.float32)
    controls = {
        name: np.empty((args.frames,) + tuple(metadata["outputs"][name][2:]), dtype=np.float32)
        for name in CONTROL_NAMES
    }
    context = np.zeros((1, 1, 64), dtype=np.float32)
    monophonic = np.zeros((1, 16, 192), dtype=np.float32)
    held = np.zeros((1, 16), dtype=np.float32)
    released = np.zeros((1, 16), dtype=np.int32)
    piano_model = np.asarray([args.piano_model], dtype=np.int32)
    reverb_condition = None
    started = time.perf_counter()
    for frame in range(args.frames):
        frame_condition = conditioning[np.newaxis, frame : frame + 1]
        frame_pedal = pedal[np.newaxis, frame : frame + 1]
        extended_frame, held, released = extend_pitch_for_release(
            frame_condition, held, released, int(metadata["release_frames"])
        )
        context_in[frame] = context[0, 0]
        monophonic_in[frame] = monophonic[0]
        extended[frame] = extended_frame[0, 0]
        values = session.run(
            output_names,
            {
                "conditioning": frame_condition,
                "pedal": frame_pedal,
                "piano_model": piano_model,
                "extended_pitch": extended_frame,
                "context_state": context,
                "monophonic_state": monophonic,
            },
        )
        for name, value in zip(CONTROL_NAMES, values[:5]):
            controls[name][frame] = value[0, 0]
        if reverb_condition is None:
            reverb_condition = values[5].copy()
        context, monophonic = values[6], values[7]
        context_out[frame] = context[0, 0]
        monophonic_out[frame] = monophonic[0]
        if (frame + 1) % 1000 == 0:
            print(f"ONNX reference {frame + 1}/{args.frames}", flush=True)
    onnx_seconds = time.perf_counter() - started
    assert reverb_condition is not None

    white_noise = np.empty((args.audio_frames, 16, 64), dtype=np.float32)
    for voice in range(16):
        rng = np.random.RandomState(args.seed + voice)
        white_noise[:, voice] = rng.rand(args.audio_frames, 64).astype(np.float32) * 2.0 - 1.0

    n_harmonics = int(metadata["n_harmonics"])
    n_bands = int(metadata["n_noise_bands"])
    n_substrings = int(metadata.get("n_substrings", 1))
    reference_synth = _StreamingDrySynthesizer(metadata, 16_000, 64, args.seed)
    numpy_harmonic = HarmonicSynthesizer(16, n_harmonics, n_substrings)
    numpy_noise = NoiseSynthesizer(16, n_bands, seed=args.seed)
    noise_ir_size = 2 * (n_bands - 1)
    noise_fft_size = get_fft_size(64, noise_ir_size)
    reference_tails = [torch.zeros(0, dtype=torch.float32) for _ in range(16)]
    envelopes = voice_envelopes(gates[: args.audio_frames])
    reference_harmonic: list[np.ndarray] = []
    reference_noise: list[np.ndarray] = []
    actual_harmonic: list[np.ndarray] = []
    actual_noise: list[np.ndarray] = []
    for start in range(0, args.audio_frames, args.block_frames):
        end = start + args.block_frames
        samples = args.block_frames * 64
        env = envelopes[:, start * 64 : end * 64]
        harmonic_block = torch.zeros(samples, dtype=torch.float32)
        noise_block = torch.zeros(samples, dtype=torch.float32)
        for voice in range(16):
            harmonic_voice = reference_synth._render_harmonic_voice(
                voice,
                torch.from_numpy(controls["amplitudes"][start:end, voice]),
                torch.from_numpy(controls["harmonic_distribution"][start:end, voice]),
                torch.from_numpy(controls["inharmonicity"][start:end, voice]),
                torch.from_numpy(controls["f0_hz"][start:end, voice]),
            )
            magnitudes = torch.from_numpy(controls["noise_magnitudes"][start:end, voice])
            impulse = frequency_impulse_response(scale_function(magnitudes).unsqueeze(0)).squeeze(0)
            frames = torch.from_numpy(white_noise[start:end, voice])
            filtered = torch.fft.irfft(
                torch.fft.rfft(frames, noise_fft_size)
                * torch.fft.rfft(impulse, noise_fft_size),
                n=noise_fft_size,
            )
            overlap = torch.zeros(samples + noise_fft_size - 64, dtype=torch.float32)
            for index in range(args.block_frames):
                overlap[index * 64 : index * 64 + noise_fft_size].add_(filtered[index])
            if reference_tails[voice].numel():
                overlap[: reference_tails[voice].numel()].add_(reference_tails[voice])
            reference_tails[voice] = overlap[samples:].clone()
            envelope = torch.from_numpy(env[voice])
            harmonic_block.add_(harmonic_voice * envelope)
            noise_block.add_(overlap[:samples] * envelope)
        reference_harmonic.append(harmonic_block.numpy())
        reference_noise.append(noise_block.numpy())
        actual_harmonic.append(
            numpy_harmonic.render(
                controls["amplitudes"][start:end],
                controls["harmonic_distribution"][start:end],
                controls["inharmonicity"][start:end],
                controls["f0_hz"][start:end],
                env,
            )
        )
        actual_noise.append(
            numpy_noise.render(
                controls["noise_magnitudes"][start:end], env, white_noise[start:end]
            )
        )
    ref_harmonic = np.concatenate(reference_harmonic)
    ref_noise = np.concatenate(reference_noise)
    np_harmonic = np.concatenate(actual_harmonic)
    np_noise = np.concatenate(actual_noise)
    ref_dry = ref_harmonic + ref_noise
    np_dry = np_harmonic + np_noise
    reference_reverb = ReferenceReverb(metadata, reverb_condition, args.block_frames * 64)
    numpy_reverb = NumpyReverb(metadata, reverb_condition, args.block_frames * 64)
    ref_wet = np.concatenate(
        [reference_reverb.process(ref_dry[index : index + args.block_frames * 64]) for index in range(0, ref_dry.size, args.block_frames * 64)]
    )
    np_wet = np.concatenate(
        [numpy_reverb.process(np_dry[index : index + args.block_frames * 64]) for index in range(0, np_dry.size, args.block_frames * 64)]
    )
    scores = {
        "harmonic_snr_db": snr_db(ref_harmonic, np_harmonic),
        "noise_snr_db": snr_db(ref_noise, np_noise),
        "dry_snr_db": snr_db(ref_dry, np_dry),
        "wet_snr_db": snr_db(ref_wet, np_wet),
    }
    if min(scores.values()) < 60.0:
        raise RuntimeError(f"NumPy DSP reference failed the 60 dB SNR threshold: {scores}")

    npz_path = output_dir / "reference-10000.npz"
    np.savez_compressed(
        npz_path,
        conditioning=conditioning,
        pedal=pedal,
        gates=gates,
        piano_model=piano_model,
        extended_pitch=extended,
        context_state_in=context_in,
        context_state_out=context_out,
        monophonic_state_in=monophonic_in,
        monophonic_state_out=monophonic_out,
        reverb_condition=reverb_condition,
        white_noise=white_noise,
        reference_harmonic=ref_harmonic,
        reference_noise=ref_noise,
        reference_dry=ref_dry,
        reference_wet=ref_wet,
        **controls,
    )
    wav_path = output_dir / "reference.wav"
    write_wav(wav_path, ref_wet)
    report = {
        "schema": "piano-ddsp-reference/v1",
        "source_commit": SOURCE_COMMIT,
        "model_id": metadata["model_id"],
        "onnx": str(model_path),
        "onnx_sha256": sha256_file(model_path),
        "metadata_sha256": sha256_file(metadata_path),
        "frames": args.frames,
        "audio_frames": args.audio_frames,
        "block_frames": args.block_frames,
        "seed": args.seed,
        "piano_model": args.piano_model,
        "onnx_seconds": onnx_seconds,
        "scores": scores,
        "npz": npz_path.name,
        "npz_sha256": sha256_file(npz_path),
        "wav": wav_path.name,
        "wav_sha256": sha256_file(wav_path),
    }
    (output_dir / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
