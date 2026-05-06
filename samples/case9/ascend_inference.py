"""
Ascend 310B NPU inference wrapper for text embedding models.

Reuses the AscendSystem / AscendModel pattern established in case1.
Provides automatic CPU fallback when NPU is unavailable.
"""

import os
import numpy as np

from config import NPU_DEVICE_ID, MAX_SEQ_LENGTH, MODEL_NAME, OM_MODEL_PATH

# ---------------------------------------------------------------------------
# NPU helpers
# ---------------------------------------------------------------------------

_ACL_AVAILABLE = False
try:
    import acl

    _ACL_AVAILABLE = True
except ImportError:
    pass


def check_ret(ret, message):
    if ret != 0:
        raise RuntimeError(f"{message} failed ret={ret}")


class AscendSystem:
    def __init__(self, device_id=NPU_DEVICE_ID):
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

    def release(self):
        if self.stream:
            acl.rt.destroy_stream(self.stream)
        if self.context:
            acl.rt.destroy_context(self.context)
        acl.rt.reset_device(self.device_id)
        acl.finalize()


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

    def _init_buffers(self):
        self.input_dataset = acl.mdl.create_dataset()
        num_inputs = acl.mdl.get_num_inputs(self.desc)
        for i in range(num_inputs):
            size = acl.mdl.get_input_size_by_index(self.desc, i)
            dev_ptr, ret = acl.rt.malloc(size, 2)
            check_ret(ret, "acl.rt.malloc input")
            self.input_buffers.append({"ptr": dev_ptr, "size": size})
            data_buffer = acl.create_data_buffer(dev_ptr, size)
            acl.mdl.add_dataset_buffer(self.input_dataset, data_buffer)

        self.output_dataset = acl.mdl.create_dataset()
        num_outputs = acl.mdl.get_num_outputs(self.desc)
        for i in range(num_outputs):
            size = acl.mdl.get_output_size_by_index(self.desc, i)
            self.output_sizes.append(size)
            dev_ptr, ret = acl.rt.malloc(size, 2)
            check_ret(ret, "acl.rt.malloc output")
            self.output_buffers.append({"ptr": dev_ptr, "size": size})
            data_buffer = acl.create_data_buffer(dev_ptr, size)
            acl.mdl.add_dataset_buffer(self.output_dataset, data_buffer)

    def execute(self, input_data_list):
        acl.rt.set_context(self.context)
        for i, data in enumerate(input_data_list):
            if i >= len(self.input_buffers):
                break
            data = np.ascontiguousarray(data)
            ptr = acl.util.numpy_to_ptr(data)
            size = data.nbytes
            ret = acl.rt.memcpy(
                self.input_buffers[i]["ptr"], self.input_buffers[i]["size"],
                ptr, size, 1,
            )
            check_ret(ret, "acl.rt.memcpy host->device")

        ret = acl.mdl.execute(self.model_id, self.input_dataset, self.output_dataset)
        check_ret(ret, "acl.mdl.execute")

        outputs = []
        for i in range(len(self.output_buffers)):
            size = self.output_buffers[i]["size"]
            host_data = np.zeros(size, dtype=np.byte)
            host_ptr = acl.util.numpy_to_ptr(host_data)
            ret = acl.rt.memcpy(host_ptr, size, self.output_buffers[i]["ptr"], size, 2)
            check_ret(ret, "acl.rt.memcpy device->host")
            outputs.append(host_data)
        return outputs

    def release(self):
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


# ---------------------------------------------------------------------------
# Embedding model
# ---------------------------------------------------------------------------

class EmbeddingModel:
    """Text embedding via Ascend NPU, with CPU fallback."""

    def __init__(self, model_path=None):
        self._tokenizer = None
        self._ascend_sys = None
        self._ascend_model = None
        self._use_npu = False

        model_path = model_path or OM_MODEL_PATH
        if _ACL_AVAILABLE and os.path.exists(model_path):
            try:
                self._ascend_sys = AscendSystem()
                self._ascend_model = AscendModel(
                    self._ascend_sys.context, model_path
                )
                self._use_npu = True
                print(f"[EmbeddingModel] Using NPU: {model_path}")
            except Exception as e:
                print(f"[EmbeddingModel] NPU init failed: {e}")
                self._use_npu = False
        else:
            self._use_npu = False

        if not self._use_npu:
            self._init_cpu_fallback()

    def _init_cpu_fallback(self):
        from sentence_transformers import SentenceTransformer
        print(f"[EmbeddingModel] Using CPU fallback: {MODEL_NAME}")
        self._cpu_model = SentenceTransformer(MODEL_NAME)

    def _get_tokenizer(self):
        if self._tokenizer is not None:
            return self._tokenizer
        from transformers import AutoTokenizer
        self._tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME)
        return self._tokenizer

    def encode(self, texts):
        """Encode texts to normalized embedding vectors.

        Args:
            texts: list of str, or single str

        Returns:
            numpy array of shape (N, 384), float32, L2-normalized
        """
        if isinstance(texts, str):
            texts = [texts]

        if self._use_npu:
            return self._encode_npu(texts)
        else:
            return self._encode_cpu(texts)

    def _encode_cpu(self, texts):
        embeddings = self._cpu_model.encode(
            texts, normalize_embeddings=True, show_progress_bar=False
        )
        return embeddings.astype(np.float32)

    def _encode_npu(self, texts):
        tokenizer = self._get_tokenizer()
        all_embeddings = []

        for text in texts:
            encoded = tokenizer(
                text, padding="max_length", truncation=True,
                max_length=MAX_SEQ_LENGTH, return_tensors="np",
            )
            input_ids = encoded["input_ids"].astype(np.int64)
            attention_mask = encoded["attention_mask"].astype(np.int64)
            token_type_ids = encoded.get(
                "token_type_ids",
                np.zeros_like(input_ids),
            ).astype(np.int64)

            outputs = self._ascend_model.execute([
                input_ids, attention_mask, token_type_ids,
            ])

            embedding = self._pool_output(outputs, attention_mask)
            all_embeddings.append(embedding)

        embeddings = np.stack(all_embeddings, axis=0)
        return self._normalize(embeddings)

    def _pool_output(self, outputs, attention_mask):
        """Mean pooling over token dimension."""
        raw = np.frombuffer(outputs[0], dtype=np.float32)
        batch_size = attention_mask.shape[0]
        seq_len = attention_mask.shape[1]
        hidden_size = raw.size // (batch_size * seq_len)
        if hidden_size * batch_size * seq_len != raw.size:
            # If the output is already pooled (e.g. [1, 384])
            if raw.size == batch_size * self.dim:
                return raw.reshape(batch_size, -1)
            hidden_size = raw.size // batch_size
            return raw.reshape(batch_size, hidden_size)
        token_embeddings = raw.reshape(batch_size, seq_len, hidden_size)
        mask = np.expand_dims(attention_mask.astype(np.float32), axis=-1)
        summed = (token_embeddings * mask).sum(axis=1)
        counts = mask.sum(axis=1).clip(min=1)
        return summed / counts

    @property
    def dim(self):
        from config import EMBEDDING_DIM
        return EMBEDDING_DIM

    @staticmethod
    def _normalize(embeddings):
        norms = np.linalg.norm(embeddings, axis=-1, keepdims=True)
        norms = np.clip(norms, 1e-12, None)
        return embeddings / norms

    def release(self):
        if self._ascend_model:
            self._ascend_model.release()
        if self._ascend_sys:
            self._ascend_sys.release()

    @property
    def use_npu(self):
        return self._use_npu


def create_embedding_model(model_path=None):
    """Factory: returns an EmbeddingModel, trying NPU first."""
    return EmbeddingModel(model_path)
