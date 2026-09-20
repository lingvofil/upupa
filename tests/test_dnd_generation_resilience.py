import asyncio
from types import SimpleNamespace

from AI import dnd_generation_resilience as resilience


def _session():
    return SimpleNamespace(
        chat_id=-1001,
        active_model="gemini",
        conversation=[
            {"role": "user", "content": "system prompt"},
            {"role": "assistant", "content": "Погнали."},
        ],
    )


def test_main_generation_falls_back_to_groq_after_fast_gemini_failure(monkeypatch):
    session = _session()

    def fail_gemini(*_args, **_kwargs):
        raise RuntimeError("503 Service Unavailable")

    monkeypatch.setattr(resilience, "_run_gemini_sync", fail_gemini)
    monkeypatch.setattr(resilience, "_run_groq_sync", lambda *_args, **_kwargs: "fallback ok")

    result = asyncio.run(resilience._generate_main_text(session, "продолжай"))

    assert result == "fallback ok"
    assert session._dnd_last_generation_provider == "groq"


def test_main_generation_retries_groq_with_compact_prompt_after_413(monkeypatch):
    session = _session()
    calls = []

    def fail_gemini(*_args, **_kwargs):
        raise RuntimeError("503 Service Unavailable")

    def groq_with_first_request_too_large(
        _session,
        _prompt,
        *,
        max_prompt_chars,
        max_tokens,
    ):
        calls.append((max_prompt_chars, max_tokens))
        if len(calls) == 1:
            error = RuntimeError("413 Request too large for tokens per minute (TPM)")
            error.status_code = 413
            raise error
        return "compact fallback ok"

    monkeypatch.setattr(resilience, "_run_gemini_sync", fail_gemini)
    monkeypatch.setattr(resilience, "_run_groq_sync", groq_with_first_request_too_large)

    result = asyncio.run(resilience._generate_main_text(session, "продолжай"))

    assert result == "compact fallback ok"
    assert calls == [
        (resilience.DND_FALLBACK_PROMPT_MAX_CHARS, resilience.DND_GROQ_FALLBACK_MAX_TOKENS),
        (
            resilience.DND_FALLBACK_RETRY_PROMPT_MAX_CHARS,
            resilience.DND_GROQ_FALLBACK_RETRY_MAX_TOKENS,
        ),
    ]


def test_fallback_prompt_is_strictly_bounded_and_keeps_latest_request():
    session = _session()
    session.conversation = [
        {"role": "user", "content": "СИСТЕМА " + "я" * 10_000},
        {"role": "assistant", "content": "старая сцена " + "э" * 10_000},
    ]

    prompt = resilience._fallback_prompt(session, "ПОСЛЕДНИЙ ХОД", max_chars=7_000)

    assert len(prompt) <= 7_000
    assert "ПОСЛЕДНИЙ ХОД" in prompt
    assert "середина истории сокращена" in prompt


def test_compact_fallback_keeps_group_actions_and_current_state_from_same_request():
    session = _session()
    session.conversation = [
        {"role": "user", "content": "СИСТЕМНЫЕ ПРАВИЛА " + "с" * 12_000},
        {"role": "assistant", "content": "ПРЕДЫДУЩАЯ СЦЕНА " + "п" * 8_000},
    ]
    current = (
        "Игроки заявили действия одновременно:\n"
        "- Six7ape: ACTIONS_SENTINEL машет руками\n"
        "- Детектор: надевает волшебный плащ\n"
        "- M&M: лезет вперёд\n"
        + "контекст " * 1_500
        + "\nБОЕВОЕ СОСТОЯНИЕ: CURRENT_STATE_SENTINEL"
    )

    prompt = resilience._fallback_prompt(session, current, max_chars=7_000)

    assert len(prompt) <= 7_000
    assert "CURRENT REQUEST" in prompt
    assert "ACTIONS_SENTINEL" in prompt
    assert "CURRENT_STATE_SENTINEL" in prompt
    assert "Не вводи нового врага" in prompt
    assert "РЕЖИССЁР СЦЕНЫ задаёт подачу" in prompt


def test_configured_generator_appends_one_canonical_exchange(monkeypatch):
    persisted = []

    async def original_generate(_session, _prompt):
        raise AssertionError("Gemini sessions must use the bounded generator")

    async def fake_main(_session, _prompt):
        return "ответ мастера"

    dnd = SimpleNamespace(
        generate_session_response=original_generate,
        dnd_sessions={},
        persist_dnd_sessions=lambda: persisted.append(True),
    )
    monkeypatch.setattr(resilience, "_generate_main_text", fake_main)
    resilience.configure_dnd_generation_resilience(dnd)

    session = _session()
    dnd.dnd_sessions[session.chat_id] = session
    result = asyncio.run(dnd.generate_session_response(session, "ход игроков"))

    assert result == "ответ мастера"
    assert session.conversation[-2:] == [
        {"role": "user", "content": "ход игроков"},
        {"role": "assistant", "content": "ответ мастера"},
    ]
    assert persisted == [True]


def test_gemini_circuit_is_isolated_per_chat(monkeypatch):
    monkeypatch.setattr(resilience, "_gemini_circuit_until", {})
    monkeypatch.setattr(resilience.time, "monotonic", lambda: 100.0)

    resilience._open_circuit(-1001)

    assert resilience._circuit_is_open(-1001) is True
    assert resilience._circuit_is_open(-2002) is False
    resilience._close_circuit(-1001)
    assert resilience._circuit_is_open(-1001) is False


def test_main_generation_marks_gemini_provider(monkeypatch):
    session = _session()
    monkeypatch.setattr(resilience, "_run_gemini_sync", lambda *_args, **_kwargs: "gemini ok")

    result = asyncio.run(resilience._generate_main_text(session, "продолжай"))

    assert result == "gemini ok"
    assert session._dnd_last_generation_provider == "gemini"


def test_auxiliary_generation_never_mutates_conversation(monkeypatch):
    session = _session()
    before = list(session.conversation)

    monkeypatch.setattr(resilience, "_circuit_is_open", lambda _chat_id: False)
    monkeypatch.setattr(
        resilience,
        "_run_gemini_sync",
        lambda *_args, **_kwargs: "[ITEM:ADD;PLAYER:1;NAME:ложка;KIND:item]",
    )

    result = asyncio.run(resilience.generate_auxiliary_text(session, "audit"))

    assert result.startswith("[ITEM:ADD")
    assert session.conversation == before


def test_auxiliary_generation_is_skipped_while_circuit_is_open(monkeypatch):
    session = _session()
    monkeypatch.setattr(resilience, "_circuit_is_open", lambda _chat_id: True)

    assert asyncio.run(resilience.generate_auxiliary_text(session, "audit")) is None
