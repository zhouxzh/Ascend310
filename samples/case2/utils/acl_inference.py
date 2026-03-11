import acl
import numpy as np

# 错误代码到消息的映射
ACL_ERROR_MAP = {
    100000: "ACL_ERROR_INVALID_PARAM: 无效参数",
    100001: "ACL_ERROR_INVALID_FILE: 无效文件",
    100002: "ACL_ERROR_INVALID_MEM: 无效内存",
    100003: "ACL_ERROR_INVALID_DEV: 无效设备",
    # ... (可以根据需要添加更多错误码)
}

def _check_ret(ret, message):
    if ret != acl.ACL_SUCCESS:
        error_msg = ACL_ERROR_MAP.get(ret, f"未知错误码: {ret}")
        raise RuntimeError(f"{message} 失败: {error_msg}")

class AclInference:
    def __init__(self, model_path):
        self.device_id = 0
        self.context = None
        self.stream = None
        self.model_id = None
        self.model_desc = None
        self.input_dataset = None
        self.output_dataset = None

        try:
            self._init_acl()
            self._load_model(model_path)
        except Exception as e:
            self.release()  # 如果初始化失败，确保资源被释放
            raise e

    def _init_acl(self):
        ret = acl.init()
        _check_ret(ret, "ACL初始化")

        ret = acl.rt.set_device(self.device_id)
        _check_ret(ret, "设置设备")

        self.context, ret = acl.rt.create_context(self.device_id)
        _check_ret(ret, "创建Context")

        self.stream, ret = acl.rt.create_stream()
        _check_ret(ret, "创建Stream")

    def _load_model(self, model_path):
        self.model_id, ret = acl.mdl.load_from_file(model_path)
        _check_ret(ret, f"加载模型失败: {model_path}")

        self.model_desc = acl.mdl.create_desc()
        ret = acl.mdl.get_desc(self.model_desc, self.model_id)
        _check_ret(ret, "获取模型描述")

    def inference(self, images):
        # 1. 数据预处理
        input_data = self._preprocess(images)

        # 2. 创建输入数据集
        self._create_input_dataset(input_data)

        # 3. 创建输出数据集
        self._create_output_dataset()

        # 4. 执行模型
        ret = acl.mdl.execute(self.model_id, self.input_dataset, self.output_dataset)
        _check_ret(ret, "模型执行")

        # 5. 获取输出
        output = self._get_output()
        return output

    def _preprocess(self, images):
        # (此为简化示例，具体实现应根据模型要求)
        processed_images = []
        for img in images:
            # Resize, Normalize, HWC to CHW, etc.
            # 确保数据类型和形状符合模型输入要求
            img = np.asarray(img, dtype=np.float32)
            processed_images.append(img)
        return processed_images

    def _create_input_dataset(self, input_data):
        self.input_dataset = acl.mdl.create_dataset()
        for i, data in enumerate(input_data):
            data_buffer = acl.util.numpy_to_device_buffer(data, self.device_id)
            dataset_buffer = acl.create_data_buffer(data_buffer, data.nbytes)
            ret = acl.mdl.add_dataset_buffer(self.input_dataset, dataset_buffer)
            _check_ret(ret, f"添加输入 {i} 到数据集")

    def _create_output_dataset(self):
        self.output_dataset = acl.mdl.create_dataset()
        num_outputs = acl.mdl.get_num_outputs(self.model_desc)
        for i in range(num_outputs):
            output_size = acl.mdl.get_output_size_by_index(self.model_desc, i)
            output_buffer, ret = acl.rt.malloc(output_size, acl.rt.ACL_MEM_MALLOC_HUGE_FIRST)
            _check_ret(ret, f"为输出 {i} 分配内存")
            dataset_buffer = acl.create_data_buffer(output_buffer, output_size)
            ret = acl.mdl.add_dataset_buffer(self.output_dataset, dataset_buffer)
            _check_ret(ret, f"添加输出 {i} 到数据集")

    def _get_output(self):
        output_data = []
        num_outputs = acl.mdl.get_num_outputs(self.model_desc)
        for i in range(num_outputs):
            dataset_buffer = acl.mdl.get_dataset_buffer(self.output_dataset, i)
            buffer_ptr = acl.get_data_buffer_addr(dataset_buffer)
            buffer_size = acl.get_data_buffer_size(dataset_buffer)
            
            # 获取输出张量的维度信息
            dims, ret = acl.mdl.get_cur_output_dims(self.model_desc, i)
            _check_ret(ret, f"获取输出 {i} 的维度")
            shape = tuple(dims['dims'])
            
            # 根据数据类型计算元素数量
            dtype_size = acl.mdl.get_output_data_type_size(self.model_desc, i)
            num_elements = buffer_size // dtype_size

            # 将指针转换为Numpy数组
            output_np = acl.util.ptr_to_numpy(buffer_ptr, (num_elements,), np.float32)
            output_data.append(output_np.reshape(shape).copy())
            
        return output_data

    def release(self):
        if self.model_id:
            acl.mdl.unload(self.model_id)
            self.model_id = None
        if self.model_desc:
            acl.mdl.destroy_desc(self.model_desc)
            self.model_desc = None
        if self.input_dataset:
            self._destroy_dataset(self.input_dataset)
            self.input_dataset = None
        if self.output_dataset:
            self._destroy_dataset(self.output_dataset)
            self.output_dataset = None
        if self.stream:
            acl.rt.destroy_stream(self.stream)
            self.stream = None
        if self.context:
            acl.rt.destroy_context(self.context)
            self.context = None
        acl.rt.reset_device(self.device_id)
        acl.finalize()

    def _destroy_dataset(self, dataset):
        if not dataset:
            return
        num_buffers = acl.mdl.get_dataset_num_buffers(dataset)
        for i in range(num_buffers):
            buffer = acl.mdl.get_dataset_buffer(dataset, i)
            if buffer:
                addr = acl.get_data_buffer_addr(buffer)
                if addr:
                    acl.rt.free(addr)
                acl.destroy_data_buffer(buffer)
        acl.mdl.destroy_dataset(dataset)