import acl  # type: ignore[import-not-found]
import numpy as np

from ssdlite.backend_base import DetectionBackend

ACL_MEM_MALLOC_HUGE_FIRST = 0
ACL_MEMCPY_HOST_TO_DEVICE = 1
ACL_MEMCPY_DEVICE_TO_HOST = 2


def _check_ret(result, msg):
	if isinstance(result, tuple):
		ret = result[-1]
	else:
		ret = result
	if not isinstance(ret, int):
		raise RuntimeError(f"{msg} returned non-integer: {ret}")
	if ret != 0:
		raise RuntimeError(f"{msg} failed, ret={ret} (0x{ret:X})")
	return ret


class NpuBackend(DetectionBackend):
	def __init__(self, model_path, device_id: int):
		super().__init__(model_path, strict_ssd=True)
		self.device_id = device_id

		self.context = None
		self.stream = None
		self.model_id = None
		self.model_desc = None
		self.input_dataset = None
		self.output_dataset = None
		self.input_buffers = []
		self.output_buffers = []
		self.output_shapes = []

		self._init_acl()
		self._load_model()
		self._prepare_io_buffers()

	def _init_acl(self) -> None:
		ret = acl.init()
		if ret not in (0, 100002):
			raise RuntimeError(f"acl.init failed, ret={ret}")

		ret = acl.rt.set_device(self.device_id)
		_check_ret(ret, "acl.rt.set_device")

		self.context, ret = acl.rt.create_context(self.device_id)
		_check_ret((self.context, ret), "acl.rt.create_context")

		self.stream, ret = acl.rt.create_stream()
		_check_ret((self.stream, ret), "acl.rt.create_stream")

	def _load_model(self) -> None:
		self.model_id, ret = acl.mdl.load_from_file(str(self.model_path))
		_check_ret((self.model_id, ret), "acl.mdl.load_from_file")

		self.model_desc = acl.mdl.create_desc()
		ret = acl.mdl.get_desc(self.model_desc, self.model_id)
		_check_ret(ret, "acl.mdl.get_desc")

	def _get_output_shape(self, output_idx: int):
		dims_info, ret = acl.mdl.get_output_dims(self.model_desc, output_idx)
		if ret != 0:
			return None

		dim_count = dims_info.get("dimCount", 0)
		dims = dims_info.get("dims", [])
		if dim_count <= 0 or not dims:
			return None

		shape = tuple(int(value) for value in dims[:dim_count])
		if np.prod(shape) <= 0:
			return None
		return shape

	def _prepare_io_buffers(self) -> None:
		self.input_dataset = acl.mdl.create_dataset()
		self.output_dataset = acl.mdl.create_dataset()

		input_num = acl.mdl.get_num_inputs(self.model_desc)
		output_num = acl.mdl.get_num_outputs(self.model_desc)

		for index in range(input_num):
			input_size = acl.mdl.get_input_size_by_index(self.model_desc, index)
			input_ptr, ret = acl.rt.malloc(input_size, ACL_MEM_MALLOC_HUGE_FIRST)
			_check_ret((input_ptr, ret), f"acl.rt.malloc input[{index}]")

			input_buffer = acl.create_data_buffer(input_ptr, input_size)
			if input_buffer is None:
				raise RuntimeError(f"acl.create_data_buffer input[{index}] failed")

			result = acl.mdl.add_dataset_buffer(self.input_dataset, input_buffer)
			_check_ret(result, f"acl.mdl.add_dataset_buffer input[{index}]")

			self.input_buffers.append({
				"ptr": input_ptr,
				"size": input_size,
				"buffer": input_buffer,
			})

		for index in range(output_num):
			output_size = acl.mdl.get_output_size_by_index(self.model_desc, index)
			output_ptr, ret = acl.rt.malloc(output_size, ACL_MEM_MALLOC_HUGE_FIRST)
			_check_ret((output_ptr, ret), f"acl.rt.malloc output[{index}]")

			output_buffer = acl.create_data_buffer(output_ptr, output_size)
			if output_buffer is None:
				raise RuntimeError(f"acl.create_data_buffer output[{index}] failed")

			result = acl.mdl.add_dataset_buffer(self.output_dataset, output_buffer)
			_check_ret(result, f"acl.mdl.add_dataset_buffer output[{index}]")

			self.output_buffers.append({
				"ptr": output_ptr,
				"size": output_size,
				"buffer": output_buffer,
			})
			self.output_shapes.append(self._get_output_shape(index))

	def _run_model(self, input_tensor: np.ndarray) -> list[np.ndarray]:
		if not isinstance(input_tensor, np.ndarray):
			raise TypeError("input_tensor must be numpy.ndarray")

		input_array = np.ascontiguousarray(input_tensor.astype(np.float32))
		input_bytes = input_array.tobytes()
		first_input = self.input_buffers[0]
		if len(input_bytes) > first_input["size"]:
			raise ValueError(f"Input bytes {len(input_bytes)} exceed model input size {first_input['size']}")

		host_in_ptr = acl.util.bytes_to_ptr(input_bytes)
		ret = acl.rt.memcpy(
			first_input["ptr"],
			first_input["size"],
			host_in_ptr,
			len(input_bytes),
			ACL_MEMCPY_HOST_TO_DEVICE,
		)
		_check_ret(ret, "acl.rt.memcpy host_to_device")

		ret = acl.mdl.execute(self.model_id, self.input_dataset, self.output_dataset)
		_check_ret(ret, "acl.mdl.execute")

		outputs = []
		for index, output in enumerate(self.output_buffers):
			float_count = output["size"] // np.dtype(np.float32).itemsize
			host_out = np.zeros(float_count, dtype=np.float32)
			host_out_ptr = host_out.ctypes.data

			ret = acl.rt.memcpy(
				host_out_ptr,
				output["size"],
				output["ptr"],
				output["size"],
				ACL_MEMCPY_DEVICE_TO_HOST,
			)
			_check_ret(ret, f"acl.rt.memcpy device_to_host output[{index}]")

			tensor = host_out
			shape = self.output_shapes[index]
			if shape is not None and int(np.prod(shape)) == tensor.size:
				tensor = tensor.reshape(shape)
			outputs.append(tensor)

		return outputs

	def print_model_io(self) -> None:
		print(f"Input size used: {self.input_hw[0]}x{self.input_hw[1]}")
		for index, shape in enumerate(self.output_shapes):
			print(f"Output[{index}] shape={shape}")

	def release(self) -> None:
		if self.input_dataset is not None:
			for buffer in self.input_buffers:
				acl.destroy_data_buffer(buffer["buffer"])
				acl.rt.free(buffer["ptr"])
			acl.mdl.destroy_dataset(self.input_dataset)
			self.input_dataset = None

		if self.output_dataset is not None:
			for buffer in self.output_buffers:
				acl.destroy_data_buffer(buffer["buffer"])
				acl.rt.free(buffer["ptr"])
			acl.mdl.destroy_dataset(self.output_dataset)
			self.output_dataset = None

		if self.model_desc is not None:
			acl.mdl.destroy_desc(self.model_desc)
			self.model_desc = None

		if self.model_id is not None:
			acl.mdl.unload(self.model_id)
			self.model_id = None

		if self.stream is not None:
			acl.rt.destroy_stream(self.stream)
			self.stream = None

		if self.context is not None:
			acl.rt.destroy_context(self.context)
			self.context = None

		acl.rt.reset_device(self.device_id)
		acl.finalize()