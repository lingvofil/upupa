"""Exercise Telegram menu requests through the real code-only reducer."""
import asyncio
import copy

from tests import test_smoke_imports

del test_smoke_imports

from AI import dnd, dnd_local_engine as engine, dnd_menu as menu
from AI import dnd_turn_lifecycle as lifecycle
from AI.dnd_local_runtime import LocalRuntime, flush_local_outbox, identity_for
from tests.test_dnd_menu import Callback, FakeBot, Message, button, latest_markup


def game():
    current = dnd.GameSession(-100987, "Алиса", starter_user_id=1, active_model="groq")
    current.campaign_id = "menu-integration"
    current.state_revision = 4
    current.mode = "participants"
    current.state = "WAITING_ACTION"
    current.participants = {str(uid): {"user_id": uid, "name": name, "active": True}
                            for uid, name in [(1, "Алиса"), (2, "Борис")]}
    current.character_sheets = {str(uid): {"hp": 14, "max_hp": 14, "status": "alive", "ac": 13,
        "stats": {"STR": 16, "DEX": 14, "CON": 13, "INT": 12, "WIS": 10, "CHA": 8}} for uid in (1, 2)}
    current.character_profiles = {"1": {"style": "Проводник"}}
    current.inventories = {"1": [{"name": "Талисман", "quantity": 1, "mechanic": "ADVANTAGE_SOCIAL",
        "requirement": "NONE", "cost": "NONE", "charges_max": 1, "charges_remaining": 1}], "2": []}
    current.scene_count = 3
    current.scene_log = ["Перед героями ворота."]
    current.scene_objects = {}
    current.enemy_combatants = {}
    current.scene_clocks = {}
    current.player_positions = {"1": {"location": "ворота"}, "2": {"location": "ворота"}}
    current.conditions = {}
    current.event_journal = []
    current.pending_generation_request = {}
    current.pending_generated_result = {}
    current.action_prompt_message_id = 50
    current.action_target_user_ids = []
    current.paused = False
    engine.ensure(current)
    lifecycle.sync_window(current)
    return current


class OfflineDnd:
    """Only transport/timer boundaries are faked; rules remain real."""
    _can_user_act = staticmethod(dnd._can_user_act)
    _is_participant_mode = staticmethod(dnd._is_participant_mode)
    _participant_ids = staticmethod(dnd._participant_ids)
    _user_is_host = staticmethod(lambda current, actor: current.starter_user_id == actor)
    _commit_roll_transaction = staticmethod(lambda *_args: [])

    def __init__(self, current):
        self.dnd_sessions = {current.chat_id: current}
        self.ai_calls = 0
        self.tasks = []
        self.free_calls = []
        self.finalized = []
        self.persisted = []

    def persist_dnd_sessions(self):
        # Same identity synchronization used by the production persistence hook.
        for current in self.dnd_sessions.values():
            lifecycle.sync_window(current)
        self.persisted.append(copy.deepcopy(vars(next(iter(self.dnd_sessions.values())))))

    async def generate_session_response(self, *_args, **_kwargs):
        self.ai_calls += 1
        raise AssertionError("Navigation and known local effects must not call AI")

    async def handle_free_action(self, event):
        self.free_calls.append(event)
        await dnd.handle_free_action(event)

    def _start_background_task(self, coroutine, *, name):
        self.tasks.append(name)
        coroutine.close()

    async def open_action_window(self, bot, chat_id, *, target_user_ids=None):
        current = self.dnd_sessions[chat_id]
        current.state = "WAITING_ACTION"
        current.action_target_user_ids = target_user_ids or []
        current.action_prompt_message_id += 1
        lifecycle.sync_window(current)

    async def finalize_group_actions(self, bot, chat_id, prompt_id):
        self.finalized.append((chat_id, prompt_id))
        current = self.dnd_sessions[chat_id]
        current.state = "RESOLVING"
        lifecycle.close_window(current)


def setup_service(monkeypatch):
    current = game()
    offline = OfflineDnd(current)
    bot = FakeBot()
    runtime = LocalRuntime(offline)
    monkeypatch.setattr("AI.dnd_local_runtime.settings_for", lambda _session=None: {"ai_mode": "economy"})
    service = menu.DndMenuService(offline, execute_action=runtime.execute, identity_for=identity_for)
    return current, offline, bot, runtime, service


def world(current):
    return {key: copy.deepcopy(value) for key, value in vars(current).items() if not key.startswith("menu_")}


async def items_card(current, bot, service):
    await service.command(Message(bot, current))
    await service.callback(Callback(bot, current, button(latest_markup(bot), "🎒 Вещи")))
    return current.menu_ui_state["cards"]["1"]


async def confirm_item(current, bot, service):
    card_id = await items_card(current, bot, service)
    await service.callback(Callback(bot, current, button(latest_markup(bot), "🧰 Талисман"), message_id=card_id))
    return Callback(bot, current, button(latest_markup(bot), "✅ Подтвердить"), message_id=card_id)


def test_real_lifecycle_identity_navigation_is_pure_and_uses_no_ai(monkeypatch):
    async def run():
        current, offline, bot, _runtime, service = setup_service(monkeypatch)
        before = world(current)
        await service.command(Message(bot, current))
        for page in ("👤 Герой", "🎒 Вещи", "📍 Сцена", "👹 Враги", "👥 Партия", "📖 Журнал"):
            card_id = current.menu_ui_state["cards"].get("1", current.menu_ui_state["cards"]["party"])
            await service.callback(Callback(bot, current, button(latest_markup(bot), page), message_id=card_id))
        assert world(current) == before
        assert offline.ai_calls == 0
        assert offline.tasks == []
        assert len(bot.sent) == 2
        assert set(current.menu_ui_state["cards"]) == {"party", "1"}
    asyncio.run(run())


def test_confirmed_item_reaches_real_engine_and_duplicate_consumes_once(monkeypatch):
    async def run():
        current, offline, bot, _runtime, service = setup_service(monkeypatch)
        callback = await confirm_item(current, bot, service)
        await service.callback(callback)
        await service.callback(callback)
        assert current.inventories["1"][0]["charges_remaining"] == 0
        assert current.item_boosts["1"] == {"domain": "SOCIAL", "source": "Талисман"}
        assert len(current.action_records) == 1
        assert current.dnd_lifecycle_v1["window"]["resolved_actors"] == [1]
        assert offline.ai_calls == 0
        assert offline.tasks == []
        assert current.local_delivery_outbox == []
        assert any("следующий подходящий бросок" in row.text for row in bot.sent)
        # Public journal must include the actual canonical effect, not only a label.
        await service.callback(Callback(bot, current, button(latest_markup(bot), "📖 Журнал"),
                                        message_id=current.menu_ui_state["cards"]["1"]))
        assert "Талисман" in bot.edited[-1].text
    asyncio.run(run())


def test_owned_effect_rejects_foreign_actor_and_stale_phase_before_engine(monkeypatch):
    async def run():
        current, offline, bot, _runtime, service = setup_service(monkeypatch)
        callback = await confirm_item(current, bot, service)
        await service.callback(Callback(bot, current, callback.data, actor=2, message_id=callback.message.message_id))
        assert current.inventories["1"][0]["charges_remaining"] == 1
        current.state = "WAITING_ROLL"
        current.pending_roll = {"type": "CHECK", "target_user_ids": [1], "ability": "STR", "dc": 10}
        lifecycle.sync_window(current)
        await service.callback(callback)
        assert current.action_records == []
        assert current.inventories["1"][0]["charges_remaining"] == 1
        assert offline.ai_calls == 0
    asyncio.run(run())


def test_registered_text_prompt_routes_into_real_group_collector_with_owner_guard(monkeypatch):
    async def run():
        current, offline, bot, _runtime, service = setup_service(monkeypatch)
        monkeypatch.setitem(dnd.dnd_sessions, current.chat_id, current)
        monkeypatch.setattr(dnd, "persist_dnd_sessions", offline.persist_dnd_sessions)
        monkeypatch.setattr(dnd, "_start_background_task", offline._start_background_task)
        monkeypatch.setattr(dnd, "settings_for", lambda _session=None: {"turn_seconds": 180})
        await service.command(Message(bot, current))
        await service.callback(Callback(bot, current, button(latest_markup(bot), "👤 Герой")))
        await service.callback(Callback(bot, current, button(latest_markup(bot), "✍️ Свой ход"), message_id=102))
        prompt_id = int(next(iter(current.menu_action_prompts)))
        assert await service.handle_message(Message(bot, current, actor=2, text="Забираю талисман", reply=prompt_id))
        assert current.pending_actions == {}
        assert await service.handle_message(Message(bot, current, text="Отвлекаю стража", reply=prompt_id))
        assert current.pending_actions["1"]["action"] == "Отвлекаю стража"
        assert len(offline.free_calls) == 1
        assert len(offline.tasks) == 1  # Group deadline begins only with a declaration.
        current.action_prompt_message_id += 1
        lifecycle.sync_window(current)
        assert await service.handle_message(Message(bot, current, text="Запоздалая идея", reply=prompt_id))
        assert len(offline.free_calls) == 1
        assert current.pending_actions["1"]["action"] == "Отвлекаю стража"
        assert offline.ai_calls == 0
    asyncio.run(run())


def test_mixed_local_and_text_actions_complete_one_group_window(monkeypatch):
    async def run():
        current, offline, bot, runtime, service = setup_service(monkeypatch)
        monkeypatch.setitem(dnd.dnd_sessions, current.chat_id, current)
        monkeypatch.setattr(dnd, "persist_dnd_sessions", offline.persist_dnd_sessions)
        monkeypatch.setattr(dnd, "_start_background_task", offline._start_background_task)
        monkeypatch.setattr(dnd, "settings_for", lambda _session=None: {"turn_seconds": 180})
        callback = await confirm_item(current, bot, service)
        await service.callback(callback)
        assert not offline.finalized
        await runtime.execute(bot, current.chat_id, 2, {**identity_for(current), "kind": "FREE_TEXT",
            "operation_id": "second-player-text", "text": "Отвлекаю стража"})
        assert offline.finalized == [(current.chat_id, 50)]
        assert current.pending_actions["2"]["action"] == "Отвлекаю стража"
        assert current.inventories["1"][0]["charges_remaining"] == 0
        assert len(current.action_records) == 1
        assert offline.ai_calls == 0  # Narration belongs to the finalization boundary.
    asyncio.run(run())


def test_delivery_timeout_keeps_committed_item_outcome_and_retry_does_not_repeat_effect(monkeypatch):
    async def run():
        current, offline, bot, _runtime, service = setup_service(monkeypatch)
        callback = await confirm_item(current, bot, service)
        send = bot.send_message

        async def fail_delivery(*_args, **_kwargs):
            raise asyncio.TimeoutError("Ambiguous Telegram delivery")

        bot.send_message = fail_delivery
        try:
            await service.callback(callback)
        except asyncio.TimeoutError:
            pass
        else:
            raise AssertionError("An ambiguous delivery must retain its pending outbox")
        assert current.inventories["1"][0]["charges_remaining"] == 0
        assert len(current.local_delivery_outbox) == 1
        assert offline.persisted[-1]["local_delivery_outbox"] == current.local_delivery_outbox
        assert len(current.action_records) == 1
        saved_text = current.local_delivery_outbox[0]["text"]
        bot.send_message = send
        await flush_local_outbox(offline, bot, current)
        await service.callback(callback)
        assert bot.sent[-1].text == saved_text
        assert current.local_delivery_outbox == []
        assert current.inventories["1"][0]["charges_remaining"] == 0
        assert len(current.action_records) == 1
        assert offline.ai_calls == 0
    asyncio.run(run())


def test_explicit_wait_button_applies_saved_clock_effect_once_without_ai(monkeypatch):
    async def run():
        current, offline, bot, _runtime, service = setup_service(monkeypatch)
        current.scene_clocks = {"alarm": {"id": "alarm", "name": "Тревога", "kind": "DANGER",
            "value": 0, "max": 4, "full": False, "history": []}}
        current.local_wait_rule = {"label": "Ждать в проходе", "uncertain": False,
            "success": {"text": "Стражи приблизились.",
                        "effects": [{"kind": "clock", "clock_id": "alarm", "delta": 1}]}}
        await service.show(bot, current, 1, "overview")
        card_id = current.menu_ui_state["cards"]["1"]
        await service.callback(Callback(bot, current, button(latest_markup(bot), "⏳ Подождать"), message_id=card_id))
        assert current.scene_clocks["alarm"]["value"] == 0  # Merely showing/confirming is navigation.
        callback = Callback(bot, current, button(latest_markup(bot), "✅ Подтвердить"), message_id=card_id)
        await service.callback(callback)
        await service.callback(callback)
        assert current.scene_clocks["alarm"]["value"] == 1
        assert len(current.action_records) == 1
        assert current.action_records[0]["kind"] == "WAIT"
        assert "Стражи приблизились." in current.action_records[0]["public_summary"]
        assert offline.ai_calls == 0 and offline.tasks == []
    asyncio.run(run())


def test_lobby_role_selection_allocates_stats_locally_without_ai(monkeypatch):
    from AI import dnd_combat

    async def run():
        current, offline, bot, _runtime, service = setup_service(monkeypatch)
        current.state = "LOBBY"
        lifecycle.sync_window(current)
        await service.show(bot, current, 1, "hero")
        card_id = current.menu_ui_state["cards"]["1"]
        await service.callback(Callback(bot, current, button(latest_markup(bot), "🎯 Разум"), message_id=card_id))
        await service.callback(Callback(bot, current, button(latest_markup(bot), "✅ Подтвердить"), message_id=card_id))
        assert current.character_profiles["1"]["archetype"] == "разум"
        stats = await dnd_combat._generate_stats(offline, current, [1])
        assert stats["1"]["INT"] == 16 and sorted(stats["1"].values()) == [8, 10, 12, 13, 14, 16]
        assert offline.ai_calls == 0 and offline.tasks == []
    asyncio.run(run())
