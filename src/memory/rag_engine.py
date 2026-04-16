"""Lightweight RAG engine backed by ChromaDB for task / context retrieval."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from src.config import DATA_DIR

VECTORSTORE_DIR = DATA_DIR / "vectorstore"

_client = None
_collection = None


_available = True


def _ensure_collection():
    global _client, _collection, _available
    if _collection is not None:
        return
    if not _available:
        return

    try:
        import chromadb
        from chromadb.config import Settings

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
    _ensure_collection()
    if _collection is None:
        return []
    results = _collection.query(query_texts=[text], n_results=n_results)

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
