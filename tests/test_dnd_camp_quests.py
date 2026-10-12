"""Camp (short rest), side quests and the party treasury."""
import asyncio
import random
from types import SimpleNamespace

import pytest

from tests import test_smoke_imports  # noqa: F401
from tests.test_dnd_menu import Callback, Message, button, latest_markup, make_service
from tests.test_dnd_table_actions import hero_session, labels

from AI import dnd_camp as camp
from AI import dnd_quests as quests


# ----------------------------------------------------------------- camp

def calm(**extra):
    values = dict(state="WAITING_ACTION", scene_count=6, enemy_combatants={}, archetype_ability_uses={},
                  dodge_flags={}, camp_last_scene={})
    values.update(extra)
    return hero_session(**values)


def test_camp_needs_a_calm_scene_and_respects_cooldown():
    current = calm(enemy_combatants={"g": {"hp": 4}})
    assert not camp.can_camp(current, 1)
    current.enemy_combatants["g"]["hp"] = 0
    assert camp.can_camp(current, 1)
    camp.apply_camp(current, 1, "prepare")
    assert not camp.can_camp(current, 1) and camp.scenes_until_camp(current, 1) == camp.CAMP_COOLDOWN_SCENES
    current.scene_count += camp.CAMP_COOLDOWN_SCENES
    assert camp.can_camp(current, 1)


def test_camp_benefits_are_applied_by_code_and_capped():
    current = calm()
    notice, declaration = camp.apply_camp(current, 1, "rest", rng=SimpleNamespace(randint=lambda a, b: 6))
    # 1d6 (6) + CON 13 (+1) = 7: 6 -> 13 of 18
    assert current.character_sheets["1"]["hp"] == 13 and "+7 HP" in notice and declaration.startswith("[Привал]")
    other = calm(archetype_ability_uses={"2": 1})
    camp.apply_camp(other, 2, "restore")
    assert other.archetype_ability_uses["2"] == 0
    healthy = calm()
    healthy.character_sheets["2"]["hp"] = 18
    assert camp.options(healthy, 2)["rest"] == "здоров"
    with pytest.raises(camp.CampError):
        camp.apply_camp(healthy, 2, "rest")
    assert camp.options(healthy, 2)["restore"] == "заряды полны"


def test_camp_button_and_view_follow_scene_and_hero_state():
    async def run():
        current, _dnd, bot, service = make_service(calm(participants={"1": {"user_id": 1, "name": "Маша"},
                                                                      "2": {"user_id": 2, "name": "Денис"}},
                                                        chat_id=-10044, campaign_id="c", turn_id="t", state_revision=1,
                                                        mode="participants", paused=False, action_prompt_message_id=5,
                                                        action_target_user_ids=[], action_deadline=None, pending_actions={},
                                                        pending_roll=None, scene_log=[""], inventories={}, scene_objects={},
                                                        scene_clocks={}, player_positions={}, event_journal=[], action_records=[]))
        await service.command(Message(bot, current))
        assert "⛺ Привал" in labels(latest_markup(bot))
        await service.callback(Callback(bot, current, button(latest_markup(bot), "⛺ Привал"),
                                        message_id=current.menu_ui_state["cards"]["party"]))
        view = labels(latest_markup(bot))
        assert {"🩹 Перевязать раны", "🛠 Подготовиться", "🔥 Байка у костра"} <= set(view)
        assert "🔄 Восстановить силы" not in view  # charges are full
        current.enemy_combatants = {"g": {"name": "Гоблин", "hp": 5, "max_hp": 5, "ac": 12}}
        await service.show(bot, current, 1, "overview")
        assert "⛺ Привал" not in labels(latest_markup(bot))
    asyncio.run(run())


def test_runtime_camp_applies_benefit_then_submits_declaration(monkeypatch):
    from AI import dnd_local_runtime as local_runtime

    seen = []
    current = calm(chat_id=-5, paused=False, action_target_user_ids=[], pending_generation_request=None,
                   pending_generated_result=None)

    class Dnd:
        dnd_sessions = {-5: current}

        def persist_dnd_sessions(self):
            pass

        def _can_user_act(self, *_args):
            return True

        async def handle_free_action(self, message):
            seen.append(message.text)

    runtime = local_runtime.LocalRuntime.__new__(local_runtime.LocalRuntime)
    runtime.dnd = Dnd()

    async def no_finalize(*_args):
        return None

    runtime.maybe_finalize = no_finalize
    identity = {"campaign_id": "c", "turn_id": "t", "phase": "WAITING_ACTION"}
    monkeypatch.setattr(local_runtime, "identity_for", lambda _session: identity)
    monkeypatch.setattr(local_runtime, "_ensure", lambda _session: None)
    monkeypatch.setattr(camp.random, "randint", lambda a, b: 3)
    sent = []
    bot = SimpleNamespace(send_message=lambda chat_id, text, **_k: asyncio.sleep(0, sent.append(text)))
    asyncio.run(runtime.execute(bot, -5, 1, {**identity, "kind": "CAMP", "activity": "rest"}))
    assert current.character_sheets["1"]["hp"] == 10 and seen[-1].startswith("[Привал] Маша перевязывает")
    asyncio.run(runtime.execute(bot, -5, 2, {**identity, "kind": "FREE_TEXT", "text": "[Привал: байка у костра] про дракона",
                                             "effect": {"type": "camp", "activity": "story"}}))
    assert seen[-1] == "[Привал: байка у костра] про дракона" and current.camp_last_scene["2"] == 6
    asyncio.run(runtime.execute(bot, -5, 2, {**identity, "kind": "CAMP", "activity": "prepare"}))
    assert "Следующий привал" in sent[-1] and len(seen) == 2


# ----------------------------------------------------------------- quests

def party():
    return SimpleNamespace(chat_id=-7, scene_count=4, mode="participants",
                           participants={"1": {"name": "Маша", "active": True}, "2": {"name": "Денис", "active": True}},
                           character_sheets={"1": {"hp": 10, "max_hp": 10, "status": "alive"},
                                             "2": {"hp": 0, "max_hp": 10, "status": "dead"}},
                           hero_progress={}, quests={}, party_gold=0)


def apply(current, text):
    return quests.apply_quest_metadata(current, text, text, [])


def test_quest_lifecycle_rewards_living_heroes_and_strips_tags():
    current = party()
    cleaned, notices = apply(current, "Капитан просит помощи. [QUEST:ADD;ID:Матрос;TITLE:Найти матроса;GOAL:вернуть Билли на корабль;FROM:Капитан Ржа]")
    assert "QUEST" not in cleaned and notices == ["📜 Новое поручение: «Найти матроса»"]
    apply(current, "[QUEST:STEP;ID:матрос;TEXT:Билли видели в таверне]")
    assert current.quests["матрос"]["steps"] == ["Билли видели в таверне"]
    _, notices = apply(current, "[QUEST:DONE;ID:матрос;EVIDENCE:Билли вернулся на борт]")
    assert "✅ Поручение выполнено" in notices[0] and "+60" in notices[0]
    assert current.hero_progress["1"]["xp"] == quests.QUEST_XP and "2" not in current.hero_progress
    _, notices = apply(current, "[QUEST:DONE;ID:матрос;EVIDENCE:ещё раз]")
    assert notices == []  # closed quests are not paid twice


def test_quest_rules_need_facts_and_limit_open_quests():
    current = party()
    apply(current, "[QUEST:ADD;ID:a;TITLE:Раз][QUEST:ADD;ID:b;TITLE:Два][QUEST:ADD;ID:c;TITLE:Три][QUEST:ADD;ID:d;TITLE:Четыре]")
    assert len(quests.open_quests(current)) == quests.MAX_OPEN and "d" not in current.quests
    apply(current, "[QUEST:DONE;ID:a]")
    assert current.quests["a"]["status"] == "open"  # no evidence, no completion
    apply(current, "[QUEST:FAIL;ID:b;REASON:заказчик погиб]")
    assert current.quests["b"]["status"] == "failed"
    report = quests.report_section(current)
    assert "Провалено: «Два»" in report and "Осталось открытым" in report


def test_treasury_never_goes_negative_and_shows_in_context():
    current = party()
    _, notices = apply(current, "[GOLD:DELTA;AMOUNT:+30;CAUSE:награда старосты]")
    assert current.party_gold == 30 and "→ 30" in notices[0]
    _, notices = apply(current, "[GOLD:DELTA;AMOUNT:-50;CAUSE:новый корабль]")
    assert current.party_gold == 30 and "не хватает" in notices[0]
    apply(current, "[GOLD:DELTA;AMOUNT:-10;CAUSE:эль]")
    assert current.party_gold == 20
    apply(current, "[QUEST:ADD;ID:q;TITLE:Сокровище;GOAL:найти клад;FROM:Старуха]")
    lines = quests.context_lines(current)
    assert any("Сокровище" in line and "Старуха" in line for line in lines) and "- казна партии: 20" in lines


def test_treasury_carries_over_through_the_campaign_archive(monkeypatch):
    archive = {"players": {}, "campaigns": []}
    monkeypatch.setattr("AI.dnd_campaign._chat_history", lambda chat_id, create=False: archive)
    old = party()
    old.party_gold = 75
    quests.archive_treasury(old, {})
    fresh = SimpleNamespace(chat_id=-7)
    quests.ensure(fresh)
    assert fresh.party_gold == 75 and fresh.quests == {}


def test_quests_page_and_command_render():
    current = party()
    apply(current, "[QUEST:ADD;ID:q;TITLE:Сокровище;GOAL:найти клад]")
    text = quests.render(current)
    assert "Открытые:" in text and "Сокровище" in text and "💰 Казна: 0" in text
    from AI import dnd_state_commands as commands

    assert "днд квесты" in commands._STATE_ALIASES["quests"]
    random.seed(0)
