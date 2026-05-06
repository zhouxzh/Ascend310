"""
Dialogue manager with RAG-powered response generation.

Supports two response modes:
  - Template + FAQ: fast, offline, no external dependencies
  - Cloud LLM: higher quality, requires API access
"""

import json
import os
import re
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from enum import Enum

import requests

from config import (
    CLOUD_LLM_ENDPOINT,
    CLOUD_LLM_KEY,
    CLOUD_LLM_MODEL,
    MAX_HISTORY_TURNS,
    SAMPLE_FAQ_PATH,
    SIMILARITY_THRESHOLD,
    TOP_K_RETRIEVAL,
)


class State(Enum):
    GREETING = "greeting"
    ACTIVE = "active"
    CLARIFYING = "clarifying"
    FAREWELL = "farewell"


@dataclass
class Turn:
    role: str  # "user" | "assistant"
    text: str
    timestamp: float = field(default_factory=time.time)


class ConversationHistory:
    """Ring buffer of recent conversation turns."""

    def __init__(self, max_turns=MAX_HISTORY_TURNS):
        self._turns: list[Turn] = []
        self._max = max_turns

    def add(self, role, text):
        self._turns.append(Turn(role=role, text=text))
        if len(self._turns) > self._max:
            self._turns.pop(0)

    def get_recent(self, n=None):
        n = n or self._max
        return list(self._turns[-n:])

    def clear(self):
        self._turns.clear()

    def to_dict_list(self):
        return [{"role": t.role, "text": t.text, "timestamp": t.timestamp}
                for t in self._turns]


class DialogueManager:
    """Orchestrates conversation flow: intent detection, RAG retrieval,
    and response generation."""

    def __init__(self, knowledge_base, cloud_enabled=False,
                 cloud_endpoint=None, cloud_key=None, cloud_model=None):
        self._kb = knowledge_base
        self._cloud_enabled = cloud_enabled
        self._cloud_endpoint = cloud_endpoint or CLOUD_LLM_ENDPOINT
        self._cloud_key = cloud_key or CLOUD_LLM_KEY
        self._cloud_model = cloud_model or CLOUD_LLM_MODEL
        self._history: dict[str, ConversationHistory] = OrderedDict()
        self._faq = self._load_faq()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def process_message(self, user_input, session_id="default"):
        """Main entry point. Returns {"response": ..., "context": [...]}."""
        user_input = user_input.strip()
        history = self._get_history(session_id)

        # 1. Detect farewell
        if self._is_farewell(user_input):
            history.add("user", user_input)
            response = self._farewell_response()
            history.add("assistant", response)
            return {"response": response, "context": [], "state": State.FAREWELL.value}

        # 2. Detect greeting (only at start or after long silence)
        if len(history.get_recent()) == 0 and self._is_greeting(user_input):
            history.add("user", user_input)
            response = self._greeting_response()
            history.add("assistant", response)
            return {"response": response, "context": [], "state": State.GREETING.value}

        # 3. Try FAQ exact match
        faq_resp = self._match_faq(user_input)
        if faq_resp:
            history.add("user", user_input)
            history.add("assistant", faq_resp)
            return {"response": faq_resp, "context": [], "state": State.ACTIVE.value}

        # 4. RAG retrieval
        retrieved = self._kb.search(user_input, k=TOP_K_RETRIEVAL)
        relevant = [r for r in retrieved if r["score"] > SIMILARITY_THRESHOLD]

        # 5. Generate response
        if self._cloud_enabled and self._cloud_key:
            response = self._generate_cloud(user_input, relevant, history)
        else:
            response = self._generate_template(user_input, relevant)

        history.add("user", user_input)
        history.add("assistant", response)
        return {
            "response": response,
            "context": relevant,
            "state": State.ACTIVE.value,
        }

    def clear_session(self, session_id="default"):
        self._history.pop(session_id, None)

    def get_history(self, session_id="default"):
        h = self._get_history(session_id)
        return h.to_dict_list()

    # ------------------------------------------------------------------
    # Intent detection
    # ------------------------------------------------------------------

    def _is_greeting(self, text):
        patterns = [r"^(你好|嗨|hello|hi|嗨喽|在吗)", r"^(早上好|下午好|晚上好)"]
        return any(re.search(p, text, re.IGNORECASE) for p in patterns)

    def _is_farewell(self, text):
        patterns = [r"(再见|拜拜|bye|回头见|下次见|晚安)"]
        return any(re.search(p, text, re.IGNORECASE) for p in patterns)

    # ------------------------------------------------------------------
    # FAQ
    # ------------------------------------------------------------------

    def _load_faq(self):
        if not os.path.exists(SAMPLE_FAQ_PATH):
            return []
        with open(SAMPLE_FAQ_PATH, encoding="utf-8") as f:
            data = json.load(f)
        return data.get("faq", [])

    def _match_faq(self, text):
        for item in self._faq:
            for kw in item.get("keywords", []):
                if kw.lower() in text.lower():
                    return item["response"]
        return None

    # ------------------------------------------------------------------
    # Response generation
    # ------------------------------------------------------------------

    def _generate_template(self, query, context):
        """Rule-based response using retrieved knowledge."""
        if context:
            pieces = [f"根据我的知识库，以下是相关内容：\n"]
            for i, item in enumerate(context, 1):
                pieces.append(f"{i}. {item['text']}\n")
            pieces.append(f"\n请问还有什么想了解的吗？")
            return "".join(pieces)

        return (
            "抱歉，我在知识库中没有找到与这个问题相关的信息。\n"
            "你可以尝试：\n"
            "  1. 换个方式提问\n"
            "  2. 询问关于昇腾310B、边缘计算、CANN、RAG等方面的问题\n"
            "  3. 配置云端API获取更强大的回答能力"
        )

    def _generate_cloud(self, query, context, history):
        """Call OpenAI-compatible LLM API with RAG context."""
        system_prompt = (
            "你是一个运行在昇腾310B边缘设备上的AI助手。"
            "请基于提供的知识库内容回答用户问题。"
            "如果知识库中没有相关信息，请诚实告知，不要编造。"
        )

        # Build context block from retrieved docs
        context_block = ""
        if context:
            for i, item in enumerate(context, 1):
                context_block += f"\n[{i}] {item['text']}\n"

        messages = [{"role": "system", "content": system_prompt}]

        # Inject recent conversation
        for turn in history.get_recent(4):
            role = "user" if turn.role == "user" else "assistant"
            messages.append({"role": role, "content": turn.text})

        if context_block:
            messages.append({
                "role": "system",
                "content": f"相关知识库内容：{context_block}",
            })

        messages.append({"role": "user", "content": query})

        try:
            resp = requests.post(
                self._cloud_endpoint,
                headers={
                    "Authorization": f"Bearer {self._cloud_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self._cloud_model,
                    "messages": messages,
                    "max_tokens": 512,
                    "temperature": 0.7,
                },
                timeout=15,
            )
            if resp.status_code == 200:
                data = resp.json()
                return data["choices"][0]["message"]["content"].strip()
            return f"[API错误 {resp.status_code}] {resp.text[:200]}"
        except requests.RequestException as e:
            return f"[网络错误] 无法连接云端API: {e}"

    def _greeting_response(self):
        hour = time.localtime().tm_hour
        if hour < 6:
            return "夜深了，还不休息吗？我是昇腾310B智能助手，有什么可以帮你的？"
        elif hour < 12:
            return "早上好！我是昇腾310B智能聊天助手，今天有什么可以帮你的？"
        elif hour < 18:
            return "下午好！我是昇腾310B智能聊天助手，有什么问题尽管问我。"
        else:
            return "晚上好！我是昇腾310B智能聊天助手，很高兴为你服务。"

    def _farewell_response(self):
        return "再见！祝你愉快，随时回来继续聊～"

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _get_history(self, session_id):
        if session_id not in self._history:
            self._history[session_id] = ConversationHistory()
        return self._history[session_id]
