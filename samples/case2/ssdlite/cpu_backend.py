from pathlib import Path

import onnxruntime as ort
import numpy as np

from ssdlite.backend_base import DetectionBackend


class CpuBackend(DetectionBackend):
	def __init__(self, model_path):
		super().__init__(model_path, strict_ssd=False)
		self.session = ort.InferenceSession(str(self.model_path), providers=["CPUExecutionProvider"])
		self.output_shapes = [output.shape for output in self.session.get_outputs()]

	def get_input(self):
		return self.session.get_inputs()[0]

	def get_outputs(self):
		return self.session.get_outputs()

	def _run_model(self, input_tensor: np.ndarray) -> dict[str, object]:
		input_name = self.get_input().name
		output_values = self.session.run(None, {input_name: input_tensor})
		output_names = [item.name for item in self.get_outputs()]
		return dict(zip(output_names, output_values))

	def print_model_io(self) -> None:
		input_meta = self.get_input()
		print(f"Input: name={input_meta.name}, shape={input_meta.shape}, type={input_meta.type}")
		print(f"Input size used: {self.input_hw[0]}x{self.input_hw[1]}")
		for output in self.get_outputs():
			print(f"Output: name={output.name}, shape={output.shape}, type={output.type}")

	def release(self) -> None:
		return None