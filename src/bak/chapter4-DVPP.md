## 图像/视频处理基础

PyACL 的媒体处理能力主要分为两大类：**AIPP（Artificial Intelligence Pre-Processing）** 与 **DVPP（Digital Vision Pre-Processing）**。AIPP 主要负责在模型侧或模型运行链路上完成诸如色域转换、裁剪填充、减均值及乘系数等预处理操作。它分为静态 AIPP 与动态 AIPP 两种模式：前者是在模型转换阶段将参数固化到 .om 模型中，后者允许在运行时通过接口灵活设置参数。DVPP 则是昇腾处理器上的硬件媒体处理单元，通过 pyACL 的 `acl.media` 接口提供硬件加速能力，在 Device 侧高效执行解码、缩放、格式转换等任务。

在实际应用中，DVPP 与 AIPP 各有侧重。DVPP 更适合承担“低级别”的高吞吐预处理任务，例如 JPEG 或视频流的解码、YUV 与 RGB 之间的格式转换、大规模的缩放与裁剪等。其核心优势在于处理速度快且能显著降低 Host 侧 CPU 的负载，但在使用时需遵循特定的图像格式与内存对齐约束。

相比之下，AIPP 则专注于“模型输入级”的精确预处理，旨在满足模型对输入数据的严格要求，如统一色域、像素变换、量化、去均值及通道顺序调整等。常见的最佳实践是将两者组合使用：首先利用 DVPP 完成高效的解码与粗略的 Resize/Crop 操作，随后通过 AIPP（静态或动态）执行最终的色域校正和像素级处理，从而构建出既高效又精准的图像处理流水线。

#### DVPP 主要功能模块
- VPC：格式转换（YUV/RGB）、缩放、裁剪、填充等。
- JPEGD / JPEGE：JPEG 解码 / 编码。
- VDEC / VENC：视频编码器/解码器（H.264/H.265）。
- PNGD：PNG 解码（-> RGB）。
这些能力通过 `acl.media.*` 系列接口（如 dvpp_create_channel_desc/dvpp_create_channel、dvpp_jpeg_decode_async、dvpp_vpc_resize_async 等）访问。

#### 典型处理场景（举例）
- 视频推理：VDEC 解码 -> DVPP 进行 YUV 格式调整与缩放 -> 若需 RGB 或额外预处理，再由 AIPP 或 VPC 完成 -> 传入模型。
- 静态图片分类：JPEGD 解码 -> VPC 缩放/裁剪 -> 如需色域/像素变换使用 AIPP（静态或动态）-> 内存拷贝到 Device 模型输入。
- 单算子/自定义预处理：在 Device 侧通过 DVPP 实现大部分工作，减少 Host 上的 Python/CPU 计算。

#### 开发流程（简要）
1. 环境准备：确保 CANN、Ascend 驱动与环境变量（set_env.sh）正确加载，Python 版本在支持范围内。
2. 目录与模型准备：若包含推理，需准备 .om 模型（静态 AIPP 可在 ATC 时配置）。
3. 初始化 ACL：`acl.init()` -> `acl.rt.set_device()` -> 创建 Context/Stream。
4. DVPP 通道与缓冲：创建 channel 描述，使用 `acl.media.dvpp_malloc` 或 `acl.rt.malloc` 分配 Device 内存。
5. 处理流程示例（伪代码）：
     ```python
     # 创建 channel 和 stream
     ch = acl.media.dvpp_create_channel_desc()
     acl.media.dvpp_create_channel(ch)
     # 申请输入 Device buffer (Host -> Device)
     in_dev = acl.media.dvpp_malloc(len(jpeg_bytes))
     acl.rt.memcpy(in_dev, size, host_ptr, size, ACL_MEMCPY_HOST_TO_DEVICE)
     # 预测解码后大小并申请输出
     out_size, _ = acl.media.dvpp_jpeg_predict_dec_size(host_ptr, size, out_cfg)
     out_dev = acl.media.dvpp_malloc(out_size)
     # 异步解码并等待
     acl.media.dvpp_jpeg_decode_async(ch, in_dev, size, out_dev, out_size, out_cfg, stream)
     acl.rt.synchronize_stream(stream)
     ```
6. 后续：对解码结果使用 VPC 进行 resize/format 转换，或配合 AIPP 做最终像素预处理；封装为模型输入 Dataset 并执行推理。
7. 资源释放：按逆序销毁 stream、context、channel、释放内存并调用 `acl.finalize()`。

#### 注意事项与最佳实践
- DVPP 输出对分辨率与地址有对齐要求（stride/padding），读取数据时需依据描述信息处理。
- 使用异步接口（*_async）时必须配合 Stream 与同步机制（record_event / stream_wait_event / synchronize_stream）以确保内存生命周期安全。
- Host->Device 的异步拷贝源内存应使用页锁定（pinned）内存（`acl.rt.malloc_host`）以保证 DMA 稳定性。
- 静态 AIPP 在 ATC 转换时固化参数；若需灵活参数，选择动态 AIPP 并在推理前通过 pyACL 接口设置。
- 优先把耗时的像素级操作下沉到 DVPP/AIPP，避免在 CPU（Python）端逐像素处理以免成为瓶颈。

#### 小结
在 PyACL 应用中，DVPP 提供高吞吐的硬件级图像/视频预处理能力，AIPP 提供与模型输入严格一致的像素级转换。合理地将两者组合，并配合异步流与事件机制，可以在边缘设备上实现高效、低延迟的图像/视频推理流水线。

### DVPP

DVPP（Digital Vision Pre-Processing）是昇腾处理器的硬件加速引擎，用于处理 JPEG 解码、缩放、抠图等，速度远超 CPU。

### JPEGD (JPEG Decode)
将 `.jpg` 数据解码为 YUV 格式。
```python
# 1. 创建图片描述信息
channel_desc = acl.media.dvpp_create_channel_desc()
acl.media.dvpp_create_channel(channel_desc)

# 2. 准备输入内存 (Host -> Device)
# 假设 np_jpg_data 是读取的二进制 jpg 数据
input_dev, _ = acl.media.dvpp_malloc(len(np_jpg_data))
acl.rt.memcpy(input_dev, len(np_jpg_data), np_jpg_data_ptr, len(np_jpg_data), 1) # 1=Host2Device

# 3. 预测解码后大小并申请输出内存
output_desc = acl.media.dvpp_create_jped_config()
output_size, _ = acl.media.dvpp_jpeg_predict_dec_size(np_jpg_data_ptr, len(np_jpg_data), output_desc)
output_dev, _ = acl.media.dvpp_malloc(output_size)

# 4. 执行异步解码
acl.media.dvpp_jpeg_decode_async(channel_desc, input_dev, len(np_jpg_data), output_dev, output_size, output_desc, stream)
acl.rt.synchronize_stream(stream) # 等待完成
```

### Resnet-SSD

```bash
atc --model=models/ssd_resnet50.onnx --framework=5 --output=models/ssd_resnet50 --input_shape="input:1,3,300,300" --soc_version=Ascend310B4
```

Nvidia 5090D Resnet50-SSD CUDA:
```bash
================ 性能测试结果 ================
处理图片数量: 4952
总耗时: 95.30s
全流程 FPS: 51.96
纯推理+解码 FPS: 59.67
============================================
正在使用 coco_gt_temp.json 计算 mAP ...
loading annotations into memory...
Done (t=0.07s)
creating index...
index created!
Loading and preparing results...
DONE (t=1.31s)
creating index...
index created!
Running per image evaluation...
Evaluate annotation type *bbox*
DONE (t=15.64s).
Accumulating evaluation results...
DONE (t=2.31s).
 Average Precision  (AP) @[ IoU=0.50:0.95 | area=   all | maxDets=100 ] = 0.253
 Average Precision  (AP) @[ IoU=0.50      | area=   all | maxDets=100 ] = 0.425
 Average Precision  (AP) @[ IoU=0.75      | area=   all | maxDets=100 ] = 0.261
 Average Precision  (AP) @[ IoU=0.50:0.95 | area= small | maxDets=100 ] = 0.084
 Average Precision  (AP) @[ IoU=0.50:0.95 | area=medium | maxDets=100 ] = 0.308
 Average Precision  (AP) @[ IoU=0.50:0.95 | area= large | maxDets=100 ] = 0.426
 Average Recall     (AR) @[ IoU=0.50:0.95 | area=   all | maxDets=  1 ] = 0.238
 Average Recall     (AR) @[ IoU=0.50:0.95 | area=   all | maxDets= 10 ] = 0.347
 Average Recall     (AR) @[ IoU=0.50:0.95 | area=   all | maxDets=100 ] = 0.364
 Average Recall     (AR) @[ IoU=0.50:0.95 | area= small | maxDets=100 ] = 0.166
 Average Recall     (AR) @[ IoU=0.50:0.95 | area=medium | maxDets=100 ] = 0.446
 Average Recall     (AR) @[ IoU=0.50:0.95 | area= large | maxDets=100 ] = 0.542
 ```


 ResNet34-SSD 5090D
 ```bash
================ 性能测试结果 ================
处理图片数量: 4952
总耗时: 99.58s
全流程 FPS: 49.73
纯推理+解码 FPS: 56.80
============================================
正在使用 coco_gt_temp.json 计算 mAP ...
loading annotations into memory...
Done (t=0.07s)
creating index...
index created!
Loading and preparing results...
DONE (t=1.75s)
creating index...
index created!
Running per image evaluation...
Evaluate annotation type *bbox*
DONE (t=15.68s).
Accumulating evaluation results...
DONE (t=2.45s).
 Average Precision  (AP) @[ IoU=0.50:0.95 | area=   all | maxDets=100 ] = 0.241
 Average Precision  (AP) @[ IoU=0.50      | area=   all | maxDets=100 ] = 0.403
 Average Precision  (AP) @[ IoU=0.75      | area=   all | maxDets=100 ] = 0.249
 Average Precision  (AP) @[ IoU=0.50:0.95 | area= small | maxDets=100 ] = 0.071
 Average Precision  (AP) @[ IoU=0.50:0.95 | area=medium | maxDets=100 ] = 0.289
 Average Precision  (AP) @[ IoU=0.50:0.95 | area= large | maxDets=100 ] = 0.423
 Average Recall     (AR) @[ IoU=0.50:0.95 | area=   all | maxDets=  1 ] = 0.232
 Average Recall     (AR) @[ IoU=0.50:0.95 | area=   all | maxDets= 10 ] = 0.335
 Average Recall     (AR) @[ IoU=0.50:0.95 | area=   all | maxDets=100 ] = 0.351
 Average Recall     (AR) @[ IoU=0.50:0.95 | area= small | maxDets=100 ] = 0.147
 Average Recall     (AR) @[ IoU=0.50:0.95 | area=medium | maxDets=100 ] = 0.431
 Average Recall     (AR) @[ IoU=0.50:0.95 | area= large | maxDets=100 ] = 0.541
```

ResNet18-SSD 5090D:
```bash
================ 性能测试结果 ================
处理图片数量: 4952
总耗时: 98.31s
全流程 FPS: 50.37
纯推理+解码 FPS: 57.67
============================================
正在使用 coco_gt_temp.json 计算 mAP ...
loading annotations into memory...
Done (t=0.07s)
creating index...
index created!
Loading and preparing results...
DONE (t=1.86s)
creating index...
index created!
Running per image evaluation...
Evaluate annotation type *bbox*
DONE (t=16.24s).
Accumulating evaluation results...
DONE (t=2.77s).
 Average Precision  (AP) @[ IoU=0.50:0.95 | area=   all | maxDets=100 ] = 0.191
 Average Precision  (AP) @[ IoU=0.50      | area=   all | maxDets=100 ] = 0.334
 Average Precision  (AP) @[ IoU=0.75      | area=   all | maxDets=100 ] = 0.192
 Average Precision  (AP) @[ IoU=0.50:0.95 | area= small | maxDets=100 ] = 0.049
 Average Precision  (AP) @[ IoU=0.50:0.95 | area=medium | maxDets=100 ] = 0.217
 Average Precision  (AP) @[ IoU=0.50:0.95 | area= large | maxDets=100 ] = 0.358
 Average Recall     (AR) @[ IoU=0.50:0.95 | area=   all | maxDets=  1 ] = 0.199
 Average Recall     (AR) @[ IoU=0.50:0.95 | area=   all | maxDets= 10 ] = 0.287
 Average Recall     (AR) @[ IoU=0.50:0.95 | area=   all | maxDets=100 ] = 0.301
 Average Recall     (AR) @[ IoU=0.50:0.95 | area= small | maxDets=100 ] = 0.110
 Average Recall     (AR) @[ IoU=0.50:0.95 | area=medium | maxDets=100 ] = 0.360
 Average Recall     (AR) @[ IoU=0.50:0.95 | area= large | maxDets=100 ] = 0.488
```bash

昇腾310B ResNet50-SSD ：
```bash
开始评估全量数据集 mAP 和 推理帧率 ...
Evaluating: 100%|███████████████████████████████████████████████████████████████████████████████████████████| 4952/4952 [06:22<00:00, 12.94it/s]

================ 性能测试结果 ================
处理图片数量: 4952
总耗时: 382.58s
全流程 FPS: 12.94
纯推理+解码 FPS: 14.65
============================================
正在使用 coco_gt_temp.json 计算 mAP ...
loading annotations into memory...
Done (t=0.34s)
creating index...
index created!
Loading and preparing results...
DONE (t=6.29s)
creating index...
index created!
Running per image evaluation...
Evaluate annotation type *bbox*
DONE (t=120.36s).
Accumulating evaluation results...
DONE (t=18.41s).
 Average Precision  (AP) @[ IoU=0.50:0.95 | area=   all | maxDets=100 ] = 0.252
 Average Precision  (AP) @[ IoU=0.50      | area=   all | maxDets=100 ] = 0.425
 Average Precision  (AP) @[ IoU=0.75      | area=   all | maxDets=100 ] = 0.261
 Average Precision  (AP) @[ IoU=0.50:0.95 | area= small | maxDets=100 ] = 0.084
 Average Precision  (AP) @[ IoU=0.50:0.95 | area=medium | maxDets=100 ] = 0.308
 Average Precision  (AP) @[ IoU=0.50:0.95 | area= large | maxDets=100 ] = 0.426
 Average Recall     (AR) @[ IoU=0.50:0.95 | area=   all | maxDets=  1 ] = 0.238
 Average Recall     (AR) @[ IoU=0.50:0.95 | area=   all | maxDets= 10 ] = 0.347
 Average Recall     (AR) @[ IoU=0.50:0.95 | area=   all | maxDets=100 ] = 0.364
 Average Recall     (AR) @[ IoU=0.50:0.95 | area= small | maxDets=100 ] = 0.166
 Average Recall     (AR) @[ IoU=0.50:0.95 | area=medium | maxDets=100 ] = 0.446
 Average Recall     (AR) @[ IoU=0.50:0.95 | area= large | maxDets=100 ] = 0.542
```

昇腾310B ResNet-34 SSD：
```bash
开始评估全量数据集 mAP 和 推理帧率 ...
Evaluating: 100%|███████████████████████████████████████████████████████████████████| 4952/4952 [05:46<00:00, 14.29it/s]

================ 性能测试结果 ================
处理图片数量: 4952
总耗时: 346.62s
全流程 FPS: 14.29
纯推理+解码 FPS: 16.42
============================================
正在使用 coco_gt_temp.json 计算 mAP ...
loading annotations into memory...
Done (t=0.33s)
creating index...
index created!
Loading and preparing results...
DONE (t=7.12s)
creating index...
index created!
Running per image evaluation...
Evaluate annotation type *bbox*
DONE (t=122.56s).
Accumulating evaluation results...
DONE (t=19.68s).
 Average Precision  (AP) @[ IoU=0.50:0.95 | area=   all | maxDets=100 ] = 0.241
 Average Precision  (AP) @[ IoU=0.50      | area=   all | maxDets=100 ] = 0.403
 Average Precision  (AP) @[ IoU=0.75      | area=   all | maxDets=100 ] = 0.249
 Average Precision  (AP) @[ IoU=0.50:0.95 | area= small | maxDets=100 ] = 0.071
 Average Precision  (AP) @[ IoU=0.50:0.95 | area=medium | maxDets=100 ] = 0.289
 Average Precision  (AP) @[ IoU=0.50:0.95 | area= large | maxDets=100 ] = 0.423
 Average Recall     (AR) @[ IoU=0.50:0.95 | area=   all | maxDets=  1 ] = 0.232
 Average Recall     (AR) @[ IoU=0.50:0.95 | area=   all | maxDets= 10 ] = 0.335
 Average Recall     (AR) @[ IoU=0.50:0.95 | area=   all | maxDets=100 ] = 0.351
 Average Recall     (AR) @[ IoU=0.50:0.95 | area= small | maxDets=100 ] = 0.147
 Average Recall     (AR) @[ IoU=0.50:0.95 | area=medium | maxDets=100 ] = 0.431
 Average Recall     (AR) @[ IoU=0.50:0.95 | area= large | maxDets=100 ] = 0.541
 ```

昇腾310B ResNet-18 SSD：
```bash
 开始评估全量数据集 mAP 和 推理帧率 ...
Evaluating: 100%|█████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████| 4952/4952 [05:28<00:00, 15.09it/s]

================ 性能测试结果 ================
处理图片数量: 4952
总耗时: 328.10s
全流程 FPS: 15.09
纯推理+解码 FPS: 17.52
============================================
正在使用 coco_gt_temp.json 计算 mAP ...
loading annotations into memory...
Done (t=0.33s)
creating index...
index created!
Loading and preparing results...
DONE (t=7.54s)
creating index...
index created!
Running per image evaluation...
Evaluate annotation type *bbox*
DONE (t=125.35s).
Accumulating evaluation results...
DONE (t=21.56s).
 Average Precision  (AP) @[ IoU=0.50:0.95 | area=   all | maxDets=100 ] = 0.191
 Average Precision  (AP) @[ IoU=0.50      | area=   all | maxDets=100 ] = 0.334
 Average Precision  (AP) @[ IoU=0.75      | area=   all | maxDets=100 ] = 0.192
 Average Precision  (AP) @[ IoU=0.50:0.95 | area= small | maxDets=100 ] = 0.049
 Average Precision  (AP) @[ IoU=0.50:0.95 | area=medium | maxDets=100 ] = 0.216
 Average Precision  (AP) @[ IoU=0.50:0.95 | area= large | maxDets=100 ] = 0.357
 Average Recall     (AR) @[ IoU=0.50:0.95 | area=   all | maxDets=  1 ] = 0.199
 Average Recall     (AR) @[ IoU=0.50:0.95 | area=   all | maxDets= 10 ] = 0.287
 Average Recall     (AR) @[ IoU=0.50:0.95 | area=   all | maxDets=100 ] = 0.301
 Average Recall     (AR) @[ IoU=0.50:0.95 | area= small | maxDets=100 ] = 0.110
 Average Recall     (AR) @[ IoU=0.50:0.95 | area=medium | maxDets=100 ] = 0.360
 Average Recall     (AR) @[ IoU=0.50:0.95 | area= large | maxDets=100 ] = 0.488
 ```

昇腾310B ResNet-101 SSD：
 ```bash
 开始评估全量数据集 mAP 和 推理帧率 ...
Evaluating: 100%|█████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████| 4952/4952 [08:22<00:00,  9.85it/s]

================ 性能测试结果 ================
处理图片数量: 4952
总耗时: 502.81s
全流程 FPS: 9.85
纯推理+解码 FPS: 10.80
============================================
正在使用 coco_gt_temp.json 计算 mAP ...
loading annotations into memory...
Done (t=0.34s)
creating index...
index created!
Loading and preparing results...
DONE (t=6.12s)
creating index...
index created!
Running per image evaluation...
Evaluate annotation type *bbox*
DONE (t=118.29s).
Accumulating evaluation results...
DONE (t=17.68s).
 Average Precision  (AP) @[ IoU=0.50:0.95 | area=   all | maxDets=100 ] = 0.283
 Average Precision  (AP) @[ IoU=0.50      | area=   all | maxDets=100 ] = 0.460
 Average Precision  (AP) @[ IoU=0.75      | area=   all | maxDets=100 ] = 0.297
 Average Precision  (AP) @[ IoU=0.50:0.95 | area= small | maxDets=100 ] = 0.102
 Average Precision  (AP) @[ IoU=0.50:0.95 | area=medium | maxDets=100 ] = 0.348
 Average Precision  (AP) @[ IoU=0.50:0.95 | area= large | maxDets=100 ] = 0.472
 Average Recall     (AR) @[ IoU=0.50:0.95 | area=   all | maxDets=  1 ] = 0.258
 Average Recall     (AR) @[ IoU=0.50:0.95 | area=   all | maxDets= 10 ] = 0.374
 Average Recall     (AR) @[ IoU=0.50:0.95 | area=   all | maxDets=100 ] = 0.392
 Average Recall     (AR) @[ IoU=0.50:0.95 | area= small | maxDets=100 ] = 0.188
 Average Recall     (AR) @[ IoU=0.50:0.95 | area=medium | maxDets=100 ] = 0.481
 Average Recall     (AR) @[ IoU=0.50:0.95 | area= large | maxDets=100 ] = 0.578
 ```

昇腾310B ResNet-151 SSD：
 ```bash
 开始评估全量数据集 mAP 和 推理帧率 ...
Evaluating: 100%|████████████████████████████████████| 4952/4952 [09:59<00:00,  8.26it/s]

================ 性能测试结果 ================
处理图片数量: 4952
总耗时: 599.39s
全流程 FPS: 8.26
纯推理+解码 FPS: 8.94
============================================
正在使用 coco_gt_temp.json 计算 mAP ...
loading annotations into memory...
Done (t=0.33s)
creating index...
index created!
Loading and preparing results...
DONE (t=5.94s)
creating index...
index created!
Running per image evaluation...
Evaluate annotation type *bbox*
DONE (t=115.48s).
Accumulating evaluation results...
DONE (t=17.20s).
 Average Precision  (AP) @[ IoU=0.50:0.95 | area=   all | maxDets=100 ] = 0.301
 Average Precision  (AP) @[ IoU=0.50      | area=   all | maxDets=100 ] = 0.483
 Average Precision  (AP) @[ IoU=0.75      | area=   all | maxDets=100 ] = 0.314
 Average Precision  (AP) @[ IoU=0.50:0.95 | area= small | maxDets=100 ] = 0.116
 Average Precision  (AP) @[ IoU=0.50:0.95 | area=medium | maxDets=100 ] = 0.370
 Average Precision  (AP) @[ IoU=0.50:0.95 | area= large | maxDets=100 ] = 0.498
 Average Recall     (AR) @[ IoU=0.50:0.95 | area=   all | maxDets=  1 ] = 0.271
 Average Recall     (AR) @[ IoU=0.50:0.95 | area=   all | maxDets= 10 ] = 0.393
 Average Recall     (AR) @[ IoU=0.50:0.95 | area=   all | maxDets=100 ] = 0.411
 Average Recall     (AR) @[ IoU=0.50:0.95 | area= small | maxDets=100 ] = 0.207
 Average Recall     (AR) @[ IoU=0.50:0.95 | area=medium | maxDets=100 ] = 0.498
 Average Recall     (AR) @[ IoU=0.50:0.95 | area= large | maxDets=100 ] = 0.600
```