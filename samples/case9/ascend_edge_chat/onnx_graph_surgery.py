from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
from typing import Any

import numpy as np


class OnnxGraphSurgeryError(ValueError):
    """Raised when an ONNX graph cannot be migrated safely."""


@dataclass(frozen=True)
class MatMulNBitsUnpack:
    weight: np.ndarray
    k: int
    n: int
    blocks: int
    bits: int
    block_size: int


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def unpack_matmul_nbits_weight(
    packed: np.ndarray,
    scales: np.ndarray,
    *,
    k: int,
    n: int,
    bits: int,
    block_size: int,
    zero_points: np.ndarray | None = None,
) -> MatMulNBitsUnpack:
    """Unpack the ORT symmetric MatMulNBits layout into a [K, N] weight.

    The screened q4f16 graph stores each pair of adjacent K values in one
    byte: the low nibble is the even K position and the high nibble is the
    following odd K position. Symmetric four-bit weights omit zero points
    and use a default zero point of eight.
    """
    if bits not in {2, 4, 8}:
        raise OnnxGraphSurgeryError("unsupported MatMulNBits bits: %s" % bits)
    if bits != 4:
        raise OnnxGraphSurgeryError(
            "this migration currently supports only four-bit MatMulNBits weights"
        )
    if block_size <= 0 or block_size % 32:
        raise OnnxGraphSurgeryError(
            "four-bit MatMulNBits block_size must be a positive multiple of 32"
        )
    if k <= 0 or n <= 0:
        raise OnnxGraphSurgeryError("MatMulNBits K and N must be positive")

    blocks = math.ceil(k / block_size)
    blob_size = math.ceil(block_size * bits / 8)
    if packed.dtype != np.uint8:
        raise OnnxGraphSurgeryError("MatMulNBits packed weights must be uint8")
    expected_shape = (n, blocks, blob_size)
    if tuple(packed.shape) != expected_shape:
        raise OnnxGraphSurgeryError(
            "packed weight shape %s does not match expected %s"
            % (tuple(packed.shape), expected_shape)
        )

    flat_scales = np.asarray(scales).reshape(-1)
    if flat_scales.size != n * blocks:
        raise OnnxGraphSurgeryError(
            "scale count %s does not match N*blocks=%s"
            % (flat_scales.size, n * blocks)
        )
    scale_matrix = flat_scales.reshape(n, blocks)

    if zero_points is not None:
        zero_array = np.asarray(zero_points)
        if zero_array.size != 1:
            raise OnnxGraphSurgeryError(
                "only a scalar MatMulNBits zero point is supported"
            )
        zero_point = int(zero_array.reshape(-1)[0])
    else:
        zero_point = 1 << (bits - 1)

    output_dtype = np.float16 if flat_scales.dtype == np.float16 else np.float32
    chunk_count = block_size // 32
    packed_chunks = packed.reshape(n, blocks, chunk_count, 16)
    low = packed_chunks & 0x0F
    high = packed_chunks >> 4
    quantized = np.stack((low, high), axis=-1).reshape(
        n, blocks, block_size
    ).astype(np.int16, copy=False)
    quantized -= int(zero_point)
    scale_matrix = scale_matrix.astype(output_dtype, copy=False)
    dequantized = quantized.astype(output_dtype, copy=False) * scale_matrix[:, :, None]
    weight = dequantized.reshape(n, blocks * block_size)[:, :k].T.copy()
    return MatMulNBitsUnpack(
        weight=weight,
        k=k,
        n=n,
        blocks=blocks,
        bits=bits,
        block_size=block_size,
    )


def _normalize_transpose_perms(model: Any, onnx: Any, graph: Any) -> int:
    ranks: dict[str, int] = {
        item.name: len(item.dims) for item in graph.initializer
    }
    for value in list(graph.input) + list(graph.value_info) + list(graph.output):
        if value.type.HasField("tensor_type"):
            ranks[value.name] = len(value.type.tensor_type.shape.dim)
    normalized = 0
    for node in graph.node:
        if node.op_type != "Transpose":
            continue
        if any(attribute.name == "perm" for attribute in node.attribute):
            continue
        rank = ranks.get(node.input[0])
        if rank is None:
            raise OnnxGraphSurgeryError(
                "%s has an implicit Transpose perm but unknown input rank"
                % node.name
            )
        node.attribute.append(
            onnx.helper.make_attribute("perm", list(reversed(range(rank))))
        )
        normalized += 1
    return normalized


def normalize_transpose_perms(
    input_path: Path | str,
    output_path: Path | str,
    report_path: Path | str | None = None,
) -> dict[str, Any]:
    """Make ONNX Transpose default permutations explicit for ATC."""
    try:
        import onnx
    except ImportError as exc:
        raise OnnxGraphSurgeryError("onnx is required to normalize Transpose") from exc
    input_path = Path(input_path)
    output_path = Path(output_path)
    if input_path.resolve() == output_path.resolve():
        raise OnnxGraphSurgeryError("input and output paths must differ")
    source_sha256 = _sha256(input_path)
    source_bytes = input_path.stat().st_size
    model = onnx.load(str(input_path), load_external_data=True)
    normalized = _normalize_transpose_perms(model, onnx, model.graph)
    if normalized == 0:
        raise OnnxGraphSurgeryError("no implicit Transpose permutations were found")
    onnx.checker.check_model(model, full_check=False)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_name(output_path.name + ".tmp")
    try:
        onnx.save_model(model, str(temporary))
        temporary.replace(output_path)
    finally:
        if temporary.exists():
            temporary.unlink()
    report = {
        "schema_version": 1,
        "source": {
            "path": str(input_path),
            "bytes": source_bytes,
            "sha256": source_sha256,
        },
        "output": {
            "path": str(output_path),
            "bytes": output_path.stat().st_size,
            "sha256": _sha256(output_path),
        },
        "normalized_transposes": normalized,
    }
    if report_path is not None:
        report_path = Path(report_path)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        encoded = json.dumps(report, indent=2, sort_keys=True) + "\n"
        temporary_report = report_path.with_name(report_path.name + ".tmp")
        temporary_report.write_text(encoded, encoding="utf-8")
        temporary_report.replace(report_path)
    return report


def _attribute(node: Any, name: str, default: int | None = None) -> int | None:
    for attribute in node.attribute:
        if attribute.name == name:
            return int(attribute.i)
    return default


def migrate_matmulnbits_to_fp16(
    input_path: Path | str,
    output_path: Path | str,
    report_path: Path | str | None = None,
) -> dict[str, Any]:
    """Replace initializer-backed MatMulNBits nodes with standard FP16 MatMul."""
    try:
        import onnx
        from onnx import numpy_helper
    except ImportError as exc:
        raise OnnxGraphSurgeryError(
            "onnx is required to migrate MatMulNBits graphs"
        ) from exc

    input_path = Path(input_path)
    output_path = Path(output_path)
    if input_path.resolve() == output_path.resolve():
        raise OnnxGraphSurgeryError("input and output paths must differ")
    source_sha256 = _sha256(input_path)
    source_bytes = input_path.stat().st_size
    model = onnx.load(str(input_path), load_external_data=True)
    graph = model.graph
    initializers = {item.name: item for item in graph.initializer}
    removed_initializers: set[str] = set()
    added_initializers: list[Any] = []
    migrated_nodes: list[Any] = []
    replaced: list[dict[str, Any]] = []
    unsupported: list[str] = []

    for node in graph.node:
        if node.op_type != "MatMulNBits":
            if node.domain not in ("", "ai.onnx"):
                unsupported.append("%s:%s" % (node.domain, node.op_type))
            migrated_nodes.append(node)
            continue
        if node.domain != "com.microsoft":
            unsupported.append("%s:%s" % (node.domain or "ai.onnx", node.op_type))
            migrated_nodes.append(node)
            continue
        if len(node.input) not in {3, 4}:
            raise OnnxGraphSurgeryError(
                "%s has unsupported input count %s"
                % (node.name, len(node.input))
            )
        activation, packed_name, scales_name = node.input[:3]
        if packed_name not in initializers or scales_name not in initializers:
            raise OnnxGraphSurgeryError(
                "%s requires initializer-backed packed weights and scales"
                % node.name
            )
        zero_points = None
        if len(node.input) == 4 and node.input[3]:
            zero_name = node.input[3]
            if zero_name not in initializers:
                raise OnnxGraphSurgeryError(
                    "%s has non-initializer zero points" % node.name
                )
            zero_points = numpy_helper.to_array(initializers[zero_name])
        k = _attribute(node, "K")
        n = _attribute(node, "N")
        bits = _attribute(node, "bits", 4)
        block_size = _attribute(node, "block_size", 32)
        if k is None or n is None or bits is None or block_size is None:
            raise OnnxGraphSurgeryError(
                "%s is missing K, N, bits, or block_size" % node.name
            )
        unpacked = unpack_matmul_nbits_weight(
            numpy_helper.to_array(initializers[packed_name]),
            numpy_helper.to_array(initializers[scales_name]),
            k=k,
            n=n,
            bits=bits,
            block_size=block_size,
            zero_points=zero_points,
        )
        weight_name = node.name + "_dequantized_weight"
        if weight_name in initializers:
            raise OnnxGraphSurgeryError(
                "generated initializer name already exists: %s" % weight_name
            )
        weight_initializer = numpy_helper.from_array(unpacked.weight, name=weight_name)
        added_initializers.append(weight_initializer)
        migrated_node = onnx.helper.make_node(
            "MatMul",
            [activation, weight_name],
            list(node.output),
            name=node.name + "_FP16MatMul",
        )
        migrated_nodes.append(migrated_node)
        removed_initializers.update({packed_name, scales_name})
        if len(node.input) == 4 and node.input[3]:
            removed_initializers.add(node.input[3])
        replaced.append(
            {
                "node": node.name,
                "activation": activation,
                "output": node.output[0],
                "K": unpacked.k,
                "N": unpacked.n,
                "blocks": unpacked.blocks,
                "bits": unpacked.bits,
                "block_size": unpacked.block_size,
                "weight_shape": list(unpacked.weight.shape),
                "weight_dtype": str(unpacked.weight.dtype),
            }
        )

    if not replaced:
        raise OnnxGraphSurgeryError("no MatMulNBits nodes were found")
    if unsupported:
        raise OnnxGraphSurgeryError(
            "graph still contains unsupported custom operators: %s"
            % ", ".join(sorted(set(unsupported)))
        )

    referenced = {
        name
        for node in migrated_nodes
        for name in list(node.input) + list(node.output)
    }
    kept_initializers = [
        item
        for item in graph.initializer
        if item.name not in removed_initializers or item.name in referenced
    ]
    kept_initializers.extend(added_initializers)
    del graph.initializer[:]
    graph.initializer.extend(kept_initializers)
    del graph.node[:]
    graph.node.extend(migrated_nodes)

    removed_opsets = [
        item for item in model.opset_import if item.domain == "com.microsoft"
    ]
    for item in removed_opsets:
        model.opset_import.remove(item)

    normalized_transposes = _normalize_transpose_perms(model, onnx, graph)
    onnx.checker.check_model(model, full_check=False)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_name(output_path.name + ".tmp")
    try:
        onnx.save_model(model, str(temporary))
        temporary.replace(output_path)
    finally:
        if temporary.exists():
            temporary.unlink()

    operator_types = Counter(
        "%s:%s" % (node.domain or "ai.onnx", node.op_type)
        for node in graph.node
    )
    report = {
        "schema_version": 1,
        "source": {
            "path": str(input_path),
            "bytes": source_bytes,
            "sha256": source_sha256,
        },
        "output": {
            "path": str(output_path),
            "bytes": output_path.stat().st_size,
            "sha256": _sha256(output_path),
        },
        "replaced_nodes": len(replaced),
        "removed_initializers": len(removed_initializers),
        "added_initializers": len(added_initializers),
        "normalized_transposes": normalized_transposes,
        "removed_opset_domains": [item.domain for item in removed_opsets],
        "operator_counts": dict(sorted(operator_types.items())),
        "nodes": replaced,
    }
    if report_path is not None:
        report_path = Path(report_path)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        encoded = json.dumps(report, indent=2, sort_keys=True) + "\n"
        temporary_report = report_path.with_name(report_path.name + ".tmp")
        temporary_report.write_text(encoded, encoding="utf-8")
        temporary_report.replace(report_path)
    return report
