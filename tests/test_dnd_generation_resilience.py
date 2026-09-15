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


def test_auxiliary_generation_never_mutates_conversation(monkeypatch):
    session = _session()
    before = list(session.conversation)

    monkeypatch.setattr(resilience, "_circuit_is_open", lambda: False)
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
    monkeypatch.setattr(resilience, "_circuit_is_open", lambda: True)

    assert asyncio.run(resilience.generate_auxiliary_text(session, "audit")) is None
