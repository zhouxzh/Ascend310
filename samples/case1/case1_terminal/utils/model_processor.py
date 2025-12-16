import acl
import numpy as np
import ctypes

class ModelProcessor:
    def __init__(self, acl_resource, model_path):
        self.acl_resource = acl_resource
        self.model_path = model_path
        self.model_id = None
        self.model_desc = None
        self.input_dataset = None
        self.output_dataset = None

    def load_model(self):
        # Load the offline model
        self.model_id, ret = acl.mdl.load_from_file(self.model_path)
        if ret != 0:
            raise RuntimeError(f"acl.mdl.load_from_file failed: {ret}")

        # Get model description
        self.model_desc = acl.mdl.create_desc()
        ret = acl.mdl.get_desc(self.model_desc, self.model_id)
        if ret != 0:
            raise RuntimeError(f"acl.mdl.get_desc failed: {ret}")

        print("Model loaded successfully.")

    def _prepare_input(self, data):
        # Create a dataset for the model input
        self.input_dataset = acl.mdl.create_dataset()
        num_inputs = acl.mdl.get_num_inputs(self.model_desc)

        for i in range(num_inputs):
            # Get input buffer size
            input_size = acl.mdl.get_input_size_by_index(self.model_desc, i)

            # Allocate device memory for input
            input_ptr, ret = acl.rt.malloc(input_size, 0)
            if ret != 0:
                raise RuntimeError(f"acl.rt.malloc failed: {ret}")

            # Copy data from host to device
            ret = acl.rt.memcpy(input_ptr, input_size, data.ctypes.data, input_size, 1)
            if ret != 0:
                raise RuntimeError(f"acl.rt.memcpy failed: {ret}")

            # Create a data buffer
            data_buffer = acl.create_data_buffer(input_ptr, input_size)
            _, ret = acl.mdl.add_dataset_buffer(self.input_dataset, data_buffer)
            if ret != 0:
                raise RuntimeError(f"acl.mdl.add_dataset_buffer failed: {ret}")

    def _prepare_output(self):
        # Create a dataset for the model output
        self.output_dataset = acl.mdl.create_dataset()
        num_outputs = acl.mdl.get_num_outputs(self.model_desc)

        for i in range(num_outputs):
            # Get output buffer size
            output_size = acl.mdl.get_output_size_by_index(self.model_desc, i)

            # Allocate device memory for output
            output_ptr, ret = acl.rt.malloc(output_size, 0)
            if ret != 0:
                raise RuntimeError(f"acl.rt.malloc failed: {ret}")

            # Create a data buffer
            data_buffer = acl.create_data_buffer(output_ptr, output_size)
            _, ret = acl.mdl.add_dataset_buffer(self.output_dataset, data_buffer)
            if ret != 0:
                raise RuntimeError(f"acl.mdl.add_dataset_buffer failed: {ret}")

    def predict(self, data):
        # Prepare input and output
        self._prepare_input(data)
        self._prepare_output()

        # Execute the model
        ret = acl.mdl.execute(self.model_id, self.input_dataset, self.output_dataset)
        if ret != 0:
            raise RuntimeError(f"acl.mdl.execute failed: {ret}")

        # Get the result
        num_outputs = acl.mdl.get_num_outputs(self.model_desc)
        results = []
        for i in range(num_outputs):
            # Get the output data buffer
            data_buffer = acl.mdl.get_dataset_buffer(self.output_dataset, i)
            # Get the pointer and size of the output data
            output_ptr = acl.get_data_buffer_addr(data_buffer)
            output_size = acl.get_data_buffer_size(data_buffer)

            # Allocate host memory
            host_mem, _ = acl.rt.malloc_host(output_size)
            # Copy data from device to host
            ret = acl.rt.memcpy(host_mem, output_size, output_ptr, output_size, 2)
            if ret != 0:
                raise RuntimeError(f"acl.rt.memcpy D2H failed: {ret}")

            # Convert to numpy array
            output_np = np.copy(np.ctypeslib.as_array(ctypes.cast(host_mem, ctypes.POINTER(ctypes.c_float)), (output_size // 4,)))
            results.append(output_np)

            # Free host memory
            acl.rt.free_host(host_mem)

        self._destroy_dataset(self.input_dataset)
        self._destroy_dataset(self.output_dataset)

        return results

    def _destroy_dataset(self, dataset):
        if not dataset:
            return
        num_buffers = acl.mdl.get_dataset_num_buffers(dataset)
        for i in range(num_buffers):
            data_buffer = acl.mdl.get_dataset_buffer(dataset, i)
            if data_buffer:
                buffer_ptr = acl.get_data_buffer_addr(data_buffer)
                if buffer_ptr:
                    acl.rt.free(buffer_ptr)
                acl.destroy_data_buffer(data_buffer)
        acl.mdl.destroy_dataset(dataset)

    def unload_model(self):
        if self.model_id:
            ret = acl.mdl.unload(self.model_id)
            if ret != 0:
                print(f"Warning: acl.mdl.unload failed: {ret}")
        if self.model_desc:
            acl.mdl.destroy_desc(self.model_desc)