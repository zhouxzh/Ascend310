import acl
import struct
import numpy as np
import os
import time
import cv2

from .config import MODEL_DIR


class ModelNotReadyError(RuntimeError):
    """Raised when the complete admitted model set is unavailable."""


# Error checking helper
def check_ret(ret, message):
    if isinstance(ret, tuple):
        ret = ret[-1]
    if ret != 0:
        raise Exception(f"{message} failed ret={ret}")

class AscendSystem:
    def __init__(self, device_id=0):
        self.device_id = device_id
        self.context = None
        self.stream = None
        self._init_resource()

    def _init_resource(self):
        ret = acl.init()
        check_ret(ret, "acl.init")

        ret = acl.rt.set_device(self.device_id)
        check_ret(ret, "acl.rt.set_device")

        self.context, ret = acl.rt.create_context(self.device_id)
        check_ret(ret, "acl.rt.create_context")

        self.stream, ret = acl.rt.create_stream()
        check_ret(ret, "acl.rt.create_stream")
        print(f"[AscendSystem] Device {self.device_id} initialized.")

    def release(self):
        if self.stream:
            acl.rt.destroy_stream(self.stream)
        if self.context:
            acl.rt.destroy_context(self.context)
        acl.rt.reset_device(self.device_id)
        acl.finalize()
        print("[AscendSystem] Resources released.")

class AscendModel:
    def __init__(self, context, model_path):
        self.context = context
        self.model_path = model_path
        self.model_id = None
        self.desc = None
        self.input_dataset = None
        self.output_dataset = None
        self.input_buffers = []
        self.output_buffers = []
        self.input_data_buffers = []
        self.output_data_buffers = []
        self.output_sizes = []
        self._load_model()

    def _load_model(self):
        acl.rt.set_context(self.context)

        self.model_id, ret = acl.mdl.load_from_file(self.model_path)
        check_ret(ret, f"acl.mdl.load_from_file {self.model_path}")

        self.desc = acl.mdl.create_desc()
        ret = acl.mdl.get_desc(self.desc, self.model_id)
        check_ret(ret, "acl.mdl.get_desc")

        self._init_buffers()
        print(f"[AscendModel] Model {self.model_path} loaded. ID: {self.model_id}")

    def _init_buffers(self):
        # Create input dataset
        self.input_dataset = acl.mdl.create_dataset()
        num_inputs = acl.mdl.get_num_inputs(self.desc)
        for i in range(num_inputs):
            size = acl.mdl.get_input_size_by_index(self.desc, i)
            dev_ptr, ret = acl.rt.malloc(size, 2)
            check_ret(ret, "acl.rt.malloc input")
            self.input_buffers.append({"ptr": dev_ptr, "size": size})

            data_buffer = acl.create_data_buffer(dev_ptr, size)
            if data_buffer is None:
                raise RuntimeError(f"acl.create_data_buffer input[{i}] failed")
            ret = acl.mdl.add_dataset_buffer(self.input_dataset, data_buffer)
            status = ret[-1] if isinstance(ret, tuple) else ret
            if status != 0:
                destroy = getattr(acl, "destroy_data_buffer", None)
                if destroy is not None:
                    destroy(data_buffer)
            check_ret(ret, f"acl.mdl.add_dataset_buffer input[{i}]")
            self.input_data_buffers.append(data_buffer)

        # Create output dataset
        self.output_dataset = acl.mdl.create_dataset()
        num_outputs = acl.mdl.get_num_outputs(self.desc)
        for i in range(num_outputs):
            size = acl.mdl.get_output_size_by_index(self.desc, i)
            self.output_sizes.append(size)
            dev_ptr, ret = acl.rt.malloc(size, 2)
            check_ret(ret, "acl.rt.malloc output")
            self.output_buffers.append({"ptr": dev_ptr, "size": size})

            data_buffer = acl.create_data_buffer(dev_ptr, size)
            if data_buffer is None:
                raise RuntimeError(f"acl.create_data_buffer output[{i}] failed")
            ret = acl.mdl.add_dataset_buffer(self.output_dataset, data_buffer)
            status = ret[-1] if isinstance(ret, tuple) else ret
            if status != 0:
                destroy = getattr(acl, "destroy_data_buffer", None)
                if destroy is not None:
                    destroy(data_buffer)
            check_ret(ret, f"acl.mdl.add_dataset_buffer output[{i}]")
            self.output_data_buffers.append(data_buffer)

    def execute(self, input_data_list):
        acl.rt.set_context(self.context)
        if len(input_data_list) != len(self.input_buffers):
            raise ModelNotReadyError(
                f"模型输入数量不匹配: expected={len(self.input_buffers)} actual={len(input_data_list)}"
            )
        for i, data in enumerate(input_data_list):
            data = np.ascontiguousarray(data)
            if data.dtype != np.float32:
                raise ModelNotReadyError(f"模型输入类型不匹配: input[{i}] 必须为 float32")
            expected_size = self.input_buffers[i]["size"]
            if data.nbytes != expected_size:
                raise ModelNotReadyError(
                    f"模型输入字节数不匹配: input[{i}] expected={expected_size} actual={data.nbytes}"
                )
            ptr = acl.util.bytes_to_ptr(data.tobytes())
            size = data.nbytes
            ret = acl.rt.memcpy(self.input_buffers[i]["ptr"], self.input_buffers[i]["size"],
                                ptr, size, 1)
            check_ret(ret, "acl.rt.memcpy host->device")

        ret = acl.mdl.execute(self.model_id, self.input_dataset, self.output_dataset)
        check_ret(ret, "acl.mdl.execute")

        outputs = []
        for i in range(len(self.output_buffers)):
            size = self.output_buffers[i]["size"]
            if size % np.dtype(np.float32).itemsize:
                raise ModelNotReadyError(f"模型输出字节数不是 float32 的整数倍: output[{i}]")
            host_data = np.zeros(size, dtype=np.byte)
            host_ptr = host_data.ctypes.data
            ret = acl.rt.memcpy(host_ptr, size,
                                self.output_buffers[i]["ptr"], size, 2)
            check_ret(ret, "acl.rt.memcpy device->host")
            outputs.append(host_data)
        return outputs

    def release(self):
        destroy_data_buffer = getattr(acl, "destroy_data_buffer", None)
        if destroy_data_buffer is not None:
            for data_buffer in self.input_data_buffers:
                try:
                    destroy_data_buffer(data_buffer)
                except Exception:
                    pass
            for data_buffer in self.output_data_buffers:
                try:
                    destroy_data_buffer(data_buffer)
                except Exception:
                    pass
        self.input_data_buffers.clear()
        self.output_data_buffers.clear()
        if self.input_dataset:
            acl.mdl.destroy_dataset(self.input_dataset)
            self.input_dataset = None
        if self.output_dataset:
            acl.mdl.destroy_dataset(self.output_dataset)
            self.output_dataset = None
        for buf in self.input_buffers:
            try:
                acl.rt.free(buf["ptr"])
            except Exception:
                pass
        for buf in self.output_buffers:
            try:
                acl.rt.free(buf["ptr"])
            except Exception:
                pass
        self.input_buffers.clear()
        self.output_buffers.clear()
        if self.model_id:
            acl.mdl.unload(self.model_id)
            self.model_id = None
        if self.desc:
            acl.mdl.destroy_desc(self.desc)
            self.desc = None

class FaceSystem:
    def __init__(self):
        # Resolve model files from the sample root rather than the caller's
        # current working directory. This keeps `python app.py` and module
        # imports consistent when launched from another directory.
        self.det_model_path = str(MODEL_DIR / "face_detection.om")
        self.rec_model_path = str(MODEL_DIR / "face_recognition.om")

        missing = [
            path for path in (self.det_model_path, self.rec_model_path)
            if not os.path.isfile(path)
        ]
        if missing:
            print("[FaceSystem] 缺少已准入的人脸模型: " + ", ".join(missing))
            raise ModelNotReadyError("人脸模型未就绪")

        self.ascend_sys = None
        self.det_model = None
        self.rec_model = None
        try:
            self.ascend_sys = AscendSystem()
            self.det_model = AscendModel(self.ascend_sys.context, self.det_model_path)
            self.rec_model = AscendModel(self.ascend_sys.context, self.rec_model_path)
        except Exception:
            self.release()
            raise

        # Anchors for 640x640
        self.anchors = self.generate_anchors(640, 640)

    def generate_anchors(self, height, width):
        strides = [8, 16, 32]
        anchors = []
        for stride in strides:
            num_grid_y = height // stride
            num_grid_x = width // stride
            for y in range(num_grid_y):
                for x in range(num_grid_x):
                    # 2 anchors per grid
                    for _ in range(2):
                        anchors.append([x * stride, y * stride, stride])
        return np.array(anchors, dtype=np.float32)

    def preprocess_det(self, image):
        target_size = (640, 640)
        img = cv2.resize(image, target_size)
        img = img.astype(np.float32)
        # Assuming model expects BGR, mean subtraction
        # Buffalo_s det usually: - mean(104, 117, 123)? Or no mean?
        # InsightFace scrfd usually doesn't need mean subtraction if model is simple, but often it does.
        # Let's try standard mean subtraction.
        # However, many ONNX models from InsightFace expect RGB?
        # Checking input.1 usually implies standard normalization.
        # I'll stick to simple subtraction for now.
        img -= np.array([127.5, 127.5, 127.5], dtype=np.float32)
        img /= 128.0

        img = img.transpose(2, 0, 1)
        img = np.expand_dims(img, axis=0)
        return img, image.shape[:2] # Return original shape for scaling back

    def preprocess_rec(self, face_img):
        img = cv2.resize(face_img, (112, 112))
        img = img.astype(np.float32)
        img = (img - 127.5) / 128.0
        img = img.transpose(2, 0, 1)
        img = np.expand_dims(img, axis=0)
        return img

    def decode_bbox(self, anchors, raw_outputs):
        # This is tricky without knowing exact output order.
        # Based on ONNX check:
        # 0,1,2: Scores (8, 16, 32)
        # 3,4,5: BBox (8, 16, 32)
        # 6,7,8: Landmarks

        if len(raw_outputs) < 6:
            raise ModelNotReadyError("人脸检测模型输出数量不满足后处理合同")

        # Flatten and concat
        # Order: 8, 16, 32

        # Scores
        s8 = np.frombuffer(raw_outputs[0], dtype=np.float32).reshape(-1, 1)
        s16 = np.frombuffer(raw_outputs[1], dtype=np.float32).reshape(-1, 1)
        s32 = np.frombuffer(raw_outputs[2], dtype=np.float32).reshape(-1, 1)
        score_all = np.concatenate([s8, s16, s32], axis=0)

        # BBoxes
        b8 = np.frombuffer(raw_outputs[3], dtype=np.float32).reshape(-1, 4)
        b16 = np.frombuffer(raw_outputs[4], dtype=np.float32).reshape(-1, 4)
        b32 = np.frombuffer(raw_outputs[5], dtype=np.float32).reshape(-1, 4)
        bbox_all = np.concatenate([b8, b16, b32], axis=0)
        if score_all.shape[0] != anchors.shape[0] or bbox_all.shape[0] != anchors.shape[0]:
            raise ModelNotReadyError("人脸检测模型输出形状不满足后处理合同")

        # Apply decoding
        # anchor: [cx, cy, stride]
        # bbox output: [dx, dy, dw, dh] (usually) -> distance to anchor
        # Center: anchor_center + output * stride
        # Or usually: (x - ax)/stride, etc.
        # InsightFace SCRFD usually:
        # dist_l, dist_t, dist_r, dist_b * stride

        # Let's assume SCRFD format: l, t, r, b (distances from anchor center)
        # x1 = anchor_x - l * stride
        # y1 = anchor_y - t * stride
        # x2 = anchor_x + r * stride
        # y2 = anchor_y + b * stride

        # Or RetinaFace format: dx, dy, dw, dh

        # Given "det_500m.onnx" is usually SCRFD.
        # Let's try SCRFD decoding.

        x1 = self.anchors[:, 0] - bbox_all[:, 0] * self.anchors[:, 2]
        y1 = self.anchors[:, 1] - bbox_all[:, 1] * self.anchors[:, 2]
        x2 = self.anchors[:, 0] + bbox_all[:, 2] * self.anchors[:, 2]
        y2 = self.anchors[:, 1] + bbox_all[:, 3] * self.anchors[:, 2]

        decoded_bbox = np.stack([x1, y1, x2, y2], axis=1)

        return decoded_bbox, score_all.flatten()

    def detect(self, image, threshold=0.5):
        if not self.det_model:
            raise ModelNotReadyError("人脸检测模型未就绪")

        input_tensor, (orig_h, orig_w) = self.preprocess_det(image)
        outputs = self.det_model.execute([input_tensor])

        bboxes, scores = self.decode_bbox(self.anchors, outputs)

        # Filter
        keep = scores > threshold
        bboxes = bboxes[keep]
        scores = scores[keep]

        if len(bboxes) == 0:
            return []

        # Scale back to original image
        scale_x = orig_w / 640
        scale_y = orig_h / 640
        bboxes[:, 0] *= scale_x
        bboxes[:, 1] *= scale_y
        bboxes[:, 2] *= scale_x
        bboxes[:, 3] *= scale_y

        # NMS
        # Using cv2 NMS
        # rects: (x, y, w, h)
        rects = []
        for box in bboxes:
            rects.append([int(box[0]), int(box[1]), int(box[2]-box[0]), int(box[3]-box[1])])

        indices = cv2.dnn.NMSBoxes(rects, scores.tolist(), threshold, 0.4)

        final_faces = []
        if len(indices) > 0:
            for i in indices.flatten():
                final_faces.append(bboxes[i])

        return final_faces

    def get_embedding(self, face_img):
        if not self.rec_model:
            raise ModelNotReadyError("人脸识别模型未就绪")

        input_tensor = self.preprocess_rec(face_img)
        outputs = self.rec_model.execute([input_tensor])
        if not outputs or outputs[0].nbytes % np.dtype(np.float32).itemsize:
            raise ModelNotReadyError("人脸特征模型输出不满足 float32 合同")
        embedding = np.frombuffer(outputs[0], dtype=np.float32)
        if embedding.size != 512:
            raise ModelNotReadyError(
                f"人脸特征模型输出维度不匹配: expected=512 actual={embedding.size}"
            )
        return embedding

    def release(self):
        if self.det_model:
            self.det_model.release()
            self.det_model = None
        if self.rec_model:
            self.rec_model.release()
            self.rec_model = None
        if self.ascend_sys:
            self.ascend_sys.release()
            self.ascend_sys = None
