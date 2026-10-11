"""Semantic retrieval helpers for participant imitation."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
import threading
import time
from collections import OrderedDict, deque

import numpy as np

from infrastructure.ai.clients import gemini_client


# text-only retrieval intentionally uses gemini-embedding-001: the current
# Python SDK returns independent vectors for a list of strings with this model.
# gemini-embedding-2 has different multi-content aggregation semantics.
EMBEDDING_MODEL_NAME = os.getenv("GEMINI_EMBEDDING_MODEL", "gemini-embedding-001")
EMBEDDING_OUTPUT_DIMENSION = 768
# Several participants (~225 candidates each) across chats must fit without
# evicting each other: 2048 * 768 * float32 is roughly 6 MiB.
DOCUMENT_EMBEDDING_CACHE_SIZE = 2048
EMBEDDING_BATCH_SIZE = 64
SIMILARITY_THRESHOLD = 0.35
EMBEDDING_CALL_TIMEOUT_SECONDS = 3.0
EMBEDDING_FAILURE_COOLDOWN_SECONDS = 60.0
# The free tier counts every embedded text as one request (limit 100/min).
# A cold participant cache has ~225 candidates, so embedding them all at once
# always hit 429 and disabled semantic memory. Stay under the limit instead and
# fill the cache over the next turns, newest candidates first.
EMBEDDING_REQUESTS_PER_MINUTE = int(os.getenv("GEMINI_EMBEDDING_RPM", "80"))
EMBEDDING_BUDGET_WINDOW_SECONDS = 60.0
_RETRY_DELAY_RE = re.compile(r"retry in ([0-9]+(?:\.[0-9]+)?)s", re.IGNORECASE)

_DOCUMENT_EMBEDDING_CACHE: OrderedDict[str, np.ndarray] = OrderedDict()
_EMBEDDING_DISABLED_UNTIL = 0.0
_EMBEDDING_USAGE: deque[tuple[float, int]] = deque()
_EMBEDDING_USAGE_LOCK = threading.Lock()


def _reserve_embedding_budget(requested: int) -> int:
    """Reserve up to ``requested`` embedded texts in the rolling one-minute window."""
    if requested <= 0:
        return 0
    now = time.monotonic()
    with _EMBEDDING_USAGE_LOCK:
        while _EMBEDDING_USAGE and now - _EMBEDDING_USAGE[0][0] >= EMBEDDING_BUDGET_WINDOW_SECONDS:
            _EMBEDDING_USAGE.popleft()
        used = sum(count for _timestamp, count in _EMBEDDING_USAGE)
        allowed = max(0, min(requested, EMBEDDING_REQUESTS_PER_MINUTE - used))
        if allowed:
            _EMBEDDING_USAGE.append((now, allowed))
        return allowed


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
    cooldown = EMBEDDING_FAILURE_COOLDOWN_SECONDS
    match = _RETRY_DELAY_RE.search(reason or "")
    if match:
        # Respect the provider's own retry hint instead of a fixed minute.
        cooldown = min(EMBEDDING_FAILURE_COOLDOWN_SECONDS, max(5.0, float(match.group(1)) + 1.0))
    _EMBEDDING_DISABLED_UNTIL = time.monotonic() + cooldown
    logging.warning(
        "Semantic embeddings disabled for %.0fs model=%s reason=%s",
        cooldown,
        EMBEDDING_MODEL_NAME,
        (reason or "")[:300],
    )


def reset_embedding_runtime_state() -> None:
    """Test/maintenance helper."""
    global _EMBEDDING_DISABLED_UNTIL
    _DOCUMENT_EMBEDDING_CACHE.clear()
    _EMBEDDING_DISABLED_UNTIL = 0.0
    with _EMBEDDING_USAGE_LOCK:
        _EMBEDDING_USAGE.clear()


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
        logging.error("Не удалось получить вектор запроса model=%s: %s", EMBEDDING_MODEL_NAME, str(exc)[:300])
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
        logging.error("Batch embedding failed model=%s: %s", EMBEDDING_MODEL_NAME, str(exc)[:300])
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
    query_embedding = None
    if _reserve_embedding_budget(1):
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
    # Callers pass history oldest-first with interaction pairs at the end, so
    # newest material is embedded first when the budget runs out.
    misses.reverse()

    documents_started = time.perf_counter()
    budget_limited = 0
    for start in range(0, len(misses), EMBEDDING_BATCH_SIZE):
        if not _embedding_available():
            break
        chunk = misses[start : start + EMBEDDING_BATCH_SIZE]
        allowed = _reserve_embedding_budget(len(chunk))
        if allowed < len(chunk):
            budget_limited = len(misses) - start - allowed
            chunk = chunk[:allowed]
        if not chunk:
            break
        vectors = await _run_embedding_call(_embed_documents_sync, chunk)
        if vectors is None:
            break
        for message, vector in zip(chunk, vectors):
            if vector is not None:
                embeddings[message] = _cache_put(message, vector)
        if budget_limited:
            break
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
        "Semantic search timing model=%s candidates=%s cache_hits=%s misses=%s deferred=%s query=%.3fs documents=%.3fs total=%.3fs results=%s",
        EMBEDDING_MODEL_NAME,
        len(search_pool),
        len(search_pool) - len(misses),
        len(misses),
        budget_limited,
        query_elapsed,
        documents_elapsed,
        time.perf_counter() - total_started,
        len(result),
    )
    return result
