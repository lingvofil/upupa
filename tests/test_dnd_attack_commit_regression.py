import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tests import test_smoke_imports  # noqa: F401
from AI import dnd, dnd_combat, dnd_player_combat, dnd_cinematic_combat
from AI import dnd_conditions, dnd_special_moves


@pytest.mark.parametrize("kind", ["ATTACK", "CINEMATIC_ATTACK"])
@pytest.mark.parametrize("natural", [1, 18])
def test_actual_attack_handler_commits_one_shot_resources_before_failed_narration(monkeypatch, kind, natural):
    game = dnd.GameSession.from_record({"chat_id": -889, "active_model": "groq", "mode": "participants",
        "state": "WAITING_ROLL", "participants": {"1": {"user_id": 1, "name": "Игрок"}}})
    game.character_sheets = {"1": {"stats": {"STR": 16, "DEX": 14}, "hp": 14, "max_hp": 14, "ac": 13, "status": "alive"}}
    game.character_profiles = {"1": {"special": "Рывок"}}
    game.special_move_charges = {"1": 1}
    game.special_move_pending_user_id = 1
    game.conditions = {"1": [{"name": "Усталость", "effect": "COMBAT_DISADVANTAGE", "uses_remaining": 1}]}
    key, enemy = dnd_player_combat._register_enemy(game, name="Огр", power="HIGH", hp=28, ac=14)
    game.pending_roll = {"type": kind, "target_user_ids": [1], "mode": "NORMAL", "dc": 16,
        "attack": {"enemy_key": key, "weapon": "длинный меч", "style": "MELEE"},
        "cinematic": {"enemy_key": key, "ability": "STR", "method": "обрушить люстру"},
        "condition_uses_pending": [{"player": "1", "effect": "COMBAT_DISADVANTAGE"}]}
    consumed = []

    def commit(session, roll, actor):
        consumed.append(actor)
        dnd_special_moves._commit_completed_special_roll(session, roll, actor)
        dnd_conditions._consume_pending_uses(session, roll)

    monkeypatch.setattr(dnd, "_roll_commit_hooks", [commit])
    rolled = []
    saved = []
    module = SimpleNamespace(_can_user_act=lambda *args: True,
        _roll_d20=lambda mode: (rolled.append(mode) or [natural], natural),
        _commit_roll_transaction=dnd._commit_roll_transaction,
        with_scene_direction=lambda session, prompt: prompt,
        persist_dnd_sessions=lambda: saved.append((game.special_move_charges["1"], list(game.conditions["1"]), bool(game.pending_generation_request))),
        generate_session_response=AsyncMock(side_effect=RuntimeError("provider unavailable")),
        parse_and_execute_turn=AsyncMock(), open_action_window=AsyncMock())
    message = SimpleNamespace(from_user=SimpleNamespace(id=1), chat=SimpleNamespace(id=game.chat_id), bot=object(), answer=AsyncMock())
    if kind == "ATTACK":
        handler = lambda: dnd_player_combat._resolve_player_attack_roll(module, dnd_combat, message, game)
    else:
        handler = lambda: dnd_cinematic_combat._resolve_cinematic_roll(module, dnd_combat, dnd_player_combat, message, game)
    asyncio.run(handler())
    hp = enemy["hp"]
    assert saved == [(0, [], True)]
    assert game.pending_generation_request["kind"] == "ROLL_CONTINUATION"
    assert game.state == "RESOLVING" and game.pending_roll is None
    module.open_action_window.assert_not_awaited()
    asyncio.run(handler())
    assert consumed == [1] and len(rolled) == 1 and enemy["hp"] == hp
    module.generate_session_response.assert_awaited_once()


def test_roll_commit_same_object_is_idempotent(monkeypatch):
    calls = []
    monkeypatch.setattr(dnd, "_roll_commit_hooks", [lambda *args: calls.append(1)])
    pending = {"type": "CHECK"}
    dnd._commit_roll_transaction(object(), pending, 1)
    dnd._commit_roll_transaction(object(), pending, 1)
    assert calls == [1]
