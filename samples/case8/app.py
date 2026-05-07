"""
Case 8: Gesture Recognition — Gradio web interface.

Launches a real-time gesture recognition UI using the webcam.
Supports both NPU (Ascend 310B) and CPU (PyTorch) backends.
"""

import argparse
import json
import os
import time
from collections import deque

import gradio as gr
import numpy as np

from ascend_inference import GestureClassifier
from config import (
    CONFIDENCE_THRESHOLD,
    GESTURE_CLASSES,
    IMAGE_SIZE,
    NUM_CLASSES,
    OM_MODEL_PATH,
    PTH_MODEL_PATH,
)

# ---------------------------------------------------------------------------
# Lazy-initialized globals
# ---------------------------------------------------------------------------

_classifier = None
_prediction_history = deque(maxlen=20)  # recent predictions


def get_classifier():
    global _classifier
    if _classifier is None:
        _classifier = GestureClassifier()
    return _classifier


# ---------------------------------------------------------------------------
# Gradio event handlers
# ---------------------------------------------------------------------------

def predict_frame(image):
    """Process a single webcam frame, return annotated image + results text."""
    if image is None:
        return _empty_result()

    clf = get_classifier()
    t0 = time.time()
    results = clf.predict(image, top_k=3)
    elapsed_ms = (time.time() - t0) * 1000

    # Build display text
    if not results:
        output = _format_no_detection(elapsed_ms)
    else:
        output = _format_results(results, elapsed_ms)
        top = results[0]
        _prediction_history.append({
            "label_cn": top["label_cn"],
            "confidence": top["confidence"],
        })

    # Build history display
    history_text = _format_history()

    return output, history_text


def _empty_result():
    return "等待摄像头输入...", ""


def _format_no_detection(elapsed_ms):
    lines = [
        "🔍 未检测到手势",
        f"⏱ 推理耗时: {elapsed_ms:.1f} ms",
        "",
        "请将手放在摄像头前，做出清晰的手势。",
    ]
    return "\n".join(lines)


def _format_results(results, elapsed_ms):
    emoji_map = {c["en"]: c.get("emoji", "") for c in GESTURE_CLASSES}
    # Build a lookup from config
    gesture_map = {g["en"]: g for g in GESTURE_CLASSES}

    lines = []
    for i, r in enumerate(results):
        ginfo = gesture_map.get(r["label_en"], {})
        emoji = ginfo.get("emoji", "")
        bar = _confidence_bar(r["confidence"])

        if i == 0 and r["confidence"] >= CONFIDENCE_THRESHOLD:
            lines.append(f"## {emoji} {r['label_cn']} ({r['label_en']})")
            lines.append(f"置信度: {r['confidence']:.1%} {bar}")
        elif i == 0:
            lines.append(f"### {r['label_cn']} ({r['label_en']}) (低于阈值)")
            lines.append(f"置信度: {r['confidence']:.1%} {bar}")
        else:
            lines.append(f"- {r['label_cn']} ({r['label_en']}): "
                         f"{r['confidence']:.1%}")

    lines.append("")
    lines.append(f"⏱ 推理耗时: {elapsed_ms:.1f} ms")
    backend = "NPU" if get_classifier().use_npu else "CPU"
    lines.append(f"🖥 推理后端: {backend}")

    return "\n".join(lines)


def _confidence_bar(value, width=20):
    filled = int(value * width)
    return f"`{'█' * filled}{'░' * (width - filled)}`"


def _format_history():
    if not _prediction_history:
        return "(暂无识别记录)"
    lines = []
    for i, p in enumerate(reversed(list(_prediction_history)[-10:]), 1):
        lines.append(f"{i}. {p['label_cn']} ({p['confidence']:.1%})")
    return "\n".join(lines)


def load_model_info():
    """Gather model and backend information."""
    clf = get_classifier()

    has_om = os.path.exists(OM_MODEL_PATH)
    has_pth = os.path.exists(PTH_MODEL_PATH)
    backend = "NPU (Ascend 310B)" if clf.use_npu else "CPU (PyTorch)"

    lines = [
        f"推理后端: {backend}",
        f"模型: MobileNetV3-Small",
        f"输入尺寸: {IMAGE_SIZE}×{IMAGE_SIZE}",
        f"手势类别: {NUM_CLASSES} 种",
        f"置信度阈值: {CONFIDENCE_THRESHOLD:.0%}",
        f"OM 模型: {'✓' if has_om else '✗'} {OM_MODEL_PATH}",
        f"PTH 权重: {'✓' if has_pth else '✗'} {PTH_MODEL_PATH}",
        "",
        "### 手势列表",
    ]
    for g in GESTURE_CLASSES:
        emoji = g.get("emoji", "")
        lines.append(f"- {emoji} {g['cn']} ({g['en']})")

    return "\n".join(lines)


def update_threshold(new_threshold):
    """Update confidence threshold at runtime."""
    from config import CONFIDENCE_THRESHOLD as _orig
    import config
    config.CONFIDENCE_THRESHOLD = new_threshold
    return f"阈值已更新: {new_threshold:.0%} (原值: {_orig:.0%})"


# ---------------------------------------------------------------------------
# Build UI
# ---------------------------------------------------------------------------

def build_ui():
    theme = gr.themes.Soft()

    with gr.Blocks(theme=theme, title="手势识别 - Ascend 310B") as demo:
        gr.Markdown("""
        # ✋ 手势识别系统
        **MobileNetV3-Small** 实时手势分类，支持 NPU (昇腾 310B) 和 CPU 双后端推理。
        """)

        with gr.Tabs():
            # -- Tab 1: Recognition --
            with gr.TabItem("📷 手势识别"):
                with gr.Row():
                    with gr.Column(scale=2):
                        camera_input = gr.Image(
                            source="webcam",
                            type="numpy",
                            label="摄像头",
                            streaming=True,
                            mirror_webcam=True,
                        )
                        stream_btn = gr.Button("开始实时识别", variant="primary")

                    with gr.Column(scale=1):
                        gr.Markdown("### 📊 识别结果")
                        result_display = gr.Markdown(
                            value="等待摄像头输入...",
                            elem_id="result-display",
                        )

                        gr.Markdown("### 📜 识别历史")
                        history_display = gr.Textbox(
                            label="最近识别",
                            lines=8,
                            interactive=False,
                            max_lines=12,
                        )

                # Streaming: process every frame from webcam
                stream_btn.click(
                    fn=lambda: None, inputs=None, outputs=None
                )
                camera_input.stream(
                    predict_frame,
                    inputs=[camera_input],
                    outputs=[result_display, history_display],
                    time_limit=0.3,   # max 0.3s per frame
                    stream_every=0.5,  # process every 0.5s
                )

            # -- Tab 2: Settings --
            with gr.TabItem("⚙️ 设置"):
                with gr.Row():
                    with gr.Column():
                        gr.Markdown("### 系统信息")
                        sys_info = gr.Markdown(value=load_model_info())
                        refresh_btn = gr.Button("刷新")
                        refresh_btn.click(load_model_info, None, sys_info)

                    with gr.Column():
                        gr.Markdown("### 推理设置")
                        threshold_slider = gr.Slider(
                            minimum=0.3,
                            maximum=0.95,
                            value=CONFIDENCE_THRESHOLD,
                            step=0.05,
                            label="置信度阈值",
                            info="低于此值的预测结果将被忽略",
                        )
                        threshold_status = gr.Textbox(
                            label="状态", interactive=False)
                        threshold_slider.change(
                            update_threshold,
                            threshold_slider,
                            threshold_status,
                        )

        # Initialize
        demo.load(load_model_info, None, sys_info)

    return demo


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Gesture Recognition")
    parser.add_argument("--port", type=int, default=7860, help="Server port")
    parser.add_argument("--share", action="store_true", help="Create public link")
    args = parser.parse_args()

    print("=" * 50)
    print("Case 8 — 手势识别系统")
    print("=" * 50)
    # Trigger early init
    clf = get_classifier()
    backend = "NPU (Ascend 310B)" if clf.use_npu else "CPU (PyTorch)"
    print(f"  Backend: {backend}")
    print(f"  Model: MobileNetV3-Small ({NUM_CLASSES} classes)")
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
