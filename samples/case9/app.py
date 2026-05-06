"""
Ascend 310B Smart Chatbot — Gradio web interface.

Launches a chat UI with text + voice I/O, backed by the RAG pipeline
and optional cloud LLM augmentation.
"""

import argparse
import os

import gradio as gr

from ascend_inference import create_embedding_model
from config import (
    CLOUD_LLM_ENABLED,
    CLOUD_LLM_ENDPOINT,
    CLOUD_LLM_KEY,
    CLOUD_LLM_MODEL,
    SAMPLE_KNOWLEDGE_PATH,
    SAMPLE_FAQ_PATH,
    VOICE_ENABLED,
)
from dialogue import DialogueManager
from knowledge_base import KnowledgeBase
from voice_io import SpeechRecognizer, TextToSpeech

# ---------------------------------------------------------------------------
# Lazy-initialized globals (follows case1 pattern)
# ---------------------------------------------------------------------------

_embedding_model = None
_knowledge_base = None
_dialogue = None
_asr = None
_tts = None


def get_embedding_model():
    global _embedding_model
    if _embedding_model is None:
        _embedding_model = create_embedding_model()
    return _embedding_model


def get_knowledge_base():
    global _knowledge_base
    if _knowledge_base is None:
        _knowledge_base = KnowledgeBase(get_embedding_model())
        # Load sample knowledge on first use
        if os.path.exists(SAMPLE_KNOWLEDGE_PATH):
            _knowledge_base.add_document(SAMPLE_KNOWLEDGE_PATH)
            print(f"[KnowledgeBase] Loaded sample knowledge, "
                  f"total chunks: {_knowledge_base.size}")
    return _knowledge_base


def get_dialogue():
    global _dialogue
    if _dialogue is None:
        _dialogue = DialogueManager(
            get_knowledge_base(),
            cloud_enabled=CLOUD_LLM_ENABLED,
            cloud_endpoint=CLOUD_LLM_ENDPOINT,
            cloud_key=CLOUD_LLM_KEY,
            cloud_model=CLOUD_LLM_MODEL,
        )
    return _dialogue


def get_asr():
    global _asr
    if _asr is None:
        _asr = SpeechRecognizer()
    return _asr


def get_tts():
    global _tts
    if _tts is None:
        _tts = TextToSpeech()
    return _tts


# ---------------------------------------------------------------------------
# Gradio event handlers
# ---------------------------------------------------------------------------

def chat_fn(message, history):
    """Handle a text chat turn."""
    if not message or not message.strip():
        return "", history

    dm = get_dialogue()
    result = dm.process_message(message)

    # Build Gradio chat history format: list of (user, bot) tuples
    if history is None:
        history = []
    history.append((message, result["response"]))
    return "", history


def voice_input_fn(audio):
    """Handle voice input: (sample_rate, audio_array) from gradio.Audio."""
    if audio is None:
        return "", None

    sr_val, audio_data = audio
    import numpy as np
    import io
    import wave

    # Write audio_data to a WAV buffer for speech_recognition
    audio_int16 = (audio_data * 32767).astype(np.int16)
    buf = io.BytesIO()

    # Determine channels
    if audio_data.ndim == 1:
        channels = 1
    else:
        channels = audio_data.shape[1] if audio_data.shape[1] <= 2 else 1

    with wave.open(buf, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(2)
        wf.setframerate(sr_val)
        wf.writeframes(audio_int16.tobytes())

    buf.seek(0)

    # Save to temp file for speech_recognition
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        tmp.write(buf.read())
        tmp_path = tmp.name

    asr = get_asr()
    text = asr.recognize_from_file(tmp_path)
    os.unlink(tmp_path)

    if text is None or text.startswith("[错误]"):
        return "", None

    return text, text


def voice_output_fn(text):
    """Speak the bot's latest response."""
    if not text or not VOICE_ENABLED:
        return
    tts = get_tts()
    tts.speak(text)


def get_rag_context(message):
    """Return the RAG context snippets for the latest query (used by settings panel)."""
    if not message:
        return ""
    kb = get_knowledge_base()
    results = kb.search(message, k=3)
    lines = []
    for i, item in enumerate(results, 1):
        lines.append(f"[{i}] score={item['score']:.3f}  {item['text'][:120]}...")
    return "\n".join(lines) if lines else "(no matching knowledge found)"


def update_settings(cloud_enabled, cloud_endpoint, cloud_key, voice_enabled):
    """Apply settings changes at runtime."""
    global _dialogue
    if _dialogue is not None:
        _dialogue._cloud_enabled = cloud_enabled
        _dialogue._cloud_endpoint = cloud_endpoint
        _dialogue._cloud_key = cloud_key
    if not cloud_enabled:
        status = "💻 模板模式 (离线)"
    else:
        status = "☁️ 云端模式 (在线)"
    return status


def load_kb_stats():
    kb = get_knowledge_base()
    npu_status = "✓ NPU" if get_embedding_model().use_npu else "⚠ CPU fallback"
    return (
        f"知识库文档数: {kb.size}\n"
        f"嵌入模型: all-MiniLM-L6-v2 (384维)\n"
        f"推理后端: {npu_status}\n"
        f"云API: {'启用' if CLOUD_LLM_ENABLED else '未启用'}"
    )


# ---------------------------------------------------------------------------
# Build UI
# ---------------------------------------------------------------------------

def build_ui():
    theme = gr.themes.Soft()

    with gr.Blocks(theme=theme, title="Ascend 310B 智能聊天机器人") as demo:
        gr.Markdown("""
        # 🤖 Ascend 310B 智能聊天机器人
        **三层架构**：文本嵌入 (NPU) → 向量检索 (FAISS) → 回复生成 (模板 / 云端LLM)
        """)

        # -- main chat tab --
        with gr.Tabs():
            with gr.TabItem("💬 对话"):
                with gr.Row():
                    with gr.Column(scale=3):
                        chatbot = gr.Chatbot(
                            height=500, bubble_full_width=False,
                            placeholder="等待输入...",
                        )
                        with gr.Row():
                            msg_box = gr.Textbox(
                                label="输入消息", placeholder="输入问题...",
                                scale=4, container=False,
                            )
                            send_btn = gr.Button("发送", variant="primary", scale=1)
                        with gr.Row():
                            clear_btn = gr.Button("清空对话", size="sm")

                    with gr.Column(scale=1):
                        gr.Markdown("### 🎤 语音输入")
                        audio_in = gr.Audio(
                            sources=["microphone"], type="numpy",
                            label="点击录音",
                        )
                        voice_status = gr.Textbox(
                            label="识别结果", placeholder="录音后自动识别...",
                            interactive=False,
                        )

                        gr.Markdown("### 📊 检索上下文")
                        rag_panel = gr.Textbox(
                            label="RAG 检索结果", lines=8,
                            interactive=False, max_lines=12,
                        )

                # Events
                msg_box.submit(
                    chat_fn, [msg_box, chatbot], [msg_box, chatbot]
                ).then(voice_output_fn, chatbot, None)
                send_btn.click(
                    chat_fn, [msg_box, chatbot], [msg_box, chatbot]
                ).then(voice_output_fn, chatbot, None)
                clear_btn.click(lambda: None, None, chatbot, queue=False)

                # After each response, update voice output and RAG panel
                msg_box.submit(
                    lambda m, h: get_rag_context(m),
                    [msg_box, chatbot], rag_panel,
                )
                send_btn.click(
                    lambda m, h: get_rag_context(m),
                    [msg_box, chatbot], rag_panel,
                )

                # Voice input → transcribe → fill text box
                audio_in.stop_recording(
                    voice_input_fn, audio_in, [voice_status, msg_box]
                )

            with gr.TabItem("⚙️ 设置"):
                with gr.Row():
                    with gr.Column():
                        gr.Markdown("### 云端 API 配置")
                        cloud_toggle = gr.Checkbox(
                            label="启用云端 LLM", value=CLOUD_LLM_ENABLED,
                        )
                        cloud_endpoint = gr.Textbox(
                            label="API 端点", value=CLOUD_LLM_ENDPOINT,
                        )
                        cloud_key = gr.Textbox(
                            label="API Key", value=CLOUD_LLM_KEY,
                            type="password",
                        )
                        voice_toggle = gr.Checkbox(
                            label="启用语音输出", value=VOICE_ENABLED,
                        )
                        settings_btn = gr.Button("应用设置", variant="primary")
                        settings_status = gr.Textbox(
                            label="状态", interactive=False,
                        )
                        settings_btn.click(
                            update_settings,
                            [cloud_toggle, cloud_endpoint, cloud_key, voice_toggle],
                            settings_status,
                        )

                    with gr.Column():
                        gr.Markdown("### 系统信息")
                        sys_info = gr.Textbox(
                            label="当前状态", lines=6, interactive=False,
                        )
                        refresh_btn = gr.Button("刷新")
                        refresh_btn.click(load_kb_stats, None, sys_info)

        # Initialize system info on load
        demo.load(load_kb_stats, None, sys_info)

    return demo


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Ascend 310B Smart Chatbot")
    parser.add_argument("--port", type=int, default=7860, help="Server port")
    parser.add_argument("--share", action="store_true", help="Create public link")
    args = parser.parse_args()

    print("=" * 50)
    print("Ascend 310B 智能聊天机器人")
    print("=" * 50)
    # Trigger early init
    get_embedding_model()
    get_knowledge_base()
    print(f"  Embedding model: {'NPU' if get_embedding_model().use_npu else 'CPU'}")
    print(f"  Knowledge base: {get_knowledge_base().size} documents")
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
