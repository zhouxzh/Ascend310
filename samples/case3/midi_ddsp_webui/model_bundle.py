from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path

import numpy as np

from pyacl_midi_ddsp import MidiDdspAclRunner, MidiDdspAclRuntime, TensorSpec


SOURCE_COMMIT = "d7af42704a63b47267ae6a1bc0fee1ed7dc5c855"
TIMBRE_MAX_FRAMES = 65_536
STATEFUL_COMPONENTS = {
    "midi_ddsp_v2_expression_context_forward_notes32",
    "midi_ddsp_v2_expression_context_backward_notes32",
    "midi_ddsp_v2_expression_decode_notes32",
    "midi_ddsp_v2_synthesis_precondition_frames64",
    "midi_ddsp_v2_synthesis_context_forward_frames64",
    "midi_ddsp_v2_synthesis_context_backward_frames64",
    "midi_ddsp_v2_synthesis_f0_decode_frames64",
    f"midi_ddsp_v2_synthesis_timbre_frames{TIMBRE_MAX_FRAMES}",
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _dtype(value: str) -> np.dtype:
    normalized = value.lower()
    if "int64" in normalized:
        return np.dtype(np.int64)
    if "float" in normalized:
        return np.dtype(np.float32)
    raise ValueError(f"Unsupported bundle tensor type: {value}")


def _specs(items: object) -> tuple[TensorSpec, ...]:
    if not isinstance(items, list):
        raise ValueError("Bundle tensor specifications must be a list")
    result = []
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("Invalid bundle tensor specification")
        shape = tuple(int(value) for value in item["shape"])
        if not shape or any(value <= 0 for value in shape):
            raise ValueError(f"Invalid bundle tensor shape: {shape}")
        result.append(TensorSpec(str(item["name"]), shape, _dtype(str(item["type"]))))
    return tuple(result)


@dataclass(frozen=True)
class RuntimeComponent:
    name: str
    export_name: str
    voice_batch_size: int
    path: Path
    sha256: str
    inputs: tuple[TensorSpec, ...]
    outputs: tuple[TensorSpec, ...]

    def open(self, device_id: int) -> MidiDdspAclRunner:
        return MidiDdspAclRunner(
            self.path, self.inputs, self.outputs, device_id=device_id
        )


@dataclass(frozen=True)
class RuntimeBundle:
    id: str
    name: str
    manifest_path: Path
    source_commit: str
    seed: int
    expression_block: int
    synthesis_block: int
    timbre_max_frames: int
    components: dict[str, RuntimeComponent]
    component_sets: dict[int, dict[str, RuntimeComponent]]
    voice_batch_sizes: tuple[int, ...]
    precision: str
    onnx_dtype: str

    def component(self, name: str, voice_batch_size: int = 1) -> RuntimeComponent:
        try:
            return self.component_sets[int(voice_batch_size)][name]
        except KeyError as exc:
            raise KeyError(
                f"Bundle component is missing: {name} batch={voice_batch_size}"
            ) from exc

    def select_voice_batch_size(self, voice_count: int) -> int:
        if voice_count <= 0:
            raise ValueError("voice_count must be positive")
        for batch_size in self.voice_batch_sizes:
            if batch_size >= voice_count:
                return batch_size
        return self.voice_batch_sizes[-1]

    def runtime_session(self, device_id: int) -> MidiDdspAclRuntime:
        return MidiDdspAclRuntime(device_id=device_id)


def load_runtime_bundle(path: Path) -> RuntimeBundle:
    path = Path(path).resolve()
    data = json.loads(path.read_text(encoding="utf-8"))
    schema_version = int(data.get("schema_version", 0))
    if schema_version not in {1, 2, 3}:
        raise ValueError("Unsupported MIDI-DDSP bundle schema")
    if data.get("architecture") != "stateful-v2":
        raise ValueError("Runtime bundle is not stateful-v2")
    if data.get("source_commit") != SOURCE_COMMIT:
        raise ValueError("Runtime bundle uses an unexpected MIDI-DDSP source commit")
    if data.get("precision", "origin") != "origin":
        raise ValueError("Runtime MIDI-DDSP bundle must use origin precision")
    raw_components = data.get("components")
    if not isinstance(raw_components, dict):
        raise ValueError("Runtime bundle components are invalid")
    component_sets: dict[int, dict[str, RuntimeComponent]] = {}
    for export_name, raw in raw_components.items():
        if not isinstance(raw, dict):
            raise ValueError(f"Invalid runtime component: {export_name}")
        logical_name = str(raw.get("logical_name", export_name))
        batch_size = int(raw.get("voice_batch_size", 1))
        if logical_name not in STATEFUL_COMPONENTS:
            raise ValueError(f"Unknown runtime component: {logical_name}")
        if batch_size <= 0:
            raise ValueError(f"Invalid voice batch size: {batch_size}")
        component_path = (path.parent / str(raw["file"])).resolve()
        if component_path.parent != path.parent:
            raise ValueError(f"Component escapes bundle directory: {export_name}")
        actual_sha = _sha256(component_path)
        expected_sha = str(raw["sha256"])
        if actual_sha != expected_sha:
            raise ValueError(f"SHA256 mismatch for {component_path.name}")
        components = component_sets.setdefault(batch_size, {})
        if logical_name in components:
            raise ValueError(
                f"Duplicate runtime component: {logical_name} batch={batch_size}"
            )
        components[logical_name] = RuntimeComponent(
            name=logical_name,
            export_name=str(export_name),
            voice_batch_size=batch_size,
            path=component_path,
            sha256=actual_sha,
            inputs=_specs(raw["inputs"]),
            outputs=_specs(raw["outputs"]),
        )
    for batch_size, components in component_sets.items():
        missing = STATEFUL_COMPONENTS - set(components)
        if missing:
            raise ValueError(
                f"Runtime bundle batch {batch_size} is incomplete: {sorted(missing)}"
            )
    if 1 not in component_sets:
        raise ValueError("Runtime bundle must include batch 1 components")
    declared_batch_sizes = tuple(
        sorted(int(value) for value in data.get("voice_batch_sizes", component_sets))
    )
    if declared_batch_sizes != tuple(sorted(component_sets)):
        raise ValueError("Runtime bundle voice_batch_sizes do not match components")
    if schema_version == 3 and data.get("onnx_dtype") != "float32":
        raise ValueError("Schema 3 MIDI-DDSP bundles require float32 ONNX sources")
    return RuntimeBundle(
        id=str(data["id"]),
        name=str(data["name"]),
        manifest_path=path,
        source_commit=str(data["source_commit"]),
        seed=int(data.get("seed", 20260724)),
        expression_block=int(data["expression_block"]),
        synthesis_block=int(data["synthesis_block"]),
        timbre_max_frames=int(data["timbre_max_frames"]),
        components=component_sets[1],
        component_sets=component_sets,
        voice_batch_sizes=declared_batch_sizes,
        precision=str(data.get("precision", "origin")),
        onnx_dtype=str(data.get("onnx_dtype", "float32")),
    )
