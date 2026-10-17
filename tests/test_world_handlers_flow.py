"""Handler-level flows of World of Upupa with fake Telegram objects."""

import asyncio
from types import SimpleNamespace

import pytest
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import EditMessageText

from tests import test_smoke_imports  # noqa: F401  (env + моки)
import features.world.service as world_service_module
from features.world.service import WorldService
import handlers.world_common as common
import handlers.world_hub as hub
import handlers.world_interactions as interactions
from infrastructure.persistence.sqlite_world import SQLiteWorldRepository


def _run(coro):
    return asyncio.run(coro)


class FakeBot:
    def __init__(self):
        self.sent = []

    async def send_message(self, chat_id, text, **kwargs):
        self.sent.append((chat_id, text))
        return SimpleNamespace(message_id=len(self.sent))

    async def get_chat_member_count(self, chat_id):
        return 5


class FakeMessage:
    def __init__(self, chat_id, text=None, user_id=7, *, title="Чат"):
        self.chat = SimpleNamespace(id=chat_id, type="supergroup", title=title)
        self.text = text
        self.caption = None
        self.from_user = SimpleNamespace(id=user_id, full_name="Вася", username="vasya", is_bot=False)
        self.reply_to_message = None
        self.message_id = 50
        self.replies = []
        self.answers = []
        self.edits = []

    async def reply(self, text, **kwargs):
        self.replies.append(text)
        return SimpleNamespace(message_id=99)

    async def answer(self, text, **kwargs):
        self.answers.append(text)
        return SimpleNamespace(message_id=98)

    async def edit_text(self, text, **kwargs):
        if self.edits and self.edits[-1] == text:
            raise TelegramBadRequest(
                method=EditMessageText(text=text), message="Bad Request: message is not modified"
            )
        self.edits.append(text)

    async def edit_reply_markup(self, **kwargs):
        return None


class FakeQuery:
    def __init__(self, message, data, user_id=7):
        self.message = message
        self.data = data
        self.from_user = SimpleNamespace(id=user_id, full_name="Вася")
        self.id = "q1"
        self.answers = []

    async def answer(self, text=None, show_alert=False):
        self.answers.append((text, show_alert))


@pytest.fixture
def world(tmp_path, monkeypatch):
    repo = SQLiteWorldRepository(tmp_path / "world.db")
    repo.init_schema()
    service = WorldService(repo)
    world_service_module.configure_world_service(service)
    bot = FakeBot()
    monkeypatch.setattr(interactions, "bot", bot)
    monkeypatch.setattr(hub, "bot", bot)
    alpha = _run(service.enable_state(-1001, "Alpha"))
    beta = _run(service.enable_state(-1002, "Beta"))
    yield service, bot, alpha, beta
    world_service_module.configure_world_service(None)


def _diplomats(monkeypatch, allowed: bool):
    async def check(_chat_id, _user_id):
        return allowed

    monkeypatch.setattr(interactions, "is_diplomat", check)
    monkeypatch.setattr(common, "is_diplomat", check)


def test_text_alliance_proposal_requires_admin_or_ambassador(world, monkeypatch):
    service, bot, alpha, beta = world
    _diplomats(monkeypatch, False)
    message = FakeMessage(alpha.chat_id, f"упупа предложи союз {beta.world_id}")
    _run(interactions.diplomacy_text_command(message))
    assert message.replies == ["Это дипломатическое решение доступно администраторам и назначенному послу."]
    assert bot.sent == []

    _diplomats(monkeypatch, True)
    _run(interactions.diplomacy_text_command(message))
    assert any("Предложение союза" in text for _chat, text in bot.sent)


def test_repeated_menu_tap_does_not_crash(world, monkeypatch):
    service, bot, alpha, _beta = world
    message = FakeMessage(alpha.chat_id, "🌍 Мир Упупы")
    query = FakeQuery(message, "worldhub:main")
    _run(hub.world_hub_callback(query))
    _run(hub.world_hub_callback(query))
    assert len(message.edits) == 1
    assert message.answers == []


def test_war_needs_confirmation_and_starts_a_battle(world, monkeypatch):
    service, bot, alpha, beta = world
    _diplomats(monkeypatch, True)
    message = FakeMessage(alpha.chat_id, "menu")
    _run(interactions.world_action_callback(FakeQuery(message, f"worldx:do:war:{beta.world_id}")))
    assert "Объявить войну" in message.edits[-1]
    assert _run(service.get_relation(alpha.world_id, beta.world_id)) is None

    _run(interactions.world_action_callback(FakeQuery(message, f"worldx:confirm:war:{beta.world_id}")))
    assert _run(service.get_relation(alpha.world_id, beta.world_id)) == "war"
    assert len(service.battles.list_active_battles()) == 1
    assert any("Битва началась" in text for _chat, text in bot.sent)


def test_target_picker_is_paginated(world, monkeypatch):
    service, _bot, alpha, _beta = world
    for index in range(14):
        _run(service.enable_state(-2000 - index, f"Страна {index}"))
    _diplomats(monkeypatch, True)
    message = FakeMessage(alpha.chat_id, "menu")
    query = FakeQuery(message, "worldx:choose:insult")
    captured = {}

    async def fake_edit(msg, text, markup=None, **kwargs):
        captured["text"], captured["markup"] = text, markup

    monkeypatch.setattr(interactions, "safe_edit", fake_edit)
    _run(interactions.world_action_callback(query))
    buttons = [button.callback_data for row in captured["markup"].inline_keyboard for button in row]
    assert "стр. 1/2" in captured["text"]
    assert "worldx:choose:insult:1" in buttons
    assert sum(data.startswith("worldx:do:insult:") for data in buttons) == 10
