"""Small, dependency-free retrieval layer for the first XiaoZhi gateway cut.

It intentionally provides lexical retrieval rather than claiming an untested
Ascend embedding path. A board-admitted embedding provider can replace this
module later without changing the OpenAI API contract.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path


_TEXT_SUFFIXES = {".md", ".txt"}
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_WORD_RE = re.compile(r"[a-z0-9][a-z0-9_-]*")
_SENTENCE_BOUNDARY_RE = re.compile(r"(?<=[。！？.!?])\s*")
_MAX_DOCUMENT_BYTES = 1_000_000
_CHUNK_CHARACTERS = 900
_CHUNK_OVERLAP = 120


@dataclass(frozen=True)
class RetrievalHit:
    """One scored reference that may be injected into an LLM request."""

    source: str
    text: str
    score: float


@dataclass(frozen=True)
class _Chunk:
    source: str
    text: str
    terms: Counter[str]
    norm: float


class LocalRetriever:
    """Loads small UTF-8 documents and scores them with lexical cosine match."""

    def __init__(self, documents_dir: Path):
        self._documents_dir = documents_dir
        self._chunks = self._load_chunks()

    @property
    def document_count(self) -> int:
        return len({chunk.source for chunk in self._chunks})

    @property
    def chunk_count(self) -> int:
        return len(self._chunks)

    def search(self, query: str, *, limit: int, min_score: float) -> list[RetrievalHit]:
        query_terms = Counter(_tokenize(query))
        query_norm = _norm(query_terms)
        if not query_terms or query_norm == 0:
            return []

        hits: list[RetrievalHit] = []
        for chunk in self._chunks:
            score = _cosine(query_terms, query_norm, chunk.terms, chunk.norm)
            if score >= min_score:
                hits.append(RetrievalHit(chunk.source, chunk.text, score))

        hits.sort(key=lambda item: (-item.score, item.source, item.text))
        return hits[:limit]

    def _load_chunks(self) -> list[_Chunk]:
        if not self._documents_dir.exists():
            return []
        if not self._documents_dir.is_dir():
            raise ValueError(f"RAG_DOCUMENTS_DIR is not a directory: {self._documents_dir}")

        root = self._documents_dir.resolve()
        chunks: list[_Chunk] = []
        for candidate in sorted(root.rglob("*")):
            if candidate.suffix.lower() not in _TEXT_SUFFIXES or not candidate.is_file():
                continue
            try:
                resolved = candidate.resolve()
                resolved.relative_to(root)
            except ValueError:
                continue
            if resolved.stat().st_size > _MAX_DOCUMENT_BYTES:
                continue
            try:
                content = resolved.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            source = resolved.relative_to(root).as_posix()
            for text in _split_chunks(content):
                terms = Counter(_tokenize(text))
                norm = _norm(terms)
                if terms and norm:
                    chunks.append(_Chunk(source, text, terms, norm))
        return chunks


def _tokenize(text: str) -> list[str]:
    normalized = text.lower()
    words = _WORD_RE.findall(normalized)
    characters = _CJK_RE.findall(normalized)
    bigrams = ["".join(characters[index : index + 2]) for index in range(len(characters) - 1)]
    return words + characters + bigrams


def _norm(terms: Counter[str]) -> float:
    return math.sqrt(sum(count * count for count in terms.values()))


def _cosine(
    left: Counter[str], left_norm: float, right: Counter[str], right_norm: float
) -> float:
    if left_norm == 0 or right_norm == 0:
        return 0.0
    dot = sum(count * right.get(term, 0) for term, count in left.items())
    return dot / (left_norm * right_norm)


def _split_chunks(content: str) -> list[str]:
    paragraphs = [line.strip() for line in content.splitlines() if line.strip()]
    chunks: list[str] = []
    for paragraph in paragraphs:
        if len(paragraph) <= _CHUNK_CHARACTERS:
            chunks.append(paragraph)
            continue
        sentences = [item for item in _SENTENCE_BOUNDARY_RE.split(paragraph) if item]
        current = ""
        for sentence in sentences:
            if current and len(current) + len(sentence) + 1 > _CHUNK_CHARACTERS:
                chunks.append(current)
                current = current[-_CHUNK_OVERLAP:] + sentence
            else:
                current = f"{current} {sentence}".strip()
        if current:
            chunks.append(current)
    return chunks
