#!/usr/bin/env python3
"""Extract the original MIDI-DDSP per-instrument reverb IRs."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np


REVERB_VARIABLE = (
    "reverb_module/magnitudes_embedding/embeddings/.ATTRIBUTES/VARIABLE_VALUE"
)
REVERB_SAMPLE_RATE = 16_000
REVERB_LENGTH = 48_000
DECAY_START = 16_000
DECAY_EXPONENT = 4.0


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def original_inference_decay(
    total_length: int = REVERB_LENGTH,
    start_length: int = DECAY_START,
    decay_exponent: float = DECAY_EXPONENT,
) -> np.ndarray:
    if not 0 <= start_length <= total_length:
        raise ValueError("decay start must be inside the impulse response")
    tail = np.linspace(0.0, 1.0, total_length - start_length, dtype=np.float32)
    decay = np.exp(-np.float32(decay_exponent) * tail).astype(np.float32)
    return np.concatenate([np.ones(start_length, dtype=np.float32), decay])


def prepare_impulse_responses(raw: np.ndarray) -> np.ndarray:
    raw = np.asarray(raw, dtype=np.float32)
    if raw.ndim != 2 or raw.shape[1] != REVERB_LENGTH:
        raise ValueError(
            f"expected [instrument, {REVERB_LENGTH}] reverb weights, got {raw.shape}"
        )
    result = raw * original_inference_decay()[None, :]
    # ddsp.effects.Reverb._mask_dry_ir() removes this sample before convolution.
    result[:, 0] = 0.0
    return result.astype(np.float32)


def parse_args() -> argparse.Namespace:
    root = Path(__file__).resolve().parents[1]
    weights = (
        root
        / "models/midi_ddsp/weights/midi_ddsp_model_weights_urmp_9_10"
        / "synthesis_generator/50000"
    )
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", type=Path, default=weights)
    parser.add_argument(
        "--output",
        type=Path,
        default=root / "models/om/midi_ddsp_reverb_ir.npz",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=root / "models/conversion_logs/midi_ddsp/reverb_export.json",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        import tensorflow as tf
    except ImportError as exc:
        raise RuntimeError(
            "TensorFlow is required only for local checkpoint extraction. "
            "Install requirements-export.txt in the export environment."
        ) from exc

    checkpoint = str(args.checkpoint.resolve())
    reader = tf.train.load_checkpoint(checkpoint)
    variables = reader.get_variable_to_shape_map()
    if REVERB_VARIABLE not in variables:
        raise KeyError(f"checkpoint variable not found: {REVERB_VARIABLE}")
    raw = reader.get_tensor(REVERB_VARIABLE)
    impulse_responses = prepare_impulse_responses(raw)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        args.output,
        impulse_responses=impulse_responses,
        sample_rate=np.asarray(REVERB_SAMPLE_RATE, dtype=np.int32),
        decay_start=np.asarray(DECAY_START, dtype=np.int32),
        decay_exponent=np.asarray(DECAY_EXPONENT, dtype=np.float32),
        add_dry=np.asarray(1, dtype=np.int8),
    )

    checkpoint_files = sorted(args.checkpoint.parent.glob(args.checkpoint.name + ".*"))
    report = {
        "source": "google/midi-ddsp ReverbModules inference path",
        "checkpoint_prefix": str(args.checkpoint.resolve()),
        "checkpoint_files": [
            {
                "name": path.name,
                "size_bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }
            for path in checkpoint_files
        ],
        "checkpoint_variable": REVERB_VARIABLE,
        "instrument_count": int(impulse_responses.shape[0]),
        "sample_rate": REVERB_SAMPLE_RATE,
        "reverb_length": REVERB_LENGTH,
        "decay_start": DECAY_START,
        "decay_exponent": DECAY_EXPONENT,
        "first_sample_masked": True,
        "add_dry": True,
        "output": str(args.output.resolve()),
        "output_size_bytes": args.output.stat().st_size,
        "output_sha256": sha256_file(args.output),
        "ir_peak_by_instrument": [
            float(value) for value in np.max(np.abs(impulse_responses), axis=1)
        ],
        "tensorflow": tf.__version__,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
