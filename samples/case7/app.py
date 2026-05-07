"""
Case 7: Smart Album — Gradio web interface.

Three tabs:
  1. Photo browser — gallery grid with face-count filter
  2. Similar search — upload a photo, find visually similar ones
  3. Management — index a folder of photos, view stats
"""

import argparse
import os

import cv2
import gradio as gr
import numpy as np

from config import (
    DATA_DIR,
    FAISS_INDEX_PATH,
    METADATA_PATH,
    PHOTO_DIR,
    TOP_K_RESULTS,
)
from feature_extractor import FeatureExtractor
from photo_index import PhotoIndex

# ---------------------------------------------------------------------------
# Lazy-initialized globals (case9 pattern)
# ---------------------------------------------------------------------------

_extractor = None
_photo_index = None


def get_extractor():
    global _extractor
    if _extractor is None:
        _extractor = FeatureExtractor()
    return _extractor


def get_photo_index():
    global _photo_index
    if _photo_index is None:
        _photo_index = PhotoIndex(get_extractor())
        if os.path.exists(FAISS_INDEX_PATH):
            _photo_index.load()
            print(f"[App] Loaded existing index: {_photo_index.size} photos")
    return _photo_index


# ---------------------------------------------------------------------------
# Tab 1: Photo Browser
# ---------------------------------------------------------------------------

def browse_photos(filter_mode):
    """Return gallery list for gr.Gallery: [(path, caption), ...]."""
    pi = get_photo_index()

    if filter_mode == "has_people":
        photos = pi.get_photos_by_face_count(min_faces=1)
    elif filter_mode == "no_people":
        photos = pi.get_photos_by_face_count(min_faces=0, max_faces=0)
    else:
        photos = pi.get_all_photos()

    gallery = []
    for m in photos:
        label = f"{m['filename']} | {m.get('face_count', 0)}人"
        gallery.append((m["filepath"], label))
    return gallery


def update_gallery(filter_mode):
    return browse_photos(filter_mode)


# ---------------------------------------------------------------------------
# Tab 2: Similar Search
# ---------------------------------------------------------------------------

def search_similar(query_image):
    """Upload an image, return top-k similar results + markdown report."""
    if query_image is None:
        return [], "_请上传一张照片进行搜索_"

    pi = get_photo_index()

    if pi.size == 0:
        return [], "**索引为空** — 请先在「管理」页签中索引照片文件夹。"

    results = pi.search(query_image, k=TOP_K_RESULTS)

    gallery = []
    lines = ["## 🔍 搜索结果", ""]
    for i, r in enumerate(results):
        pct = r["score"] * 100
        bar = _score_bar(r["score"])
        lines.append(
            f"{i+1}. **{r['filename']}**  "
            f"相似度 {pct:.1f}% {bar}  "
            f"({r.get('face_count', 0)}人)"
        )
        gallery.append((r["filepath"], f"#{i+1} {r['filename']}"))

    return gallery, "\n".join(lines)


def _score_bar(value, width=16):
    filled = int(value * width)
    return f"`{'█' * filled}{'░' * (width - filled)}`"


# ---------------------------------------------------------------------------
# Tab 3: Management
# ---------------------------------------------------------------------------

def index_folder(folder_path, progress=gr.Progress()):
    """Index all photos in a folder."""
    if not folder_path or not os.path.isdir(folder_path):
        return ("**错误**: 请提供有效的文件夹路径", [])

    pi = get_photo_index()

    # Progress callback
    def on_progress(cur, total):
        progress((cur, total), desc=f"正在索引... {cur}/{total}")

    indexed, skipped, elapsed = pi.index_photos(folder_path, on_progress)
    pi.save()

    # Build report
    st = pi.stats()
    lines = [
        "## 索引完成",
        "",
        f"- **已索引**: {indexed} 张",
        f"- **跳过**: {skipped} 张",
        f"- **耗时**: {elapsed:.1f} 秒",
        f"- **索引总大小**: {st['total_photos']} 张",
        f"- **含人脸照片**: {st['photos_with_faces']} 张",
        f"- **特征维度**: {st['feature_dim']}",
    ]

    # Preview first 20
    all_photos = pi.get_all_photos()[:20]
    gallery = []
    for m in all_photos:
        label = f"{m['filename']} | {m.get('face_count', 0)}人"
        gallery.append((m["filepath"], label))

    return "\n".join(lines), gallery


def clear_index():
    """Remove FAISS index and metadata."""
    pi = get_photo_index()
    # Re-init
    pi._metadata = []
    pi._init_index()
    if os.path.exists(FAISS_INDEX_PATH):
        os.remove(FAISS_INDEX_PATH)
    if os.path.exists(METADATA_PATH):
        os.remove(METADATA_PATH)
    return "索引已清除，请重新索引照片文件夹。"


def get_system_info():
    """Return system / model / index status."""
    fe = get_extractor()
    pi = get_photo_index()
    backend = "NPU (Ascend 310B)" if fe.use_npu else "CPU (PyTorch)"
    st = pi.stats()

    lines = [
        f"**推理后端**: {backend}",
        f"**模型**: ResNet50 Feature Extractor (2048-dim)",
        f"**已索引照片**: {st['total_photos']} 张",
        f"**含人脸照片**: {st['photos_with_faces']} 张",
        f"**FAISS 索引**: {'✓' if os.path.exists(FAISS_INDEX_PATH) else '✗'}",
        f"**元数据**: {'✓' if os.path.exists(METADATA_PATH) else '✗'}",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Build UI
# ---------------------------------------------------------------------------

def build_ui():
    theme = gr.themes.Soft()

    with gr.Blocks(theme=theme, title="智能相册 - Ascend 310B") as demo:
        gr.Markdown("""
        # 🖼️ 智能相册
        **ResNet50 特征提取** + **FAISS 向量检索**，在昇腾 310B 上运行。
        支持 NPU 加速和 CPU 回退。
        """)

        with gr.Tabs():
            # -- Tab 1: Browser --
            with gr.TabItem("📷 照片浏览"):
                with gr.Row():
                    with gr.Column(scale=1):
                        filter_radio = gr.Radio(
                            choices=[
                                ("全部照片", "all"),
                                ("有人脸", "has_people"),
                                ("无人脸", "no_people"),
                            ],
                            value="all",
                            label="筛选条件",
                            interactive=True,
                        )
                        refresh_btn = gr.Button("刷新", variant="secondary")

                    with gr.Column(scale=4):
                        browser_gallery = gr.Gallery(
                            label="照片库",
                            columns=4,
                            rows=3,
                            height="auto",
                            object_fit="contain",
                        )

                refresh_btn.click(
                    update_gallery,
                    inputs=[filter_radio],
                    outputs=[browser_gallery],
                )
                filter_radio.change(
                    update_gallery,
                    inputs=[filter_radio],
                    outputs=[browser_gallery],
                )

            # -- Tab 2: Search --
            with gr.TabItem("🔍 相似搜索"):
                with gr.Row():
                    with gr.Column(scale=1):
                        query_image = gr.Image(
                            type="numpy",
                            label="上传查询照片",
                            height=300,
                        )
                        search_btn = gr.Button(
                            "搜索相似照片", variant="primary"
                        )

                    with gr.Column(scale=2):
                        search_results = gr.Gallery(
                            label="相似照片 (Top 12)",
                            columns=4,
                            rows=3,
                            height="auto",
                            object_fit="contain",
                        )
                        search_report = gr.Markdown(
                            value="_请上传一张照片进行搜索_"
                        )

                search_btn.click(
                    search_similar,
                    inputs=[query_image],
                    outputs=[search_results, search_report],
                )

            # -- Tab 3: Management --
            with gr.TabItem("⚙️ 管理"):
                with gr.Row():
                    with gr.Column(scale=1):
                        gr.Markdown("### 索引照片文件夹")
                        folder_input = gr.Textbox(
                            label="照片目录路径",
                            placeholder=f"例如: {PHOTO_DIR}",
                            value=PHOTO_DIR,
                        )
                        index_btn = gr.Button(
                            "开始索引", variant="primary"
                        )
                        clear_btn = gr.Button(
                            "清除索引", variant="stop"
                        )
                        index_status = gr.Markdown(
                            value="_请指定照片目录并开始索引_"
                        )

                    with gr.Column(scale=1):
                        gr.Markdown("### 系统信息")
                        sys_info = gr.Markdown(value=get_system_info())
                        refresh_sys_btn = gr.Button("刷新")
                        refresh_sys_btn.click(
                            get_system_info, None, sys_info
                        )

                index_preview = gr.Gallery(
                    label="索引预览 (前 20 张)",
                    columns=5,
                    rows=4,
                    height="auto",
                    object_fit="contain",
                )

                index_btn.click(
                    index_folder,
                    inputs=[folder_input],
                    outputs=[index_status, index_preview],
                )
                clear_btn.click(
                    clear_index, None, index_status
                )

        # Initialize
        demo.load(
            lambda: browse_photos("all"), None, browser_gallery
        )

    return demo


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Smart Album - Case 7")
    parser.add_argument("--port", type=int, default=7860, help="Server port")
    parser.add_argument("--share", action="store_true",
                        help="Create public link")
    args = parser.parse_args()

    os.makedirs(DATA_DIR, exist_ok=True)

    print("=" * 50)
    print("Case 7 — 智能相册")
    print("=" * 50)
    fe = get_extractor()
    backend = "NPU (Ascend 310B)" if fe.use_npu else "CPU (PyTorch)"
    print(f"  Backend: {backend}")
    print(f"  Model: ResNet50 Feature Extractor (2048-dim)")
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
