"""DnD-style action menu, archetype abilities, Help and Dodge."""
import asyncio
import random
from types import SimpleNamespace

import pytest

from tests import test_smoke_imports  # noqa: F401
from tests.test_dnd_menu import Callback, Message, button, latest_markup, make_service, session

from AI import dnd_archetype_abilities as abilities
from AI import dnd_combat as combat
from AI import dnd_item_actions as item_actions


def labels(markup):
    return [item.text for row in markup.inline_keyboard for item in row]


def hero_session(**extra):
    base = dict(
        participants={"1": {"user_id": 1, "name": "Маша", "active": True},
                      "2": {"user_id": 2, "name": "Денис", "active": True}},
        character_profiles={"1": {"archetype": "мудрость"}, "2": {}},
        character_sheets={
            "1": {"hp": 6, "max_hp": 18, "ac": 13, "status": "alive",
                  "stats": {"STR": 8, "DEX": 12, "CON": 13, "INT": 10, "WIS": 16, "CHA": 14}},
            "2": {"hp": 10, "max_hp": 18, "ac": 12, "status": "alive",
                  "stats": {"STR": 10, "DEX": 16, "CON": 14, "INT": 13, "WIS": 12, "CHA": 8}},
        },
        item_boosts={}, hero_progress={},
    )
    base.update(extra)
    return SimpleNamespace(**base)


# ----------------------------------------------------------------- mechanics

def test_archetype_is_chosen_role_or_inferred_from_highest_base_stat():
    current = hero_session()
    assert abilities.archetype_of(current, 1) == "мудрость"
    assert abilities.archetype_of(current, 2) == "ловкость"
    assert abilities.ability_for(current, 2)["title"] == "Ловкий манёвр"


def test_healing_ability_spends_charges_and_respects_max_hp():
    current = hero_session()
    rng = SimpleNamespace(randint=lambda low, high: 5)
    text = abilities.use_ability(current, 1, 2, rng=rng)
    # 1d8 (5) + WIS mod (+3) = 8 -> 10 + 8 = 18
    assert current.character_sheets["2"]["hp"] == 18 and "Осталось 0/1" in text
    with pytest.raises(abilities.AbilityError):
        abilities.use_ability(current, 1, 1, rng=rng)


def test_charges_grow_with_level():
    current = hero_session(hero_progress={"1": {"xp": 1200, "level": 5}})
    assert abilities.charges(current, 1) == (2, 2)


def test_boost_abilities_and_help_share_one_prepared_advantage_slot():
    current = hero_session(character_profiles={"1": {"archetype": "харизма"}, "2": {}})
    abilities.use_ability(current, 1, 2)
    assert current.item_boosts["2"] == {"domain": "ANY", "source": "Вдохновение"}
    with pytest.raises(abilities.AbilityError):
        abilities.prepare_help(current, 1, 2)
    with pytest.raises(abilities.AbilityError):
        abilities.use_ability(current, 1, 1)  # inspiration targets allies only


def test_any_domain_boost_upgrades_the_next_roll_once():
    current = SimpleNamespace(item_boosts={"2": {"domain": "ANY", "source": "помощь: Маша"}}, item_world_facts=[])
    guarded, source = item_actions.apply_item_boost(
        current, "Замок трещит. [ACTION:ROLL;TYPE:CHECK;ABILITY:STR;SKILL:Атлетика;REASON:выбить дверь;DC:12;TARGETS:2]")
    assert "MODE:ADVANTAGE" in guarded and source == "помощь: Маша"
    assert "2" not in current.item_boosts


def test_dodge_imposes_disadvantage_on_one_enemy_attack(monkeypatch):
    current = hero_session(dodge_flags={"1": True}, enemy_combatants={})
    current.participants = {"1": {"user_id": 1, "name": "Маша", "active": True}}
    current.character_sheets["1"]["ac"] = 15
    rolls = iter([18, 3])
    monkeypatch.setattr(random, "randint", lambda low, high: next(rolls))
    summary, _prompt, _dead = combat._resolve_enemy_attack(current, {"target_user_id": 1, "power": "LOW", "reason": "удар"})
    assert "уклонение: 18/3" in summary and "мимо" in summary
    assert current.dodge_flags == {}


# ----------------------------------------------------------------- menu

def test_turn_cards_show_table_actions_and_one_navigation_row():
    async def run():
        current, _dnd, bot, service = make_service()
        await service.command(Message(bot, current))
        shared = labels(latest_markup(bot))
        assert {"⚔️ Атака", "🎯 Навык", "🤝 Помочь", "🛡 Уклонение", "✨ Умение", "🎒 Предмет", "✍️ Свой ход"} <= set(shared)
        # One navigation row; the remaining sections live under «Ещё».
        assert {"👤 Герой", "📍 Сцена", "☰ Ещё"} <= set(shared) and "👹 Враги" not in shared
        await service.show(bot, current, 1, "overview")
        personal = labels(latest_markup(bot))
        assert "🛡 Уклонение" in personal
        assert any(text.startswith("💪 Богатырский натиск") for text in personal)
    asyncio.run(run())


def test_skill_choice_prefixes_the_free_text_request():
    async def run():
        requests = []

        async def execute(*args):
            requests.append(args[3])

        current, _dnd, bot, service = make_service(execute=execute)
        await service.show(bot, current, 1, "overview")
        await service.callback(Callback(bot, current, button(latest_markup(bot), "🎯 Навык")))
        skill = next(text for text in labels(latest_markup(bot)) if text.startswith("Скрытность"))
        assert skill == "Скрытность +2"
        await service.callback(Callback(bot, current, button(latest_markup(bot), skill)))
        prompt = bot.sent[-1]
        assert "Скрытность (Ловкость +2)" in prompt.text
        await service.handle_message(Message(bot, current, text="крадусь вдоль стены", reply=prompt.message_id))
        assert requests[-1]["kind"] == "FREE_TEXT"
        assert requests[-1]["text"] == "[Навык: Скрытность (Ловкость)] крадусь вдоль стены"
    asyncio.run(run())


def test_help_flow_carries_effect_and_ability_confirms_with_target():
    async def run():
        requests = []

        async def execute(*args):
            requests.append(args[3])

        current, _dnd, bot, service = make_service(execute=execute)
        current.action_target_user_ids = []
        await service.show(bot, current, 1, "overview")
        await service.callback(Callback(bot, current, button(latest_markup(bot), "🤝 Помочь")))
        await service.callback(Callback(bot, current, button(latest_markup(bot), "🤝 Денис")))
        prompt = bot.sent[-1]
        await service.handle_message(Message(bot, current, text="подсаживаю на стену", reply=prompt.message_id))
        assert requests[-1]["text"].startswith("[Помощь: Денис] ")
        assert requests[-1]["effect"] == {"type": "help", "target_id": 2}

        await service.show(bot, current, 1, "overview")
        ability = next(text for text in labels(latest_markup(bot)) if text.startswith("💪"))
        await service.callback(Callback(bot, current, button(latest_markup(bot), ability)))
        await service.callback(Callback(bot, current, button(latest_markup(bot), "✅ Применить")))
        await service.callback(Callback(bot, current, button(latest_markup(bot), "✅ Подтвердить")))
        assert requests[-1]["kind"] == "ABILITY" and requests[-1]["target_id"] == 1
    asyncio.run(run())


# ----------------------------------------------------------------- runtime

def test_local_runtime_applies_help_before_submitting_and_dodge_submits_its_own_turn(monkeypatch):
    from AI import dnd_local_runtime as local_runtime

    seen = []

    class Dnd:
        dnd_sessions = {}

        def persist_dnd_sessions(self):
            pass

        def _can_user_act(self, *_args):
            return True

        async def handle_free_action(self, message):
            seen.append((message.text, dict(current.item_boosts), dict(current.dodge_flags)))

    current = hero_session(chat_id=-5, state="WAITING_ACTION", paused=False, action_target_user_ids=[],
                           pending_generation_request=None, pending_generated_result=None,
                           enemy_combatants={"g": {"name": "Страж", "hp": 5}})
    dnd = Dnd()
    dnd.dnd_sessions[-5] = current
    runtime = local_runtime.LocalRuntime.__new__(local_runtime.LocalRuntime)
    runtime.dnd = dnd

    async def no_finalize(*_args):
        return None

    runtime.maybe_finalize = no_finalize
    identity = {"campaign_id": "c", "turn_id": "t", "phase": "WAITING_ACTION"}
    monkeypatch.setattr(local_runtime, "identity_for", lambda _session: identity)
    monkeypatch.setattr(local_runtime, "_ensure", lambda _session: None)

    class Bot:
        def __init__(self):
            self.sent = []

        async def send_message(self, chat_id, text, **_kwargs):
            self.sent.append(text)

    bot = Bot()
    asyncio.run(runtime.execute(bot, -5, 1, {**identity, "kind": "FREE_TEXT", "text": "[Помощь: Денис] держу верёвку",
                                             "effect": {"type": "help", "target_id": 2}}))
    assert seen[-1][0] == "[Помощь: Денис] держу верёвку"
    assert seen[-1][1]["2"]["domain"] == "ANY"
    asyncio.run(runtime.execute(bot, -5, 2, {**identity, "kind": "DODGE"}))
    assert seen[-1][0].startswith("[Оборона]") and seen[-1][2] == {"2": True}
