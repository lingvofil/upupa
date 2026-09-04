import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

import numpy as np

from tests import test_smoke_imports  # noqa: F401


def test_style_profile_preserves_short_reactions_and_real_length():
    from AI.dialog.style import create_user_style_prompt

    messages = [
        "ага",
        "ору",
        "бля",
        "/служебная_команда",
        "ну это вообще пиздец",
        "я бы туда не пошел",
        "чего???",
        "ахах",
        "да нормально все",
        "короче потом расскажу",
    ]

    prompt = create_user_style_prompt(messages, "Вася")

    assert "ага" in prompt
    assert "ору" in prompt
    assert "/служебная_команда" not in prompt
    assert "медиана" in prompt
    assert "очень коротких" in prompt
    assert "не более 50 слов" not in prompt
    assert "Не навязывай универсальный лимит длины" in prompt


def test_style_ngrams_never_cross_message_boundaries():
    from AI.dialog.style import _frequent_phrases

    phrases = _frequent_phrases(
        ["красный кот", "спит дома", "красный кот", "спит дома"],
        n=2,
        top_n=10,
    )
    phrase_names = [phrase for phrase, _count in phrases]

    assert "красный кот" in phrase_names
    assert "спит дома" in phrase_names
    assert "кот спит" not in phrase_names


def test_participant_sampling_is_bounded_and_keeps_recent_tail(tmp_path, monkeypatch):
    from AI.dialog import participant_imitation

    participant_imitation.clear_participant_history_cache()
    log_path = tmp_path / "user_messages.log"
    lines = [
        f"2026-09-01T12:00:00 - Chat -1001 (Чат) - User 42 (vasya) [Вася]: сообщение {index}\n"
        for index in range(1000)
    ]
    log_path.write_text("".join(lines), encoding="utf-8")
    monkeypatch.setattr(participant_imitation, "LOG_FILE", log_path)

    messages, count = asyncio.run(
        participant_imitation.sample_participant_messages(
            42,
            -1001,
            sample_size=20,
            recent_size=10,
        )
    )

    assert count == 1000
    assert len(messages) == 20
    assert messages[-10:] == [f"сообщение {index}" for index in range(990, 1000)]


def test_participant_history_is_scanned_only_once_per_warm_cache(tmp_path, monkeypatch):
    from AI.dialog import participant_imitation

    participant_imitation.clear_participant_history_cache()
    log_path = tmp_path / "user_messages.log"
    log_path.write_text(
        "".join(
            f"2026-09-01T12:00:00 - Chat -2002 (Чат) - User 42 (vasya) [Вася]: сообщение {index}\n"
            for index in range(50)
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(participant_imitation, "LOG_FILE", log_path)

    original_scan = participant_imitation._scan_participant_history_sync
    calls = {"scan": 0}

    def counted_scan(*args, **kwargs):
        calls["scan"] += 1
        return original_scan(*args, **kwargs)

    monkeypatch.setattr(participant_imitation, "_scan_participant_history_sync", counted_scan)

    first = asyncio.run(
        participant_imitation.sample_participant_messages(42, -2002, sample_size=20, recent_size=10)
    )
    second = asyncio.run(
        participant_imitation.sample_participant_messages(42, -2002, sample_size=20, recent_size=10)
    )

    assert first == second
    assert calls["scan"] == 1


def test_warm_participant_cache_updates_incrementally_without_rescan(tmp_path, monkeypatch):
    from AI.dialog import participant_imitation

    participant_imitation.clear_participant_history_cache()
    log_path = tmp_path / "user_messages.log"
    log_path.write_text(
        "".join(
            f"2026-09-01T12:00:00 - Chat -3003 (Чат) - User 42 (vasya) [Вася]: старое {index}\n"
            for index in range(5)
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(participant_imitation, "LOG_FILE", log_path)

    messages, count = asyncio.run(
        participant_imitation.sample_participant_messages(42, -3003, sample_size=4, recent_size=2)
    )
    assert count == 5
    assert messages[-2:] == ["старое 3", "старое 4"]

    def forbidden_rescan(*_args, **_kwargs):
        raise AssertionError("warm cache must not rescan user_messages.log")

    monkeypatch.setattr(participant_imitation, "_scan_participant_history_sync", forbidden_rescan)
    participant_imitation.record_participant_message(
        SimpleNamespace(
            chat=SimpleNamespace(id=-3003),
            from_user=SimpleNamespace(id=42),
            text="новое сообщение",
        )
    )

    updated, updated_count = asyncio.run(
        participant_imitation.sample_participant_messages(42, -3003, sample_size=4, recent_size=2)
    )
    assert updated_count == 6
    assert updated[-2:] == ["старое 4", "новое сообщение"]


def test_identity_resolution_pins_telegram_user_id(tmp_path, monkeypatch):
    from AI.dialog import participant_imitation

    log_path = tmp_path / "user_messages.log"
    log_path.write_text(
        "".join(
            [
                "2026-09-01T12:00:00 - Chat -1001 (Чат) - User 11 (first) [Алексей]: раз\n",
                "2026-09-01T12:01:00 - Chat -1001 (Чат) - User 22 (vasya) [Алексей]: два\n",
                "2026-09-01T12:02:00 - Chat -1001 (Чат) - User 22 (vasya) [Алексей]: три\n",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(participant_imitation, "LOG_FILE", log_path)

    by_username = asyncio.run(participant_imitation.resolve_participant_identity("@vasya", -1001))
    by_name = asyncio.run(participant_imitation.resolve_participant_identity("Алексей", -1001))

    assert by_username["user_id"] == 22
    assert by_name["user_id"] == 22
    assert by_username["username"] == "vasya"


def test_style_profile_refreshes_after_fifty_new_messages():
    from AI.dialog.participant_imitation import refresh_style_profile

    settings = {
        "prompt": "old prompt",
        "prompt_name": "Вася",
        "imitated_user": {"user_id": 42, "display_name": "Вася"},
        "style_profile_message_count": 100,
        "style_profile_updated_at": datetime.now(timezone.utc).isoformat(),
    }
    messages = ["ага", "ну нормально", "короче потом"]

    assert refresh_style_profile(settings, messages, 149) is False
    assert settings["prompt"] == "old prompt"

    assert refresh_style_profile(settings, messages, 150) is True
    assert settings["prompt"] != "old prompt"
    assert settings["style_profile_message_count"] == 150


def test_semantic_search_uses_supported_text_embedding_model():
    from services import smart_search

    assert smart_search.EMBEDDING_MODEL_NAME != "models/text-embedding-004"
    assert smart_search.EMBEDDING_MODEL_NAME != "text-embedding-004"
    assert smart_search.EMBEDDING_OUTPUT_DIMENSION == 768


def test_semantic_search_considers_historical_candidates_beyond_last_thirty(monkeypatch):
    from services import smart_search

    smart_search.reset_embedding_runtime_state()
    document_calls = []

    monkeypatch.setattr(
        smart_search,
        "_embed_query_sync",
        lambda _text: np.asarray([1.0, 0.0], dtype=np.float32),
    )

    def fake_embed_documents(messages):
        document_calls.extend(messages)
        return [
            np.asarray([1.0, 0.0], dtype=np.float32)
            if message == "старое релевантное сообщение"
            else np.asarray([0.0, 1.0], dtype=np.float32)
            for message in messages
        ]

    monkeypatch.setattr(smart_search, "_embed_documents_sync", fake_embed_documents)
    candidates = ["старое релевантное сообщение"] + [f"новое нерелевантное {index}" for index in range(99)]

    result = asyncio.run(smart_search.find_relevant_context("нужная тема", candidates, top_k=3))

    assert result == ["старое релевантное сообщение"]
    assert "старое релевантное сообщение" in document_calls
    assert len(document_calls) == 100


def test_document_embeddings_are_reused_from_bounded_cache(monkeypatch):
    from services import smart_search

    smart_search.reset_embedding_runtime_state()
    calls = {"documents": 0}

    monkeypatch.setattr(
        smart_search,
        "_embed_query_sync",
        lambda _text: np.asarray([1.0, 0.0], dtype=np.float32),
    )

    def fake_embed_documents(messages):
        calls["documents"] += 1
        return [np.asarray([1.0, 0.0], dtype=np.float32) for _message in messages]

    monkeypatch.setattr(smart_search, "_embed_documents_sync", fake_embed_documents)

    first = asyncio.run(smart_search.find_relevant_context("тема", ["одно", "два"], top_k=2))
    second = asyncio.run(smart_search.find_relevant_context("другая тема", ["одно", "два"], top_k=2))

    assert first == ["одно", "два"]
    assert second == ["одно", "два"]
    assert calls["documents"] == 1


def test_embedding_failure_circuit_breaker_fails_fast(monkeypatch):
    from services import smart_search

    smart_search.reset_embedding_runtime_state()
    calls = {"query": 0}

    def fail_query(_text):
        calls["query"] += 1
        smart_search._disable_embeddings("test failure")
        return None

    monkeypatch.setattr(smart_search, "_embed_query_sync", fail_query)

    first = asyncio.run(smart_search.find_relevant_context("тема", ["одно"], top_k=1))
    second = asyncio.run(smart_search.find_relevant_context("тема 2", ["одно"], top_k=1))

    assert first == []
    assert second == []
    assert calls["query"] == 1
