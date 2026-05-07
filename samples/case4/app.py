"""
Case 4: Smart Palmprint Recognition — Gradio web interface.

Three tabs:
  1. Enroll — capture palm samples and register a user
  2. Verify — capture a palm and verify identity
  3. Management — list / remove users, system info
"""

import argparse
import os
import time
import uuid

import cv2
import gradio as gr
import numpy as np

from config import (
    DATA_DIR,
    FAISS_INDEX_PATH,
    FEATURE_DIM,
    METADATA_PATH,
    OM_MODEL_PATH,
    PTH_MODEL_PATH,
    TOP_K_RESULTS,
    VERIFICATION_THRESHOLD,
)
from palm_extractor import PalmExtractor
from palm_index import PalmIndex

# ---------------------------------------------------------------------------
# Lazy-initialised globals
# ---------------------------------------------------------------------------

_extractor = None
_palm_index = None


def get_extractor():
    global _extractor
    if _extractor is None:
        _extractor = PalmExtractor()
    return _extractor


def get_palm_index():
    global _palm_index
    if _palm_index is None:
        _palm_index = PalmIndex(get_extractor())
        if os.path.exists(FAISS_INDEX_PATH):
            _palm_index.load()
            st = _palm_index.stats()
            print(f"[App] Loaded index: {st['total_users']} users, "
                  f"{st['total_embeddings']} embeddings")
    return _palm_index


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _confidence_bar(value, width=16):
    filled = int(value * width)
    return f"`{'█' * filled}{'░' * (width - filled)}`"


def _resize_for_display(image_bgr, max_size=400):
    """Resize BGR image for Gradio display while keeping aspect ratio."""
    h, w = image_bgr.shape[:2]
    scale = max_size / max(h, w)
    if scale < 1.0:
        new_w, new_h = int(w * scale), int(h * scale)
        image_bgr = cv2.resize(image_bgr, (new_w, new_h))
    return cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)


# ---------------------------------------------------------------------------
# Tab 1: Enrollment
# ---------------------------------------------------------------------------

# Per-session enrollment buffer (holds captured samples until confirmed)
_enroll_buffer = {}  # session_id → {"samples": [(bgr, display_rgb), ...], "user_name": str}


def capture_enroll_sample(session_id, user_name, camera_frame):
    """Capture one palm sample and add to the enrollment buffer."""
    if camera_frame is None:
        return None, _enroll_status(session_id)

    if not session_id or session_id not in _enroll_buffer:
        session_id = uuid.uuid4().hex[:8]
        _enroll_buffer[session_id] = {"samples": [], "user_name": user_name}

    buf = _enroll_buffer[session_id]
    buf["user_name"] = user_name

    fe = get_extractor()
    vec = fe.extract(camera_frame)
    if vec is None:
        return (camera_frame,
                f"### ⚠️ 未检测到有效掌纹\n请将手掌放在摄像头前，"
                f"确保光线均匀，背景简洁。\n{_enroll_status(session_id)}")

    display_rgb = _resize_for_display(camera_frame)
    buf["samples"].append((camera_frame.copy(), display_rgb))

    return camera_frame, _enroll_status(session_id)


def _enroll_status(session_id):
    """Build markdown status for current enrollment buffer."""
    if not session_id or session_id not in _enroll_buffer:
        return "### 就绪\n点击「采集掌纹」开始注册。"

    buf = _enroll_buffer[session_id]
    n = len(buf["samples"])
    user_name = buf["user_name"] or "(未填写)"
    target = 3

    lines = [
        f"### 📝 注册中: {user_name}",
        f"已采集: **{n}** / {target} 张",
    ]
    if n >= target:
        lines.append("✅ 样本充足，点击「完成注册」保存。")
    else:
        lines.append(f"⚠️ 还需 {target - n} 张，请调整手掌角度再采集。")

    # Show thumbnails
    if buf["samples"]:
        lines.append("")
        lines.append("**已采集样本预览**:")
    return "\n".join(lines)


def get_enroll_gallery(session_id):
    """Return list of display images for the enrollment gallery."""
    if not session_id or session_id not in _enroll_buffer:
        return []
    return [rgb for _, rgb in _enroll_buffer[session_id]["samples"]]


def confirm_enrollment(session_id):
    """Finalise enrollment: add samples to FAISS index and save."""
    if not session_id or session_id not in _enroll_buffer:
        return "### ❌ 没有待保存的样本。", []

    buf = _enroll_buffer[session_id]
    samples = [bgr for bgr, _ in buf["samples"]]
    user_name = buf["user_name"] or f"用户{session_id}"

    if not samples:
        return "### ❌ 没有样本可保存。", []

    pi = get_palm_index()
    ok = pi.enroll_multiple(samples, session_id, user_name)
    pi.save()

    # Clean up buffer
    del _enroll_buffer[session_id]
    gallery = [rgb for _, rgb in buf["samples"]]

    st = pi.stats()
    msg = (
        f"### ✅ 注册成功！\n"
        f"- 用户: **{user_name}** ({session_id})\n"
        f"- 成功录入: **{ok}** / {len(samples)} 个样本\n"
        f"- 系统总用户数: **{st['total_users']}**\n"
        f"- 总嵌入向量: **{st['total_embeddings']}**"
    )
    return msg, gallery


def reset_enrollment():
    """Reset session and start fresh."""
    global _enroll_buffer
    _enroll_buffer = {}
    return "", "### 就绪\n请输入姓名并开始采集。", []


# ---------------------------------------------------------------------------
# Tab 2: Verification
# ---------------------------------------------------------------------------

def verify_palmprint(camera_frame):
    """Capture → extract → search FAISS → return result."""
    if camera_frame is None:
        return None, "### 📷 请使用摄像头采集掌纹"

    pi = get_palm_index()
    if pi.stats()["total_embeddings"] == 0:
        return (_resize_for_display(camera_frame),
                "### ⚠️ 系统无注册用户\n请先在「注册掌纹」页签注册。")

    t0 = time.time()
    result = pi.verify(camera_frame, k=TOP_K_RESULTS)
    elapsed = (time.time() - t0) * 1000

    display = _resize_for_display(camera_frame)

    if not result["top_matches"]:
        return display, "### ⚠️ 未检测到有效掌纹\n请重新放置手掌。"

    lines = []
    if result["verified"]:
        bar = _confidence_bar(result["score"])
        lines.append("## ✅ 验证通过")
        lines.append(f"**用户**: {result['user_name']} ({result['user_id']})")
        lines.append(f"**相似度**: {result['score']:.1%} {bar}")
    else:
        bar = _confidence_bar(result["score"])
        lines.append("## ❌ 验证失败")
        if result["below_threshold"]:
            lines.append(f"最佳匹配 **{result['user_name']}** "
                         f"相似度 {result['score']:.1%} {bar} 低于阈值 "
                         f"({VERIFICATION_THRESHOLD:.0%})")
        else:
            lines.append("未找到匹配用户。")

    lines.append("")
    lines.append(f"⏱ 耗时: {elapsed:.1f} ms")
    lines.append("")
    lines.append("### 📊 Top-5 匹配")
    for i, m in enumerate(result["top_matches"][:5]):
        marker = "→" if i == 0 else "  "
        bar = _confidence_bar(m["score"])
        lines.append(f"- {marker} **{m['user_name']}** "
                     f"相似度 {m['score']:.1%} {bar}")

    fe = get_extractor()
    backend = "NPU (Ascend 310B)" if fe.use_npu else "CPU (PyTorch)"
    lines.append("")
    lines.append(f"🖥 后端: {backend}  |  特征维度: {FEATURE_DIM}")

    return display, "\n".join(lines)


# ---------------------------------------------------------------------------
# Tab 3: Management
# ---------------------------------------------------------------------------

def get_user_list():
    """Return enrolled users as a DataFrame."""
    pi = get_palm_index()
    users = pi.get_users()
    if not users:
        return [["(无)", "(无)", 0]]
    return [[u["user_id"], u["user_name"], u["num_samples"]] for u in users]


def remove_user(user_id):
    """Remove a user from the index."""
    if not user_id or user_id == "(无)":
        return get_user_list(), "### ❌ 请提供有效的用户 ID"

    pi = get_palm_index()
    ok = pi.remove_user(user_id)
    pi.save()

    if ok:
        msg = f"### ✅ 已删除用户: {user_id}"
    else:
        msg = f"### ⚠️ 未找到用户: {user_id}"
    return get_user_list(), msg


def get_system_info():
    """Return backend / model / index status."""
    fe = get_extractor()
    pi = get_palm_index()
    backend = "NPU (Ascend 310B)" if fe.use_npu else "CPU (PyTorch)"
    st = pi.stats()

    has_om = os.path.exists(OM_MODEL_PATH)
    has_pth = os.path.exists(PTH_MODEL_PATH)

    lines = [
        f"**推理后端**: {backend}",
        f"**模型**: GhostNet 1.0x Feature Extractor ({FEATURE_DIM}-dim)",
        f"**OM 模型**: {'✓' if has_om else '✗'} {OM_MODEL_PATH}",
        f"**PTH 权重**: {'✓' if has_pth else '✗'} {PTH_MODEL_PATH}",
        "",
        f"**注册用户数**: {st['total_users']}",
        f"**总嵌入向量**: {st['total_embeddings']}",
        f"**特征维度**: {st['feature_dim']}",
        f"**验证阈值**: {VERIFICATION_THRESHOLD:.0%} (余弦相似度)",
        "",
        f"**FAISS 索引**: {'✓' if os.path.exists(FAISS_INDEX_PATH) else '✗'}",
        f"**元数据**: {'✓' if os.path.exists(METADATA_PATH) else '✗'}",
    ]
    return "\n".join(lines)


def update_threshold(new_threshold):
    """Update the verification threshold at runtime."""
    import config
    config.VERIFICATION_THRESHOLD = float(new_threshold)
    return f"### ✅ 阈值已更新为: {new_threshold}"


# ---------------------------------------------------------------------------
# Build UI
# ---------------------------------------------------------------------------

def build_ui():
    theme = gr.themes.Soft()

    with gr.Blocks(theme=theme, title="掌纹识别机 - Ascend 310B") as demo:
        gr.Markdown("""
        # 🖐️ 智能掌纹识别机
        **GhostNet 1.0x** 特征提取 + **FAISS** 向量检索，在昇腾 310B 上运行。
        支持 NPU 加速和 CPU 回退。适用于门禁、考勤、身份验证等场景。
        """)

        with gr.Tabs():
            # ========================================================
            # Tab 1: Enrollment
            # ========================================================
            with gr.TabItem("📝 注册掌纹"):
                enroll_session = gr.Textbox(
                    value=uuid.uuid4().hex[:8],
                    label="会话 ID (自动生成)",
                    visible=False,
                )

                with gr.Row():
                    with gr.Column(scale=2):
                        enroll_cam = gr.Image(
                            type="numpy",
                            label="摄像头",
                            source="webcam",
                            streaming=True,
                            mirror_webcam=True,
                        )
                    with gr.Column(scale=1):
                        user_name_input = gr.Textbox(
                            label="用户姓名",
                            placeholder="请输入姓名",
                            value="张三",
                        )
                        capture_btn = gr.Button(
                            "📷 采集掌纹", variant="primary"
                        )
                        confirm_btn = gr.Button(
                            "✅ 完成注册 (≥3 张)", variant="stop"
                        )
                        reset_btn = gr.Button(
                            "🔄 重新采集", variant="secondary"
                        )

                        enroll_status = gr.Markdown(
                            value="### 就绪\n请输入姓名并点击采集。"
                        )

                enroll_gallery = gr.Gallery(
                    label="已采集样本",
                    columns=3,
                    rows=1,
                    height="auto",
                    object_fit="contain",
                )

                # Events
                reset_btn.click(
                    reset_enrollment,
                    outputs=[user_name_input, enroll_status, enroll_gallery],
                )

                capture_btn.click(
                    capture_enroll_sample,
                    inputs=[enroll_session, user_name_input, enroll_cam],
                    outputs=[enroll_cam, enroll_status],
                ).then(
                    lambda sid: get_enroll_gallery(sid),
                    inputs=[enroll_session],
                    outputs=[enroll_gallery],
                )

                confirm_btn.click(
                    confirm_enrollment,
                    inputs=[enroll_session],
                    outputs=[enroll_status, enroll_gallery],
                )

            # ========================================================
            # Tab 2: Verification
            # ========================================================
            with gr.TabItem("🔍 身份验证"):
                with gr.Row():
                    with gr.Column(scale=1):
                        verify_cam = gr.Image(
                            type="numpy",
                            label="摄像头",
                            source="webcam",
                            streaming=True,
                            mirror_webcam=True,
                        )
                        verify_btn = gr.Button(
                            "🔍 开始验证", variant="primary"
                        )

                    with gr.Column(scale=1):
                        verify_display = gr.Image(
                            type="numpy",
                            label="掌纹预览",
                        )
                        verify_result = gr.Markdown(
                            value="### 📷 请使用摄像头采集掌纹"
                        )

                verify_btn.click(
                    verify_palmprint,
                    inputs=[verify_cam],
                    outputs=[verify_display, verify_result],
                )

            # ========================================================
            # Tab 3: Management
            # ========================================================
            with gr.TabItem("⚙️ 系统管理"):
                with gr.Row():
                    with gr.Column(scale=1):
                        gr.Markdown("### 注册用户")
                        user_table = gr.DataFrame(
                            headers=["用户 ID", "姓名", "样本数"],
                            value=get_user_list(),
                            label="已注册用户",
                            interactive=False,
                        )
                        with gr.Row():
                            remove_input = gr.Textbox(
                                label="要删除的用户 ID",
                                placeholder="输入用户 ID...",
                            )
                            remove_btn = gr.Button(
                                "删除用户", variant="stop"
                            )
                        remove_status = gr.Markdown()

                    with gr.Column(scale=1):
                        gr.Markdown("### 系统信息")
                        sys_info = gr.Markdown(value=get_system_info())
                        refresh_sys_btn = gr.Button("刷新")

                        gr.Markdown("### 验证阈值")
                        threshold_slider = gr.Slider(
                            minimum=0.50, maximum=0.95, step=0.01,
                            value=VERIFICATION_THRESHOLD,
                            label="余弦相似度阈值",
                        )
                        threshold_btn = gr.Button("应用")
                        threshold_status = gr.Markdown()

                remove_btn.click(
                    remove_user,
                    inputs=[remove_input],
                    outputs=[user_table, remove_status],
                )
                refresh_sys_btn.click(
                    get_system_info,
                    outputs=[sys_info],
                )
                threshold_btn.click(
                    update_threshold,
                    inputs=[threshold_slider],
                    outputs=[threshold_status],
                )

        # Load system info on open
        demo.load(get_system_info, outputs=[sys_info])

    return demo


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Smart Palmprint Recognition - Case 4"
    )
    parser.add_argument("--port", type=int, default=7860,
                        help="Server port")
    parser.add_argument("--share", action="store_true",
                        help="Create public link")
    args = parser.parse_args()

    os.makedirs(DATA_DIR, exist_ok=True)

    print("=" * 50)
    print("Case 4 — 智能掌纹识别机")
    print("=" * 50)

    fe = get_extractor()
    backend = "NPU (Ascend 310B)" if fe.use_npu else "CPU (PyTorch)"
    print(f"  Backend: {backend}")
    print(f"  Model: GhostNet 1.0x Feature Extractor ({FEATURE_DIM}-dim)")

    pi = get_palm_index()
    st = pi.stats()
    print(f"  Users: {st['total_users']}")
    print(f"  Embeddings: {st['total_embeddings']}")
    print(f"  Threshold: {VERIFICATION_THRESHOLD:.0%}")
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
