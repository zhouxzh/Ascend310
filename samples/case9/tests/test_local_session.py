from __future__ import annotations

import unittest
import httpx

from local_session import (
    Conversation,
    ConversationStore,
    LocalLLMError,
    OpenAIChatClient,
    SessionLimitError,
    sentence_chunks,
)


class ConversationTests(unittest.TestCase):
    def test_evicts_oldest_turns_for_count_and_character_limits(self) -> None:
        conversation = Conversation(max_messages=3, max_characters=8)
        conversation.add("user", "one")
        conversation.add("assistant", "two")
        conversation.add("user", "three")

        self.assertEqual(
            conversation.snapshot(),
            [
                {"role": "assistant", "content": "two"},
                {"role": "user", "content": "three"},
            ],
        )
        self.assertEqual(conversation.character_count, 8)

    def test_rejects_a_single_message_larger_than_the_memory_budget(self) -> None:
        conversation = Conversation(max_messages=2, max_characters=3)
        with self.assertRaises(SessionLimitError):
            conversation.add("user", "four")

    def test_commits_and_evicts_complete_turns_atomically(self) -> None:
        conversation = Conversation(max_messages=4, max_characters=12)
        conversation.add_turn("one", "reply")
        conversation.add_turn("two", "answer")

        self.assertEqual(
            conversation.snapshot(),
            [
                {"role": "user", "content": "two"},
                {"role": "assistant", "content": "answer"},
            ],
        )

        before = conversation.snapshot()
        with self.assertRaises(SessionLimitError):
            conversation.add_turn("new", "x" * 10)
        self.assertEqual(conversation.snapshot(), before)

    def test_preview_does_not_mutate_history(self) -> None:
        conversation = Conversation(max_messages=4, max_characters=20)
        conversation.add_turn("hello", "world")
        before = conversation.snapshot()
        prompt = conversation.preview_user("next")
        self.assertEqual(prompt[-1], {"role": "user", "content": "next"})
        self.assertEqual(conversation.snapshot(), before)

    def test_store_reuses_an_explicit_session_and_clear_discards_messages(self) -> None:
        store = ConversationStore(max_sessions=2, max_messages=2, max_characters=20)
        session_id, conversation = store.get_or_create("browser-a")
        conversation.add("user", "hello")
        reused_id, reused = store.get_or_create(session_id)
        store.clear(session_id)

        self.assertEqual(reused_id, session_id)
        self.assertIs(reused, conversation)
        self.assertEqual(reused.snapshot(), [])

    def test_clear_all_replaces_the_session_namespace_atomically(self) -> None:
        store = ConversationStore(max_sessions=3, max_messages=4, max_characters=40)
        _, old_a = store.get_or_create("browser-a")
        _, old_b = store.get_or_create("browser-b")
        old_a.add("user", "old context A")
        old_b.add("user", "old context B")

        self.assertEqual(store.clear_all(), 2)
        self.assertEqual(store.session_count, 0)
        # A request that retained an old object cannot make that context
        # visible again after the namespace replacement.
        _, fresh_a = store.get_or_create("browser-a")
        self.assertIsNot(fresh_a, old_a)
        self.assertEqual(fresh_a.snapshot(), [])


class SentenceChunkTests(unittest.TestCase):
    def test_splits_chinese_sentence_boundaries_and_retains_tail(self) -> None:
        self.assertEqual(
            list(sentence_chunks("第一句。第二句！尾巴")),
            ["第一句。", "第二句！", "尾巴"],
        )

    def test_splits_a_long_unpunctuated_chunk(self) -> None:
        self.assertEqual(list(sentence_chunks("abcdef", max_chunk_characters=3)), ["abc", "def"])


class OpenAIStreamingTests(unittest.IsolatedAsyncioTestCase):
    async def test_decodes_openai_sse_deltas_and_done(self) -> None:
        async def send(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                content=(
                    (
                        'data: {"choices":[{"delta":{"content":"你好"}}]}\n\n'
                        'data: {"choices":[{"delta":{"content":"。"}}]}\n\n'
                        'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n'
                        "data: [DONE]\n\n"
                    ).encode("utf-8")
                ),
                request=request,
            )

        client = OpenAIChatClient()
        await client._client.aclose()
        client._client = httpx.AsyncClient(transport=httpx.MockTransport(send))
        try:
            chunks = [chunk async for chunk in client.stream([{"role": "user", "content": "hi"}])]
        finally:
            await client.aclose()

        self.assertEqual(chunks, ["你好", "。"])
        self.assertEqual(client.last_finish_reason, "stop")

    async def test_records_length_finish_reason_instead_of_hiding_truncation(self) -> None:
        async def send(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                content=(
                    'data: {"choices":[{"delta":{"content":"半句"}}]}\n\n'
                    'data: {"choices":[{"delta":{},"finish_reason":"length"}]}\n\n'
                    "data: [DONE]\n\n"
                ).encode("utf-8"),
                request=request,
            )

        client = OpenAIChatClient()
        await client._client.aclose()
        client._client = httpx.AsyncClient(transport=httpx.MockTransport(send))
        try:
            chunks = [chunk async for chunk in client.stream([])]
        finally:
            await client.aclose()

        self.assertEqual(chunks, ["半句"])
        self.assertEqual(client.last_finish_reason, "length")

    async def test_internal_client_does_not_use_environment_proxies(self) -> None:
        client = OpenAIChatClient()
        try:
            self.assertFalse(client._client._trust_env)
        finally:
            await client.aclose()

    async def test_rejects_a_non_sse_response(self) -> None:
        async def send(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, headers={"content-type": "application/json"}, json={}, request=request)

        client = OpenAIChatClient()
        await client._client.aclose()
        client._client = httpx.AsyncClient(transport=httpx.MockTransport(send))
        try:
            with self.assertRaisesRegex(LocalLLMError, "SSE"):
                _ = [chunk async for chunk in client.stream([])]
        finally:
            await client.aclose()

    async def test_rejects_an_incomplete_sse_stream_without_done(self) -> None:
        async def send(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                content='data: {"choices":[{"delta":{"content":"partial"}}]}\n\n'.encode(
                    "utf-8"
                ),
                request=request,
            )

        client = OpenAIChatClient()
        await client._client.aclose()
        client._client = httpx.AsyncClient(transport=httpx.MockTransport(send))
        try:
            with self.assertRaisesRegex(LocalLLMError, r"before \[DONE\]"):
                _ = [chunk async for chunk in client.stream([])]
        finally:
            await client.aclose()
