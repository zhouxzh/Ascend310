from __future__ import annotations

import importlib.util
import threading
import unittest


HAS_CV2 = importlib.util.find_spec("cv2") is not None


@unittest.skipUnless(HAS_CV2, "ACL lifecycle tests require the OpenCV runtime")
class AclLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        from palmprint_workbench.runtime import adapters as palm_backends

        self.backends = palm_backends
        self.environment = palm_backends._AclEnvironment
        with self.environment.lock:
            self.saved = {
                "acl": self.environment.acl,
                "initialized": self.environment.initialized,
                "owns_runtime": self.environment.owns_runtime,
                "device_set": self.environment.device_set,
                "device_reset": self.environment.device_reset,
                "active_runners": self.environment.active_runners,
                "events": list(self.environment.events),
                "last_shutdown": self.environment.last_shutdown,
            }
            self.environment.acl = None
            self.environment.initialized = False
            self.environment.owns_runtime = False
            self.environment.device_set = False
            self.environment.device_reset = False
            self.environment.active_runners = 0
            self.environment.events = []
            self.environment.last_shutdown = None

    def tearDown(self) -> None:
        with self.environment.lock:
            for key, value in self.saved.items():
                setattr(self.environment, key, value)

    class _Runtime:
        def __init__(self, calls: list[str]) -> None:
            self.calls = calls

        def set_context(self, _context: object) -> int:
            self.calls.append("set_context")
            return 0

        def synchronize_device(self) -> int:
            self.calls.append("synchronize_device")
            return 0

        def free(self, pointer: object) -> int:
            self.calls.append(f"free:{pointer}")
            return 0

        def reset_device(self, _device: int) -> int:
            self.calls.append("reset_device")
            return 0

        def destroy_context(self, context: object) -> int:
            self.calls.append(f"destroy_context:{context}")
            return 0

    class _Model:
        def __init__(self, calls: list[str]) -> None:
            self.calls = calls

        def destroy_dataset(self, dataset: object) -> int:
            self.calls.append(f"destroy_dataset:{dataset}")
            return 0

        def destroy_desc(self, desc: object) -> int:
            self.calls.append(f"destroy_desc:{desc}")
            return 0

        def unload(self, model_id: object) -> int:
            self.calls.append(f"unload:{model_id}")
            return 0

    class _Acl:
        def __init__(self, calls: list[str]) -> None:
            self.calls = calls
            self.rt = AclLifecycleTests._Runtime(calls)
            self.mdl = AclLifecycleTests._Model(calls)

        def destroy_data_buffer(self, data_buffer: object) -> int:
            self.calls.append(f"destroy_data_buffer:{data_buffer}")
            return 0

        def finalize(self) -> int:
            self.calls.append("finalize")
            return 0

    def test_shutdown_blocks_reset_until_live_runner_is_released(self) -> None:
        calls: list[str] = []
        fake_acl = self._Acl(calls)
        with self.environment.lock:
            self.environment.acl = fake_acl
            self.environment.initialized = True
            self.environment.owns_runtime = True
            self.environment.device_set = True
            self.environment.active_runners = 1

        blocked = self.backends.shutdown_acl_runtime()
        self.assertFalse(blocked["ok"])
        self.assertEqual(blocked["status"], "blocked_active_runners")
        self.assertEqual(calls, [])

        with self.environment.lock:
            self.environment.active_runners = 0
        released = self.backends.shutdown_acl_runtime()
        self.assertTrue(released["ok"])
        self.assertEqual(released["status"], "released")
        self.assertEqual(calls, ["reset_device", "finalize"])

    def test_runner_close_uses_dataset_before_device_memory_order(self) -> None:
        calls: list[str] = []
        runner = object.__new__(self.backends._OmRunner)
        runner.acl = self._Acl(calls)
        runner.context = "context"
        runner.model_id = "model"
        runner.desc = "desc"
        runner.input_dataset = "input_dataset"
        runner.output_dataset = "output_dataset"
        runner.input_buffer = {"data_buffer": "input_buffer", "pointer": "input_ptr"}
        runner.output_buffer = {"data_buffer": "output_buffer", "pointer": "output_ptr"}
        runner.lock = threading.Lock()
        runner.closed = False
        runner.closing = False
        runner._runtime_registered = False
        runner.cleanup_diagnostics = []

        diagnostics = runner.close()

        self.assertTrue(all(item["ok"] for item in diagnostics))
        self.assertLess(calls.index("destroy_data_buffer:input_buffer"), calls.index("destroy_dataset:input_dataset"))
        self.assertLess(calls.index("destroy_dataset:input_dataset"), calls.index("free:input_ptr"))
        self.assertLess(calls.index("destroy_data_buffer:output_buffer"), calls.index("destroy_dataset:output_dataset"))
        self.assertLess(calls.index("destroy_dataset:output_dataset"), calls.index("free:output_ptr"))
        self.assertLess(calls.index("free:output_ptr"), calls.index("destroy_desc:desc"))

    def test_failed_resource_cleanup_keeps_runner_owned_until_retry(self) -> None:
        calls: list[str] = []
        fake_acl = self._Acl(calls)
        failed_once = {"value": False}
        original_destroy_dataset = fake_acl.mdl.destroy_dataset

        def destroy_dataset_once(dataset: object) -> int:
            if dataset == "input_dataset" and not failed_once["value"]:
                failed_once["value"] = True
                calls.append(f"destroy_dataset_failed:{dataset}")
                return 7
            return original_destroy_dataset(dataset)

        fake_acl.mdl.destroy_dataset = destroy_dataset_once
        with self.environment.lock:
            self.environment.acl = fake_acl
            self.environment.initialized = True
            self.environment.owns_runtime = True
            self.environment.device_set = True
            self.environment.active_runners = 1

        runner = object.__new__(self.backends._OmRunner)
        runner.acl = fake_acl
        runner.context = "context"
        runner.model_id = "model"
        runner.desc = "desc"
        runner.input_dataset = "input_dataset"
        runner.output_dataset = "output_dataset"
        runner.input_buffer = {"data_buffer": "input_buffer", "pointer": "input_ptr"}
        runner.output_buffer = {"data_buffer": "output_buffer", "pointer": "output_ptr"}
        runner.input_shape = runner.output_shape = ()
        runner.lock = threading.Lock()
        runner.closed = False
        runner.closing = False
        runner._runtime_registered = True
        runner.cleanup_diagnostics = []

        first = runner.close(suppress_errors=True)
        self.assertFalse(runner.closed)
        self.assertTrue(runner.closing)
        self.assertTrue(runner._runtime_registered)
        self.assertTrue(any(item.get("ok") is False for item in first))
        self.assertEqual(self.environment.active_runners, 1)

        second = runner.close()
        self.assertTrue(runner.closed)
        self.assertFalse(runner._runtime_registered)
        self.assertTrue(all(item.get("ok") is True for item in second if item.get("phase") != "free_input_device_buffer" or item.get("status") != "blocked"))
        self.assertEqual(self.environment.active_runners, 0)

    def test_failed_data_buffer_does_not_destroy_its_dataset(self) -> None:
        calls: list[str] = []
        fake_acl = self._Acl(calls)
        failed_once = {"value": False}
        original_destroy_buffer = fake_acl.destroy_data_buffer

        def destroy_buffer_once(data_buffer: object) -> int:
            if data_buffer == "input_buffer" and not failed_once["value"]:
                failed_once["value"] = True
                calls.append(f"destroy_data_buffer_failed:{data_buffer}")
                return 9
            return original_destroy_buffer(data_buffer)

        fake_acl.destroy_data_buffer = destroy_buffer_once
        with self.environment.lock:
            self.environment.acl = fake_acl
            self.environment.initialized = True
            self.environment.owns_runtime = True
            self.environment.device_set = True
            self.environment.active_runners = 1

        runner = object.__new__(self.backends._OmRunner)
        runner.acl = fake_acl
        runner.context = "context"
        runner.model_id = "model"
        runner.desc = "desc"
        runner.input_dataset = "input_dataset"
        runner.output_dataset = "output_dataset"
        runner.input_buffer = {"data_buffer": "input_buffer", "pointer": "input_ptr"}
        runner.output_buffer = {"data_buffer": "output_buffer", "pointer": "output_ptr"}
        runner.input_shape = runner.output_shape = ()
        runner.lock = threading.Lock()
        runner.closed = False
        runner.closing = False
        runner._runtime_registered = True
        runner.cleanup_diagnostics = []

        first = runner.close(suppress_errors=True)
        self.assertFalse(runner.closed)
        self.assertIn("destroy_data_buffer_failed:input_buffer", calls)
        self.assertNotIn("destroy_dataset:input_dataset", calls)
        self.assertTrue(any(item.get("status") == "blocked" for item in first))

        second = runner.close()
        self.assertTrue(runner.closed)
        self.assertEqual(self.environment.active_runners, 0)
        self.assertIn("destroy_dataset:input_dataset", calls)

    def test_allocate_retains_partial_handles_for_constructor_cleanup(self) -> None:
        """A failed allocation must not lose local ACL handles before close()."""

        calls: list[str] = []
        fake_acl = self._Acl(calls)
        fake_acl.mdl.create_dataset = lambda: "partial_input_dataset"
        fake_acl.mdl.get_input_size_by_index = lambda _desc, _index: 16
        fake_acl.mdl.add_dataset_buffer = lambda _dataset, _buffer: 11
        fake_acl.rt.malloc = lambda _size, _policy: ("partial_input_ptr", 0)
        fake_acl.create_data_buffer = lambda _pointer, _size: "partial_input_buffer"
        fake_acl.destroy_data_buffer = lambda _buffer: 7

        runner = object.__new__(self.backends._OmRunner)
        runner.acl = fake_acl
        runner.desc = "desc"
        runner.input_dataset = None
        runner.input_buffer = None
        runner.output_dataset = None
        runner.output_buffer = None
        runner.cleanup_diagnostics = []

        with self.assertRaises(RuntimeError):
            runner._allocate(True, 0)

        self.assertEqual(runner.input_dataset, "partial_input_dataset")
        self.assertIsNotNone(runner.input_buffer)
        self.assertEqual(runner.input_buffer["pointer"], "partial_input_ptr")
        self.assertEqual(runner.input_buffer["data_buffer"], "partial_input_buffer")


if __name__ == "__main__":
    unittest.main()
