import asyncio
from types import SimpleNamespace

from tests import test_smoke_imports

del test_smoke_imports

from AI.dnd_player_agency import (
    DND_PLAYER_AGENCY_MARKER,
    _with_player_agency,
    configure_dnd_player_agency,
)
from AI.dnd_target_mentions import configure_dnd_target_mentions


def _session(*, mode="participants"):
    return SimpleNamespace(
        chat_id=-100991,
        mode=mode,
        participants={
            "1": {"user_id": 1, "name": "Алиса"},
            "2": {"user_id": 2, "name": "Боря"},
        },
    )


def test_participant_prompt_forbids_deciding_for_another_player():
    fake_dnd = SimpleNamespace(_is_participant_mode=lambda session: session.mode == "participants")

    prompt = _with_player_agency(
        fake_dnd,
        _session(),
        "Игрок Алиса заявил: Боря использует лечилку.",
    )

    assert DND_PLAYER_AGENCY_MARKER in prompt
    assert "ТОЛЬКО его собственного персонажа" in prompt
    assert "Боря использует лечилку" in prompt
    assert "НЕ является действием Бори" in prompt
    assert "ACTION:INPUT;TARGETS:<его ID>" in prompt
    assert "решение о её использовании принимает только её владелец" in prompt
    assert "не является согласием владельца" in prompt


def test_abstract_mode_is_unchanged():
    fake_dnd = SimpleNamespace(_is_participant_mode=lambda session: session.mode == "participants")

    assert _with_player_agency(fake_dnd, _session(mode="abstract"), "Продолжай") == "Продолжай"


def test_agency_marker_is_not_duplicated():
    fake_dnd = SimpleNamespace(_is_participant_mode=lambda session: session.mode == "participants")
    session = _session()

    first = _with_player_agency(fake_dnd, session, "Продолжай")
    second = _with_player_agency(fake_dnd, session, first)

    assert second.count(DND_PLAYER_AGENCY_MARKER) == 1


def test_generation_wrapper_is_idempotent_and_passes_guarded_prompt():
    prompts = []

    async def original_generate(session, prompt):
        prompts.append((session, prompt))
        return "ok"

    fake_dnd = SimpleNamespace(
        generate_session_response=original_generate,
        _is_participant_mode=lambda session: session.mode == "participants",
    )
    session = _session()

    configure_dnd_player_agency(fake_dnd)
    configure_dnd_player_agency(fake_dnd)
    result = asyncio.run(fake_dnd.generate_session_response(session, "Ход игроков"))

    assert result == "ok"
    assert len(prompts) == 1
    assert DND_PLAYER_AGENCY_MARKER in prompts[0][1]
    assert prompts[0][1].count(DND_PLAYER_AGENCY_MARKER) == 1


def test_target_mentions_startup_hook_installs_agency_guard():
    prompts = []

    async def original_generate(session, prompt):
        prompts.append(prompt)
        return "ok"

    async def original_open(bot, chat_id, target_user_ids=None):
        return None

    async def original_parse(bot, chat_id, text_response):
        return None

    fake_dnd = SimpleNamespace(
        generate_session_response=original_generate,
        open_action_window=original_open,
        parse_and_execute_turn=original_parse,
        dnd_sessions={},
        _is_participant_mode=lambda session: session.mode == "participants",
    )

    configure_dnd_target_mentions(fake_dnd)
    result = asyncio.run(fake_dnd.generate_session_response(_session(), "Резолв хода"))

    assert result == "ok"
    assert fake_dnd._upupa_dnd_player_agency_configured is True
    assert DND_PLAYER_AGENCY_MARKER in prompts[0]
