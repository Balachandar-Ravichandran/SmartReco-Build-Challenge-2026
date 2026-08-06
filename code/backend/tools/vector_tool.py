"""Chroma wrapper — the only module that talks to Chroma directly.

Two collections: course_embeddings, path_embeddings (Section 13.3).
"""
import json
from typing import Literal

import chromadb

from backend.core.config import get_settings

_client = None
_collections: dict[str, any] = {}


def get_client():
    global _client
    if _client is None:
        settings = get_settings()
        _client = chromadb.PersistentClient(
            path=settings.CHROMA_PATH,
            settings=chromadb.Settings(anonymized_telemetry=False),
        )
    return _client


def get_collection(name: Literal["course_embeddings", "path_embeddings"]):
    if name not in _collections:
        _collections[name] = get_client().get_or_create_collection(name=name)
    return _collections[name]


def warm_collections():
    """Startup step 3: touch each collection, surfaces connection errors early.

    Uses .count() rather than a probe .query() — a query needs a vector of the
    right dimension, which depends on whichever embedding model is configured
    (e.g. 1536 vs 3072) and would raise a dimension-mismatch error against a
    populated collection for no reason connectivity-related. count() verifies
    the same thing (collection is reachable) without assuming a dimension.
    """
    for name in ("course_embeddings", "path_embeddings"):
        collection = get_collection(name)
        collection.count()


def upsert_course(
    course_id: str, description: str, embedding: list[float], metadata: dict
):
    collection = get_collection("course_embeddings")
    collection.upsert(
        ids=[course_id],
        embeddings=[embedding],
        documents=[description],
        metadatas=[_flatten_metadata(metadata)],
    )


def upsert_path(path_id: str, description: str, embedding: list[float], metadata: dict):
    collection = get_collection("path_embeddings")
    collection.upsert(
        ids=[path_id],
        embeddings=[embedding],
        documents=[description],
        metadatas=[_flatten_metadata(metadata)],
    )


def exists(collection_name: Literal["course_embeddings", "path_embeddings"], item_id: str) -> bool:
    """Ground-truth check: is this id actually present in Chroma? Used by
    embed_catalog.py's idempotency check — the DB's `embedding_synced_at`
    column can't be trusted alone since the synthetic seed data pre-populates
    it with illustrative timestamps that were never backed by a real upsert."""
    collection = get_collection(collection_name)
    result = collection.get(ids=[item_id])
    return bool(result.get("ids"))


def delete_by_id(collection_name: Literal["course_embeddings", "path_embeddings"], item_id: str):
    collection = get_collection(collection_name)
    collection.delete(ids=[item_id])


def patch_metadata(
    collection_name: Literal["course_embeddings", "path_embeddings"],
    item_id: str,
    metadata: dict,
):
    """Metadata-only patch (price/level change) — skips re-embed (Section 13.4)."""
    collection = get_collection(collection_name)
    collection.update(ids=[item_id], metadatas=[_flatten_metadata(metadata)])


def query(
    collection_name: Literal["course_embeddings", "path_embeddings"],
    query_vector: list[float],
    top_k: int = 5,
) -> list[dict]:
    """Semantic lane of hybrid_search() (Section 7.1)."""
    collection = get_collection(collection_name)
    results = collection.query(query_embeddings=[query_vector], n_results=top_k)

    candidates = []
    ids = results.get("ids", [[]])[0]
    distances = results.get("distances", [[]])[0]
    metadatas = results.get("metadatas", [[]])[0]

    for item_id, distance, metadata in zip(ids, distances, metadatas):
        # Chroma returns cosine distance; similarity = 1 - distance
        similarity = 1.0 - distance
        candidates.append(
            {
                "item_id": item_id,
                "semantic_similarity": similarity,
                "metadata": metadata,
            }
        )
    return candidates


def _flatten_metadata(metadata: dict) -> dict:
    """Chroma metadata values must be str/int/float/bool — flatten lists to CSV."""
    flat = {}
    for key, value in metadata.items():
        if isinstance(value, list):
            flat[key] = ",".join(str(v) for v in value)
        elif value is None:
            continue  # Chroma rejects None values
        else:
            flat[key] = value
    return flat
