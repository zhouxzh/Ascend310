# 第5章 代码样例

本章代码按 DVPP 子模块分目录组织。所有脚本假定从**项目根目录**运行。

## 运行前提

```bash
export LD_LIBRARY_PATH="/usr/local/Ascend/ascend-toolkit/latest/aarch64-linux/lib64:/usr/local/Ascend/driver/lib64:$LD_LIBRARY_PATH"
export PYTHONPATH="/usr/local/Ascend/ascend-toolkit/latest/python/site-packages:$PYTHONPATH"
```

## 快速诊断

```bash
python samples/chapter5/check_cann.py
```

验证 CANN 环境、NPU 设备和 DVPP 驱动是否正常。

## 目录与文件

### [venc/](venc/) — VENC 硬件视频编码

| 文件 | 说明 | 运行时间 |
|------|------|----------|
| `venc_minimal.py` | 原始 API 示例：回调线程、DVPP 内存、编码一帧 | ~3s |
| `bench_venc.py` | `CannVenc` 封装类 + 5 分辨率扫描 vs libx264 | ~20s |

### [vdec/](vdec/) — VDEC 硬件视频解码

| 文件 | 说明 |
|------|------|
| `vdec_minimal.py` | 原始 API 示例：libx264 编码 → VDEC 解码 5 阶段演示 |
| `vdec_acllite_demo.py` | acllite `DvppVdec` 封装：一行创建、一行解码 |
| `bench_vdec.py` | 4 分辨率扫描 vs CPU 解码性能对比 |

### [vpc/](vpc/) — VPC 硬件图像处理

| 文件 | 说明 |
|------|------|
| `vpc_minimal.py` | 原始 API 示例：Resize / Crop+Resize |
| `vpc_acllite_demo.py` | acllite 封装：一行 resize / jpege / jpegd |
| `bench_vpc.py` | VPC resize vs CPU cv2.resize 性能对比 |

### [jpeg/](jpeg/) — JPEG 硬件编解码

| 文件 | 说明 |
|------|------|
| `jpeg_minimal.py` | 原始 API：NV12 → JPEGE → JPEGD → NV12 闭环验证 |

### [WebRTC/](WebRTC/) — WebRTC 推流综合案例

完整的 WebRTC 视频推流应用，将 VENC 硬件编码集成到 aiortc 中。包含服务端、前端页面和测试套件。详见 [WebRTC/README.md](WebRTC/README.md)。

## 学习路线

1. 先跑 `check_cann.py` 确认环境正常
2. 从 `*_minimal.py` 开始理解每个子模块的底层 API
3. 再看 `*_acllite_demo.py` 了解 acllite 高层封装
4. 最后跑 `bench_*.py` 看性能数据
5. [WebRTC/](WebRTC/) 是综合实战案例，学完各子模块后再看
