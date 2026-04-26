"""Lightweight RAG engine backed by ChromaDB for task / context retrieval."""

from __future__ import annotations

import os
import warnings
from typing import Any

from src.config import DATA_DIR

VECTORSTORE_DIR = DATA_DIR / "vectorstore"

_client = None
_collection = None


_available = True


def _rag_disabled() -> bool:
    return os.environ.get("OURAGENTTEAMS_DISABLE_RAG", "").strip().lower() in (
        "1", "true", "yes", "on",
    )


def _ensure_collection():
    global _client, _collection, _available
    if _collection is not None:
        return
    if not _available or _rag_disabled():
        return

    try:
        import chromadb
        from chromadb.config import Settings

        # Avoid accidental telemetry / network during tests or air-gapped use
        os.environ.setdefault("ANONYMIZED_TELEMETRY", "False")
        os.environ.setdefault("CHROMA_DISABLE_TELEMETRY", "1")

        VECTORSTORE_DIR.mkdir(parents=True, exist_ok=True)
        _client = chromadb.PersistentClient(
            path=str(VECTORSTORE_DIR),
            settings=Settings(anonymized_telemetry=False),
        )
        _collection = _client.get_or_create_collection(
            name="agent_team_memory",
            metadata={"hnsw:space": "cosine"},
        )
    except ImportError:
        _available = False
    except Exception as exc:  # chroma / embedding / IO / SSL, etc.
        _available = False
        warnings.warn(
            f"RAG (Chroma) unavailable, continuing without retrieved context: {exc}",
            RuntimeWarning,
            stacklevel=2,
        )


def add_document(doc_id: str, text: str, metadata: dict[str, Any] | None = None) -> None:
    _ensure_collection()
    if _collection is None:
        return
    _collection.upsert(
        ids=[doc_id],
        documents=[text],
        metadatas=[metadata or {}],
    )


def query(text: str, n_results: int = 5) -> list[dict[str, Any]]:
    if _rag_disabled():
        return []
    try:
        _ensure_collection()
        if _collection is None:
            return []
        results = _collection.query(query_texts=[text], n_results=n_results)
    except Exception as exc:
        global _available
        _available = False
        warnings.warn(
            f"RAG query failed, continuing without retrieved context: {exc}",
            RuntimeWarning,
            stacklevel=2,
        )
        return []

    docs: list[dict[str, Any]] = []
    ids = results.get("ids", [[]])[0]
    documents = results.get("documents", [[]])[0]
    metadatas = results.get("metadatas", [[]])[0]
    distances = results.get("distances", [[]])[0]

    for i, doc_id in enumerate(ids):
        docs.append({
            "id": doc_id,
            "text": documents[i] if i < len(documents) else "",
            "metadata": metadatas[i] if i < len(metadatas) else {},
            "distance": distances[i] if i < len(distances) else 1.0,
        })
    return docs


def index_task_result(task_id: str, description: str, result_summary: str, model: str) -> None:
    """Index a completed task for future retrieval."""
    text = f"Task: {description}\nModel: {model}\nResult: {result_summary}"
    add_document(
        doc_id=f"task_{task_id}",
        text=text,
        metadata={"task_id": task_id, "model": model, "type": "task_result"},
    )
