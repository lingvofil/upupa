import asyncio
import copy
from types import SimpleNamespace

from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import EditMessageText

from AI import dnd_menu as menu


def session(**extra):
    values = dict(
        chat_id=-10044, campaign_id="campaign-one", turn_id="turn-one", state_revision=3,
        state="WAITING_ACTION", mode="participants", scene_count=5, paused=False,
        action_prompt_message_id=50, action_target_user_ids=[1], pending_actions={}, pending_roll=None,
        participants={"1": {"user_id": 1, "name": "Маша"}, "2": {"user_id": 2, "name": "Денис"}},
        mission_goal="Вывести купца", scene_log=["Купец у северных ворот."],
        character_profiles={"1": {"style": "Проводник", "special": "Блеф"}},
        character_sheets={"1": {"hp": 11, "max_hp": 18}, "2": {"hp": 18, "max_hp": 18}},
        inventories={"1": [{"name": "Зелье", "quantity": 1, "mechanic": "ADVANTAGE_SOCIAL", "charges_max": 1, "charges_remaining": 1}]},
        scene_objects={"cart": {"name": "Телега", "state": "У ворот", "available": True,
                                "interactions": {"hide": {"label": "Спрятаться", "uncertain": False,
                                    "success": {"text": "Герой спрятался за телегой.", "effects": []}}}}},
        enemy_combatants={"guard": {"name": "Страж", "hp": 10, "max_hp": 10, "ac": 12}},
        scene_clocks={"gates": {"name": "Ворота", "value": 2, "max": 4, "kind": "DANGER"}},
        player_positions={"1": {"location": "У телеги"}}, event_journal=[], action_records=[],
    )
    values.update(extra)
    return SimpleNamespace(**values)


class FakeDnd:
    def __init__(self, current):
        self.dnd_sessions = {current.chat_id: current}
        self.persist_calls = 0
        self.calls = []

    def persist_dnd_sessions(self):
        self.persist_calls += 1

    def _user_is_host(self, current, actor):
        return actor == 1

    def _can_user_act(self, current, actor, targets):
        return str(actor) in current.participants and current.participants[str(actor)].get("active", True) and (not targets or actor in targets)

    async def generate_session_response(self, *_args, **_kwargs):
        raise AssertionError("The menu must never invoke AI")

    async def handle_free_action(self, event):
        self.calls.append(event)


class FakeBot:
    id = 999

    def __init__(self):
        self.sent = []
        self.edited = []
        self.error = None
        self.next_id = 100
        self.delay = 0

    async def send_message(self, chat_id, text, **kwargs):
        if self.delay:
            await asyncio.sleep(self.delay)
        self.next_id += 1
        row = SimpleNamespace(message_id=self.next_id, chat=SimpleNamespace(id=chat_id), text=text, **kwargs)
        self.sent.append(row)
        return row

    async def edit_message_text(self, text, **kwargs):
        if self.error:
            error, self.error = self.error, None
            raise error
        self.edited.append(SimpleNamespace(text=text, **kwargs))
        return True


class Message:
    def __init__(self, bot, current, *, actor=1, text="днд меню", reply=None, message_id=900):
        self.bot = bot
        self.chat = SimpleNamespace(id=current.chat_id)
        self.from_user = SimpleNamespace(id=actor, first_name="Маша")
        self.text = text
        self.caption = None
        self.message_id = message_id
        self.reply_to_message = SimpleNamespace(message_id=reply, from_user=SimpleNamespace(id=bot.id, is_bot=True)) if reply else None
        self.answers = []

    async def answer(self, text, **kwargs):
        self.answers.append(text)
        return await self.bot.send_message(self.chat.id, text, **kwargs)


class Callback:
    def __init__(self, bot, current, data, *, actor=1, message_id=101):
        self.bot = bot
        self.message = SimpleNamespace(chat=SimpleNamespace(id=current.chat_id), message_id=message_id)
        self.from_user = SimpleNamespace(id=actor)
        self.data = data
        self.answers = []

    async def answer(self, text=None, **kwargs):
        self.answers.append(text)


def button(markup, label):
    return next(item.callback_data for row in markup.inline_keyboard for item in row if item.text == label)


def latest_markup(bot):
    return (bot.edited[-1] if bot.edited else bot.sent[-1]).reply_markup


def make_service(current=None, execute=None):
    current = current or session()
    dnd, bot = FakeDnd(current), FakeBot()
    return current, dnd, bot, menu.DndMenuService(dnd, execute_action=execute)


def test_snapshot_and_all_views_never_ensure_repair_or_mutate_game_state():
    current = session()
    before = copy.deepcopy(vars(current))
    snapshot = menu.snapshot_session(current)
    for page in menu._PAGES:
        assert menu.render_page(snapshot, page, 1)
    snapshot["inventories"]["1"][0]["quantity"] = 999
    assert vars(current) == before


def test_navigation_reuses_shared_and_one_player_card_without_ai_or_world_changes():
    async def run():
        current, dnd, bot, service = make_service()
        world_before = menu.snapshot_session(current)
        await service.command(Message(bot, current))
        await service.command(Message(bot, current))
        assert len(bot.sent) == 1
        data = button(latest_markup(bot), "👤 Герой")
        await service.callback(Callback(bot, current, data))
        assert len(bot.sent) == 2
        player_card = current.menu_ui_state["cards"]["1"]
        await service.callback(Callback(bot, current, button(latest_markup(bot), "🎒 Вещи"), message_id=player_card))
        await service.callback(Callback(bot, current, button(latest_markup(bot), "📖 Журнал"), message_id=player_card))
        assert len(bot.sent) == 2
        assert menu.snapshot_session(current) == world_before
        assert dnd.persist_calls > 0
        assert set(current.menu_info_message_ids) == {101, 102}
        assert all(len(item.callback_data.encode("utf-8")) <= 64 for row in latest_markup(bot).inline_keyboard for item in row)
    asyncio.run(run())


def test_concurrent_commands_do_not_create_duplicate_shared_cards():
    async def run():
        current, _dnd, bot, service = make_service()
        bot.delay = 0.01
        await asyncio.gather(*(service.command(Message(bot, current)) for _ in range(5)))
        assert len(bot.sent) == 1
        assert len(bot.edited) == 4
    asyncio.run(run())


def test_deleted_card_replaced_but_timeout_never_creates_a_second_card():
    async def run():
        current, _dnd, bot, service = make_service()
        await service.command(Message(bot, current))
        bot.error = asyncio.TimeoutError()
        try:
            await service.command(Message(bot, current))
        except asyncio.TimeoutError:
            pass
        else:
            raise AssertionError("A timeout must remain ambiguous")
        assert len(bot.sent) == 1
        bot.error = TelegramBadRequest(method=EditMessageText(chat_id=current.chat_id, message_id=101, text="x"), message="Bad Request: message to edit not found")
        await service.command(Message(bot, current))
        assert len(bot.sent) == 2
        assert current.menu_ui_state["cards"]["party"] == 102
        assert 101 in current.menu_info_message_ids
    asyncio.run(run())


def test_owner_guard_and_old_navigation_refresh_without_applying_an_action():
    async def run():
        current, _dnd, bot, service = make_service()
        await service.command(Message(bot, current))
        old_shared_nav = button(latest_markup(bot), "👤 Герой")
        await service.callback(Callback(bot, current, old_shared_nav))
        own_nav = button(latest_markup(bot), "🎒 Вещи")
        callback = Callback(bot, current, own_nav, actor=2, message_id=102)
        await service.callback(callback)
        assert "другого игрока" in callback.answers[-1]
        current.turn_id = "new-turn"
        current.state_revision += 1
        await service.callback(Callback(bot, current, old_shared_nav, actor=2))
        assert current.menu_ui_state["cards"]["2"] == 103
        assert "Денис" in bot.sent[-1].text
    asyncio.run(run())


def test_confirmation_validates_immutable_selector_and_dispatches_only_once():
    async def run():
        calls = []
        async def execute(*args):
            calls.append(args)
        current, _dnd, bot, service = make_service(execute=execute)
        await service.command(Message(bot, current))
        await service.callback(Callback(bot, current, button(latest_markup(bot), "🎒 Вещи")))
        await service.callback(Callback(bot, current, button(latest_markup(bot), "🧰 Зелье"), message_id=102))
        confirm = button(latest_markup(bot), "✅ Подтвердить")
        callback = Callback(bot, current, confirm, message_id=102)
        await service.callback(callback)
        await service.callback(callback)
        assert len(calls) == 1
        payload = calls[0][3]
        assert payload["kind"] == "USE_ITEM" and payload["actor_id"] == 1
        assert payload["item_id"].startswith("legacy:")
        assert payload["operation_id"] and payload["item_fingerprint"]
        assert current.inventories["1"][0]["quantity"] == 1  # UI fabricates no effects.
    asyncio.run(run())


def test_changed_turn_phase_resource_and_paused_state_reject_effects():
    async def scenario(change):
        calls = []
        async def execute(*args):
            calls.append(args)
        current, _dnd, bot, service = make_service(execute=execute)
        await service.command(Message(bot, current))
        await service.callback(Callback(bot, current, button(latest_markup(bot), "🎒 Вещи")))
        await service.callback(Callback(bot, current, button(latest_markup(bot), "🧰 Зелье"), message_id=102))
        data = button(latest_markup(bot), "✅ Подтвердить")
        change(current)
        callback = Callback(bot, current, data, message_id=102)
        await service.callback(callback)
        assert calls == []
        assert callback.answers
    for change in (lambda current: setattr(current, "turn_id", "later"),
                   lambda current: setattr(current, "state", "WAITING_ROLL"),
                   lambda current: current.inventories["1"][0].update(quantity=0),
                   lambda current: setattr(current, "paused", True)):
        asyncio.run(scenario(change))


def test_registered_input_reused_owned_and_stale_reply_consumed():
    async def run():
        calls = []
        async def execute(*args):
            calls.append(args)
        current, _dnd, bot, service = make_service(execute=execute)
        await service.command(Message(bot, current))
        await service.callback(Callback(bot, current, button(latest_markup(bot), "👤 Герой")))
        compose = button(latest_markup(bot), "✍️ Свой ход")
        await service.callback(Callback(bot, current, compose, message_id=102))
        await service.callback(Callback(bot, current, compose, message_id=102))
        assert len(current.menu_action_prompts) == 1
        assert len(bot.sent) == 3
        prompt_id = int(next(iter(current.menu_action_prompts)))
        info_reply = Message(bot, current, text="как работает меню", reply=101)
        assert await service.handle_message(info_reply)
        assert calls == []
        assert await service.handle_message(Message(bot, current, actor=2, text="Я тоже иду", reply=prompt_id))
        assert calls == []
        current.state_revision += 1  # Another player's resource change doesn't close this window.
        message = Message(bot, current, text="Отвлекаю стража", reply=prompt_id)
        assert await service.handle_message(message)
        assert calls[-1][3]["kind"] == "FREE_TEXT"
        assert calls[-1][3]["expected_revision"] == current.state_revision
        current.turn_id = "later"
        assert await service.handle_message(Message(bot, current, text="Запоздавший ход", reply=prompt_id))
        assert len(calls) == 1
    asyncio.run(run())


def test_known_scene_rule_has_explicit_confirmation_and_hidden_rule_stays_hidden():
    async def run():
        current, _dnd, bot, service = make_service()
        current.scene_objects["cart"]["interactions"]["secret"] = {"label": "Скрытая ловушка", "hidden": True}
        await service.command(Message(bot, current))
        await service.callback(Callback(bot, current, button(latest_markup(bot), "🔎 Телега")))
        labels = [item.text for row in latest_markup(bot).inline_keyboard for item in row]
        assert "Спрятаться" in labels and "Скрытая ловушка" not in labels
        await service.callback(Callback(bot, current, button(latest_markup(bot), "Спрятаться"), message_id=102))
        assert "Телега — Спрятаться" in bot.edited[-1].text
        token = button(latest_markup(bot), "✅ Подтвердить").removeprefix(menu.CALLBACK_PREFIX)
        assert current.menu_ui_state["tokens"][token]["payload"]["inputs"] == {"rule_id": "hide"}
    asyncio.run(run())


def test_object_confirmation_previews_known_check_outcomes_without_applying_them():
    async def run():
        calls = []
        async def execute(*args):
            calls.append(args)
        current, _dnd, bot, service = make_service(execute=execute)
        rule = {"label": "Толкнуть телегу", "uncertain": True, "ability": "STR", "dc": 12,
                "failure_price": True, "clean_success_margin": 5,
                "success": {"text": "Проход перекрыт.", "effects": [{"kind": "object", "object_id": "cart", "state": "перекрывает проход"}]},
                "failure": {"text": "Телега загрохотала.", "effects": [{"kind": "clock", "clock_id": "gates", "delta": 1}]},
                "success_with_cost": {"text": "Проход перекрыт, но стража услышала шум.",
                    "effects": [{"kind": "clock", "clock_id": "gates", "delta": 1}]}}
        rule["success"]["effects"].extend({"kind": "fact", "text": f"Последствие {index}"} for index in range(1, 5))
        current.scene_objects["cart"]["interactions"] = {"push": rule}
        before = menu.snapshot_session(current)
        await service.command(Message(bot, current))
        await service.callback(Callback(bot, current, button(latest_markup(bot), "🔎 Телега")))
        await service.callback(Callback(bot, current, button(latest_markup(bot), "Толкнуть телегу"), message_id=102))
        preview = bot.edited[-1].text
        assert "Проверка: STR, DC 12" in preview
        assert "Успех: Проход перекрыт." in preview
        assert "Цена провала: Телега загрохотала." in preview and "Ворота +1" in preview
        assert "Успех с ценой: Проход перекрыт, но стража услышала шум." in preview
        assert "результат 12–16; без цены — от 17" in preview
        assert "Дополнительных эффектов: 1" in preview and "Последствие 4" not in preview
        assert menu.snapshot_session(current) == before
        assert calls == []
    asyncio.run(run())


def test_invalid_or_hidden_object_outcomes_never_become_action_previews():
    async def run():
        current, _dnd, bot, service = make_service()
        valid = {"label": "Публичное действие", "uncertain": False,
                 "success": {"text": "Известный итог.", "effects": []}}
        nested_secret = copy.deepcopy(valid)
        nested_secret["label"] = "Скрытый исход"
        nested_secret["success"].update(hidden=True, text="SECRET OUTCOME")
        private_effect = copy.deepcopy(valid)
        private_effect["label"] = "Скрытый эффект"
        private_effect["success"]["effects"] = [{"kind": "fact", "text": "SECRET EFFECT", "hidden": True}]
        invalid = {"label": "Неполное правило", "uncertain": True, "ability": "STR", "dc": 12}
        current.scene_objects["cart"]["interactions"] = {
            "valid": valid, "secret": nested_secret, "private_effect": private_effect, "invalid": invalid}
        await service.command(Message(bot, current))
        await service.callback(Callback(bot, current, button(latest_markup(bot), "🔎 Телега")))
        labels = [item.text for row in latest_markup(bot).inline_keyboard for item in row]
        assert "Публичное действие" in labels
        assert not {"Скрытый исход", "Скрытый эффект", "Неполное правило"} & set(labels)
        data = button(latest_markup(bot), "Публичное действие")
        # The same opaque action button must be revalidated if its rule changes.
        current.scene_objects["cart"]["interactions"]["valid"] = nested_secret
        callback = Callback(bot, current, data, message_id=102)
        await service.callback(callback)
        assert "недоступно" in callback.answers[-1]
        assert all("SECRET" not in row.text for row in bot.sent + bot.edited)
    asyncio.run(run())


def test_menu_attack_confirmation_explicitly_shows_unarmed_weapon_choice():
    async def run():
        calls = []
        async def execute(*args):
            calls.append(args)
        current, _dnd, bot, service = make_service(execute=execute)
        await service.command(Message(bot, current))
        await service.callback(Callback(bot, current, button(latest_markup(bot), "👹 Враги")))
        await service.callback(Callback(bot, current, button(latest_markup(bot), "⚔️ Страж"), message_id=102))
        assert "Атаковать → Страж" in bot.edited[-1].text
        assert "Оружие: без оружия" in bot.edited[-1].text
        assert calls == [] and current.enemy_combatants["guard"]["hp"] == 10
    asyncio.run(run())


def test_hidden_fields_and_unknown_journal_payloads_are_not_rendered():
    current = session(event_journal=[{"type": "UNKNOWN", "data": {"prompt": "password"}},
                                     {"type": "THREAT_CHANGED", "data": {"before": {"level": 1}, "after": {"level": 2}}}],
                      action_records=[{"kind": "ATTACK", "actor_id": 1, "result": {"prompt": "password"}},
                                      {"kind": "ATTACK", "hidden": True, "public_summary": "SECRET"}])
    current.inventories["1"].append({"name": "SECRET", "hidden": True})
    current.scene_objects["hidden"] = {"name": "SECRET", "secret": True}
    snapshot = menu.snapshot_session(current)
    for page in menu._PAGES:
        text = menu.render_page(snapshot, page, 1)
        assert "password" not in text and "SECRET" not in text
    assert "1 → 2" in menu.render_journal(snapshot)


def test_ui_registry_restored_without_reusing_unknown_tokens():
    from AI.dnd_campaign_state import DndCampaignStatePolicy
    from aiogram import Router
    current, dnd, _bot, _service = make_service()
    policy = DndCampaignStatePolicy(lambda _: None, lambda _: {}, lambda _s, _r: None)
    router = Router()
    configured = menu.configure_dnd_menu(dnd, router, state_policy=policy)
    assert menu.configure_dnd_menu(dnd, router) is configured
    policy.ensure(current)
    current.menu_info_message_ids = [91]
    current.menu_action_prompts = {"92": {"actor_id": 1, "turn_id": "old"}}
    restored = session()
    policy.restore(restored, policy.state(current))
    assert restored.menu_info_message_ids == [91]
    assert restored.menu_action_prompts["92"]["turn_id"] == "old"
    assert restored.menu_ui_state["tokens"] == {}


def test_fallback_rejects_information_and_registered_input_including_old_turns(monkeypatch):
    from tests import test_smoke_imports  # noqa: F401
    from AI import dnd, dnd_any_bot_reply
    current = session()
    current.menu_info_message_ids = [91]
    current.menu_action_prompts = {"92": {"actor_id": 1, "turn_id": "old"}}
    monkeypatch.setitem(dnd.dnd_sessions, current.chat_id, current)
    for message_id in (91, 92):
        message = Message(FakeBot(), current, text="иду вперёд", reply=message_id)
        assert not dnd_any_bot_reply.is_any_bot_action_reply(message)
        current.state = "WAITING_POLL"
        current.pending_poll = {"options": ["Первый", "Второй"]}
        assert not dnd_any_bot_reply.is_any_bot_poll_reply(message)
        current.state = "WAITING_ACTION"


def test_shared_card_only_navigates_and_public_wait_requires_a_saved_rule():
    async def run():
        current, _dnd, bot, service = make_service()
        current.local_wait_rule = {"label": "Переждать патруль", "uncertain": False,
                                  "success": {"text": "Патруль прошёл мимо.", "effects": []}}
        await service.command(Message(bot, current))
        shared = [item.text for row in latest_markup(bot).inline_keyboard for item in row]
        assert "✍️ Свой ход" not in shared and "⏳ Подождать" not in shared
        await service.callback(Callback(bot, current, button(latest_markup(bot), "👤 Герой")))
        await service.callback(Callback(bot, current, button(latest_markup(bot), "🔄 Обновить"), message_id=102))
        await service.show(bot, current, 1, "overview")
        assert button(latest_markup(bot), "⏳ Подождать")
        await service.callback(Callback(bot, current, button(latest_markup(bot), "⏳ Подождать"), message_id=102))
        assert "Патруль прошёл мимо." in bot.edited[-1].text
        current.local_wait_rule["public"] = False
        await service.show(bot, current, 1, "overview")
        assert all(item.text != "⏳ Подождать" for row in latest_markup(bot).inline_keyboard for item in row)
        current.local_wait_rule = {"label": "Ожидание", "uncertain": False}  # No authored outcome.
        await service.show(bot, current, 1, "overview")
        assert all(item.text != "⏳ Подождать" for row in latest_markup(bot).inline_keyboard for item in row)
    asyncio.run(run())


def test_archetype_choices_are_actor_owned_confirmed_and_available_only_in_lobby():
    async def run():
        calls = []
        async def execute(*args):
            calls.append(args)
        current, _dnd, bot, service = make_service(execute=execute)
        current.state = "LOBBY"
        await service.command(Message(bot, current))
        await service.callback(Callback(bot, current, button(latest_markup(bot), "👤 Герой")))
        choice = button(latest_markup(bot), "🎯 Харизма")
        await service.callback(Callback(bot, current, choice, actor=2, message_id=102))
        assert calls == []
        await service.callback(Callback(bot, current, choice, message_id=102))
        assert "CHA 16" in bot.edited[-1].text
        await service.callback(Callback(bot, current, button(latest_markup(bot), "✅ Подтвердить"), message_id=102))
        assert calls[0][3]["kind"] == "CHOOSE_ARCHETYPE" and calls[0][3]["choice"] == "харизма"
        current.state = "WAITING_ACTION"
        await service.show(bot, current, 1, "hero")
        assert all(not item.text.startswith("🎯 ") for row in latest_markup(bot).inline_keyboard for item in row)
    asyncio.run(run())
