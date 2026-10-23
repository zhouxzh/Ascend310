# 运行环境与入口

本章程序分为四层：Python/conda 依赖、CANN/PyACL、模型转换、模型推理。某一层通过不能替代下一层；例如 `import acl` 成功不代表设备可用，设备可用也不代表 OM 能加载或输出正确。

## 板端环境

仓库统一要求 conda 环境 `ascend`、Python `3.9.25` 和用户站点隔离：

```bash
source /usr/local/miniconda3/etc/profile.d/conda.sh
conda activate ascend
export PYTHONNOUSERSITE=1
source /usr/local/Ascend/ascend-toolkit/set_env.sh
python -c "import acl; print('PyACL import ok')"
```

`PyACL import ok` 只说明 Python 能找到 `acl`。继续执行设备烟测前，应保存 `npu-smi info`、CANN 版本文件、驱动/固件版本和 Python/ABI。

## 设备烟测

入口是 [check_ascend_device.py](../check_ascend_device/check_ascend_device.py)：

```bash
cd samples/chapter4/check_ascend_device
python check_ascend_device.py
```

执行顺序为 `acl.init("")`、`acl.rt.get_device_count()`、`acl.finalize()`，不创建 context，不加载模型，也不运行算子。

失败语义：

- `acl.init` 返回非零时打印 `ret` 并以状态 1 退出。
- `get_device_count` 失败时脚本只打印错误，随后仍执行 `acl.finalize()` 并以状态 0 退出。
- 成功输出形如 `Found N Ascend devices.`；`N` 必须与目标板及隔离策略一致。

由于设备计数失败不会传播为非零退出码，自动化流程不能只检查进程状态，还必须解析输出中的 `Found ` 记录。该脚本没有 `finally`，若 `get_device_count` 抛异常，`acl.finalize()` 不保证执行。

## 依赖边界

下表按实际 import 和现有 requirements 整理：

| 路径 | Python 依赖 | 板端依赖 |
|---|---|---|
| `check_ascend_device` | `acl` | CANN/PyACL |
| `resnet18/inference_cpu.py` | `onnxruntime`, `numpy`, `datasets`, `tqdm` | 可本地执行 |
| `resnet18/inference_npu.py` | `acl`, `numpy`, `datasets`, `tqdm` | CANN/PyACL、Ascend 设备 |
| `resnet18/train_resnet18_tiny_imagenet.py` | `torch`, `torchvision`, `datasets`, `tensorboard` | CUDA 或 CPU，不使用 NPU |
| `SSD/inference_cpu.py` | `onnxruntime`, `numpy`, `datasets`, `pycocotools`, `matplotlib`, `tqdm` | 可本地执行 |
| `SSD/inference_cuda.py` | 上述依赖加 CUDA ONNX Runtime provider | CUDA 主机 |
| `SSD/inference_npu.py` | `acl`, `numpy`, `datasets`, `pycocotools`, `matplotlib`, `tqdm` | CANN/PyACL、Ascend 设备 |
| `SSD/train_cuda.py` | `torch`, `torchvision`, `datasets`, `pycocotools`, `tensorboard` | CUDA；当前训练循环硬编码 CUDA AMP |
| `SSDLite` | [requirements.txt](../SSDLite/requirements.txt) | CPU 路径无需 CANN；ATC/PyACL 需要 CANN |

除 `SSDLite/requirements.txt` 外，本章没有锁文件。`requirements.txt` 也没有固定版本，无法保证未来解析到相同依赖。

## 本地与板端分工

本地可以执行：

- Python 语法检查。
- 纯 numpy 的默认框、解码、softmax 和 NMS 静态检查。
- 在已有模型和数据时使用 ONNX Runtime CPU provider 做接口烟测。
- 文档、链接和资源清单检查。

必须在 Ascend 310B 板端执行：

- ATC ONNX→OM。
- `acl.init`、device/context/stream、OM 加载与推理。
- NPU 数值一致性、任务准确率和性能。
- 任何 8T/20T 功耗、吞吐或稳定性结论。

## 通用停止条件

出现以下情况时立即停止该路径并保留日志：

- `import acl` 失败，或进程启动前未 source CANN 环境。
- 设备数量为 0，或 `acl.init`/`set_device`/context 创建失败。
- 模型文件不存在、为空、哈希不明或 OM 与模型 revision 无法对应。
- ATC 非零退出、日志报告算子不支持，或 OM 未生成。
- 输入 shape/dtype 与模型合同不一致。
- 输出数量、shape 或 dtype 无法匹配后处理合同。
- 数据集下载、标注解析或图像解码失败。
- 只得到空预测，却准备把自动写出的 `mAP=0.0` 当作有效准确率。
