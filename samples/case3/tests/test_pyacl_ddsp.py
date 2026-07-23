from __future__ import annotations

import ctypes
from pathlib import Path
import tempfile
import threading
import unittest

import numpy as np

from pyacl_ddsp import (
    ACL_MEMCPY_DEVICE_TO_HOST,
    ACL_MEMCPY_HOST_TO_DEVICE,
    INPUT_SHAPES,
    OUTPUT_SHAPES,
    PyAclModelRunner,
)
from realtime_ddsp import create_controls_model


class FakeUtil:
    def __init__(self, owner: "FakeAcl") -> None:
        self.owner = owner

    def bytes_to_ptr(self, value: bytes) -> int:
        pointer = self.owner.next_pointer()
        self.owner.host_bytes[pointer] = value
        return pointer


class FakeRt:
    def __init__(self, owner: "FakeAcl") -> None:
        self.owner = owner

    def set_device(self, device_id: int) -> int:
        self.owner.device_id = device_id
        return 0

    def reset_device(self, _device_id: int) -> int:
        self.owner.reset_count += 1
        return 0

    def create_context(self, device_id: int) -> tuple[str, int]:
        return f"context-{device_id}", 0

    def set_context(self, context: str) -> int:
        self.owner.context_calls.append((threading.get_ident(), context))
        return 0

    def destroy_context(self, _context: str) -> int:
        self.owner.destroy_context_count += 1
        return 0

    def malloc(self, size: int, _policy: int) -> tuple[int, int]:
        pointer = self.owner.next_pointer()
        self.owner.device_memory[pointer] = bytearray(size)
        return pointer, 0

    def free(self, pointer: int) -> int:
        self.owner.device_memory.pop(pointer)
        return 0

    def memcpy(
        self,
        destination: int,
        _destination_size: int,
        source: int,
        count: int,
        kind: int,
    ) -> int:
        if kind == ACL_MEMCPY_HOST_TO_DEVICE:
            self.owner.device_memory[destination][:count] = self.owner.host_bytes[
                source
            ][:count]
        elif kind == ACL_MEMCPY_DEVICE_TO_HOST:
            ctypes.memmove(
                destination, bytes(self.owner.device_memory[source][:count]), count
            )
        else:
            raise AssertionError(f"Unexpected memcpy kind: {kind}")
        return 0


class FakeMdl:
    input_names = ("pw_scaled", "state", "f0_scaled")
    output_names = (
        "node:noise_amps",
        "node:state_out",
        "node:amplitude",
        "node:harmonics",
    )

    def __init__(self, owner: "FakeAcl") -> None:
        self.owner = owner

    def load_from_file(self, _path: str) -> tuple[int, int]:
        return 7, 0

    def unload(self, _model_id: int) -> int:
        self.owner.unload_count += 1
        return 0

    @staticmethod
    def create_desc() -> dict[str, object]:
        return {}

    @staticmethod
    def get_desc(_desc: object, _model_id: int) -> int:
        return 0

    def destroy_desc(self, _desc: object) -> int:
        self.owner.destroy_desc_count += 1
        return 0

    @staticmethod
    def create_dataset() -> list[dict[str, int]]:
        return []

    @staticmethod
    def add_dataset_buffer(
        dataset: list[dict[str, int]], buffer: dict[str, int]
    ) -> int:
        dataset.append(buffer)
        return 0

    def destroy_dataset(self, _dataset: object) -> int:
        self.owner.destroy_dataset_count += 1
        return 0

    def get_num_inputs(self, _desc: object) -> int:
        return len(self.input_names)

    def get_num_outputs(self, _desc: object) -> int:
        return len(self.output_names)

    def get_input_name_by_index(self, _desc: object, index: int) -> str:
        return self.input_names[index]

    def get_output_name_by_index(self, _desc: object, index: int) -> str:
        return self.output_names[index]

    def get_input_dims(self, _desc: object, index: int):
        shape = INPUT_SHAPES[self.input_names[index]]
        return {"dimCount": len(shape), "dims": list(shape)}, 0

    def get_output_dims(self, _desc: object, index: int):
        name = self.output_names[index].split(":")[-1]
        shape = OUTPUT_SHAPES[name]
        return {"dimCount": len(shape), "dims": list(shape)}, 0

    @staticmethod
    def get_input_data_type(_desc: object, _index: int) -> int:
        return 0

    @staticmethod
    def get_output_data_type(_desc: object, _index: int) -> int:
        return 0

    def get_input_size_by_index(self, _desc: object, index: int) -> int:
        return int(np.prod(INPUT_SHAPES[self.input_names[index]])) * 4

    def get_output_size_by_index(self, _desc: object, index: int) -> int:
        name = self.output_names[index].split(":")[-1]
        return int(np.prod(OUTPUT_SHAPES[name])) * 4

    def execute(
        self,
        _model_id: int,
        input_dataset: list[dict[str, int]],
        output_dataset: list[dict[str, int]],
    ) -> int:
        inputs = {}
        for name, buffer in zip(self.input_names, input_dataset):
            inputs[name] = np.frombuffer(
                self.owner.device_memory[buffer["ptr"]], dtype=np.float32
            ).copy()
        expected_outputs = {
            "amplitude": np.asarray(
                [inputs["f0_scaled"][0] + inputs["pw_scaled"][0]],
                dtype=np.float32,
            ),
            "harmonics": np.arange(60, dtype=np.float32),
            "noise_amps": np.arange(65, dtype=np.float32) / 10.0,
            "state_out": inputs["state"] + 1.0,
        }
        for raw_name, buffer in zip(self.output_names, output_dataset):
            name = raw_name.split(":")[-1]
            self.owner.device_memory[buffer["ptr"]][:] = expected_outputs[
                name
            ].tobytes()
        return 0


class FakeAcl:
    ACL_FLOAT = 0

    def __init__(self) -> None:
        self._next_pointer = 100
        self.host_bytes: dict[int, bytes] = {}
        self.device_memory: dict[int, bytearray] = {}
        self.context_calls: list[tuple[int, str]] = []
        self.device_id = None
        self.finalize_count = 0
        self.reset_count = 0
        self.unload_count = 0
        self.destroy_context_count = 0
        self.destroy_desc_count = 0
        self.destroy_dataset_count = 0
        self.util = FakeUtil(self)
        self.rt = FakeRt(self)
        self.mdl = FakeMdl(self)

    def next_pointer(self) -> int:
        self._next_pointer += 1
        return self._next_pointer

    @staticmethod
    def init() -> int:
        return 0

    def finalize(self) -> int:
        self.finalize_count += 1
        return 0

    @staticmethod
    def create_data_buffer(pointer: int, size: int) -> dict[str, int]:
        return {"ptr": pointer, "size": size}

    @staticmethod
    def destroy_data_buffer(_buffer: object) -> int:
        return 0


class PyAclModelRunnerTest(unittest.TestCase):
    def setUp(self) -> None:
        handle = tempfile.NamedTemporaryFile(suffix=".om", delete=False)
        handle.close()
        self.model_path = Path(handle.name)

    def tearDown(self) -> None:
        self.model_path.unlink(missing_ok=True)

    def test_name_mapping_inference_and_idempotent_cleanup(self) -> None:
        acl = FakeAcl()
        runner = PyAclModelRunner(self.model_path, device_id=2, acl_module=acl)
        outputs = runner.infer(
            {
                "state": np.arange(512, dtype=np.float32),
                "f0_scaled": np.asarray([0.25], dtype=np.float32),
                "pw_scaled": np.asarray([0.5], dtype=np.float32),
            }
        )

        self.assertEqual(acl.device_id, 2)
        self.assertEqual(set(outputs), set(OUTPUT_SHAPES))
        self.assertAlmostEqual(float(outputs["amplitude"][0]), 0.75)
        np.testing.assert_array_equal(
            outputs["state_out"], np.arange(512, dtype=np.float32) + 1.0
        )
        self.assertEqual(acl.context_calls[-1][1], "context-2")

        runner.close()
        runner.close()
        self.assertEqual(acl.finalize_count, 1)
        self.assertEqual(acl.reset_count, 1)
        self.assertEqual(acl.unload_count, 1)
        self.assertEqual(acl.destroy_context_count, 1)
        self.assertEqual(acl.destroy_dataset_count, 2)
        self.assertFalse(acl.device_memory)

    def test_invalid_input_shape_is_rejected_before_execute(self) -> None:
        runner = PyAclModelRunner(
            self.model_path, acl_module=FakeAcl()
        )
        try:
            with self.assertRaisesRegex(ValueError, "state shape"):
                runner.infer(
                    {
                        "state": np.zeros(511, dtype=np.float32),
                        "f0_scaled": np.zeros(1, dtype=np.float32),
                        "pw_scaled": np.zeros(1, dtype=np.float32),
                    }
                )
        finally:
            runner.close()

    def test_auto_backend_rejects_unknown_model_extension(self) -> None:
        with self.assertRaisesRegex(ValueError, "Cannot infer model backend"):
            create_controls_model(Path("model.bin"))


if __name__ == "__main__":
    unittest.main()
