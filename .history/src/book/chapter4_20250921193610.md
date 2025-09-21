---
title: "第4讲：典型模型部署实践"
author: [周贤中]
date: 2025-09-04
subject: "Markdown"
keywords: [模型部署, 分类, 检测, OCR, NLP, Pipeline, Benchmark]
lang: zh-cn
---

## 章节总览
本章以“统一流程 → 四类典型任务（分类/检测/OCR/NLP）→ 多模型 Pipeline → 工程化目录与脚本 → 性能基线采集 → 问题诊断”逻辑展开，强调“可复现、可量化、可演进”的部署范式。所有示例策略均可推广到后续复杂场景（多输入、多分辨率、流式/批式混合）。

## 统一部署工作流与契约化
标准六步：模型选择 → 框架导出 ONNX → ATC 转换（参数冻结）→ 推理引擎封装（I/O 契约）→ 运行形态编排 → 验证（精度 + 性能）。
核心产物：
| 文件 | 作用 |
| ---- | ---- |
| export.py | 导出 & 简化 ONNX |
| atc.sh | 标准化转换命令 |
| config.yaml | 输入/归一化/颜色/阈值 |
| signature.json | 模型输入输出字段与 dtype |
| metrics.json | 性能统计（avg/p95/memory） |

输入预处理必须模块化，业务层仅提供原始图像对象；可在 AIPP 中下沉部分（色彩空间、均值/方差），减少 Host 侧拷贝和转换。

## 图像分类：ResNet / MobileNet
### 模型导出
PyTorch → ONNX：`torch.onnx.export(model, dummy, opset_version=13, dynamic_axes=None)`；确保去掉训练专属层（Dropout, BN 置 eval）。
### 预处理一致性
1. Resize: 保持短边 256 → CenterCrop 224。
2. Normalize: mean/std 与训练保持一致。
3. Layout: NCHW；若原始图像为 HWC(RGB) → 转 BGR/或保持一致并在 config 标记。
### 转换要点
`--precision_mode=allow_fp32_to_fp16`；若需 INT8：先做离线标定导出校准表，再加量化参数。
### 推理后处理
Softmax → ArgTopK → LabelMap。为避免数值不稳定：FP16 logits 可先转 FP32 再 softmax。
### 性能采集
Warmup 5 次，采集 100 次：记录 avg, p50, p95, max；统计预处理耗时占比：`pre_ms / total_ms`，超过 25% 提示 AIPP 下沉或批处理优化。

## 目标检测：YOLO / FasterRCNN
### 输入尺寸与 Letterbox
Letterbox 使图像等比例缩放 + 填充，保持方形输入。部署需重现训练阶段相同逻辑，否则框坐标偏移。保存 `scale` 与 `pad` 用于反算原始坐标。
### 多输出解析
YOLOv5s OM 输出通常包含一个或多个特征拼接张量：`(num_boxes, attributes)`；后处理：过滤 conf > 阈值 → 按类合并 → NMS。
### NMS 实现决策
| 方案 | 优点 | 缺点 |
| ---- | ---- | ---- |
| CPU Python | 简单 | 高开销，多框场景慢 |
| CPU C++ SIMD | 中等复杂 | 仍需 D2H 拷贝 |
| Device Kernel | 减少拷贝 | 实现复杂 |
先评估 D2H + CPU NMS 占比，>15% 再考虑下沉。
### 动态尺度支持
转换阶段可生成多尺度 OM 或使用动态 shape；推荐：统计输入分辨率 → 选择 3 桶（640/704/768）提升命中率。

## OCR：文本检测 + 识别 Pipeline
### 结构
检测模型（DB） → 文本框多边形 → 透视裁剪 → 识别模型（CRNN / SVTR）。
### 难点与策略
| 环节 | 风险 | 对策 |
| ---- | ---- | ---- |
| 多边形裁剪 | 仿射失真 | 统一仿射矩阵 + padding |
| 长短文本差异 | 序列长度不均 | 动态 Batch 分组（长度分桶） |
| 识别延迟 | 串行处理 | 检测与上一批识别并行 |
| 字典映射 | 乱码/对齐 | 固定 vocab + 版本号 |
### CTC 解码
贪心：移除重复与 blank；大规模需 Beam Search（权衡性能）。

## NLP：BERT 推理优化
### 序列长度策略
1. 静态最大长度（简单，浪费算力）。
2. Bucketing：按输入长短分类（32/64/128/256），多 OM。
3. 动态 shape：需评估内存分配抖动；提前预热各常见长度。
### FP16 注意点
LayerNorm/Softmax 数值范围敏感；若发现精度下降：保持部分算子 FP32（通过混合精度策略或模型修改）。
### 性能指标
tokens/s、avg_latency_ms（batch=1 与 batch>1）、内存占用；观察自注意力占比，必要时进行剪枝（去除冗余 head）或蒸馏。

## 多模型 Pipeline 串联
案例：检测 → 裁剪 → 分类。
| Stage | 输入/输出 | 并行策略 | 指标采集 |
| ----- | -------- | -------- | -------- |
| Detector | 原始帧 → 框 | 批处理+单模型 | 时延/框数 |
| Cropper | 帧+框 → Patch 列表 | 多线程 CPU | 单 Patch 平均耗时 |
| Classifier | Patch → TopK 类别 | 合批 (N≤32) | FPS/准确率 |
### 优化要点
1. Buffer 池：重用图像与 Patch 内存，避免频繁 malloc。
2. 批量裁剪：收集一定数量 Patch 再统一预处理。
3. 超时控制：某帧超过阈值后续结果丢弃，保持实时性。
4. 滑窗统计：最近 60s FPS、平均队列深度。

## 工程目录与脚本标准
```
deploy/
  classify/
    export.py
    atc.sh
    config.yaml
  detect/
    export.py
    atc.sh
  ocr/
    export_det.py
    export_rec.py
    atc_det.sh
    atc_rec.sh
runtime/
  core/acl_session.cpp
  preprocess/
  postprocess/
  pipelines/
tests/
  data/
  benchmark/
docs/
  model_cards/
```
版本归档要求：
| 产物 | 检查点 |
| ---- | ------- |
| *.om | 与 atc.log hash 对应 |
| signature.json | 与运行时动态查询一致 |
| metrics.json | 包含时间戳/commit_sha |
| model_card.md | 模型来源/License/精度 | 

## 性能基线方法与统计置信
推荐：
1. Warmup 5~10 次；
2. 收集 ≥200 次稳定样本；
3. 计算 avg, p50, p95, p99；
4. 计算置信区间：`mean ± 1.96 * (std/sqrt(n))`；
5. 记录环境：芯片序列号/温度区间/电源模式/版本矩阵。 
差异判定：新版本 avg 降低 >5% 或 p95 上升 >8% 触发报警分析。

## 常见问题诊断深度版
| 问题 | 表现 | 诊断步骤 | 修复 |
| ---- | ---- | -------- | ---- |
| 输出全 0 | logits 恒定 | Dump 中间 tensor | 校验预处理/权重损坏 |
| 检测框偏移 | 坐标不准 | 可视化缩放/Pad 参数 | 修正 letterbox 逆变换 |
| OCR 乱码 | 字符错位 | 对比 index→char 映射 | 统一 vocab & 排序 |
| BERT 性能差 | tokens/s 低 | 分析长度分布 | 分桶/裁剪长度 |
| Pipeline 堵塞 | 帧延迟增长 | 监控队列深度 | 降帧/扩线程池 |
| 内存持续上涨 | long run OOM | 内存快照/工具 | 释放缓存/池化 |

## 章节小结
本章提供四类典型任务部署详解，并抽象了跨任务可复用的脚手架与性能度量方法。重点在于“输入契约统一”、“阶段解耦”、“可观察性内建”。掌握后可进入性能与算子优化专题。

## 实践任务
1. 部署 ResNet50：输出 Top5 及概率、提交 metrics.json。
2. 部署 YOLOv5s：5 张测试图片生成可视化结果（描述框坐标与类别统计）。
3. 构建 OCR 双模型流水线：统计单帧平均文本块数 + 平均识别耗时。
4. BERT：对 3 组长度(32/64/128) 测 tokens/s 与时延差异，生成对比表。
5. Pipeline 检测→分类：实现批裁剪 + Buffer 池，比较优化前后平均时延下降百分比。

