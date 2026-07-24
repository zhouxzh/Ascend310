from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path

import numpy as np

from pyacl_midi_ddsp import MidiDdspAclRunner, TensorSpec


SOURCE_COMMIT = "d7af42704a63b47267ae6a1bc0fee1ed7dc5c855"
STATEFUL_COMPONENTS = {
    "midi_ddsp_v2_expression_context_forward_notes32",
    "midi_ddsp_v2_expression_context_backward_notes32",
    "midi_ddsp_v2_expression_decode_notes32",
    "midi_ddsp_v2_synthesis_precondition_frames64",
    "midi_ddsp_v2_synthesis_context_forward_frames64",
    "midi_ddsp_v2_synthesis_context_backward_frames64",
    "midi_ddsp_v2_synthesis_f0_decode_frames64",
    "midi_ddsp_v2_synthesis_timbre_frames312",
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
    timbre_halo: int
    components: dict[str, RuntimeComponent]

    def component(self, name: str) -> RuntimeComponent:
        try:
            return self.components[name]
        except KeyError as exc:
            raise KeyError(f"Bundle component is missing: {name}") from exc


def load_runtime_bundle(path: Path) -> RuntimeBundle:
    path = Path(path).resolve()
    data = json.loads(path.read_text(encoding="utf-8"))
    if int(data.get("schema_version", 0)) != 1:
        raise ValueError("Unsupported MIDI-DDSP bundle schema")
    if data.get("architecture") != "stateful-v2":
        raise ValueError("Runtime bundle is not stateful-v2")
    if data.get("source_commit") != SOURCE_COMMIT:
        raise ValueError("Runtime bundle uses an unexpected MIDI-DDSP source commit")
    raw_components = data.get("components")
    if not isinstance(raw_components, dict):
        raise ValueError("Runtime bundle components are invalid")
    missing = STATEFUL_COMPONENTS - set(raw_components)
    if missing:
        raise ValueError(f"Runtime bundle is incomplete: {sorted(missing)}")
    components = {}
    for name in sorted(STATEFUL_COMPONENTS):
        raw = raw_components[name]
        if not isinstance(raw, dict):
            raise ValueError(f"Invalid runtime component: {name}")
        component_path = (path.parent / str(raw["file"])).resolve()
        if component_path.parent != path.parent:
            raise ValueError(f"Component escapes bundle directory: {name}")
        actual_sha = _sha256(component_path)
        expected_sha = str(raw["sha256"])
        if actual_sha != expected_sha:
            raise ValueError(f"SHA256 mismatch for {component_path.name}")
        components[name] = RuntimeComponent(
            name=name,
            path=component_path,
            sha256=actual_sha,
            inputs=_specs(raw["inputs"]),
            outputs=_specs(raw["outputs"]),
        )
    return RuntimeBundle(
        id=str(data["id"]),
        name=str(data["name"]),
        manifest_path=path,
        source_commit=str(data["source_commit"]),
        seed=int(data.get("seed", 20260724)),
        expression_block=int(data["expression_block"]),
        synthesis_block=int(data["synthesis_block"]),
        timbre_halo=int(data["timbre_halo"]),
        components=components,
    )
