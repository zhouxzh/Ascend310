"""
Case 6: Smart Car Perception — Gradio web interface.

Two tabs:
  1. Road Perception — upload image or use webcam, see lane overlay +
     scene classification
  2. System Info — model status, backend, lane detection params
"""

import argparse
import os
import time

import cv2
import gradio as gr
import numpy as np

from config import (
    DATA_DIR,
    IMAGE_SIZE,
    NUM_SCENES,
    OM_MODEL_PATH,
    PTH_MODEL_PATH,
    SCENE_CLASSES,
)
from lane_detector import LaneDetector
from scene_classifier import SceneClassifier

# ---------------------------------------------------------------------------
# Lazy-initialized globals
# ---------------------------------------------------------------------------

_detector = None
_classifier = None


def get_detector():
    global _detector
    if _detector is None:
        _detector = LaneDetector()
    return _detector


def get_classifier():
    global _classifier
    if _classifier is None:
        _classifier = SceneClassifier()
    return _classifier


# ---------------------------------------------------------------------------
# Tab 1: Road Perception
# ---------------------------------------------------------------------------

def perceive(image):
    """Process a road image: detect lanes + classify scene.

    Returns:
        annotated image (BGR) with lane overlay,
        markdown result text
    """
    if image is None:
        return None, "_等待输入..._"

    t0 = time.time()

    # Lane detection
    ld = get_detector()
    lane_result = ld.detect(image)
    annotated = ld.draw_overlay(image, lane_result)

    # Scene classification
    sc = get_classifier()
    scene_result = sc.classify(image)

    elapsed = (time.time() - t0) * 1000

    # Build markdown report
    lines = [
        "## 🚗 感知结果",
        "",
        "### 🛣️ 车道线检测",
    ]

    if lane_result["left_lane"] or lane_result["right_lane"]:
        left_str = "✓" if lane_result["left_lane"] else "✗"
        right_str = "✓" if lane_result["right_lane"] else "✗"
        lines.append(f"- 左车道: {left_str}  |  右车道: {right_str}")
        hough_count = lane_result["num_lines_found"]
        lines.append(f"- Hough 线段数: {hough_count}")
    else:
        lines.append("- 未检测到车道线")
        lines.append("  请确认图像中有清晰的车道标线")

    lines.append("")
    lines.append("### 🏷️ 驾驶场景分类")

    top = scene_result
    bar = _confidence_bar(top["confidence"])
    lines.append(f"- **{top['label_cn']}** ({top['label_en']})  "
                 f"置信度: {top['confidence']:.1%} {bar}")
    lines.append(f"- 💡 建议: {top['advice']}")

    lines.append("")
    lines.append("### 📊 Top-3 预测")
    for i, p in enumerate(top["all_probs"][:3]):
        marker = "→" if i == 0 else "  "
        lines.append(f"- {marker} {p['label_cn']} ({p['label_en']}): "
                     f"{p['confidence']:.1%}")

    lines.append("")
    lines.append(f"⏱ 总耗时: {elapsed:.1f} ms")
    backend = "NPU" if sc.use_npu else "CPU"
    lines.append(f"🖥 推理后端: {backend}")

    return annotated, "\n".join(lines)


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _confidence_bar(value, width=16):
    filled = int(value * width)
    return f"`{'█' * filled}{'░' * (width - filled)}`"


# ---------------------------------------------------------------------------
# Tab 2: System Info
# ---------------------------------------------------------------------------

def get_system_info():
    """Gather model and system status."""
    sc = get_classifier()
    backend = "NPU (Ascend 310B)" if sc.use_npu else "CPU (PyTorch)"

    has_om = os.path.exists(OM_MODEL_PATH)
    has_pth = os.path.exists(PTH_MODEL_PATH)

    lines = [
        f"**推理后端**: {backend}",
        f"**模型**: ResNet18",
        f"**输入尺寸**: {IMAGE_SIZE}×{IMAGE_SIZE}",
        f"**场景类别**: {NUM_SCENES} 种",
        f"**OM 模型**: {'✓' if has_om else '✗'} {OM_MODEL_PATH}",
        f"**PTH 权重**: {'✓' if has_pth else '✗'} {PTH_MODEL_PATH}",
        "",
        "### 场景类别",
    ]

    for s in SCENE_CLASSES:
        lines.append(f"- **{s['cn']}** ({s['en']}): {s['advice']}")

    lines.append("")
    lines.append("### 车道线检测参数")
    from config import (CANNY_LOW, CANNY_HIGH, HOUGH_THRESHOLD,
                        HOUGH_MIN_LINE_LEN, ROI_TOP)
    lines.append(f"- Canny 阈值: {CANNY_LOW} ~ {CANNY_HIGH}")
    lines.append(f"- Hough 阈值: {HOUGH_THRESHOLD}")
    lines.append(f"- 最小线段长度: {HOUGH_MIN_LINE_LEN} px")
    lines.append(f"- ROI 顶部: {ROI_TOP:.0%} 图像高度")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Build UI
# ---------------------------------------------------------------------------

def build_ui():
    theme = gr.themes.Soft()

    with gr.Blocks(theme=theme, title="智能小车视觉感知 - Ascend 310B") as demo:
        gr.Markdown("""
        # 🚗 智能小车视觉感知
        **车道线检测** (经典 CV) + **驾驶场景分类** (ResNet18 on NPU)。
        支持 NPU 加速和 CPU 回退。
        """)

        with gr.Tabs():
            with gr.TabItem("🛣️ 道路感知"):
                with gr.Row():
                    with gr.Column(scale=2):
                        input_image = gr.Image(
                            type="numpy",
                            label="输入道路图像",
                            height=360,
                        )
                        examples = gr.Examples(
                            examples=[],
                            inputs=input_image,
                            label="示例图片 (放入 photos/ 目录后自动显示)",
                        )
                        perceive_btn = gr.Button(
                            "开始感知", variant="primary"
                        )

                    with gr.Column(scale=1):
                        gr.Markdown("### 📊 感知结果")
                        result_md = gr.Markdown(
                            value="_上传道路图像开始感知..._"
                        )

                with gr.Row():
                    output_image = gr.Image(
                        type="numpy",
                        label="车道线检测结果",
                        height=400,
                    )

                perceive_btn.click(
                    perceive,
                    inputs=[input_image],
                    outputs=[output_image, result_md],
                )
                # Also trigger on image upload
                input_image.change(
                    perceive,
                    inputs=[input_image],
                    outputs=[output_image, result_md],
                )

            with gr.TabItem("⚙️ 系统信息"):
                sys_info = gr.Markdown(value=get_system_info())
                refresh_btn = gr.Button("刷新")
                refresh_btn.click(get_system_info, None, sys_info)

    return demo


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Smart Car Perception - Case 6"
    )
    parser.add_argument("--port", type=int, default=7860,
                        help="Server port")
    parser.add_argument("--share", action="store_true",
                        help="Create public link")
    args = parser.parse_args()

    os.makedirs(DATA_DIR, exist_ok=True)

    print("=" * 50)
    print("Case 6 — 智能小车视觉感知")
    print("=" * 50)
    sc = get_classifier()
    backend = "NPU (Ascend 310B)" if sc.use_npu else "CPU (PyTorch)"
    print(f"  Backend: {backend}")
    print(f"  Model: ResNet18 ({NUM_SCENES} scene classes)")
    print(f"  Lane Detection: OpenCV classic CV pipeline")
    print("-" * 50)

    demo = build_ui()
    demo.queue()
    demo.launch(
        server_name="0.0.0.0",
        server_port=args.port,
        share=args.share,
    )


if __name__ == "__main__":
    main()
