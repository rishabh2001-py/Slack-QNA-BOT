"""
Vector store — thin wrapper around ChromaDB.
Handles: upsert, search, and deletion by source.

Used by:
  - ingest/*.py  (write)
  - retriever.py (read)
"""

import os
import hashlib
import logging
from dataclasses import dataclass, field
from typing import Optional

import chromadb
from chromadb.utils import embedding_functions

log = logging.getLogger(__name__)

COLLECTION_NAME = "devops_knowledge"
EMBED_MODEL     = "all-MiniLM-L6-v2"   # local model, no API needed


@dataclass
class Chunk:
    """One indexable unit of knowledge."""
    text:        str
    source_url:  str
    source_type: str          # "confluence" | "jira" | "github" | "slack"
    title:       str = ""
    updated_at:  str = ""
    extra:       dict = field(default_factory=dict)

    @property
    def doc_id(self) -> str:
        """Stable ID — same content + URL always produces same ID."""
        key = f"{self.source_url}::{self.text[:120]}"
        return hashlib.md5(key.encode()).hexdigest()

    @property
    def metadata(self) -> dict:
        return {
            "source_url":  self.source_url,
            "source_type": self.source_type,
            "title":       self.title,
            "updated_at":  self.updated_at,
            **self.extra,
        }


@dataclass
class SearchResult:
    text:        str
    source_url:  str
    source_type: str
    title:       str
    score:       float          # lower = more similar (Chroma uses L2 by default)


class VectorStore:
    def __init__(self, persist_dir: Optional[str] = None):
        persist_dir = persist_dir or os.getenv("CHROMA_PERSIST_DIR", "./data/chroma")
        os.makedirs(persist_dir, exist_ok=True)

        self._client = chromadb.PersistentClient(path=persist_dir)

        self._embed_fn = embedding_functions.SentenceTransformerEmbeddingFunction(
            model_name=EMBED_MODEL,
        )

        self._col = self._client.get_or_create_collection(
            name=COLLECTION_NAME,
            embedding_function=self._embed_fn,
            metadata={"hnsw:space": "cosine"},   # cosine sim feels more natural
        )
        log.info(f"VectorStore ready — {self._col.count()} chunks indexed")

    # ── Write ────────────────────────────────────────────────────────────────

    _BATCH_SIZE = 2000

    def upsert(self, chunks: list[Chunk]) -> None:
        """Add or update chunks. Skips empty text. Batches to stay within ChromaDB limits."""
        chunks = [c for c in chunks if c.text.strip()]
        if not chunks:
            return

        for i in range(0, len(chunks), self._BATCH_SIZE):
            batch = chunks[i : i + self._BATCH_SIZE]
            self._col.upsert(
                ids        = [c.doc_id   for c in batch],
                documents  = [c.text     for c in batch],
                metadatas  = [c.metadata for c in batch],
            )
        log.info(f"Upserted {len(chunks)} chunks")

    def delete_by_source(self, source_type: str) -> None:
        """Wipe all chunks from a given source before re-ingesting."""
        self._col.delete(where={"source_type": source_type})
        log.info(f"Deleted all chunks for source_type={source_type!r}")

    # ── Read ─────────────────────────────────────────────────────────────────

    def search(
        self,
        query:       str,
        top_k:       int = 6,
        source_type: Optional[str] = None,
    ) -> list[SearchResult]:
        """
        Semantic search. Returns up to top_k results.
        Optionally filter by source_type.
        """
        where = {"source_type": source_type} if source_type else None

        results = self._col.query(
            query_texts     = [query],
            n_results       = top_k,
            where           = where,
            include         = ["documents", "metadatas", "distances"],
        )

        out = []
        for doc, meta, dist in zip(
            results["documents"][0],
            results["metadatas"][0],
            results["distances"][0],
        ):
            out.append(SearchResult(
                text        = doc,
                source_url  = meta.get("source_url", ""),
                source_type = meta.get("source_type", ""),
                title       = meta.get("title", ""),
                score       = dist,
            ))
        return out

    def count(self) -> int:
        return self._col.count()

    def count_by_source(self, source_type: str) -> int:
        """Returns the number of chunks for a given source_type (0 = none indexed yet)."""
        result = self._col.get(where={"source_type": source_type}, include=["metadatas"])
        return len(result.get("ids", []))
