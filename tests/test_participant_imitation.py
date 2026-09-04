import asyncio
from datetime import datetime, timezone

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


def test_semantic_search_considers_historical_candidates_beyond_last_thirty(monkeypatch):
    from services import smart_search

    smart_search._DOCUMENT_EMBEDDING_CACHE.clear()
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

    smart_search._DOCUMENT_EMBEDDING_CACHE.clear()
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
