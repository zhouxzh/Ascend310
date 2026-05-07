"""
Case 5: Smart Data Acquisition — Gradio dashboard for multi-motor
condition monitoring.

Three tabs:
  1. Real-time Monitoring — sensor values + anomaly alerts per motor
  2. Vibration Spectrum — mel-spectrogram + NPU fault classification
  3. System Info — model status, thresholds, log statistics
"""

import argparse
import os
import time

import gradio as gr
import numpy as np

from config import (
    CSV_LOG_PATH,
    DATA_DIR,
    FAULT_CLASSES,
    IMAGE_SIZE,
    MOTOR_NAMES,
    NUM_FAULT_CLASSES,
    NUM_MOTORS,
    OM_MODEL_PATH,
    PTH_MODEL_PATH,
)
from sensor_reader import SensorReader
from vibration_processor import VibrationProcessor
from fault_classifier import FaultClassifier
from anomaly_detector import AnomalyDetector
from data_logger import DataLogger

# ---------------------------------------------------------------------------
# Lazy-initialized globals
# ---------------------------------------------------------------------------

_reader = None
_vib_proc = None
_classifier = None
_detector = None
_logger = None


def get_reader():
    global _reader
    if _reader is None:
        _reader = SensorReader()
    return _reader


def get_vib_proc():
    global _vib_proc
    if _vib_proc is None:
        _vib_proc = VibrationProcessor()
    return _vib_proc


def get_classifier():
    global _classifier
    if _classifier is None:
        _classifier = FaultClassifier()
    return _classifier


def get_detector():
    global _detector
    if _detector is None:
        _detector = AnomalyDetector()
    return _detector


def get_logger():
    global _logger
    if _logger is None:
        _logger = DataLogger()
    return _logger


# ---------------------------------------------------------------------------
# Tab 1: Real-time Monitoring
# ---------------------------------------------------------------------------

def refresh_monitor():
    """Read sensors, detect anomalies, return display data."""
    reader = get_reader()
    detector = get_detector()
    logger = get_logger()

    motors = reader.read()
    if not motors:
        return _empty_monitor()

    # Anomaly detection
    anomalies = detector.update(motors)

    # Log to CSV
    logger.log(motors)

    # Build sensor display table
    table_data = []
    for m in motors:
        # Check for threshold alerts
        temp_flag = "⚠️" if m["temperature"] >= 65 else "✓"
        cur_flag = "⚠️" if m["current"] >= 2.0 else "✓"
        rpm_flag = "⚠️" if (m["rpm"] < 100 and m["rpm"] > 0) else "✓"

        table_data.append([
            m["name"],
            f"{temp_flag} {m['temperature']:.1f} °C",
            f"{cur_flag} {m['current']:.2f} A",
            f"{rpm_flag} {m['rpm']:.0f} RPM",
        ])

    # Build alert markdown
    all_alerts = []
    all_alerts.extend(reader.get_temperature_alerts(motors))
    all_alerts.extend(reader.get_current_alerts(motors))

    for a in anomalies:
        all_alerts.append(a)

    alert_lines = []
    if all_alerts:
        for a in all_alerts:
            icon = "🔴" if a.get("level") == "critical" else "🟡"
            if "z_score" in a:
                alert_lines.append(
                    f"- {icon} **{a['name']}** {a['parameter']} 异常: "
                    f"当前={a['value']}, 均值={a['mean']}, "
                    f"z-score={a['z_score']}"
                )
            else:
                alert_lines.append(
                    f"- {icon} **{a['name']}** {a['parameter']} 超阈值: "
                    f"{a['value']} (阈值 {a['threshold']})"
                )

    alert_md = "\n".join(alert_lines) if alert_lines else "✅ 所有电机运行正常"

    # Log stats
    log_stats = logger.get_stats()

    # Summary markdown
    ts = time.strftime("%H:%M:%S")
    summary = (
        f"**更新时间**: {ts}  |  "
        f"**数据行数**: {log_stats['total_rows']}  |  "
        f"**日志大小**: {log_stats['file_size_mb']} MB"
    )

    return table_data, alert_md, summary


def _empty_monitor():
    empty = [["—", "—", "—", "—"] for _ in range(NUM_MOTORS)]
    return empty, "_等待数据..._", "**就绪**"


# ---------------------------------------------------------------------------
# Tab 2: Vibration Spectrum + NPU Fault Classification
# ---------------------------------------------------------------------------

def analyze_vibration(motor_choice):
    """Generate spectrogram and classify fault for selected motor."""
    motor_id = int(motor_choice.split(":")[0]) if motor_choice else 0

    vib = get_vib_proc()
    clf = get_classifier()

    # Get spectrogram
    spec_bgr, spec_float = vib.get_spectrogram_for_npu(motor_id)

    # NPU fault classification
    t0 = time.time()
    result = clf.classify(spec_float)
    elapsed = (time.time() - t0) * 1000

    # Build result markdown
    top = result
    bar = _confidence_bar(top["confidence"])

    lines = [
        f"### 🏷️ 故障诊断: {top['label_cn']} ({top['label_en']})",
        f"置信度: {top['confidence']:.1%} {bar}",
        f"_{top['desc']}_",
        "",
        "### 📊 Top-3 预测",
    ]
    for i, p in enumerate(top["all_probs"][:3]):
        marker = "→" if i == 0 else "  "
        lines.append(f"- {marker} {p['label_cn']} ({p['label_en']}): "
                     f"{p['confidence']:.1%}")

    lines.append("")
    backend = "NPU" if clf.use_npu else "CPU"
    lines.append(f"⏱ 推理耗时: {elapsed:.1f} ms  |  🖥 后端: {backend}")

    return spec_bgr, "\n".join(lines)


# ---------------------------------------------------------------------------
# Tab 3: System Info
# ---------------------------------------------------------------------------

def get_system_info():
    """Gather system status."""
    clf = get_classifier()
    logger = get_logger()
    backend = "NPU (Ascend 310B)" if clf.use_npu else "CPU (PyTorch)"
    log_stats = logger.get_stats()

    has_om = os.path.exists(OM_MODEL_PATH)
    has_pth = os.path.exists(PTH_MODEL_PATH)

    lines = [
        f"**推理后端**: {backend}",
        f"**模型**: EfficientNet-B0",
        f"**输入尺寸**: {IMAGE_SIZE}×{IMAGE_SIZE}",
        f"**故障类别**: {NUM_FAULT_CLASSES} 种",
        f"**监测电机数**: {NUM_MOTORS}",
        f"**OM 模型**: {'✓' if has_om else '✗'} {OM_MODEL_PATH}",
        f"**PTH 权重**: {'✓' if has_pth else '✗'} {PTH_MODEL_PATH}",
        "",
        "### 故障类别",
    ]

    for fc in FAULT_CLASSES:
        lines.append(f"- **{fc['cn']}** ({fc['en']}): {fc['desc']}")

    lines.append("")
    lines.append("### 数据日志")
    lines.append(f"- 文件: {CSV_LOG_PATH}")
    lines.append(f"- 总行数: {log_stats['total_rows']}")
    lines.append(f"- 文件大小: {log_stats['file_size_mb']} MB")

    lines.append("")
    lines.append("### 硬件接口")
    lines.append(f"- STM32 → UART ({config.UART_PORT} @ "
                 f"{config.UART_BAUDRATE} baud)")
    lines.append("- FPGA → SPI (振动高速采集)")
    lines.append("- 传感器: DS18B20 + ACS712 + 霍尔 + ADXL345")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _confidence_bar(value, width=16):
    filled = int(value * width)
    return f"`{'█' * filled}{'░' * (width - filled)}`"


# ---------------------------------------------------------------------------
# Build UI
# ---------------------------------------------------------------------------

def build_ui():
    theme = gr.themes.Soft()

    with gr.Blocks(theme=theme,
                   title="多电机数据采集 - Ascend 310B") as demo:
        gr.Markdown("""
        # 📊 智能数据采集仪 — 多电机状态监测
        **STM32** 低速传感 (温度/电流/转速) + **FPGA** 高速振动采集
        + **NPU** 故障分类 (EfficientNet-B0)。
        适用于机器人关节电机、无人机电机等小电机群监测。
        """)

        with gr.Tabs():
            # ====================================================
            # Tab 1: Real-time Monitoring
            # ====================================================
            with gr.TabItem("📋 实时监测"):
                alert_md = gr.Markdown("_启动中..._")

                with gr.Row():
                    refresh_btn = gr.Button("🔄 刷新数据",
                                            variant="primary")
                    summary_md = gr.Markdown("")

                data_table = gr.DataFrame(
                    headers=["电机", "温度", "电流", "转速"],
                    value=[["—", "—", "—", "—"]
                           for _ in range(NUM_MOTORS)],
                    label="实时传感数据",
                    interactive=False,
                )

                refresh_btn.click(
                    refresh_monitor,
                    outputs=[data_table, alert_md, summary_md],
                )

                # Auto-refresh on load
                demo.load(
                    refresh_monitor,
                    outputs=[data_table, alert_md, summary_md],
                )

            # ====================================================
            # Tab 2: Vibration Spectrum + NPU Fault Classification
            # ====================================================
            with gr.TabItem("📈 振动频谱分析"):
                motor_sel = gr.Dropdown(
                    choices=[f"{i}: {n}" for i, n
                             in enumerate(MOTOR_NAMES)],
                    value="0: 电机1",
                    label="选择电机",
                )
                analyze_btn = gr.Button("🔍 分析振动频谱",
                                        variant="primary")

                with gr.Row():
                    spec_img = gr.Image(
                        type="numpy",
                        label="振动梅尔频谱图 (128×128 mel)",
                        height=320,
                    )
                    fault_md = gr.Markdown("_点击分析按钮..._")

                analyze_btn.click(
                    analyze_vibration,
                    inputs=[motor_sel],
                    outputs=[spec_img, fault_md],
                )

            # ====================================================
            # Tab 3: System Info
            # ====================================================
            with gr.TabItem("⚙️ 系统信息"):
                sys_md = gr.Markdown(value=get_system_info())
                sys_refresh = gr.Button("刷新")
                sys_refresh.click(get_system_info, outputs=[sys_md])

    return demo


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Smart Data Acquisition - Case 5"
    )
    parser.add_argument("--port", type=int, default=7860,
                        help="Server port")
    parser.add_argument("--share", action="store_true",
                        help="Create public link")
    args = parser.parse_args()

    os.makedirs(DATA_DIR, exist_ok=True)

    print("=" * 50)
    print("Case 5 — 智能数据采集仪")
    print("=" * 50)

    reader = get_reader()
    if reader.use_hardware:
        print("  STM32: Connected")
    else:
        print("  STM32: Simulation mode")

    clf = get_classifier()
    backend = "NPU (Ascend 310B)" if clf.use_npu else "CPU (PyTorch)"
    print(f"  NPU Backend: {backend}")
    print(f"  Model: EfficientNet-B0 ({NUM_FAULT_CLASSES} fault classes)")
    print(f"  Motors: {NUM_MOTORS}")
    print("-" * 50)

    demo = build_ui()
    demo.queue()
    demo.launch(
        server_name="0.0.0.0",
        server_port=args.port,
        share=args.share,
    )


if __name__ == "__main__":
    import config
    main()
