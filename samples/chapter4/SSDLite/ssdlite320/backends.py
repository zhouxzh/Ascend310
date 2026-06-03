from __future__ import annotations

import numpy as np

ACL_MEM_MALLOC_HUGE_FIRST = 0
ACL_MEMCPY_HOST_TO_DEVICE = 1
ACL_MEMCPY_DEVICE_TO_HOST = 2
acl = None


class OnnxCpuRunner:
    def __init__(self, model_path: str):
        import onnxruntime as ort

        self.session = ort.InferenceSession(
            str(model_path),
            ort.SessionOptions(),
            providers=["CPUExecutionProvider"],
        )
        self.input_name = self.session.get_inputs()[0].name

    def infer(self, image_batch):
        return self.session.run(["boxes", "scores"], {self.input_name: image_batch})


def _ret_code(result):
    if isinstance(result, tuple):
        result = result[-1]
    if not isinstance(result, int):
        raise RuntimeError(f"ACL call returned non-integer status: {result}")
    return result


def _check_ret(ret, msg):
    ret = _ret_code(ret)
    if ret != 0:
        recent_error = ""
        if acl is not None and hasattr(acl, "get_recent_err_msg"):
            recent_error = f", recent_error={acl.get_recent_err_msg()}"
        raise RuntimeError(f"{msg} failed, ret={ret}{recent_error}")
    return ret


class AclNpuRunner:
    def __init__(self, model_path: str, device_id: int = 0):
        global acl
        if acl is None:
            import acl as acl_module

            acl = acl_module

        self.model_path = model_path
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
        self._acl_initialized = False

        self._init_acl()
        self._load_model()
        self._prepare_io_buffers()

    def _init_acl(self):
        ret = acl.init()
        if ret not in (0, 100002):
            raise RuntimeError(f"acl.init failed, ret={ret}")
        self._acl_initialized = True

        ret = acl.rt.set_device(self.device_id)
        _check_ret(ret, "acl.rt.set_device")

        self.context, ret = acl.rt.create_context(self.device_id)
        _check_ret(ret, "acl.rt.create_context")

        self.stream, ret = acl.rt.create_stream()
        _check_ret(ret, "acl.rt.create_stream")

    def _load_model(self):
        self.model_id, ret = acl.mdl.load_from_file(self.model_path)
        _check_ret(ret, "acl.mdl.load_from_file")

        self.model_desc = acl.mdl.create_desc()
        ret = acl.mdl.get_desc(self.model_desc, self.model_id)
        _check_ret(ret, "acl.mdl.get_desc")

    def _get_output_shape(self, output_idx):
        dims_info, ret = acl.mdl.get_output_dims(self.model_desc, output_idx)
        if ret != 0:
            return None

        dim_count = dims_info.get("dimCount", 0)
        dims = dims_info.get("dims", [])
        if dim_count <= 0 or not dims:
            return None

        shape = tuple(int(x) for x in dims[:dim_count])
        if np.prod(shape) <= 0:
            return None
        return shape

    def _prepare_io_buffers(self):
        self.input_dataset = acl.mdl.create_dataset()
        self.output_dataset = acl.mdl.create_dataset()

        input_num = acl.mdl.get_num_inputs(self.model_desc)
        output_num = acl.mdl.get_num_outputs(self.model_desc)

        for idx in range(input_num):
            input_size = acl.mdl.get_input_size_by_index(self.model_desc, idx)
            input_ptr, ret = acl.rt.malloc(input_size, ACL_MEM_MALLOC_HUGE_FIRST)
            _check_ret(ret, f"acl.rt.malloc input[{idx}]")

            input_buf = acl.create_data_buffer(input_ptr, input_size)
            ret = acl.mdl.add_dataset_buffer(self.input_dataset, input_buf)
            _check_ret(ret, f"acl.mdl.add_dataset_buffer input[{idx}]")
            self.input_buffers.append({"ptr": input_ptr, "size": input_size, "buffer": input_buf})

        for idx in range(output_num):
            output_size = acl.mdl.get_output_size_by_index(self.model_desc, idx)
            output_ptr, ret = acl.rt.malloc(output_size, ACL_MEM_MALLOC_HUGE_FIRST)
            _check_ret(ret, f"acl.rt.malloc output[{idx}]")

            output_buf = acl.create_data_buffer(output_ptr, output_size)
            ret = acl.mdl.add_dataset_buffer(self.output_dataset, output_buf)
            _check_ret(ret, f"acl.mdl.add_dataset_buffer output[{idx}]")
            self.output_buffers.append({"ptr": output_ptr, "size": output_size, "buffer": output_buf})
            self.output_shapes.append(self._get_output_shape(idx))

    def infer(self, input_np):
        if not isinstance(input_np, np.ndarray):
            raise TypeError("input_np must be numpy.ndarray")

        input_np = np.ascontiguousarray(input_np.astype(np.float32, copy=False))
        input_bytes = input_np.tobytes()

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
        for idx, out in enumerate(self.output_buffers):
            host_ptr, ret = acl.rt.malloc_host(out["size"])
            _check_ret(ret, f"acl.rt.malloc_host output[{idx}]")
            try:
                ret = acl.rt.memcpy(
                    host_ptr,
                    out["size"],
                    out["ptr"],
                    out["size"],
                    ACL_MEMCPY_DEVICE_TO_HOST,
                )
                _check_ret(ret, f"acl.rt.memcpy device_to_host output[{idx}]")

                output_bytes = acl.util.ptr_to_bytes(host_ptr, out["size"])
                tensor = np.frombuffer(output_bytes, dtype=np.float32).copy()
                shape = self.output_shapes[idx]
                if shape is not None and int(np.prod(shape)) == tensor.size:
                    tensor = tensor.reshape(shape)
                outputs.append(tensor)
            finally:
                acl.rt.free_host(host_ptr)

        return outputs

    def release(self):
        if self.input_dataset is not None:
            for buf in self.input_buffers:
                acl.destroy_data_buffer(buf["buffer"])
                acl.rt.free(buf["ptr"])
            acl.mdl.destroy_dataset(self.input_dataset)
            self.input_dataset = None

        if self.output_dataset is not None:
            for buf in self.output_buffers:
                acl.destroy_data_buffer(buf["buffer"])
                acl.rt.free(buf["ptr"])
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

        if self._acl_initialized:
            acl.rt.reset_device(self.device_id)
            acl.finalize()
            self._acl_initialized = False
