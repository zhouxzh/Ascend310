"""
Knowledge base with FAISS vector search and Chinese-aware text chunking.

Provides the RAG retrieval engine: stores documents as embedding vectors,
searches for top-k relevant chunks given a query.
"""

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any, Optional

import numpy as np

try:
    import faiss
except ImportError:
    faiss = None

try:
    import jieba
except ImportError:
    jieba = None

from config import EMBEDDING_DIM, TOP_K_RETRIEVAL, CHUNK_SIZE, CHUNK_OVERLAP


@dataclass
class Document:
    id: int
    text: str
    metadata: dict = field(default_factory=dict)


class KnowledgeBase:
    """Manages a FAISS-backed vector index of text documents."""

    def __init__(self, embedding_model, dim=None):
        dim = dim or EMBEDDING_DIM
        self._model = embedding_model
        self._dim = dim
        self._documents: list[Document] = []
        self._index = None
        self._init_index()

    def _init_index(self):
        if faiss is None:
            raise ImportError("faiss-cpu is required: pip install faiss-cpu")
        self._index = faiss.IndexFlatIP(self._dim)

    # -- write ---------------------------------------------------------------

    def add_texts(self, texts, metadatas=None):
        """Embed a list of texts and add them to the index."""
        if not texts:
            return
        embeddings = self._model.encode(texts)
        self._index.add(embeddings)
        if metadatas is None:
            metadatas = [{}] * len(texts)
        for text, meta in zip(texts, metadatas):
            doc = Document(id=len(self._documents), text=text, metadata=meta)
            self._documents.append(doc)

    def add_document(self, filepath, metadata=None):
        """Read a text file, split into chunks, and add all chunks."""
        with open(filepath, encoding="utf-8") as f:
            content = f.read()
        chunks = self._split_text(content)
        metas = [metadata or {"source": os.path.basename(filepath)}] * len(chunks)
        self.add_texts(chunks, metas)

    def _split_text(self, text):
        """Split text into overlapping chunks, aware of Chinese sentence
        boundaries when jieba is available."""
        paragraphs = [p.strip() for p in text.split("\n") if p.strip()]
        chunks = []

        for para in paragraphs:
            if len(para) <= CHUNK_SIZE:
                chunks.append(para)
                continue
            sentences = self._split_sentences(para)
            current = ""
            for sent in sentences:
                if len(current) + len(sent) <= CHUNK_SIZE:
                    current += sent
                else:
                    if current:
                        chunks.append(current)
                    current = current[-CHUNK_OVERLAP:] + sent if current else sent
            if current:
                chunks.append(current)

        return chunks

    def _split_sentences(self, text):
        """Split on Chinese/English sentence-ending punctuation."""
        parts = re.split(r"(?<=[。！？；\.\!\?\n])", text)
        return [p for p in parts if p.strip()]

    # -- read ----------------------------------------------------------------

    def search(self, query, k=None):
        """Return top-k relevant documents with similarity scores."""
        k = k or TOP_K_RETRIEVAL
        if self._index.ntotal == 0:
            return []

        query_vec = self._model.encode([query])
        scores, indices = self._index.search(query_vec, k)

        results = []
        for score, idx in zip(scores[0], indices[0]):
            if idx < 0 or idx >= len(self._documents):
                continue
            doc = self._documents[idx]
            results.append({
                "text": doc.text,
                "score": float(score),
                "metadata": doc.metadata,
            })
        return results

    # -- persistence ---------------------------------------------------------

    def save(self, index_path=None, docs_path=None):
        from config import FAISS_INDEX_PATH, DOCS_PATH

        index_path = index_path or FAISS_INDEX_PATH
        docs_path = docs_path or DOCS_PATH

        os.makedirs(os.path.dirname(index_path), exist_ok=True)
        if faiss is not None:
            faiss.write_index(self._index, index_path)

        with open(docs_path, "w", encoding="utf-8") as f:
            json.dump([
                {"id": d.id, "text": d.text, "metadata": d.metadata}
                for d in self._documents
            ], f, ensure_ascii=False, indent=2)

    def load(self, index_path=None, docs_path=None):
        from config import FAISS_INDEX_PATH, DOCS_PATH

        index_path = index_path or FAISS_INDEX_PATH
        docs_path = docs_path or DOCS_PATH

        if os.path.exists(index_path) and faiss is not None:
            self._index = faiss.read_index(index_path)
        if os.path.exists(docs_path):
            with open(docs_path, encoding="utf-8") as f:
                raw = json.load(f)
            self._documents = [Document(**item) for item in raw]

    @property
    def size(self):
        return self._index.ntotal if self._index else 0

    def stats(self):
        return {"total_documents": self.size, "dimension": self._dim}
