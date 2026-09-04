"""Semantic retrieval helpers for participant imitation."""

from __future__ import annotations

import asyncio
import hashlib
import logging
from collections import OrderedDict

import numpy as np

from infrastructure.ai.clients import gemini_client


EMBEDDING_MODEL_NAME = "models/text-embedding-004"
DOCUMENT_EMBEDDING_CACHE_SIZE = 512
EMBEDDING_BATCH_SIZE = 64
SIMILARITY_THRESHOLD = 0.35

# Keep a deliberately small float32 LRU cache. This avoids repeatedly paying
# for the same participant messages without recreating the old RAM problem.
_DOCUMENT_EMBEDDING_CACHE: OrderedDict[str, np.ndarray] = OrderedDict()


def _cache_key(text: str) -> str:
    return hashlib.blake2b(text.encode("utf-8"), digest_size=16).hexdigest()


def _cache_get(text: str) -> np.ndarray | None:
    key = _cache_key(text)
    embedding = _DOCUMENT_EMBEDDING_CACHE.get(key)
    if embedding is not None:
        _DOCUMENT_EMBEDDING_CACHE.move_to_end(key)
    return embedding


def _cache_put(text: str, embedding) -> np.ndarray:
    key = _cache_key(text)
    vector = np.asarray(embedding, dtype=np.float32)
    _DOCUMENT_EMBEDDING_CACHE[key] = vector
    _DOCUMENT_EMBEDDING_CACHE.move_to_end(key)
    while len(_DOCUMENT_EMBEDDING_CACHE) > DOCUMENT_EMBEDDING_CACHE_SIZE:
        _DOCUMENT_EMBEDDING_CACHE.popitem(last=False)
    return vector


def _embed_query_sync(text: str) -> np.ndarray | None:
    try:
        result = gemini_client.models.embed_content(
            model=EMBEDDING_MODEL_NAME,
            contents=text,
            config={"task_type": "RETRIEVAL_QUERY"},
        )
        return np.asarray(result.embeddings[0].values, dtype=np.float32)
    except Exception as exc:
        logging.error("Не удалось получить вектор запроса: %s", exc)
        return None


def get_embedding(text: str):
    """Legacy synchronous document-embedding API."""
    cached = _cache_get(text)
    if cached is not None:
        return cached
    try:
        result = gemini_client.models.embed_content(
            model=EMBEDDING_MODEL_NAME,
            contents=text,
            config={"task_type": "RETRIEVAL_DOCUMENT", "title": "User Message"},
        )
        return _cache_put(text, result.embeddings[0].values)
    except Exception as exc:
        logging.error("Ошибка получения эмбеддинга: %s", exc)
        return None


def _embed_documents_sync(messages: list[str]) -> list[np.ndarray | None]:
    """Embed a batch, falling back to individual calls if the provider rejects it."""
    if not messages:
        return []
    try:
        result = gemini_client.models.embed_content(
            model=EMBEDDING_MODEL_NAME,
            contents=messages,
            config={"task_type": "RETRIEVAL_DOCUMENT"},
        )
        return [np.asarray(item.values, dtype=np.float32) for item in result.embeddings]
    except Exception as exc:
        logging.warning("Batch embedding failed, fallback to single: %s", exc)
        vectors: list[np.ndarray | None] = []
        for message in messages:
            try:
                result = gemini_client.models.embed_content(
                    model=EMBEDDING_MODEL_NAME,
                    contents=message,
                    config={"task_type": "RETRIEVAL_DOCUMENT", "title": "User Message"},
                )
                vectors.append(np.asarray(result.embeddings[0].values, dtype=np.float32))
            except Exception as single_exc:
                logging.warning("Single embedding failed: %s", single_exc)
                vectors.append(None)
        return vectors


def cosine_similarity(v1, v2) -> float:
    """Cosine similarity in the [-1, 1] range."""
    if v1 is None or v2 is None:
        return 0.0
    vector_1 = np.asarray(v1, dtype=np.float32)
    vector_2 = np.asarray(v2, dtype=np.float32)
    norm_1 = np.linalg.norm(vector_1)
    norm_2 = np.linalg.norm(vector_2)
    if norm_1 == 0 or norm_2 == 0:
        return 0.0
    return float(np.dot(vector_1, vector_2) / (norm_1 * norm_2))


async def find_relevant_context(query_text: str, candidate_messages: list, top_k: int = 3):
    """Search the full bounded recent+historical participant sample.

    Callers are expected to pass at most a few hundred candidates. Unlike the
    old implementation, this function does not silently reduce that pool to
    the last 30 messages.
    """
    if not candidate_messages or not query_text or top_k <= 0:
        return []

    # Preserve chronological order while removing duplicate/empty candidates.
    search_pool = list(dict.fromkeys(str(message).strip() for message in candidate_messages if str(message).strip()))
    if not search_pool:
        return []

    query_embedding = await asyncio.to_thread(_embed_query_sync, query_text)
    if query_embedding is None:
        return []

    embeddings: dict[str, np.ndarray] = {}
    misses: list[str] = []
    for message in search_pool:
        cached = _cache_get(message)
        if cached is None:
            misses.append(message)
        else:
            embeddings[message] = cached

    for start in range(0, len(misses), EMBEDDING_BATCH_SIZE):
        chunk = misses[start : start + EMBEDDING_BATCH_SIZE]
        vectors = await asyncio.to_thread(_embed_documents_sync, chunk)
        for message, vector in zip(chunk, vectors):
            if vector is not None:
                embeddings[message] = _cache_put(message, vector)

    scored_messages = [
        (cosine_similarity(query_embedding, embeddings.get(message)), message)
        for message in search_pool
        if message in embeddings
    ]
    scored_messages.sort(key=lambda item: item[0], reverse=True)

    return [
        message
        for score, message in scored_messages[:top_k]
        if score > SIMILARITY_THRESHOLD
    ]
