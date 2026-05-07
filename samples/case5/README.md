# 案例 5：智能数据采集仪 — 多电机状态监测

基于昇腾 310B 的多电机数据采集与状态监测系统，结合 **STM32 低速传感**、
**FPGA 高速振动采集**与 **NPU 故障分类**，适用于机器人关节电机、无人机
电机等小电机群的预测性维护。

## 项目说明

本项目演示**边缘 AI + 嵌入式硬件**的协同数据采集工作流：

| 阶段 | 硬件 | 操作 |
| :--- | :--- | :--- |
| 1 | STM32 | 采集温度/电流/转速 (I2C/ADC)，UART 发送 |
| 2 | FPGA | 高速振动信号采集 (5kHz)，FFT 预处理 |
| 3 | Ascend 310B | 接收数据，振动频谱 → NPU 故障分类 |
| 4 | Ascend 310B | CPU 趋势分析 + 异常检测 |
| 5 | 浏览器 | Gradio 仪表盘展示监测结果 |

## 目录结构

```text
case5/
├── app.py                 # Gradio 仪表盘入口
├── sensor_reader.py        # STM32 UART 通信 + 模拟回退
├── vibration_processor.py  # FPGA 振动数据 + 梅尔频谱图生成
├── fault_classifier.py     # NPU/CPU 双后端故障分类
├── anomaly_detector.py     # 统计异常检测 (3σ + 趋势预测)
├── data_logger.py          # CSV 数据记录
├── config.py               # 配置常量
├── prepare_models.py       # ONNX 导出 & OM 转换
├── setup.sh                # 一键环境安装
├── requirements.txt        # Python 依赖
├── stm32_protocol.md       # STM32 固件开发参考
├── data/                   # 运行时数据目录
├── models/                 # 模型文件目录
└── README.md
```

## 快速开始

### 1. 环境安装

```bash
bash setup.sh
```

### 2. 准备模型

setup.sh 已自动执行此步骤。如需手动重做：

```bash
# ONNX 导出
python3 prepare_models.py --onnx-only

# ONNX → OM 转换 (需在昇腾设备上运行)
python3 prepare_models.py
```

### 3. 启动服务

```bash
python3 app.py
```

打开 http://127.0.0.1:7860，查看实时数据仪表盘。

### 4. 命令行选项

```bash
python3 app.py --port 8080        # 指定端口
python3 app.py --share            # 生成公网分享链接
```

## 运行模式

### NPU 模式（默认）

在昇腾 310B 设备上自动检测并使用 OM 模型进行振动频谱故障分类。

### CPU 回退

没有 Ascend 310B 时自动回退到 PyTorch EfficientNet-B0。

### 模拟模式

没有 STM32/FPGA 硬件时自动生成仿真传感器数据，方便开发测试。

## 故障类别

| # | 故障类型 | 频谱特征 |
|---|---------|----------|
| 0 | 正常运行 | 频谱均匀 |
| 1 | 轴承磨损 | 高频分量增加 |
| 2 | 动平衡不良 | 1×转速频率峰值 |
| 3 | 对中不良 | 2×转速频率峰值 |
| 4 | 机械松动 | 多次谐波+基底噪声 |

## 硬件依赖

| 组件 | 用途 | 必需？ |
| :--- | :--- | :--- |
| STM32 (F4/H7) | 低速传感器采集 (温度/电流/转速) | 模拟模式可无 |
| FPGA (可选) | 高速振动采集 (ADXL345) | 模拟模式可无 |
| Ascend 310B | AI 推理 + 数据管理 | ✓ |
| USB 摄像头 (可选) | 设备外观巡检 | 可选 |

## 软件依赖

| 包 | 用途 | 必需？ |
| :--- | :--- | :--- |
| gradio | Web 仪表盘 | ✓ |
| torch + torchvision | EfficientNet-B0 / CPU 推理 | ✓ |
| opencv-python | 频谱图渲染 | ✓ |
| numpy | 数值计算 / 频谱分析 | ✓ |
| pyserial | STM32 UART 通信 | 仅硬件模式 |
| onnx | ONNX 模型校验 | 仅 prepare_models.py |
| acl (CANN) | NPU 推理 | 仅昇腾设备 |

## 使用建议

1. 先以模拟模式启动 `python3 app.py` 体验仪表盘
2. 切换到「振动频谱分析」页签，选择电机查看频谱图和故障诊断
3. 在「实时监测」页签观察传感器数据和异常告警
4. 参考 `stm32_protocol.md` 搭建 STM32 硬件，接入真实传感器
5. 在昇腾设备上运行，体验 NPU 加速的故障分类
6. 通过 `config.py` 调整告警阈值和窗口大小
