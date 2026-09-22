import asyncio
import copy
from types import SimpleNamespace

import pytest

from AI import dnd
from AI import dnd_campaign as campaign
from AI import dnd_generation_resilience as resilience
from AI import dnd_result_recovery as recovery
from AI import dnd_world_memory as world_memory
from AI.dnd_context_builder import build_memory_context
from AI.dnd_event_journal import prepare_session_events
from AI.dnd_group_action_resilience import install_dnd_group_action_resilience
from AI.dnd_inventory_fun import transfer_between_inventories
from AI.dnd_lobby_controls import _rebuild_character
from AI.dnd_player_positions import apply_player_position_metadata
from AI.dnd_state_invariants import validate_session_state


class _LegacyCampaign:
    @staticmethod
    def _campaign_context(_dnd, _session):
        return (
            "СТАРЫЙ ХУДОЖЕСТВЕННЫЙ ТЕКСТ: Алиса всё ещё держит Медный ключ, "
            "стоит во дворе и выглядит совершенно живой."
        )


def _memory_session():
    return SimpleNamespace(
        chat_id=-2001,
        campaign_id="memory-v2-campaign",
        state_revision=0,
        event_journal=[],
        campaign_started_at="2026-09-22T20:00:00+00:00",
        participants={
            "1": {"user_id": 1, "name": "Алиса"},
            "2": {"user_id": 2, "name": "Боря"},
        },
        character_profiles={
            "1": {
                "style": "курьер",
                "strength": "упрямая",
                "weakness": "лезет первой",
                "special": "договариваться",
            },
            "2": {
                "style": "архивист",
                "strength": "наблюдательный",
                "weakness": "боится воды",
                "special": "помнит долги",
            },
        },
        heritage={},
        selected_plot="Ограбить склад до рассвета",
        continuation_mode=False,
        scene_count=1,
        player_positions={
            "1": {"location": "двор", "detail": "у ворот"},
            "2": {"location": "крыша", "detail": "над складом"},
        },
        inventories={
            "1": [{"name": "Медный ключ", "kind": "artifact"}],
            "2": [],
        },
        character_sheets={
            "1": {"hp": 10, "max_hp": 10, "status": "alive"},
            "2": {"hp": 8, "max_hp": 8, "status": "alive"},
        },
        enemy_combatants={},
        npc_memory={},
        conditions={},
        reputations={},
        threat={"name": "Шум", "level": 0, "max": 6},
        scene_clocks={},
        social_relationships=[],
        learned_achievements={},
        special_move_charges={},
        luck_tokens={},
        healing_charges=[],
        achievement_world_facts=[],
        scene_log=[],
    )


def test_fixed_position_survives_many_unrelated_turns_and_stays_authoritative():
    session = _memory_session()
    prepare_session_events(session)

    apply_player_position_metadata(
        session,
        (
            "Алиса залезла в банку. "
            "[POSITION:SET;PLAYER:1;LOCATION:банка с пивом;DETAIL:сидит внутри]"
        ),
        (
            "Алиса залезла в банку. "
            "[POSITION:SET;PLAYER:1;LOCATION:банка с пивом;DETAIL:сидит внутри]"
        ),
        [],
    )
    prepare_session_events(session)

    for index in range(20):
        session.scene_count += 1
        session.threat["level"] = index % 4
        session.scene_log.append(f"Промежуточная сцена {index}.")
        prepare_session_events(session)

    assert session.player_positions["1"] == {
        "location": "банка с пивом",
        "detail": "сидит внутри",
        "updated_scene": 1,
    }

    context = build_memory_context(
        SimpleNamespace(),
        _LegacyCampaign,
        session,
        prompt="Боря открывает дверь склада",
    )

    assert "позиция: банка с пивом (сидит внутри)" in context
    assert "АВТОРИТЕТНЫЙ СНИМОК" in context


def test_group_turn_keeps_every_action_across_provider_outage_and_retry():
    session = SimpleNamespace(
        chat_id=-2002,
        state="WAITING_ACTION",
        action_prompt_message_id=77,
        pending_actions={
            "1": {"user_id": 1, "name": "Алиса", "action": "ем пирожок"},
            "2": {"user_id": 2, "name": "Боря", "action": "ищу ловушку"},
            "3": {"user_id": 3, "name": "Света", "action": "допрашиваю сторожа"},
            "4": {"user_id": 4, "name": "Мухтар", "action": "держу дверь"},
        },
        action_target_user_ids=[1, 2, 3, 4],
        action_deadline=123.0,
        conversation=[],
        pending_generated_result={},
        pending_generation_request={},
        generated_result_seq=0,
        scene_count=5,
        campaign_id="group-campaign",
        state_revision=0,
        campaign_started_at=None,
    )
    attempts = []
    parses = []

    async def generate(_session, prompt):
        attempts.append(prompt)
        if len(attempts) == 1:
            raise RuntimeError("providers unavailable")
        return "Все четыре действия получили последствия. [ACTION:INPUT]"

    async def parse(_bot, _chat_id, text):
        parses.append(text)

    persisted = []

    dnd_module = SimpleNamespace(
        dnd_sessions={session.chat_id: session},
        persist_dnd_sessions=lambda: persisted.append(True),
        _format_group_actions=lambda actions: "\n".join(
            f"- {item['name']}: {item['action']}" for item in actions
        ),
        with_scene_direction=lambda _session, prompt: prompt,
        generate_session_response=generate,
        parse_and_execute_turn=parse,
    )
    install_dnd_group_action_resilience(dnd_module)

    class Bot:
        def __init__(self):
            self.messages = []

        async def send_message(self, chat_id, text, **kwargs):
            self.messages.append((chat_id, text, kwargs))
            return SimpleNamespace(
                message_id=100 + len(self.messages),
                chat=SimpleNamespace(id=chat_id),
            )

    bot = Bot()
    asyncio.run(dnd_module.finalize_group_actions(bot, session.chat_id, 77))

    stored = session.pending_generation_request["prompt"]
    for expected in (
        "Алиса: ем пирожок (id=1)",
        "Боря: ищу ловушку (id=2)",
        "Света: допрашиваю сторожа (id=3)",
        "Мухтар: держу дверь (id=4)",
    ):
        assert expected in stored

    assert len(attempts) == 1
    assert session.pending_actions == {}

    asyncio.run(recovery.continue_pending_generation(dnd_module, bot, session))

    assert attempts == [stored, stored]
    assert parses == ["Все четыре действия получили последствия. [ACTION:INPUT]"]
    announcements = [
        text
        for _chat_id, text, _kwargs in bot.messages
        if text.startswith("🎭 Ход партии:")
    ]
    assert len(announcements) == 1


def test_transferred_item_does_not_return_after_many_later_turns():
    session = _memory_session()
    prepare_session_events(session)

    display, kind = transfer_between_inventories(
        session.inventories,
        1,
        2,
        "Медный ключ",
    )
    assert (display, kind) == ("Медный ключ", "artifact")

    transfer_events = prepare_session_events(session)
    assert [event["type"] for event in transfer_events] == ["ITEM_TRANSFERRED"]

    for index in range(12):
        session.scene_count += 1
        session.scene_log.append(f"После передачи прошло ещё {index + 1} сцен.")
        prepare_session_events(session)

    assert session.inventories["1"] == []
    assert session.inventories["2"] == [
        {"name": "Медный ключ", "kind": "artifact"}
    ]

    context = build_memory_context(
        SimpleNamespace(),
        _LegacyCampaign,
        session,
        prompt="продолжай",
    )
    alice_line = next(
        line for line in context.splitlines() if line.startswith("- ID 1 Алиса")
    )
    boris_line = next(
        line for line in context.splitlines() if line.startswith("- ID 2 Боря")
    )
    assert "инвентарь: нет" in alice_line
    assert "Медный ключ" in boris_line


def test_lobby_character_rebuild_keeps_existing_inventory_and_heritage(monkeypatch):
    session = _memory_session()
    session.mode = "participants"
    session.state = "LOBBY"
    session.heritage["1"] = {
        "artifacts": [{"name": "Медный ключ", "kind": "artifact"}],
        "reputation": ["тот самый человек с моста"],
    }
    session.reputations["1"] = ["тот самый человек с моста"]
    session.inventories["1"].append({"name": "Верёвка", "kind": "item"})
    before_inventory = copy.deepcopy(session.inventories["1"])
    before_heritage = copy.deepcopy(session.heritage["1"])
    before_reputation = copy.deepcopy(session.reputations["1"])

    async def no_refresh(_session, _bot):
        return None

    async def options(_dnd, _session, _user_id, _step):
        return ["мужчина", "женщина", "небинарный", "не указан", "другое"]

    monkeypatch.setattr(campaign, "_refresh_lobby", no_refresh)
    monkeypatch.setattr(campaign, "_generate_profile_options", options)
    monkeypatch.setattr(
        campaign,
        "_profile_choice_text",
        lambda *_args, **_kwargs: "выбери пол",
    )
    monkeypatch.setattr(campaign, "_profile_keyboard", lambda *_args, **_kwargs: None)

    class Message:
        def __init__(self):
            self.answers = []

        async def answer(self, text, **kwargs):
            self.answers.append((text, kwargs))
            return SimpleNamespace()

    class Callback:
        def __init__(self):
            self.message = Message()
            self.from_user = SimpleNamespace(id=1)
            self.bot = SimpleNamespace()
            self.answers = []

        async def answer(self, text, **kwargs):
            self.answers.append((text, kwargs))

    callback = Callback()
    fake_dnd = SimpleNamespace(
        dnd_sessions={session.chat_id: session},
        persist_dnd_sessions=lambda: None,
    )

    asyncio.run(_rebuild_character(callback, fake_dnd, campaign))

    assert session.character_profiles["1"] == {}
    assert session.inventories["1"] == before_inventory
    assert session.heritage["1"] == before_heritage
    assert session.reputations["1"] == before_reputation


def test_hp_and_death_state_remain_authoritative_over_old_narrative():
    session = _memory_session()
    session.character_sheets["1"] = {
        "hp": 0,
        "max_hp": 10,
        "status": "dead",
    }
    session.scene_log = [
        "Старая реплика по ошибке утверждала, что Алиса жива и машет рукой."
    ]
    prepare_session_events(session)

    context = build_memory_context(
        SimpleNamespace(),
        _LegacyCampaign,
        session,
        prompt="что происходит дальше",
    )

    alice_line = next(
        line for line in context.splitlines() if line.startswith("- ID 1 Алиса")
    )
    assert "HP 0/10, dead" in alice_line
    assert "Структурированные факты ниже важнее старого художественного текста" in context

    session.character_sheets["1"]["status"] = "alive"
    codes = {issue.code for issue in validate_session_state(session)}
    assert "alive_without_hp" in codes


def test_structured_npc_promise_survives_state_roundtrip_and_returns_to_context():
    source = _memory_session()
    source.npc_memory["капитан ржа"] = world_memory.normalize_npc(
        "Капитан Ржа",
        {
            "name": "Капитан Ржа",
            "event": "герои спасли корабль",
            "attitude": "благодарен",
            "obligation_kind": "NPC_OWES_PLAYERS",
            "obligation": "обещал безопасный проход через порт",
            "wants": "вернуть карту",
            "unresolved": "герои обещали найти матроса",
            "affected_player_ids": ["1", "2"],
        },
    )

    payload = copy.deepcopy(campaign._state(source))
    restored = _memory_session()
    restored.npc_memory = {}
    campaign._restore_state(restored, payload)

    row = restored.npc_memory["капитан ржа"]
    assert row["obligation_kind"] == "NPC_OWES_PLAYERS"
    assert row["obligation"] == "обещал безопасный проход через порт"
    assert row["unresolved"] == "герои обещали найти матроса"

    context = build_memory_context(
        SimpleNamespace(),
        _LegacyCampaign,
        restored,
        prompt="мы снова говорим с Капитаном Ржой",
    )
    assert "Капитан Ржа" in context
    assert "обещал безопасный проход через порт" in context
    assert "герои обещали найти матроса" in context


def test_restart_roundtrip_keeps_input_roll_poll_and_resolving_payloads():
    base = {
        "chat_id": -2007,
        "active_model": "groq",
        "conversation": [
            {"role": "user", "content": "system"},
            {"role": "assistant", "content": "Погнали."},
        ],
        "mode": "participants",
        "participants": {
            "1": {"user_id": 1, "name": "Алиса"},
            "2": {"user_id": 2, "name": "Боря"},
        },
    }

    input_session = dnd.GameSession.from_record(
        {
            **base,
            "state": "WAITING_ACTION",
            "action_prompt_message_id": 77,
            "pending_actions": {
                "1": {"user_id": 1, "name": "Алиса", "action": "осматриваю дверь"}
            },
            "action_target_user_ids": [1, 2],
        }
    )
    assert input_session.state == "WAITING_ACTION"
    assert input_session.pending_actions["1"]["action"] == "осматриваю дверь"
    assert input_session.action_target_user_ids == [1, 2]

    roll_session = dnd.GameSession.from_record(
        {
            **base,
            "state": "WAITING_ROLL",
            "pending_roll": {
                "type": "CHECK",
                "skill": "Атлетика",
                "reason": "перепрыгнуть яму",
                "dc": 12,
                "mode": "ADVANTAGE",
                "target_user_ids": [1],
                "condition_uses_pending": [
                    {"player": "1", "effect": "CHECK_DISADVANTAGE"}
                ],
            },
        }
    )
    assert roll_session.state == "WAITING_ROLL"
    assert roll_session.pending_roll["reason"] == "перепрыгнуть яму"
    assert roll_session.pending_roll["condition_uses_pending"][0]["player"] == "1"

    poll_session = dnd.GameSession.from_record(
        {
            **base,
            "state": "WAITING_POLL",
            "current_poll_id": "poll-7",
            "pending_poll": {
                "poll_id": "poll-7",
                "message_id": 501,
                "poll_chat_id": -2007,
                "options": ["лево", "право"],
                "votes": {"1": 1},
                "target_user_ids": [1, 2],
            },
        }
    )
    assert poll_session.state == "WAITING_POLL"
    assert poll_session.current_poll_id == "poll-7"
    assert poll_session.pending_poll["votes"] == {"1": 1}
    assert poll_session.pending_poll["target_user_ids"] == [1, 2]

    resolving_session = dnd.GameSession.from_record(
        {
            **base,
            "state": "RESOLVING",
        }
    )
    recovery._restore(
        resolving_session,
        {
            "pending_generation_request": {
                "id": "gen:restart",
                "prompt": "точный сохранённый ход",
                "kind": "GROUP_ACTION_CONTINUATION",
                "source_campaign_id": "campaign-restart",
                "source_revision": 9,
                "telegram_effects": [],
            },
            "pending_generated_result": {},
            "generated_result_seq": 12,
        },
    )
    assert resolving_session.state == "RESOLVING"
    assert resolving_session.pending_generation_request["prompt"] == "точный сохранённый ход"
    assert resolving_session.pending_generation_request["kind"] == "GROUP_ACTION_CONTINUATION"
    assert resolving_session.generated_result_seq == 12


class _RecoveryPolicy:
    def __init__(self):
        self.fields = {}
        self.restore_hooks = []

    def add_ensure_hook(self, _hook):
        return self

    def add_state_field(self, name, provider):
        self.fields[name] = provider
        return self

    def add_restore_hook(self, hook):
        self.restore_hooks.append(hook)
        return self

    def state(self, session):
        row = {
            "campaign_id": getattr(session, "campaign_id", None),
            "state_revision": getattr(session, "state_revision", 0),
        }
        for name, provider in self.fields.items():
            row[name] = copy.deepcopy(provider(session))
        return row

    def restore(self, session, data):
        row = data if isinstance(data, dict) else {}
        if "campaign_id" in row:
            session.campaign_id = row["campaign_id"]
        if "state_revision" in row:
            session.state_revision = int(row["state_revision"] or 0)
        for name in self.fields:
            if name in row:
                setattr(session, name, copy.deepcopy(row[name]))
        for hook in self.restore_hooks:
            hook(session, row)


class _RecoverySession:
    def __init__(self, policy, chat_id=-2010):
        self._policy = policy
        self.chat_id = chat_id
        self.active_model = "gemini"
        self.conversation = [
            {"role": "user", "content": "system"},
            {"role": "assistant", "content": "Погнали."},
        ]
        self.state = "RESOLVING"
        self.campaign_id = "provider-campaign"
        self.state_revision = 4
        self.campaign_started_at = None
        self.pending_generation_request = {}
        self.pending_generated_result = {}
        self.generated_result_seq = 0
        self.special_move_charges = {"1": 0}

    def to_record(self):
        return {
            "chat_id": self.chat_id,
            "state": self.state,
            "campaign_state": self._policy.state(self),
        }


class _RecoveryBot:
    def __init__(self):
        self.messages = []

    async def send_message(self, chat_id, text, **kwargs):
        self.messages.append((chat_id, text, kwargs))
        return SimpleNamespace(
            message_id=100 + len(self.messages),
            chat=SimpleNamespace(id=chat_id),
        )


def test_both_providers_down_keeps_exact_turn_and_retry_does_not_repeat_effect(monkeypatch):
    policy = _RecoveryPolicy()
    session = _RecoverySession(policy)
    calls = {"persist": 0, "parse": 0, "gemini": 0, "groq": 0}
    groq_prompts = []

    async def base_generate(_session, _prompt):
        raise AssertionError("Gemini sessions must use resilience wrapper")

    async def parse(_bot, _chat_id, _text):
        calls["parse"] += 1
        session.state = "WAITING_ACTION"

    dnd_module = SimpleNamespace(
        dnd_sessions={session.chat_id: session},
        dnd_router=SimpleNamespace(_upupa_dnd_campaign_state_policy=policy),
        persist_dnd_sessions=lambda: calls.__setitem__(
            "persist",
            calls["persist"] + 1,
        ),
        generate_session_response=base_generate,
        parse_and_execute_turn=parse,
        open_action_window=lambda *_args, **_kwargs: None,
        restore_dnd_sessions=lambda _bot: 1,
        _start_background_task=lambda *_args, **_kwargs: None,
    )

    resilience.configure_dnd_generation_resilience(dnd_module)
    recovery.configure_dnd_result_recovery(dnd_module, state_policy=policy)

    def gemini_down(*_args, **_kwargs):
        calls["gemini"] += 1
        raise RuntimeError("gemini unavailable")

    async def groq_flaps(_session, prompt):
        calls["groq"] += 1
        groq_prompts.append(prompt)
        if calls["groq"] == 1:
            raise RuntimeError("groq unavailable")
        return "Продолжение после сохранённого броска. [ACTION:INPUT]"

    monkeypatch.setattr(resilience, "_run_gemini_sync", gemini_down)
    monkeypatch.setattr(resilience, "_run_groq_fallback", groq_flaps)

    exact_prompt = "Игрок уже бросил 17. Не бросай кубик повторно; продолжи с этим исходом."
    recovery.reserve_generation_request(
        session,
        exact_prompt,
        kind="ROLL_CONTINUATION",
        effects=[
            {
                "method": "send_message",
                "chat_id": session.chat_id,
                "text": "🎲 Уже выпало 17.",
            }
        ],
    )

    bot = _RecoveryBot()
    assert asyncio.run(
        recovery.continue_pending_generation(dnd_module, bot, session)
    ) is False

    assert session.pending_generation_request["prompt"] == exact_prompt
    assert session.pending_generation_request["telegram_effects"][0]["status"] == recovery.EFFECT_DONE
    assert session.special_move_charges["1"] == 0
    assert len(bot.messages) == 1

    assert asyncio.run(
        recovery.continue_pending_generation(dnd_module, bot, session)
    ) is True

    assert groq_prompts == [exact_prompt, exact_prompt]
    assert calls["gemini"] == 2
    assert calls["groq"] == 2
    assert calls["parse"] == 1
    assert len(bot.messages) == 1
    assert session.special_move_charges["1"] == 0
    assert session.pending_generation_request == {}
    assert session.pending_generated_result == {}


def test_late_result_from_previous_campaign_cannot_mutate_new_campaign():
    policy = _RecoveryPolicy()
    session = _RecoverySession(policy, chat_id=-2011)
    session.campaign_id = "campaign-old"
    session.state_revision = 7
    session.world_marker = "new campaign untouched"
    calls = {"parse": 0}

    async def late_generate(_session, _prompt):
        session.campaign_id = "campaign-new"
        session.state_revision = 0
        session.world_marker = "new campaign untouched"
        return "Поздний ответ старой кампании [ACTION:INPUT]"

    async def parse(_bot, _chat_id, _text):
        calls["parse"] += 1
        session.world_marker = "MUTATED"

    dnd_module = SimpleNamespace(
        dnd_sessions={session.chat_id: session},
        dnd_router=SimpleNamespace(_upupa_dnd_campaign_state_policy=policy),
        persist_dnd_sessions=lambda: None,
        generate_session_response=late_generate,
        parse_and_execute_turn=parse,
        open_action_window=lambda *_args, **_kwargs: None,
        restore_dnd_sessions=lambda _bot: 1,
        _start_background_task=lambda *_args, **_kwargs: None,
    )
    recovery.configure_dnd_result_recovery(dnd_module, state_policy=policy)

    with pytest.raises(recovery.StaleDndSessionError, match="campaign-changed"):
        asyncio.run(
            dnd_module.generate_session_response(
                session,
                "ход старой кампании",
            )
        )

    assert calls["parse"] == 0
    assert session.campaign_id == "campaign-new"
    assert session.state_revision == 0
    assert session.world_marker == "new campaign untouched"
    assert session.pending_generation_request == {}
    assert session.pending_generated_result == {}
