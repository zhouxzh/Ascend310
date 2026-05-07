"""
Case 6: Smart Car Perception — Ascend NPU scene classification with
CPU fallback.

Reuses the AscendResource / AscendModel pattern from case8.
Classifies driving scenes into 5 types with ResNet18.
"""

import os

import numpy as np

from config import (
    IMAGE_SIZE,
    IMAGENET_MEAN,
    IMAGENET_STD,
    NUM_SCENES,
    SCENE_CLASSES,
    NPU_DEVICE_ID,
    OM_MODEL_PATH,
    PTH_MODEL_PATH,
)


# ======================================================================
# AscendResource / AscendModel — same pattern as case1/case8
# ======================================================================

def _check_ret(ret, message):
    if ret != 0:
        raise RuntimeError(f"{message} failed ret={ret}")


class AscendResource:
    """Thin wrapper around acl.init / device / context / stream."""

    def __init__(self, device_id=NPU_DEVICE_ID):
        import acl

        self.acl = acl
        self.device_id = device_id
        self.context = None
        self.stream = None
        self._init()

    def _init(self):
        ret = self.acl.init()
        _check_ret(ret, "acl.init")
        ret = self.acl.rt.set_device(self.device_id)
        _check_ret(ret, "acl.rt.set_device")
        self.context, ret = self.acl.rt.create_context(self.device_id)
        _check_ret(ret, "acl.rt.create_context")
        self.stream, ret = self.acl.rt.create_stream()
        _check_ret(ret, "acl.rt.create_stream")

    def release(self):
        if self.stream:
            self.acl.rt.destroy_stream(self.stream)
        if self.context:
            self.acl.rt.destroy_context(self.context)
        self.acl.rt.reset_device(self.device_id)
        self.acl.finalize()


class AscendModel:
    """Load an .om model, manage device buffers, execute inference."""

    def __init__(self, ascend_res, model_path):
        self._res = ascend_res
        self._acl = ascend_res.acl
        self.model_path = model_path
        self.model_id = None
        self.desc = None
        self.input_dataset = None
        self.output_dataset = None
        self.input_buffers = []
        self.output_buffers = []
        self.output_sizes = []
        self._load()

    def _load(self):
        acl = self._acl
        acl.rt.set_context(self._res.context)

        self.model_id, ret = acl.mdl.load_from_file(self.model_path)
        _check_ret(ret, f"acl.mdl.load_from_file {self.model_path}")

        self.desc = acl.mdl.create_desc()
        ret = acl.mdl.get_desc(self.desc, self.model_id)
        _check_ret(ret, "acl.mdl.get_desc")

        self._init_buffers()

    def _init_buffers(self):
        acl = self._acl

        self.input_dataset = acl.mdl.create_dataset()
        num_inputs = acl.mdl.get_num_inputs(self.desc)
        for i in range(num_inputs):
            size = acl.mdl.get_input_size_by_index(self.desc, i)
            dev_ptr, ret = acl.rt.malloc(size, 2)
            _check_ret(ret, "acl.rt.malloc input")
            self.input_buffers.append({"ptr": dev_ptr, "size": size})
            buf = acl.create_data_buffer(dev_ptr, size)
            acl.mdl.add_dataset_buffer(self.input_dataset, buf)

        self.output_dataset = acl.mdl.create_dataset()
        num_outputs = acl.mdl.get_num_outputs(self.desc)
        for i in range(num_outputs):
            size = acl.mdl.get_output_size_by_index(self.desc, i)
            self.output_sizes.append(size)
            dev_ptr, ret = acl.rt.malloc(size, 2)
            _check_ret(ret, "acl.rt.malloc output")
            self.output_buffers.append({"ptr": dev_ptr, "size": size})
            buf = acl.create_data_buffer(dev_ptr, size)
            acl.mdl.add_dataset_buffer(self.output_dataset, buf)

    def execute(self, input_data_list):
        """Copy host→device, run, copy device→host, return output bytes."""
        acl = self._acl
        acl.rt.set_context(self._res.context)

        for i, data in enumerate(input_data_list):
            if i >= len(self.input_buffers):
                break
            data = np.ascontiguousarray(data)
            ptr = acl.util.numpy_to_ptr(data)
            size = data.nbytes
            ret = acl.rt.memcpy(
                self.input_buffers[i]["ptr"],
                self.input_buffers[i]["size"],
                ptr, size, 1,
            )
            _check_ret(ret, "acl.rt.memcpy host->device")

        ret = acl.mdl.execute(self.model_id, self.input_dataset,
                              self.output_dataset)
        _check_ret(ret, "acl.mdl.execute")

        outputs = []
        for i in range(len(self.output_buffers)):
            size = self.output_buffers[i]["size"]
            host_data = np.zeros(size, dtype=np.byte)
            host_ptr = acl.util.numpy_to_ptr(host_data)
            ret = acl.rt.memcpy(
                host_ptr, size,
                self.output_buffers[i]["ptr"], size, 2,
            )
            _check_ret(ret, "acl.rt.memcpy device->host")
            outputs.append(host_data)
        return outputs

    def release(self):
        acl = self._acl
        if self.input_dataset:
            acl.mdl.destroy_dataset(self.input_dataset)
        if self.output_dataset:
            acl.mdl.destroy_dataset(self.output_dataset)
        for buf in self.input_buffers:
            acl.rt.free(buf["ptr"])
        for buf in self.output_buffers:
            acl.rt.free(buf["ptr"])
        if self.model_id:
            acl.mdl.unload(self.model_id)
        if self.desc:
            acl.mdl.destroy_desc(self.desc)


# ======================================================================
# SceneClassifier — NPU path + CPU fallback
# ======================================================================

class SceneClassifier:
    """ResNet18 driving-scene classifier (5 classes).

    Two backends:
      - NPU (Ascend 310B .om model) — requires Cann ACL
      - CPU (PyTorch) — automatic fallback when .om not found or acl not
        importable
    """

    def __init__(self):
        self._acl_resource = None
        self._om_model = None
        self._torch_model = None
        self.use_npu = False
        self._init_backend()

    def _init_backend(self):
        if os.path.exists(OM_MODEL_PATH):
            try:
                self._acl_resource = AscendResource(NPU_DEVICE_ID)
                self._om_model = AscendModel(self._acl_resource, OM_MODEL_PATH)
                self.use_npu = True
                print("[SceneClassifier] Backend: NPU (Ascend 310B)")
                return
            except Exception as exc:
                print(f"[SceneClassifier] NPU init failed ({exc}), "
                      f"falling back to CPU")
                if self._acl_resource:
                    try:
                        self._acl_resource.release()
                    except Exception:
                        pass
                    self._acl_resource = None

        self._init_cpu_backend()
        print("[SceneClassifier] Backend: CPU (PyTorch ResNet18)")

    def _init_cpu_backend(self):
        import torch
        import torchvision.models as models

        self._torch_model = models.resnet18(
            weights=models.ResNet18_Weights.IMAGENET1K_V1
        )
        in_features = self._torch_model.fc.in_features
        self._torch_model.fc = torch.nn.Linear(in_features, NUM_SCENES)

        if os.path.exists(PTH_MODEL_PATH):
            state = torch.load(PTH_MODEL_PATH, map_location="cpu",
                               weights_only=True)
            self._torch_model.load_state_dict(state)
            print(f"[SceneClassifier] Loaded weights from {PTH_MODEL_PATH}")
        else:
            print("[SceneClassifier] WARNING: no trained weights found — "
                  "using random classifier (predictions will be wrong)")

        self._torch_model.eval()

    # ------------------------------------------------------------------
    # Preprocessing
    # ------------------------------------------------------------------

    def preprocess(self, image_bgr):
        """Convert BGR numpy image (H,W,3) to normalized tensor
        (1,3,224,224)."""
        import cv2

        img = cv2.resize(image_bgr, (IMAGE_SIZE, IMAGE_SIZE))
        img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)

        img = img.astype(np.float32) / 255.0

        mean = np.array(IMAGENET_MEAN, dtype=np.float32)
        std = np.array(IMAGENET_STD, dtype=np.float32)
        img = (img - mean) / std

        img = img.transpose(2, 0, 1)
        img = np.expand_dims(img, axis=0)
        return img.astype(np.float32)

    # ------------------------------------------------------------------
    # Classification
    # ------------------------------------------------------------------

    def classify(self, image_bgr):
        """Classify a driving scene image.

        Returns:
            dict with:
                class_id: int
                label_en: str
                label_cn: str
                advice: str
                confidence: float
                all_probs: list of (label_cn, prob) sorted descending
        """
        input_tensor = self.preprocess(image_bgr)

        if self.use_npu:
            raw = self._om_model.execute([input_tensor])
            logits = np.frombuffer(raw[0], dtype=np.float32)
        else:
            import torch
            with torch.no_grad():
                tensor = torch.from_numpy(input_tensor)
                output = self._torch_model(tensor)
                logits = output.squeeze(0).numpy()

        # Softmax
        exp = np.exp(logits - np.max(logits))
        probs = exp / np.sum(exp)

        top_idx = int(np.argmax(probs))
        scene = SCENE_CLASSES[top_idx]

        all_probs = []
        for i in np.argsort(probs)[::-1]:
            s = SCENE_CLASSES[i]
            all_probs.append({
                "label_cn": s["cn"],
                "label_en": s["en"],
                "confidence": float(probs[i]),
            })

        return {
            "class_id": top_idx,
            "label_en": scene["en"],
            "label_cn": scene["cn"],
            "advice": scene["advice"],
            "confidence": float(probs[top_idx]),
            "all_probs": all_probs,
        }

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def release(self):
        if self._om_model:
            self._om_model.release()
        if self._acl_resource:
            self._acl_resource.release()
