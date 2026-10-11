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
    assert "Очень коротких" in prompt
    assert "не более 50 слов" not in prompt
    assert "реальное, а не потолок" in prompt
    assert "[ХАРАКТЕРНАЯ ЛЕКСИКА]" in prompt


def test_style_profile_includes_real_interaction_examples_and_behavior_rule():
    from AI.dialog.style import create_user_style_prompt

    prompt = create_user_style_prompt(
        ["куклу хочу", "хочу монстер хай", "не буду"],
        "Вася",
        interaction_examples=[
            "Реплика собеседника: что тебе подарить?\nОтвет участника: куклу",
        ],
        recurring_examples=["подари куклу"],
    )

    assert "[INTERACTION EXAMPLES]" in prompt
    assert "что тебе подарить?" in prompt
    assert "Ответ участника: куклу" in prompt
    assert "INTERACTION EXAMPLES важнее усреднённой вежливости" in prompt
    assert "[RECURRING PATTERNS]" in prompt
    assert "- подари куклу" in prompt
    assert "повторяющиеся просьбы" in prompt
    assert "не обязательный набор фраз" in prompt
    assert "Не повторяй одну и ту же фирменную фразу" in prompt


def test_participant_interactions_capture_preceding_other_user(monkeypatch):
    from AI.dialog import participant_imitation

    class FakeRepository:
        def scan(self, _chat_id, visitor, **_filters):
            visitor({"id": 2, "user_id": "42", "text": "подари куклу"})
            visitor({"id": 4, "user_id": "42", "text": "не буду"})

        def context(self, _chat_id, row_id, radius=1):
            assert radius >= 1
            rows = {
                2: [
                    {"id": 1, "user_id": "99", "text": "что тебе подарить?"},
                    {"id": 2, "user_id": "42", "text": "подари куклу"},
                    {"id": 3, "user_id": "42", "text": "ещё одну"},
                ],
                4: [
                    {"id": 3, "user_id": "42", "text": "ещё одну"},
                    {"id": 4, "user_id": "42", "text": "не буду"},
                ],
            }
            return rows[row_id]

    monkeypatch.setattr(
        participant_imitation,
        "get_history_repository",
        lambda _path: FakeRepository(),
    )

    interactions = participant_imitation._sample_participant_interactions_sync(
        42,
        -1001,
        sample_size=10,
        recent_size=10,
    )

    # The participant's consecutive messages form one answer turn ("серия").
    assert interactions == [
        "Реплика собеседника: что тебе подарить?\nОтвет участника: подари куклу\nещё одну"
    ]


def test_semantic_memory_searches_interaction_examples_and_excludes_current_message(monkeypatch):
    from AI.dialog import participant_imitation

    entry = participant_imitation.ParticipantHistory(
        chat_id="-1001",
        user_id=42,
        sample_size=10,
        recent_size=10,
    )
    entry.add_logged_message("старое сообщение")
    entry.add_logged_message("обезян обезян")
    entry.add_logged_message("обезян обезян")
    entry.add_logged_message("ещё как")
    entry.interactions = [
        "Реплика собеседника: А муж че не пердит?\nОтвет участника: еще как"
    ]

    async def fake_get_or_build_history(*_args, **_kwargs):
        return entry, "hit"

    captured = {}

    async def fake_find_relevant_context(query_text, candidates, top_k=3):
        captured["query"] = query_text
        captured["candidates"] = list(candidates)
        captured["top_k"] = top_k
        return [entry.interactions[0]]

    monkeypatch.setattr(participant_imitation, "_get_or_build_history", fake_get_or_build_history)
    monkeypatch.setattr(participant_imitation, "find_relevant_context", fake_find_relevant_context)
    monkeypatch.setattr(participant_imitation, "refresh_style_profile", lambda *_args, **_kwargs: False)

    memory, changed = asyncio.run(
        participant_imitation.prepare_participant_turn(
            -1001,
            {"imitated_user": {"user_id": 42, "display_name": "Вася"}},
            "еще как\n\nКонтекст сообщения, на которое отвечают:\nА муж че не пердит?",
            current_message_text="ещё как",
        )
    )

    assert changed is False
    assert "старое сообщение" in captured["candidates"]
    assert "ещё как" not in captured["candidates"]
    assert entry.interactions[0] in captured["candidates"]
    assert not any(
        candidate.startswith("Повторяющаяся реплика участника:")
        for candidate in captured["candidates"]
    )
    assert captured["top_k"] == 5
    assert "А муж че не пердит?" in captured["query"]
    assert "Ответ участника: еще как" in memory


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


def test_recurring_short_messages_survive_outside_bounded_style_sample():
    from AI.dialog.participant_imitation import ParticipantHistory

    entry = ParticipantHistory(
        chat_id="-1001",
        user_id=42,
        sample_size=2,
        recent_size=2,
    )
    for index in range(40):
        entry.add_logged_message(f"одноразовая реплика номер {index}")
        if index in {2, 9, 17, 31}:
            entry.add_logged_message("подари куклу")

    sampled, _count = entry.snapshot()

    assert "подари куклу" not in sampled
    assert "подари куклу" in entry.recurring_messages()


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
    from AI.dialog.participant_imitation import STYLE_PROFILE_VERSION, refresh_style_profile

    settings = {
        "prompt": "old prompt",
        "prompt_name": "Вася",
        "imitated_user": {"user_id": 42, "display_name": "Вася"},
        "style_profile_message_count": 100,
        "style_profile_updated_at": datetime.now(timezone.utc).isoformat(),
        "style_profile_version": STYLE_PROFILE_VERSION,
    }
    messages = ["ага", "ну нормально", "короче потом"]

    assert refresh_style_profile(settings, messages, 149) is False
    assert settings["prompt"] == "old prompt"

    assert refresh_style_profile(settings, messages, 150) is True
    assert settings["prompt"] != "old prompt"
    assert settings["style_profile_message_count"] == 150


def test_legacy_participant_profile_refreshes_for_new_behavior_schema():
    from AI.dialog.participant_imitation import STYLE_PROFILE_VERSION, refresh_style_profile

    settings = {
        "prompt": "legacy prompt",
        "prompt_name": "Вася",
        "imitated_user": {"user_id": 42, "display_name": "Вася"},
        "style_profile_message_count": 100,
        "style_profile_updated_at": datetime.now(timezone.utc).isoformat(),
    }

    changed = refresh_style_profile(
        settings,
        ["подари куклу", "хочу монстер хай"],
        100,
        ["Реплика собеседника: что хочешь?\nОтвет участника: подари куклу"],
        ["подари куклу"],
    )

    assert changed is True
    assert settings["style_profile_version"] == STYLE_PROFILE_VERSION
    assert "[INTERACTION EXAMPLES]" in settings["prompt"]
    assert "[RECURRING PATTERNS]" in settings["prompt"]


def test_semantic_search_uses_supported_text_embedding_model():
    from services import smart_search

    assert smart_search.EMBEDDING_MODEL_NAME != "models/text-embedding-004"
    assert smart_search.EMBEDDING_MODEL_NAME != "text-embedding-004"
    assert smart_search.EMBEDDING_OUTPUT_DIMENSION == 768


def test_semantic_search_considers_historical_candidates_beyond_last_thirty(monkeypatch):
    from services import smart_search

    smart_search.reset_embedding_runtime_state()
    monkeypatch.setattr(smart_search, "EMBEDDING_REQUESTS_PER_MINUTE", 1000)
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


def test_long_interaction_answers_are_not_cut_to_one_liner(monkeypatch):
    from AI.dialog import participant_imitation

    long_answer = "слушай ну тут всё сложно " * 20

    class FakeRepository:
        def scan(self, _chat_id, visitor, **_filters):
            visitor({"id": 2, "user_id": "42", "text": long_answer})

        def context(self, _chat_id, row_id, radius=1):
            return [
                {"id": 1, "user_id": "99", "text": "как дела?"},
                {"id": 2, "user_id": "42", "text": long_answer},
            ]

    monkeypatch.setattr(participant_imitation, "get_history_repository", lambda _path: FakeRepository())

    interactions = participant_imitation._sample_participant_interactions_sync(
        42, -1001, sample_size=5, recent_size=5
    )

    assert len(interactions) == 1
    assert len(interactions[0]) > 400


def test_history_groups_bursts_into_turns_and_keeps_long_tail():
    from AI.dialog.participant_imitation import ParticipantHistory

    entry = ParticipantHistory(chat_id="-1001", user_id=42, sample_size=50, recent_size=10)
    # Burst: three messages within a minute form one turn.
    entry.add_logged_message("короче слушай", "2026-09-01T12:00:00")
    entry.add_logged_message("я вчера туда сходил", "2026-09-01T12:00:20")
    entry.add_logged_message("вообще не то что обещали", "2026-09-01T12:00:45")
    # Separate turns later.
    entry.add_logged_message("ага", "2026-09-01T13:00:00")
    entry.add_logged_message(" ".join(["слово"] * 80), "2026-09-01T14:00:00")

    stats = entry.style_stats()

    assert sorted(stats["turn_samples"]) == [[1, 1], [11, 3], [80, 1]]
    assert stats["multi_message_turn_share"] == 33
    assert stats["burst_examples"] == ["короче слушай\nя вчера туда сходил\nвообще не то что обещали"]


def test_distinctive_lexicon_prefers_personal_words_over_common_ones():
    from collections import Counter

    from AI.dialog.style import distinctive_lexicon

    user = Counter({"и": 50, "не": 40, "чел": 12, "кринж": 9, "это": 30, "база": 6})
    baseline = Counter({"и": 5000, "не": 4000, "это": 3000, "чел": 3, "база": 2, "привет": 400})

    lexicon = distinctive_lexicon(user, baseline)

    assert lexicon[:3] and set(lexicon[:3]) == {"чел", "кринж", "база"}
    assert "и" not in lexicon
    assert "это" not in lexicon


def test_lexicon_without_baseline_drops_stopwords():
    from collections import Counter

    from AI.dialog.style import distinctive_lexicon

    lexicon = distinctive_lexicon(Counter({"и": 50, "не": 40, "чел": 5, "кринж": 4}))

    assert lexicon == ["чел", "кринж"]


def test_turn_length_target_reproduces_long_tail_and_formats_directive():
    import random

    from AI.dialog.style import format_turn_length_directive, pick_turn_length_target

    samples = [[2, 1]] * 6 + [[40, 1]] * 2 + [[90, 3]] * 2
    rng = random.Random(7)
    targets = [pick_turn_length_target(samples, "", rng) for _ in range(500)]

    long_share = sum(1 for words, _messages in targets if words >= 40) / len(targets)
    assert 0.3 < long_share < 0.5

    directive = format_turn_length_directive((90, 3))
    assert "[ДЛИНА ОТВЕТА]" in directive
    assert "около 90 слов" in directive
    assert "~3 сообщений" in directive
    assert "короткая реакция" in format_turn_length_directive((2, 1))
    assert format_turn_length_directive(None) == ""


def test_question_nudges_length_target_upward():
    import random

    from AI.dialog.style import pick_turn_length_target

    samples = [[1, 1]] * 7 + [[50, 1]] * 3
    plain = [pick_turn_length_target(samples, "ну", random.Random(i))[0] for i in range(300)]
    asked = [pick_turn_length_target(samples, "а ты что думаешь?", random.Random(i))[0] for i in range(300)]

    assert sum(asked) > sum(plain)


def test_style_examples_mirror_length_distribution():
    from AI.dialog.style import _select_style_examples, word_count

    long_message = " ".join(["длинно"] * 60)
    messages = (["ага"] * 30 + [long_message] * 30) * 2

    examples = _select_style_examples(messages)

    assert any(word_count(example) >= 60 for example in examples)
    assert any(word_count(example) <= 3 for example in examples)


def test_short_participant_reply_is_regenerated_toward_target(monkeypatch):
    from AI.dialog import generation

    chat_id = "-7007"
    generation.chat_settings[chat_id] = {"prompt_type": "user_style", "active_model": "groq"}
    calls = []

    def fake_generate_text(prompt, **kwargs):
        calls.append((prompt, kwargs))
        if len(calls) == 1:
            return "ну да"
        return " ".join(["развёрнуто"] * 45)

    monkeypatch.setattr(generation, "groq_ai", SimpleNamespace(generate_text=fake_generate_text))
    monkeypatch.setattr(generation, "save_chat_settings", lambda: None)

    async def run():
        generation._participant_length_target.set((50, 1))
        return await generation.generate_response("prompt", chat_id, "Вася", user_input="как оно?")

    try:
        result = asyncio.run(run())
    finally:
        generation.chat_settings.pop(chat_id, None)
        generation.conversation_history.pop(chat_id, None)

    assert len(calls) == 2
    assert "[LENGTH RETRY]" in calls[1][0]
    assert calls[0][1]["presence_penalty"] <= 0.2
    assert word_count_of(result) == 45


def word_count_of(text):
    from AI.dialog.style import word_count

    return word_count(text)


def test_cold_semantic_cache_stays_under_free_tier_rate_limit(monkeypatch):
    from services import smart_search

    smart_search.reset_embedding_runtime_state()
    monkeypatch.setattr(smart_search, "EMBEDDING_REQUESTS_PER_MINUTE", 80)
    embedded = []

    monkeypatch.setattr(
        smart_search,
        "_embed_query_sync",
        lambda _text: np.asarray([1.0, 0.0], dtype=np.float32),
    )

    def fake_embed_documents(messages):
        embedded.extend(messages)
        return [np.asarray([1.0, 0.0], dtype=np.float32) for _message in messages]

    monkeypatch.setattr(smart_search, "_embed_documents_sync", fake_embed_documents)
    candidates = [f"старое {index}" for index in range(200)] + ["пара: свежий ответ"]

    first = asyncio.run(smart_search.find_relevant_context("тема", candidates, top_k=3))

    # 1 query + 79 documents = the per-minute budget; no 429 and no cooldown.
    assert len(embedded) == 79
    assert embedded[0] == "пара: свежий ответ"
    assert smart_search._embedding_available()
    assert first

    # A second turn in the same minute has no budget left and degrades quietly.
    embedded.clear()
    second = asyncio.run(smart_search.find_relevant_context("тема", candidates, top_k=3))
    assert embedded == []
    assert second == []


def test_rate_limit_cooldown_follows_provider_retry_hint():
    import time

    from services import smart_search

    smart_search.reset_embedding_runtime_state()
    smart_search._disable_embeddings("429 RESOURCE_EXHAUSTED ... Please retry in 4.75s.")
    remaining = smart_search._EMBEDDING_DISABLED_UNTIL - time.monotonic()
    smart_search.reset_embedding_runtime_state()

    assert 4.0 < remaining <= 6.0


def test_quiz_poll_fits_telegram_limits_and_keeps_correct_answer():
    from AI.quiz import prepare_quiz_poll

    long_a = "а" * 150
    question = {
        "text": "вопрос " * 80,
        "options": [long_a, long_a + "б", "коротко"],
        "correct_answer": long_a + "б",
    }

    title, options, correct = prepare_quiz_poll(question, 0, 5)

    assert len(title) <= 300
    assert title.startswith("Вопрос 1/5\n")
    assert all(len(option) <= 100 for option in options)
    assert len(set(options)) == 3
    assert correct == 1
    assert options[2] == "коротко"
