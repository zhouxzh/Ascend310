import numpy as np


ACL_MEM_MALLOC_HUGE_FIRST = 0
ACL_MEMCPY_HOST_TO_DEVICE = 1
ACL_MEMCPY_DEVICE_TO_HOST = 2
ACL_ALREADY_INITIALIZED = 100002
acl = None


def _load_acl():
    global acl
    if acl is None:
        import acl as acl_module  # type: ignore[import-not-found]

        acl = acl_module
    return acl


def _ret_code(result):
    if isinstance(result, tuple):
        result = result[-1]
    if not isinstance(result, int):
        raise RuntimeError(f"ACL call returned non-integer status: {result}")
    return result


def _check_ret(result, message):
    ret = _ret_code(result)
    if ret != 0:
        recent_error = ""
        acl_module = _load_acl()
        if hasattr(acl_module, "get_recent_err_msg"):
            recent_error = f", recent_error={acl_module.get_recent_err_msg()}"
        raise RuntimeError(f"{message} failed, ret={ret}{recent_error}")
    return ret


def _dims_to_shape(dims_info):
    if not isinstance(dims_info, dict):
        return None

    dims = dims_info.get("dims") or []
    dim_count = int(dims_info.get("dimCount") or len(dims))
    if dim_count <= 0 or not dims:
        return None

    shape = tuple(int(value) for value in dims[:dim_count])
    if any(value <= 0 for value in shape):
        return None
    return shape


class AclRuntime:
    def __init__(self, device_id=0, finalize_on_release=True):
        self.acl = _load_acl()
        self.device_id = device_id
        self.finalize_on_release = finalize_on_release
        self.context = None
        self.stream = None
        self._initialized = False
        self._init()

    def _init(self):
        ret = self.acl.init()
        if ret not in (0, ACL_ALREADY_INITIALIZED):
            _check_ret(ret, "acl.init")
        self._initialized = True

        ret = self.acl.rt.set_device(self.device_id)
        _check_ret(ret, "acl.rt.set_device")

        self.context, ret = self.acl.rt.create_context(self.device_id)
        _check_ret(ret, "acl.rt.create_context")

        self.stream, ret = self.acl.rt.create_stream()
        _check_ret(ret, "acl.rt.create_stream")

    def set_context(self):
        if self.context is not None and hasattr(self.acl.rt, "set_context"):
            ret = self.acl.rt.set_context(self.context)
            _check_ret(ret, "acl.rt.set_context")

    def release(self):
        if self.stream is not None:
            self.acl.rt.destroy_stream(self.stream)
            self.stream = None

        if self.context is not None:
            self.acl.rt.destroy_context(self.context)
            self.context = None

        if self._initialized and self.finalize_on_release:
            self.acl.rt.reset_device(self.device_id)
            self.acl.finalize()
        self._initialized = False


class AclModel:
    def __init__(self, model_path, device_id=0, runtime=None):
        self.acl = _load_acl()
        self.model_path = str(model_path)
        self.runtime = runtime or AclRuntime(device_id=device_id)
        self._own_runtime = runtime is None
        self.model_id = None
        self.model_desc = None
        self.input_dataset = None
        self.output_dataset = None
        self.input_buffers = []
        self.output_buffers = []
        self.input_shapes = []
        self.output_shapes = []

        try:
            self._load_model()
            self._prepare_io_buffers()
        except Exception:
            self.release()
            raise

    def _load_model(self):
        self.runtime.set_context()
        self.model_id, ret = self.acl.mdl.load_from_file(self.model_path)
        _check_ret(ret, f"acl.mdl.load_from_file {self.model_path}")

        self.model_desc = self.acl.mdl.create_desc()
        ret = self.acl.mdl.get_desc(self.model_desc, self.model_id)
        _check_ret(ret, "acl.mdl.get_desc")

    def _get_input_shape(self, index):
        get_input_dims = getattr(self.acl.mdl, "get_input_dims", None)
        if get_input_dims is None:
            return None
        dims_info, ret = get_input_dims(self.model_desc, index)
        if ret != 0:
            return None
        return _dims_to_shape(dims_info)

    def _get_output_shape(self, index):
        get_output_dims = getattr(self.acl.mdl, "get_output_dims", None)
        if get_output_dims is None:
            return None
        dims_info, ret = get_output_dims(self.model_desc, index)
        if ret != 0:
            return None
        return _dims_to_shape(dims_info)

    def _prepare_io_buffers(self):
        self.input_dataset = self.acl.mdl.create_dataset()
        self.output_dataset = self.acl.mdl.create_dataset()

        input_num = self.acl.mdl.get_num_inputs(self.model_desc)
        output_num = self.acl.mdl.get_num_outputs(self.model_desc)

        for index in range(input_num):
            input_size = self.acl.mdl.get_input_size_by_index(self.model_desc, index)
            input_ptr, ret = self.acl.rt.malloc(input_size, ACL_MEM_MALLOC_HUGE_FIRST)
            _check_ret(ret, f"acl.rt.malloc input[{index}]")

            input_buffer = self.acl.create_data_buffer(input_ptr, input_size)
            if input_buffer is None:
                raise RuntimeError(f"acl.create_data_buffer input[{index}] failed")
            ret = self.acl.mdl.add_dataset_buffer(self.input_dataset, input_buffer)
            _check_ret(ret, f"acl.mdl.add_dataset_buffer input[{index}]")

            self.input_buffers.append({"ptr": input_ptr, "size": input_size, "buffer": input_buffer})
            self.input_shapes.append(self._get_input_shape(index))

        for index in range(output_num):
            output_size = self.acl.mdl.get_output_size_by_index(self.model_desc, index)
            output_ptr, ret = self.acl.rt.malloc(output_size, ACL_MEM_MALLOC_HUGE_FIRST)
            _check_ret(ret, f"acl.rt.malloc output[{index}]")

            output_buffer = self.acl.create_data_buffer(output_ptr, output_size)
            if output_buffer is None:
                raise RuntimeError(f"acl.create_data_buffer output[{index}] failed")
            ret = self.acl.mdl.add_dataset_buffer(self.output_dataset, output_buffer)
            _check_ret(ret, f"acl.mdl.add_dataset_buffer output[{index}]")

            self.output_buffers.append({"ptr": output_ptr, "size": output_size, "buffer": output_buffer})
            self.output_shapes.append(self._get_output_shape(index))

    def _prepare_input_bytes(self, input_array, input_size):
        input_array = np.ascontiguousarray(input_array.astype(np.float32, copy=False))
        if input_array.nbytes == input_size:
            return input_array.tobytes()

        input_fp16 = np.ascontiguousarray(input_array.astype(np.float16))
        if input_fp16.nbytes == input_size:
            return input_fp16.tobytes()

        raise ValueError(
            f"Input bytes do not match model input size: "
            f"float32={input_array.nbytes}, float16={input_fp16.nbytes}, model={input_size}"
        )

    def _output_dtype(self, output_size, output_shape):
        if output_shape is not None:
            element_count = int(np.prod(output_shape))
            if element_count * np.dtype(np.float32).itemsize == output_size:
                return np.float32
            if element_count * np.dtype(np.float16).itemsize == output_size:
                return np.float16

        if output_size % np.dtype(np.float32).itemsize == 0:
            return np.float32
        if output_size % np.dtype(np.float16).itemsize == 0:
            return np.float16
        raise ValueError(f"Cannot infer output dtype for {output_size} bytes")

    def infer(self, input_array):
        if not isinstance(input_array, np.ndarray):
            raise TypeError("input_array must be numpy.ndarray")
        if not self.input_buffers:
            raise RuntimeError("Model has no input buffers")

        self.runtime.set_context()
        first_input = self.input_buffers[0]
        input_bytes = self._prepare_input_bytes(input_array, first_input["size"])

        host_input_ptr = self.acl.util.bytes_to_ptr(input_bytes)
        ret = self.acl.rt.memcpy(
            first_input["ptr"],
            first_input["size"],
            host_input_ptr,
            len(input_bytes),
            ACL_MEMCPY_HOST_TO_DEVICE,
        )
        _check_ret(ret, "acl.rt.memcpy host_to_device")

        ret = self.acl.mdl.execute(self.model_id, self.input_dataset, self.output_dataset)
        _check_ret(ret, "acl.mdl.execute")

        outputs = []
        for index, output in enumerate(self.output_buffers):
            output_size = output["size"]
            host_output_ptr, ret = self.acl.rt.malloc_host(output_size)
            _check_ret(ret, f"acl.rt.malloc_host output[{index}]")
            try:
                ret = self.acl.rt.memcpy(
                    host_output_ptr,
                    output_size,
                    output["ptr"],
                    output_size,
                    ACL_MEMCPY_DEVICE_TO_HOST,
                )
                _check_ret(ret, f"acl.rt.memcpy device_to_host output[{index}]")

                output_bytes = self.acl.util.ptr_to_bytes(host_output_ptr, output_size)
                output_shape = self.output_shapes[index]
                dtype = self._output_dtype(output_size, output_shape)
                tensor = np.frombuffer(output_bytes, dtype=dtype).astype(np.float32, copy=False).copy()
                if output_shape is not None and int(np.prod(output_shape)) == tensor.size:
                    tensor = tensor.reshape(output_shape)
                outputs.append(tensor)
            finally:
                self.acl.rt.free_host(host_output_ptr)

        return outputs

    def print_model_info(self):
        print(f"[ACL] model: {self.model_path}")
        print(f"[ACL] device_id: {self.runtime.device_id}")
        for index, item in enumerate(self.input_buffers):
            print(f"[ACL] input[{index}] size={item['size']} shape={self.input_shapes[index]}")
        for index, item in enumerate(self.output_buffers):
            print(f"[ACL] output[{index}] size={item['size']} shape={self.output_shapes[index]}")

    def release(self):
        if self.input_dataset is not None:
            for item in self.input_buffers:
                if item.get("buffer") is not None:
                    self.acl.destroy_data_buffer(item["buffer"])
                if item.get("ptr") is not None:
                    self.acl.rt.free(item["ptr"])
            self.acl.mdl.destroy_dataset(self.input_dataset)
            self.input_dataset = None
        self.input_buffers.clear()
        self.input_shapes.clear()

        if self.output_dataset is not None:
            for item in self.output_buffers:
                if item.get("buffer") is not None:
                    self.acl.destroy_data_buffer(item["buffer"])
                if item.get("ptr") is not None:
                    self.acl.rt.free(item["ptr"])
            self.acl.mdl.destroy_dataset(self.output_dataset)
            self.output_dataset = None
        self.output_buffers.clear()
        self.output_shapes.clear()

        if self.model_desc is not None:
            self.acl.mdl.destroy_desc(self.model_desc)
            self.model_desc = None

        if self.model_id is not None:
            self.acl.mdl.unload(self.model_id)
            self.model_id = None

        if self._own_runtime and self.runtime is not None:
            self.runtime.release()
            self.runtime = None

    close = release
