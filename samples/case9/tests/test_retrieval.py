from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from retrieval import LocalRetriever


class RetrievalTests(unittest.TestCase):
    def test_chinese_query_returns_the_matching_document(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "xiaozhi.md").write_text(
                "小智服务端使用 OpenAI 兼容的流式接口调用网关。", encoding="utf-8"
            )
            (root / "other.txt").write_text("温度传感器的数据采集流程。", encoding="utf-8")
            retriever = LocalRetriever(root)
            hits = retriever.search("小智如何流式调用网关", limit=2, min_score=0.01)

        self.assertEqual(retriever.document_count, 2)
        self.assertEqual(hits[0].source, "xiaozhi.md")
        self.assertGreater(hits[0].score, 0)

    def test_missing_knowledge_directory_is_an_empty_index(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            retriever = LocalRetriever(Path(temp_dir) / "missing")

        self.assertEqual(retriever.document_count, 0)
        self.assertEqual(retriever.search("小智", limit=3, min_score=0), [])
