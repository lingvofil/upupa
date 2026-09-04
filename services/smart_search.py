"""Semantic retrieval helpers for participant imitation."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import time
from collections import OrderedDict

import numpy as np

from infrastructure.ai.clients import gemini_client


# text-only retrieval intentionally uses gemini-embedding-001: the current
# Python SDK returns independent vectors for a list of strings with this model.
# gemini-embedding-2 has different multi-content aggregation semantics.
EMBEDDING_MODEL_NAME = os.getenv("GEMINI_EMBEDDING_MODEL", "gemini-embedding-001")
EMBEDDING_OUTPUT_DIMENSION = 768
DOCUMENT_EMBEDDING_CACHE_SIZE = 512
EMBEDDING_BATCH_SIZE = 64
SIMILARITY_THRESHOLD = 0.35
EMBEDDING_CALL_TIMEOUT_SECONDS = 3.0
EMBEDDING_FAILURE_COOLDOWN_SECONDS = 60.0

# 512 * 768 * float32 is roughly 1.5 MiB plus Python overhead.
_DOCUMENT_EMBEDDING_CACHE: OrderedDict[str, np.ndarray] = OrderedDict()
_EMBEDDING_DISABLED_UNTIL = 0.0


def _cache_key(text: str) -> str:
    payload = f"{EMBEDDING_MODEL_NAME}\0{text}".encode("utf-8")
    return hashlib.blake2b(payload, digest_size=16).hexdigest()


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


def _embedding_available() -> bool:
    return time.monotonic() >= _EMBEDDING_DISABLED_UNTIL


def _disable_embeddings(reason: str) -> None:
    global _EMBEDDING_DISABLED_UNTIL
    _EMBEDDING_DISABLED_UNTIL = time.monotonic() + EMBEDDING_FAILURE_COOLDOWN_SECONDS
    logging.warning(
        "Semantic embeddings disabled for %.0fs model=%s reason=%s",
        EMBEDDING_FAILURE_COOLDOWN_SECONDS,
        EMBEDDING_MODEL_NAME,
        reason,
    )


def reset_embedding_runtime_state() -> None:
    """Test/maintenance helper."""
    global _EMBEDDING_DISABLED_UNTIL
    _DOCUMENT_EMBEDDING_CACHE.clear()
    _EMBEDDING_DISABLED_UNTIL = 0.0


def _embed_query_sync(text: str) -> np.ndarray | None:
    if not _embedding_available():
        return None
    try:
        result = gemini_client.models.embed_content(
            model=EMBEDDING_MODEL_NAME,
            contents=text,
            config={
                "task_type": "RETRIEVAL_QUERY",
                "output_dimensionality": EMBEDDING_OUTPUT_DIMENSION,
            },
        )
        return np.asarray(result.embeddings[0].values, dtype=np.float32)
    except Exception as exc:
        logging.error("Не удалось получить вектор запроса model=%s: %s", EMBEDDING_MODEL_NAME, exc)
        _disable_embeddings(str(exc))
        return None


def get_embedding(text: str):
    """Legacy synchronous document-embedding API."""
    cached = _cache_get(text)
    if cached is not None:
        return cached
    if not _embedding_available():
        return None
    try:
        result = gemini_client.models.embed_content(
            model=EMBEDDING_MODEL_NAME,
            contents=text,
            config={
                "task_type": "RETRIEVAL_DOCUMENT",
                "title": "User Message",
                "output_dimensionality": EMBEDDING_OUTPUT_DIMENSION,
            },
        )
        return _cache_put(text, result.embeddings[0].values)
    except Exception as exc:
        logging.error("Ошибка получения эмбеддинга model=%s: %s", EMBEDDING_MODEL_NAME, exc)
        _disable_embeddings(str(exc))
        return None


def _embed_documents_sync(messages: list[str]) -> list[np.ndarray | None]:
    """Embed independent text documents in one SDK batch call."""
    if not messages or not _embedding_available():
        return [None] * len(messages)
    try:
        result = gemini_client.models.embed_content(
            model=EMBEDDING_MODEL_NAME,
            contents=messages,
            config={
                "task_type": "RETRIEVAL_DOCUMENT",
                "output_dimensionality": EMBEDDING_OUTPUT_DIMENSION,
            },
        )
        vectors = [np.asarray(item.values, dtype=np.float32) for item in result.embeddings]
        if len(vectors) != len(messages):
            raise RuntimeError(
                f"embedding batch returned {len(vectors)} vectors for {len(messages)} documents"
            )
        return vectors
    except Exception as exc:
        # Do not fan one provider/model failure out into dozens of slow single
        # requests. Semantic memory is optional, so fail fast for a short period.
        logging.error("Batch embedding failed model=%s: %s", EMBEDDING_MODEL_NAME, exc)
        _disable_embeddings(str(exc))
        return [None] * len(messages)


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


async def _run_embedding_call(func, *args):
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(func, *args),
            timeout=EMBEDDING_CALL_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        _disable_embeddings(f"provider call exceeded {EMBEDDING_CALL_TIMEOUT_SECONDS:.1f}s")
        return None


async def find_relevant_context(query_text: str, candidate_messages: list, top_k: int = 3):
    """Search the full bounded recent+historical participant sample."""
    if not candidate_messages or not query_text or top_k <= 0 or not _embedding_available():
        return []

    total_started = time.perf_counter()
    search_pool = list(
        dict.fromkeys(
            str(message).strip()
            for message in candidate_messages
            if str(message).strip()
        )
    )
    if not search_pool:
        return []

    query_started = time.perf_counter()
    query_embedding = await _run_embedding_call(_embed_query_sync, query_text)
    query_elapsed = time.perf_counter() - query_started
    if query_embedding is None:
        logging.info(
            "Semantic search timing model=%s candidates=%s query=%.3fs documents=0.000s total=%.3fs result=disabled",
            EMBEDDING_MODEL_NAME,
            len(search_pool),
            query_elapsed,
            time.perf_counter() - total_started,
        )
        return []

    embeddings: dict[str, np.ndarray] = {}
    misses: list[str] = []
    for message in search_pool:
        cached = _cache_get(message)
        if cached is None:
            misses.append(message)
        else:
            embeddings[message] = cached

    documents_started = time.perf_counter()
    for start in range(0, len(misses), EMBEDDING_BATCH_SIZE):
        if not _embedding_available():
            break
        chunk = misses[start : start + EMBEDDING_BATCH_SIZE]
        vectors = await _run_embedding_call(_embed_documents_sync, chunk)
        if vectors is None:
            break
        for message, vector in zip(chunk, vectors):
            if vector is not None:
                embeddings[message] = _cache_put(message, vector)
    documents_elapsed = time.perf_counter() - documents_started

    scored_messages = [
        (cosine_similarity(query_embedding, embeddings.get(message)), message)
        for message in search_pool
        if message in embeddings
    ]
    scored_messages.sort(key=lambda item: item[0], reverse=True)

    result = [
        message
        for score, message in scored_messages[:top_k]
        if score > SIMILARITY_THRESHOLD
    ]
    logging.info(
        "Semantic search timing model=%s candidates=%s cache_hits=%s misses=%s query=%.3fs documents=%.3fs total=%.3fs results=%s",
        EMBEDDING_MODEL_NAME,
        len(search_pool),
        len(search_pool) - len(misses),
        len(misses),
        query_elapsed,
        documents_elapsed,
        time.perf_counter() - total_started,
        len(result),
    )
    return result
