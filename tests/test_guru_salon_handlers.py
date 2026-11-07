"""Салон «Гуру»: Telegram-слой (флаг эксперимента, партия, зрители, таймаут, журнал)."""

from tests import test_smoke_imports  # noqa: F401  (env + моки до импорта handlers)

import asyncio
import random
from types import SimpleNamespace

import pytest

from core.settings import ADMIN_ID
from features.lab import events as lab_events
from features.lab import flags as lab_flags
from features.lab.events import EventLog, read_events
from features.lab.flags import ExperimentFlags
from games.guru import engine, render
from handlers import guru_salon

CHAT = -100900
PLAYER = 501
WATCHER = 502


class MemoryRepository:
    def load(self):
        raise FileNotFoundError

    def save(self, value):
        pass


class FakeBot:
    def __init__(self):
        self.sent = []
        self.edits = []
        self._next_id = 1000

    async def send_message(self, chat_id, text, **kwargs):
        self._next_id += 1
        self.sent.append(SimpleNamespace(chat_id=chat_id, text=text, message_id=self._next_id, **kwargs))
        return SimpleNamespace(message_id=self._next_id)

    async def edit_message_text(self, **kwargs):
        self.edits.append(SimpleNamespace(**kwargs))


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


@pytest.fixture
def flags(monkeypatch):
    instance = ExperimentFlags(MemoryRepository())
    instance.load()
    instance.enable(CHAT, "guru")
    monkeypatch.setattr(lab_flags, "_flags", instance)
    return instance


@pytest.fixture
def events(monkeypatch, tmp_path):
    log = EventLog(tmp_path / "lab_events.jsonl")
    monkeypatch.setattr(lab_events, "_event_log", log)

    def read():
        return list(read_events(log.path)) if log.path.exists() else []

    return read


@pytest.fixture
def clock(monkeypatch):
    instance = Clock()
    monkeypatch.setattr(guru_salon, "_clock", instance)
    return instance


@pytest.fixture
def bot(monkeypatch, flags, clock):
    monkeypatch.setattr(guru_salon, "_registry", engine.SalonRegistry())
    monkeypatch.setattr(guru_salon, "_rng", random.Random(3))
    return FakeBot()


def user(user_id, name="Зоя"):
    return SimpleNamespace(id=user_id, first_name=name, username=None)


def message(bot, text, user_id=PLAYER, chat_id=CHAT):
    replies = []

    async def reply(answer, **kwargs):
        replies.append(answer)

    return SimpleNamespace(
        text=text,
        chat=SimpleNamespace(id=chat_id),
        from_user=user(user_id, "Зоя" if user_id == PLAYER else "Гоша"),
        bot=bot,
        message_id=1,
        message_thread_id=None,
        is_topic_message=False,
        reply=reply,
        replies=replies,
    )


def callback(bot, data, user_id=PLAYER, message_id=None, chat_id=CHAT):
    answers = []

    async def answer(text=None, **kwargs):
        answers.append(text)

    return SimpleNamespace(
        data=data,
        from_user=user(user_id),
        message=SimpleNamespace(chat=SimpleNamespace(id=chat_id), message_id=message_id, message_thread_id=None),
        bot=bot,
        answer=answer,
        answers=answers,
    )


def current():
    return guru_salon._registry.get(CHAT)


def press(bot, move, user_id=PLAYER):
    salon = current()
    query = callback(bot, render.move_callback(salon, move), user_id=user_id, message_id=salon.message_id)
    asyncio.run(guru_salon.salon_callback(query))
    return query


def start(bot, user_id=PLAYER):
    msg = message(bot, "Салон", user_id=user_id)
    asyncio.run(guru_salon.start_salon(msg))
    return msg


def names(records):
    return [record["event"] for record in records]


# --- флаг эксперимента ------------------------------------------------------

def _accepts(observer, event):
    return any(asyncio.run(handler.check(event))[0] for handler in observer.handlers)


def test_salon_does_not_exist_where_experiment_is_off(flags, monkeypatch):
    bot = FakeBot()
    start_msg = message(bot, "салон")
    other_chat = message(bot, "салон", chat_id=-1)
    button = callback(bot, "guru:abc:0:0:bill", chat_id=-1)

    assert _accepts(guru_salon.router.message, start_msg)
    assert not _accepts(guru_salon.router.message, other_chat)
    assert not _accepts(guru_salon.router.callback_query, button)
    flags.disable(CHAT, "guru")
    assert not _accepts(guru_salon.router.message, start_msg)


@pytest.mark.parametrize("text", ["салон", "  САЛОН ", "салон стоп", "Салон закрыть"])
def test_commands_match(flags, text):
    assert _accepts(guru_salon.router.message, message(FakeBot(), text))


@pytest.mark.parametrize("text", ["салоны", "салон красоты", "гороскоп", "мой салон", None])
def test_other_text_passes_through(flags, text):
    assert not _accepts(guru_salon.router.message, message(FakeBot(), text))


def test_router_is_registered_before_dialog():
    from handlers import ROUTERS, dialog

    assert ROUTERS.index(guru_salon.router) < ROUTERS.index(dialog.router)


# --- партия -----------------------------------------------------------------

def test_start_sends_rules_and_first_client_and_logs(bot, events):
    start(bot)

    salon = current()
    assert salon is not None and salon.player_id == PLAYER and salon.player_name == "Зоя"
    rules, client = bot.sent
    assert "открывает салон" in rules.text
    assert "клиент 1/5" in client.text
    assert client.reply_markup is not None and salon.message_id == client.message_id
    (record,) = events()
    assert record["event"] == "start" and record["exp"] == "guru"
    assert record["user_id"] == PLAYER and record["session"] == salon.session_id
    assert record["data"]["via"] == "command"
    assert sorted(record["data"]["clients"]) == sorted(salon.plan)
    assert record["data"]["rules"] == engine.RULES_VERSION


def test_full_party_logs_actions_and_finish(bot, events, clock):
    start(bot)
    session = current().session_id

    moves = 0
    while current() is not None:
        clock.now += 7
        press(bot, "bill" if moves % 2 else "barnum")
        moves += 1

    records = events()
    assert names(records) == ["start"] + ["action"] * moves + ["finish"]
    assert {record["session"] for record in records} == {session}
    action = records[1]["data"]
    assert action["move"] == "barnum" and action["client_no"] == 0 and action["turn"] == 0
    assert {"client", "hit", "clue", "trust", "money_delta", "result"} <= action.keys()
    finish = records[-1]["data"]
    assert finish["clients"] == 5 and finish["reason"] == "completed"
    assert finish["duration_s"] == pytest.approx(7 * moves)
    assert {"money", "reputation", "exposures", "title"} <= finish.keys()
    summary = bot.sent[-1]
    assert "закрыт на сегодня" in summary.text
    assert summary.reply_markup.inline_keyboard[0][0].callback_data == render.AGAIN_CALLBACK
    assert len([m for m in bot.sent if "клиент " in m.text and "/5" in m.text]) == 5


def test_move_edits_client_message_in_place(bot, events):
    start(bot)
    message_id = current().message_id

    press(bot, "barnum")

    edit = bot.edits[-1]
    assert edit.message_id == message_id and edit.chat_id == CHAT
    assert "— Ты:" in edit.text and edit.reply_markup is not None


def test_spectator_cannot_play_but_is_counted(bot, events):
    start(bot)
    turn = current().client.turn

    query = press(bot, "specific", user_id=WATCHER)

    assert "мадам Зоя" in query.answers[0]
    assert current().client.turn == turn
    assert names(events()) == ["start", "spectator"]
    assert events()[-1]["user_id"] == WATCHER


def test_double_click_plays_once(bot, events):
    start(bot)
    salon = current()
    data = render.move_callback(salon, "barnum")

    first = callback(bot, data, message_id=salon.message_id)
    second = callback(bot, data, message_id=salon.message_id)
    asyncio.run(guru_salon.salon_callback(first))
    asyncio.run(guru_salon.salon_callback(second))

    assert names(events()) == ["start", "action"]
    assert second.answers == ["Эта кнопка уже не работает."]


def test_second_salon_in_chat_is_rejected(bot, events):
    start(bot)
    start(bot, user_id=WATCHER)

    assert current().player_id == PLAYER
    assert "занят" in bot.sent[-1].text and "Зоя" in bot.sent[-1].text
    record = events()[-1]
    assert record["event"] == "start_rejected" and record["data"]["reason"] == "busy"


def test_replay_button_starts_new_party_for_whoever_presses(bot, events):
    start(bot)
    while current() is not None:
        press(bot, "bill")

    query = callback(bot, render.AGAIN_CALLBACK, user_id=WATCHER)
    asyncio.run(guru_salon.salon_callback(query))

    assert current().player_id == WATCHER
    record = events()[-1]
    assert record["event"] == "start" and record["data"]["via"] == "replay"


# --- брошенные партии -------------------------------------------------------

def test_idle_salon_is_abandoned_by_sweep(bot, events, clock):
    start(bot)
    press(bot, "barnum")
    salon = current()

    clock.now += engine.IDLE_TIMEOUT_S - 1
    assert asyncio.run(guru_salon.sweep_idle_salons(bot)) == 0
    clock.now += 1
    assert asyncio.run(guru_salon.sweep_idle_salons(bot)) == 1

    assert current() is None
    record = events()[-1]
    assert record["event"] == "abandon" and record["session"] == salon.session_id
    data = record["data"]
    assert data["reason"] == "timeout" and data["idle_s"] == engine.IDLE_TIMEOUT_S
    assert data["clients"] == 0 and data["client_no"] == 0 and data["stuck_client"] == salon.plan[0]
    assert {"money", "reputation", "duration_s"} <= data.keys()
    assert render.ABANDON_LABELS["timeout"] in bot.edits[-1].text and bot.edits[-1].reply_markup is None
    assert "брошен" in bot.sent[-1].text


def test_old_buttons_do_nothing_after_timeout(bot, events, clock):
    start(bot)
    salon = current()
    data = render.move_callback(salon, "bill")
    clock.now += engine.IDLE_TIMEOUT_S
    asyncio.run(guru_salon.sweep_idle_salons(bot))

    query = callback(bot, data, message_id=salon.message_id)
    asyncio.run(guru_salon.salon_callback(query))

    assert query.answers == ["Эта кнопка уже не работает."]
    assert names(events()) == ["start", "abandon"]


def test_start_over_expired_salon_abandons_it_first(bot, events, clock):
    start(bot)
    old = current()
    clock.now += engine.IDLE_TIMEOUT_S + 5

    start(bot, user_id=WATCHER)

    assert current().player_id == WATCHER
    assert names(events()) == ["start", "abandon", "start"]
    assert events()[1]["session"] == old.session_id and events()[1]["data"]["reason"] == "timeout"


def test_player_can_quit(bot, events):
    start(bot)
    msg = message(bot, "салон стоп")

    asyncio.run(guru_salon.stop_salon(msg))

    assert current() is None
    assert events()[-1]["event"] == "abandon" and events()[-1]["data"]["reason"] == "quit"
    assert render.ABANDON_LABELS["quit"] in bot.edits[-1].text


def test_only_player_or_admin_can_quit(bot, events):
    start(bot)

    stranger = message(bot, "салон стоп", user_id=WATCHER)
    asyncio.run(guru_salon.stop_salon(stranger))
    assert current() is not None and "только мадам Зоя" in stranger.replies[0]

    admin = message(bot, "салон стоп", user_id=ADMIN_ID)
    asyncio.run(guru_salon.stop_salon(admin))
    assert current() is None


def test_quit_without_salon(bot, events):
    msg = message(bot, "салон стоп")
    asyncio.run(guru_salon.stop_salon(msg))
    assert "и так закрыт" in msg.replies[0]
    assert events() == []


def test_shutdown_logs_unfinished_parties(bot, events):
    start(bot)
    salon = current()

    async def run_and_stop():
        task = asyncio.ensure_future(guru_salon.guru_salon_timeout_loop(bot, interval=3600))
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(run_and_stop())

    assert current() is None
    record = events()[-1]
    assert record["event"] == "abandon" and record["session"] == salon.session_id
    assert record["data"]["reason"] == "restart"


def test_failed_send_does_not_leave_zombie_salon(bot, events, monkeypatch):
    from aiogram.exceptions import TelegramForbiddenError

    async def refuse(*args, **kwargs):
        raise TelegramForbiddenError(method=None, message="bot was kicked")

    monkeypatch.setattr(bot, "send_message", refuse)

    start(bot)

    assert current() is None
    assert names(events()) == ["start", "abandon"]
    assert events()[-1]["data"]["reason"] == "send_failed"
