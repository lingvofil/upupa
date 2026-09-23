import asyncio
from copy import deepcopy
from types import SimpleNamespace

from tests import test_smoke_imports

del test_smoke_imports

from AI import dnd, dnd_campaign, dnd_generation_resilience as providers
from AI import dnd_state_commands as commands
from AI.dnd_inventory_reliability import _audit_missing_inventory_tags
from AI.dnd_turn_contract import (
    TURN_CONTRACT, align_roll_question, install_turn_contract_guard, roll_needs_repair,
)


def session():
    return SimpleNamespace(
        chat_id=-123, mode="participants", scene_count=4, state="WAITING_ACTION",
        participants={"1": {"user_id": 1, "name": "Алина"}, "2": {"user_id": 2, "name": "Детектор"}},
        action_target_user_ids=[1], action_prompt_message_id=42,
        pending_actions={"1": {"user_id": 1, "name": "Алина", "action": "Открываю шлюз"}},
        action_deadline=None, scene_log=["Ключ застрял в шлюзе."],
        pending_roll=None, enemy_combatants={}, spotlight_decisions_since_poll=5,
        conversation=[{"role": "user", "content": "SYSTEM " + "x" * 30000},
                      {"role": "assistant", "content": "Начало"}],
    )


def test_critical_mechanics_survive_both_provider_budgets():
    game = session()
    request = "CURRENT_ACTOR_1_OPENS_GATE " + "y" * 12000 + " CURRENT_TAIL"
    contents = providers._history_contents(game, request)
    direct = "\n".join(part["text"] for row in contents for part in row["parts"])
    assert len(direct) <= providers.DND_GEMINI_INPUT_MAX_CHARS
    for budget in (7000, 12000):
        fallback = providers._fallback_prompt(game, request, max_chars=budget)
        assert len(fallback) <= budget
        for sent in (direct, fallback):
            assert TURN_CONTRACT in sent
            assert "CURRENT_ACTOR_1_OPENS_GATE" in sent
            assert "CURRENT_TAIL" in sent
            assert "Первой схватки ещё не было" in sent


def test_other_hero_question_does_not_override_pending_roll():
    game = session()
    response = ("Алина поворачивает ключ. Детектор, что ты делаешь?\n"
                "[ACTION:ROLL;TYPE:CHECK;ABILITY:STR;REASON:повернуть заевший ключ;DC:12;TARGETS:1]")
    fixed = align_roll_question(game, response)
    assert "Детектор" not in fixed
    assert "Алина поворачивает ключ" in fixed
    assert "TARGETS:1" in fixed


def test_clear_roll_keeps_question_to_its_actor():
    response = "Алина, готова повернуть ключ?\n[ACTION:ROLL;TYPE:CHECK;ABILITY:STR;REASON:повернуть ключ;DC:12;TARGETS:1]"
    assert align_roll_question(session(), response) == response
    assert not roll_needs_repair(response)


def test_roll_rejects_reason_about_another_actor_and_unknown_target():
    response = "[ACTION:ROLL;TYPE:CHECK;ABILITY:STR;REASON:Алина поворачивает ключ;DC:12;TARGETS:2]"
    assert roll_needs_repair(response, session())
    assert roll_needs_repair(response.replace("TARGETS:2", "TARGETS:999"), session())


def test_first_combat_is_scheduled_after_opening_not_during_it():
    game = session()
    game.recent_scene_types = []
    game.scene_count = 0
    assert "схватка" not in dnd.choose_next_scene_type(game)
    game.scene_count = 3
    assert "первая сюжетная схватка" in dnd.choose_next_scene_type(game)
    game.enemy_combatants = {"враг": {"hp": 8}}
    assert "первая сюжетная схватка" not in dnd.choose_next_scene_type(game)


def test_ambiguous_roll_is_repaired_from_current_declaration(monkeypatch):
    game = session()

    async def original(_session, _prompt):
        return "Ключ заедает. Детектор, что ты делаешь?\n[ACTION:ROLL;DC:12]"

    async def auxiliary(_session, prompt, **kwargs):
        assert "Алина открывает шлюз" in prompt
        return "[ACTION:ROLL;TYPE:CHECK;ABILITY:STR;REASON:повернуть заевший ключ;DC:12;TARGETS:1]"

    module = SimpleNamespace(generate_session_response=original)
    monkeypatch.setattr(providers, "generate_auxiliary_text", auxiliary)
    install_turn_contract_guard(module)
    response = asyncio.run(module.generate_session_response(game, "Алина открывает шлюз"))
    assert "TARGETS:1" in response
    assert "Детектор" not in response
    assert not roll_needs_repair(response)


def test_failed_roll_repair_does_not_make_an_arbitrary_check(monkeypatch):
    async def original(_session, _prompt):
        return "Ключ заедает.\n[ACTION:ROLL;REASON:проверка по ситуации;TARGETS:1]"

    async def unavailable(*args, **kwargs):
        return None

    module = SimpleNamespace(generate_session_response=original)
    monkeypatch.setattr(providers, "generate_auxiliary_text", unavailable)
    install_turn_contract_guard(module)
    response = asyncio.run(module.generate_session_response(session(), "Открываю дверь"))
    assert "ACTION:ROLL" not in response
    assert "[ACTION:INPUT;TARGETS:1]" in response
    assert "Уточни" in response


def test_inventory_audit_recovers_second_item_without_duplicate_first(monkeypatch):
    game = session()
    response = ("Алина берёт ключ и ложку.\n"
                "[ITEM:ADD;PLAYER:1;NAME:ключ;KIND:item]\n[ACTION:INPUT]")

    async def auxiliary(*args, **kwargs):
        return ("[ITEM:ADD;PLAYER:1;NAME:ключ;KIND:item]"
                "[ITEM:ADD;PLAYER:1;NAME:ложка;KIND:item]"
                "[ITEM:ADD;PLAYER:1;NAME:ложка;KIND:item]")

    monkeypatch.setattr(providers, "generate_auxiliary_text", auxiliary)
    result = asyncio.run(_audit_missing_inventory_tags(dnd, dnd_campaign, game, "Беру вещи", response))
    assert result.count("NAME:ключ") == 1
    assert result.count("NAME:ложка") == 1


def test_party_command_shows_pending_personal_action_without_reset(monkeypatch):
    game = session()
    before = deepcopy(vars(game))
    monkeypatch.setattr(commands, "_active_session", lambda *args: game)
    text = commands.render_party(dnd, game.chat_id)
    assert commands.command_kind("днд партия") == "party"
    assert "Личный ход: Алина" in text
    assert "Ключ застрял" in text
    assert "Уже ответили: Алина" in text
    assert vars(game) == before


def test_party_command_shows_roll_actor_and_enemy_stats(monkeypatch):
    game = session()
    game.state = "WAITING_ROLL"
    game.pending_roll = {"type": "ATTACK", "target_user_ids": [2], "reason": "ударить разбойника", "dc": 12, "ability": "STR"}
    game.enemy_combatants = {"разбойник": {"name": "Разбойник", "hp": 8, "max_hp": 16, "ac": 12, "status": "alive"}}
    monkeypatch.setattr(commands, "_active_session", lambda *args: game)
    text = commands.render_party(dnd, game.chat_id)
    assert "Бросок: Детектор" in text
    assert "ударить разбойника" in text
    assert "8/16" in text and "КБ 12" in text
    assert "кидаю" in text


def test_party_command_reposts_same_poll_without_changing_votes(monkeypatch):
    game = session()
    game.state = "WAITING_POLL"
    game.pending_poll = {"message_id": 90, "poll_chat_id": game.chat_id,
                         "options": ["Через лес", "По дороге"], "votes": {"1": 0}}
    before = deepcopy(game.pending_poll)
    forwarded, answers = [], []

    async def forward(**kwargs):
        forwarded.append(kwargs)

    async def answer(text, **kwargs):
        answers.append(text)

    async def handler(*args):
        raise AssertionError("state query must not become a player action")

    event = SimpleNamespace(text="днд партия", chat=SimpleNamespace(id=game.chat_id),
                            from_user=SimpleNamespace(id=1, first_name="Алина"),
                            answer=answer, bot=SimpleNamespace(forward_message=forward))
    monkeypatch.setattr(dnd, "dnd_sessions", {game.chat_id: game})
    monkeypatch.setattr(commands, "_active_session", lambda *args: game)
    asyncio.run(commands.DndStateCommandMiddleware()(handler, event, {}))
    assert "Через лес" in answers[0]
    assert forwarded == [{"chat_id": game.chat_id, "from_chat_id": game.chat_id, "message_id": 90}]
    assert game.pending_poll == before


def test_reply_to_party_status_is_accepted_for_current_actor(monkeypatch):
    from AI.dnd_any_bot_reply import is_any_bot_action_reply

    game = session()
    event = SimpleNamespace(chat=SimpleNamespace(id=game.chat_id),
                            from_user=SimpleNamespace(id=1), text="Открываю шлюз", bot=SimpleNamespace(id=55),
                            reply_to_message=SimpleNamespace(message_id=999, from_user=SimpleNamespace(id=55, is_bot=True)))
    monkeypatch.setattr(dnd, "dnd_sessions", {game.chat_id: game})
    assert is_any_bot_action_reply(event)
    event.from_user.id = 2
    assert not is_any_bot_action_reply(event)


def test_personal_turn_resolves_immediately_without_timer(monkeypatch):
    game = session()
    game.pending_actions = {}
    resolved = []

    async def finalize(bot, chat_id, prompt_id):
        resolved.append((chat_id, prompt_id, deepcopy(game.pending_actions)))

    async def answer(*args, **kwargs):
        raise AssertionError("personal turn must not announce a group timer")

    event = SimpleNamespace(chat=SimpleNamespace(id=game.chat_id), from_user=SimpleNamespace(id=1, first_name="Алина"),
                            text="Поворачиваю ключ", bot=object(), answer=answer)
    monkeypatch.setattr(dnd, "dnd_sessions", {game.chat_id: game})
    monkeypatch.setattr(dnd, "persist_dnd_sessions", lambda: None)
    monkeypatch.setattr(dnd, "finalize_group_actions", finalize)
    asyncio.run(dnd.handle_free_action(event))
    assert resolved[0][:2] == (game.chat_id, 42)
    assert resolved[0][2]["1"]["action"] == "Поворачиваю ключ"
    assert game.action_deadline is None
    assert dnd._action_prompt_text(game) == "🎭 Ход: Алина."
