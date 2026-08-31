import threading
import time
import unittest
from types import SimpleNamespace

import numpy as np

from embedding_backend import EmbeddingBackend, EmbeddingError, ModelManager, _check_ret
class FakeResource:
    def release(self):
        return None


class FakeRegistry:
    def __init__(self):
        self.records = {}

    def add(self, model_id):
        self.records[model_id] = SimpleNamespace(model_id=model_id)

    def get(self, model_id):
        if model_id not in self.records:
            raise KeyError(model_id)
        return self.records[model_id]


class SerializedBackend(EmbeddingBackend):
    active = 0
    peak = 0
    guard = threading.Lock()

    def __init__(self, record, resource):
        self._model_id = record.model_id

    @property
    def model_id(self):
        return self._model_id

    @property
    def embedding_dim(self):
        return 2

    def encode_image(self, image_bgr):
        with self.guard:
            type(self).active += 1
            type(self).peak = max(type(self).peak, type(self).active)
        time.sleep(0.01)
        with self.guard:
            type(self).active -= 1
        return np.array([1.0, 0.0], dtype=np.float32)

    def encode_text(self, text):
        return self.encode_image(None)

    def release(self):
        return None


class ModelManagerTests(unittest.TestCase):
    def test_acl_result_tuple_uses_the_return_code(self):
        _check_ret((object(), 0), "tuple success")
        with self.assertRaises(EmbeddingError):
            _check_ret((object(), 12), "tuple failure")

    def test_encode_calls_are_serial_across_model_switches(self):
        registry = FakeRegistry()
        registry.add("a__npu__mixed_fp16")
        registry.add("b__npu__mixed_fp16")
        manager = ModelManager(
            registry=registry,
            resource_factory=lambda _device: FakeResource(),
            backend_factory=SerializedBackend,
        )
        threads = [
            threading.Thread(
                target=manager.encode_image,
                args=(model_id, np.zeros((1, 1, 3), dtype=np.uint8)),
            )
            for model_id in ("a__npu__mixed_fp16", "b__npu__mixed_fp16")
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(SerializedBackend.peak, 1)
        manager.release()


if __name__ == "__main__":
    unittest.main()
