"""Emergency heals: one explicit module, owners decide, durable everywhere."""
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import textwrap
from types import SimpleNamespace

import pytest

from tests import test_smoke_imports  # noqa: F401

from AI import dnd_combat as combat
from AI import dnd_healing as healing

ROOT = Path(__file__).resolve().parents[1]


def party(size=2, charges=None, **extra):
    names = ["Алиса", "Боря", "Цель", "Четвёртый", "Пятый", "Шестой", "Седьмой"]
    values = dict(
        participants={str(uid): {"user_id": uid, "name": names[uid - 1]} for uid in range(1, size + 1)},
        character_sheets={str(uid): {"hp": 10, "max_hp": 12, "ac": 5, "status": "alive", "stats": {}}
                          for uid in range(1, size + 1)},
        healing_charge={"owner_id": 1, "used": False},
        healing_charges=list(charges or []),
        pending_heal_decision=None,
    )
    values.update(extra)
    return SimpleNamespace(**values)


# ----------------------------------------------------------------- quota

def test_charge_count_is_one_per_three_heroes_rounded_up():
    expected = {0: 0, 1: 1, 2: 1, 3: 1, 4: 2, 5: 2, 6: 2, 7: 3, 10: 4}
    assert {size: healing.charge_count(size) for size in expected} == expected


def test_quota_migrates_and_resizes_old_saves():
    five = party(5)
    charges = healing.ensure_charges(five)
    assert len(charges) == 2 and len({c["owner_id"] for c in charges}) == 2
    assert five.healing_charge["owner_id"] == charges[0]["owner_id"]
    three = party(3, charges=[{"id": 1, "owner_id": 1}, {"id": 2, "owner_id": 2}])
    assert healing.ensure_charges(three) == [{"id": 1, "owner_id": 1, "used": False, "lost": False}]
    seven = party(7, charges=[{"id": 1, "owner_id": 1, "used": True}, {"id": 2, "owner_id": 2}])
    charges = healing.ensure_charges(seven)
    assert [c["id"] for c in charges] == [1, 2, 3] and charges[0]["used"] and len({c["owner_id"] for c in charges}) == 3


def test_fresh_party_deal_announces_owners():
    current = party(4)
    text = healing.deal_charges(current)
    assert len(current.healing_charges) == 2 and "лечилки (2) достались" in text


# ----------------------------------------------------------------- lethal hit

def test_lethal_enemy_hit_opens_owner_decision_without_spending(monkeypatch):
    current = party(2, charges=[{"id": 1, "owner_id": 1}])
    current.character_sheets["2"]["hp"] = 2
    rolls = iter([15, 6])
    monkeypatch.setattr(combat.random, "randint", lambda a, b: next(rolls))
    summary, prompt, all_dead = combat._resolve_enemy_attack(current, {"target_user_id": 2, "power": "MEDIUM", "reason": "орк рубит"})
    sheet = current.character_sheets["2"]
    assert sheet["status"] == "dying" and sheet["hp"] == 0 and all_dead is False
    decision = current.pending_heal_decision
    assert decision["owner_id"] == 1 and decision["charge_id"] == 1 and decision["attack_prompt"] in prompt
    assert current.healing_charges[0]["used"] is False
    assert "при смерти" in summary and "выбывает" not in summary


def test_lethal_hit_without_any_charge_kills(monkeypatch):
    current = party(2, charges=[{"id": 1, "owner_id": 1, "used": True}])
    current.character_sheets["2"]["hp"] = 2
    rolls = iter([15, 6])
    monkeypatch.setattr(combat.random, "randint", lambda a, b: next(rolls))
    summary, _prompt, _dead = combat._resolve_enemy_attack(current, {"target_user_id": 2, "power": "MEDIUM", "reason": "удар"})
    assert current.character_sheets["2"]["status"] == "dead" and current.pending_heal_decision is None
    assert "выбывает" in summary


# ----------------------------------------------------------------- decisions

def dying(size=5, target=3, owner=1, charge_id=1, **pending):
    current = party(size, charges=[{"id": 1, "owner_id": 1}, {"id": 2, "owner_id": 2}])
    current.character_sheets[str(target)].update(hp=0, status="dying")
    current.pending_heal_decision = {"target_id": target, "owner_id": owner, "charge_id": charge_id,
                                     "declined_charge_ids": [], "nonce": 111111, "attack_prompt": "Смертельный удар.", **pending}
    return current


def test_refusing_someone_else_passes_to_next_owner_and_keeps_charge():
    current = dying()
    text, _prompt, all_dead = healing.apply_decision(current, use_heal=False)
    assert "Теперь решает Боря" in text and all_dead is False
    assert current.pending_heal_decision["owner_id"] == 2 and current.healing_charges[0]["used"] is False
    assert healing.available_count(current, target_id=3) == 1


def test_second_owner_saves_and_only_that_charge_is_spent():
    current = dying()
    healing.apply_decision(current, use_heal=False)
    text, prompt, _ = healing.apply_decision(current, use_heal=True, rng=SimpleNamespace(randint=lambda a, b: 4))
    assert current.character_sheets["3"] == {**current.character_sheets["3"], "hp": 6, "status": "alive"}
    assert [c["used"] for c in current.healing_charges] == [False, True] and current.pending_heal_decision is None
    assert "Боря тратит" in text and "одну из аварийных лечилок" in prompt


def test_self_refusal_loses_own_charge_but_others_may_save():
    current = dying(target=1, owner=1)
    text, _prompt, _ = healing.apply_decision(current, use_heal=False)
    assert "она потеряна" in text and current.healing_charges[0]["lost"] is True
    assert current.pending_heal_decision["owner_id"] == 2


def test_last_refusal_kills_and_legacy_decision_without_charge_id_still_resolves():
    current = dying(size=2, target=2, owner=1)
    current.healing_charges = [{"id": 1, "owner_id": 1, "used": False, "lost": False}]
    text, prompt, _ = healing.apply_decision(current, use_heal=False)
    assert current.character_sheets["2"]["status"] == "dead" and "погиб" in text and current.healing_charges[0]["used"] is False
    legacy = dying(size=2, target=2, owner=1)
    legacy.healing_charges = [{"id": 1, "owner_id": 1, "used": False, "lost": False}]
    del legacy.pending_heal_decision["charge_id"]
    healing.apply_decision(legacy, use_heal=True, rng=SimpleNamespace(randint=lambda a, b: 1))
    assert legacy.character_sheets["2"]["status"] == "alive" and legacy.healing_charges[0]["used"] is True


def test_decision_callback_reserves_a_durable_continuation(monkeypatch):
    from AI import dnd

    current = dying(size=2, target=2, owner=1)
    current.chat_id, current.state, current.paused = -9, "WAITING_HEAL", False
    current.healing_charges = [{"id": 1, "owner_id": 1, "used": False, "lost": False}]
    reserved, started = [], []
    monkeypatch.setitem(dnd.dnd_sessions, -9, current)
    monkeypatch.setattr(dnd, "persist_dnd_sessions", lambda: None)
    monkeypatch.setattr(dnd, "with_scene_direction", lambda session, prompt: prompt)
    monkeypatch.setattr(dnd, "_start_background_task", lambda coro, name: (started.append(name), coro.close()))
    monkeypatch.setattr("AI.dnd_result_recovery.reserve_generation_request",
                        lambda session, prompt, kind: (reserved.append((kind, prompt)), setattr(session, "pending_generation_request", {"id": "r1"})))
    answers, edits = [], []
    callback = SimpleNamespace(data="dnd:heal:yes:111111", from_user=SimpleNamespace(id=1), bot=None,
                               message=SimpleNamespace(chat=SimpleNamespace(id=-9), edit_text=lambda text: asyncio.sleep(0, edits.append(text))),
                               answer=lambda *a, **k: asyncio.sleep(0, answers.append(a)))
    asyncio.run(healing.handle_decision_callback(callback))
    assert current.state == "RESOLVING" and current.character_sheets["2"]["status"] == "alive"
    assert reserved and reserved[0][0] == healing.DECISION_KIND and "Смертельный удар." in reserved[0][1]
    assert started and "dnd-heal-decision" in started[0] and "возвращается" in edits[0]


def test_foreign_or_stale_button_is_rejected(monkeypatch):
    from AI import dnd

    current = dying(size=2, target=2, owner=1)
    current.state = "WAITING_HEAL"
    monkeypatch.setitem(dnd.dnd_sessions, -9, current)
    answers = []
    for user, data in ((2, "dnd:heal:yes:111111"), (1, "dnd:heal:yes:999999")):
        callback = SimpleNamespace(data=data, from_user=SimpleNamespace(id=user),
                                   message=SimpleNamespace(chat=SimpleNamespace(id=-9)),
                                   answer=lambda *a, **k: asyncio.sleep(0, answers.append(a[0])))
        asyncio.run(healing.handle_decision_callback(callback))
    assert "не твоя" in answers[0] and "Старая кнопка" in answers[1]
    assert current.character_sheets["2"]["status"] == "dying"


# ----------------------------------------------------------------- composed runtime regressions

_SCRIPT = textwrap.dedent(r'''
    import asyncio, json, pathlib, sys
    from types import SimpleNamespace
    from tests import test_smoke_imports  # noqa: F401
    from AI import dnd
    dnd.DND_STATE_PATH = pathlib.Path(sys.argv[1]) / "state.json"
    from AI.dnd_runtime import configure_dnd_runtime; configure_dnd_runtime()
    from AI.dnd_style import configure_dnd_style; configure_dnd_style()
    from AI.dnd_player_agency import configure_dnd_player_agency; configure_dnd_player_agency()
    from AI.dnd_target_mentions import configure_dnd_target_mentions; configure_dnd_target_mentions()
    from AI.dnd_result_recovery import configure_dnd_result_recovery; configure_dnd_result_recovery()
    from AI.dnd_local_runtime import configure_dnd_local_runtime; configure_dnd_local_runtime()
    import AI.dnd_result_recovery as recovery
    import AI.dnd_combat as combat

    CHAT = -1001707530786
    continued = []
    async def capture_continue(_dnd, _bot, session):
        continued.append((session.pending_generation_request or {}).get("kind"))
        return True
    recovery.continue_pending_generation = capture_continue
    sent = []
    class Bot:
        async def send_message(self, chat_id, text, **kwargs):
            sent.append({"text": str(text), "markup": bool(kwargs.get("reply_markup"))})
            return SimpleNamespace(message_id=len(sent), chat=SimpleNamespace(id=chat_id))

    async def attack(hp_before, charges):
        session = dnd.GameSession(CHAT, "Маша", conversation=[])
        dnd.dnd_sessions[CHAT] = session
        session.mode, session.state = "participants", "RESOLVING"
        session.participants = {"1": {"user_id": 1, "name": "Маша", "active": True},
                                "2": {"user_id": 2, "name": "Денис", "active": True}}
        session.character_sheets = {str(u): {"hp": hp_before, "max_hp": 12, "ac": 5, "status": "alive",
            "stats": {k: 10 for k in ("STR","DEX","CON","INT","WIS","CHA")}} for u in (1, 2)}
        session.healing_charges = charges
        combat.random.randint = lambda a, b: 15 if b == 20 else min(b, 6)
        sent.clear()
        await dnd.parse_and_execute_turn(Bot(), CHAT,
            "Орк бьёт! [ACTION:ENEMY_ATTACK;TARGETS:2;ENEMY:Орк;HP:9;AC:12;POWER:MEDIUM;REASON:удар топором]")
        return session

    async def main():
        out = {}
        calm = await attack(12, [{"id": 1, "owner_id": 1, "used": False, "lost": False}])
        out["calm_request_kind"] = continued[-1] if continued else None
        out["calm_hp"] = calm.character_sheets["2"]["hp"]
        continued.clear()
        lethal = await attack(2, [{"id": 1, "owner_id": 1, "used": False, "lost": False}])
        out["lethal_state"] = lethal.state
        out["lethal_status"] = lethal.character_sheets["2"]["status"]
        out["decision_owner"] = (lethal.pending_heal_decision or {}).get("owner_id")
        out["asked_with_buttons"] = any(row["markup"] and "Тратишь свою лечилку" in row["text"] for row in sent)
        out["charge_used"] = lethal.healing_charges[0]["used"]
        out["lethal_continued"] = list(continued)
        print(json.dumps(out))
    asyncio.run(main())
''')


def test_composed_runtime_routes_master_attacks_through_one_durable_handler(tmp_path):
    env = dict(os.environ, PYTHONPATH=str(ROOT))
    result = subprocess.run([sys.executable, "-c", _SCRIPT, str(tmp_path)], cwd=ROOT, env=env,
                            capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stderr[-3000:]
    data = json.loads(result.stdout.strip().splitlines()[-1])
    # Non-lethal: the durable continuation of dnd_combat (it used to be shadowed).
    assert data["calm_request_kind"] == "ENEMY_ATTACK_CONTINUATION" and data["calm_hp"] == 6
    # Lethal with a charge: owners decide; nothing is spent behind their back.
    assert data["lethal_state"] == "WAITING_HEAL" and data["lethal_status"] == "dying"
    assert data["decision_owner"] == 1 and data["asked_with_buttons"] and data["charge_used"] is False
    assert data["lethal_continued"] == []  # no continuation before the owner decides


def test_local_engine_lethal_hit_asks_the_owner_instead_of_a_free_save():
    from tests.test_dnd_local_engine import backend, session
    from AI import dnd_local_engine as engine
    from AI import dnd_turn_lifecycle as lifecycle

    sess = session()
    sess.healing_charges = [{"id": 1, "owner_id": 2, "used": False, "lost": False}]
    sess.healing_charge = {"owner_id": 2, "used": False}
    sess.character_sheets["1"]["hp"] = 1
    sess.local_enemy_rules = {"огр": {"policy": "simple", "style": "brute", "retreat_below": 0.2}}
    lifecycle.close_window(sess)
    be, _calls = backend()
    be.combat = combat
    rolls = iter([19, 8])
    original = combat.random.randint
    combat.random.randint = lambda a, b: next(rolls)
    try:
        lifecycle.start_combat_round(sess)
        for uid in (1, 2):
            lifecycle.note_actor_resolved(sess, uid)
        engine.execute_enemy_action(sess, "огр", operation_id="op-lethal", backend=be)
    finally:
        combat.random.randint = original
    assert sess.character_sheets["1"]["status"] == "dying"
    assert sess.pending_heal_decision["owner_id"] == 2
    assert sess.healing_charges[0]["used"] is False


@pytest.mark.parametrize("response,expected", [
    ("[ACTION:POLL;OPTIONS:Лезть;Вылечить Детектора;Бежать]", "[ACTION:POLL;OPTIONS:Лезть;Бежать]"),
    ("[ACTION:POLL;TARGETS:123;OPTIONS:Лечить Алису;Ждать]", "[ACTION:INPUT;TARGETS:123]"),
])
def test_heal_options_never_stay_in_model_polls(response, expected):
    assert healing.strip_healing_poll_options(response) == expected
