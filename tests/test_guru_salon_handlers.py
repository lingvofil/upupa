"""Салон «Гуру»: Telegram-слой (флаг эксперимента, личка, партия, зрители, таймаут, журнал)."""

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
from games.guru import rules, textui
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
        self.markup_edits = []
        self._next_id = 1000

    async def send_message(self, chat_id, text, **kwargs):
        self._next_id += 1
        self.sent.append(SimpleNamespace(chat_id=chat_id, text=text, message_id=self._next_id, **kwargs))
        return SimpleNamespace(message_id=self._next_id)

    async def edit_message_text(self, **kwargs):
        self.edits.append(SimpleNamespace(**kwargs))

    async def edit_message_reply_markup(self, **kwargs):
        self.markup_edits.append(SimpleNamespace(**kwargs))


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
    instance.enable(PLAYER, "guru")  # личка игрока с ботом
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
    monkeypatch.setattr(guru_salon, "_registry", rules.SessionRegistry())
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


def current(chat_id=CHAT):
    return guru_salon._registry.get(chat_id)


def press(bot, action, user_id=PLAYER, chat_id=CHAT):
    session = current(chat_id)
    data = textui.encode(rules.build_view(session), action)
    query = callback(bot, data, user_id=user_id, message_id=session.message_id, chat_id=chat_id)
    asyncio.run(guru_salon.salon_callback(query))
    return query


def first_option(phase, chat_id=CHAT):
    return next(a.id for a in rules.available_actions(current(chat_id)) if a.id.startswith(f"s:{phase}:"))


def serve_client(bot, chat_id=CHAT, confidence="maybe"):
    press(bot, "go", chat_id=chat_id)
    for phase in ("who", "what", "when"):
        press(bot, first_option(phase, chat_id), chat_id=chat_id)
    press(bot, f"a:{confidence}", chat_id=chat_id)


def start(bot, user_id=PLAYER, chat_id=CHAT):
    msg = message(bot, "Гуру лаб", user_id=user_id, chat_id=chat_id)
    asyncio.run(guru_salon.start_salon(msg))
    return msg


def names(records):
    return [record["event"] for record in records]


# --- флаг эксперимента и команды --------------------------------------------

def _accepts(observer, event):
    return any(asyncio.run(handler.check(event))[0] for handler in observer.handlers)


def test_salon_does_not_exist_where_experiment_is_off(flags):
    bot = FakeBot()
    assert _accepts(guru_salon.router.message, message(bot, "гуру лаб"))
    assert not _accepts(guru_salon.router.message, message(bot, "гуру лаб", chat_id=-1))
    assert not _accepts(guru_salon.router.callback_query, callback(bot, "guru:abc:0:go", chat_id=-1))
    flags.disable(CHAT, "guru")
    assert not _accepts(guru_salon.router.message, message(bot, "гуру лаб"))


@pytest.mark.parametrize("text", ["гуру лаб", "  ГУРУ   ЛАБ ", "гуру лаб стоп"])
def test_commands_match(flags, text):
    assert _accepts(guru_salon.router.message, message(FakeBot(), text))


@pytest.mark.parametrize("text", ["гуру", "салон", "гуру лаба", "гуру лабы", "лаба вкл гуру", None])
def test_other_text_passes_through(flags, text):
    assert not _accepts(guru_salon.router.message, message(FakeBot(), text))


def test_router_is_registered_before_dialog():
    from handlers import ROUTERS, dialog

    assert ROUTERS.index(guru_salon.router) < ROUTERS.index(dialog.router)


def test_works_in_private_chat_with_the_bot(bot, events):
    """В личке chat_id = id пользователя; эксперимент включается там же командой «лаба вкл гуру»."""
    assert _accepts(guru_salon.router.message, message(bot, "гуру лаб", chat_id=PLAYER))
    start(bot, chat_id=PLAYER)
    for _ in range(3):
        serve_client(bot, chat_id=PLAYER)
    assert current(PLAYER) is None
    assert names(events())[0] == "start" and names(events())[-1] == "finish"
    assert {record["chat_id"] for record in events()} == {PLAYER}


# --- партия -----------------------------------------------------------------

def test_start_sends_intro_and_first_client_and_logs(bot, events):
    start(bot)

    session = current()
    assert session is not None and session.host.user_id == PLAYER and session.host.name == "Зоя"
    intro, client = bot.sent
    assert "открывает салон" in intro.text and "Легенда" in intro.text
    assert "клиент 1/3" in client.text and "📒 Блокнот" in client.text
    assert client.reply_markup is not None and session.message_id == client.message_id
    (record,) = events()
    assert record["event"] == "start" and record["exp"] == "guru"
    assert record["user_id"] == PLAYER and record["session"] == session.session_id
    assert record["data"]["via"] == "command" and record["data"]["legend"] == 2
    assert record["data"]["stories"] == list(session.plan) and record["data"]["rules"] == rules.RULES_VERSION


def test_full_party_logs_everything(bot, events, clock):
    start(bot)
    session = current()
    for _ in range(3):
        clock.now += 30
        serve_client(bot)

    records = events()
    kinds = names(records)
    assert kinds[0] == "start" and kinds[-1] == "finish"
    assert kinds.count("client_reveal") == 3
    assert [r["data"]["kind"] for r in records if r["event"] == "action"] == ["prediction"] * 3
    assert {record["session"] for record in records} == {session.session_id}
    reveal = next(r["data"] for r in records if r["event"] == "client_reveal")
    assert {"outcome", "slots", "hints", "echo_slots", "fee", "points", "legend_after", "duration_s"} <= reveal.keys()
    finish = records[-1]["data"]
    for key in ("max_streak", "legend_left", "hits", "partials", "misses", "exposures", "methods_used", "duration_s"):
        assert key in finish
    assert finish["clients"] == 3 and finish["reason"] in ("completed", "exposed")
    summary = bot.sent[-1]
    assert "Хотите ещё одну смену?" in summary.text
    labels = [button.text for row in summary.reply_markup.inline_keyboard for button in row]
    assert labels == [textui.AGAIN_YES, textui.AGAIN_NO]
    finished_edits = [edit for edit in bot.edits if "━━ Разбор ━━" in edit.text]
    assert len(finished_edits) == 3 and all(edit.reply_markup is None for edit in finished_edits)


def test_info_action_edits_message_and_logs(bot, events):
    start(bot)
    message_id = current().message_id

    press(bot, "m:tarot")

    edit = bot.edits[-1]
    assert edit.message_id == message_id and "🃏" in edit.text and edit.reply_markup is not None
    record = events()[-1]
    assert record["event"] == "action" and record["data"]["kind"] == "info" and record["data"]["method"] == "tarot"


def test_spectator_cannot_play_but_is_counted(bot, events):
    start(bot)
    step = current().step

    query = press(bot, "m:tarot", user_id=WATCHER)

    assert "мадам Зоя" in query.answers[0]
    assert current().step == step
    assert names(events()) == ["start", "spectator"] and events()[-1]["user_id"] == WATCHER


def test_double_click_plays_once(bot, events):
    start(bot)
    session = current()
    data = textui.encode(rules.build_view(session), "m:tarot")

    first = callback(bot, data, message_id=session.message_id)
    second = callback(bot, data, message_id=session.message_id)
    asyncio.run(guru_salon.salon_callback(first))
    asyncio.run(guru_salon.salon_callback(second))

    assert names(events()) == ["start", "action"]
    assert second.answers == ["Эта кнопка уже не работает."]


def test_second_salon_in_chat_is_rejected(bot, events):
    start(bot)
    start(bot, user_id=WATCHER)

    assert current().host.user_id == PLAYER
    assert "занят" in bot.sent[-1].text and "Зоя" in bot.sent[-1].text
    record = events()[-1]
    assert record["event"] == "start_rejected" and record["data"]["reason"] == "busy"


def test_replay_yes_starts_new_party_and_no_closes(bot, events):
    start(bot)
    old = current().session_id
    for _ in range(3):
        serve_client(bot)
        if current() is None:
            break

    yes = callback(bot, textui.again_data(old, "yes"), user_id=WATCHER)
    asyncio.run(guru_salon.salon_callback(yes))
    assert current().host.user_id == WATCHER
    answer, started = events()[-2:]
    assert answer["event"] == "replay_answer" and answer["data"]["answer"] == "yes" and answer["session"] == old
    assert started["event"] == "start" and started["data"]["via"] == "replay" and started["data"]["previous"] == old

    no = callback(bot, textui.again_data(old, "no"), message_id=77)
    asyncio.run(guru_salon.salon_callback(no))
    assert events()[-1]["data"]["answer"] == "no"
    assert bot.markup_edits[-1].message_id == 77


# --- брошенные партии -------------------------------------------------------

def test_idle_salon_is_abandoned_by_sweep(bot, events, clock):
    start(bot)
    press(bot, "m:tarot")
    session = current()

    clock.now += rules.IDLE_TIMEOUT_S - 1
    assert asyncio.run(guru_salon.sweep_idle_salons(bot)) == 0
    clock.now += 1
    assert asyncio.run(guru_salon.sweep_idle_salons(bot)) == 1

    assert current() is None
    record = events()[-1]
    assert record["event"] == "abandon" and record["session"] == session.session_id
    data = record["data"]
    assert data["reason"] == "timeout" and data["idle_s"] == rules.IDLE_TIMEOUT_S
    assert data["clients"] == 0 and data["client_no"] == 0 and data["phase"] == "research"
    assert textui.ABANDON_NOTES["timeout"] in bot.edits[-1].text and bot.edits[-1].reply_markup is None
    assert "брошен" in bot.sent[-1].text


def test_old_buttons_do_nothing_after_timeout(bot, events, clock):
    start(bot)
    session = current()
    data = textui.encode(rules.build_view(session), "go")
    clock.now += rules.IDLE_TIMEOUT_S
    asyncio.run(guru_salon.sweep_idle_salons(bot))

    query = callback(bot, data, message_id=session.message_id)
    asyncio.run(guru_salon.salon_callback(query))

    assert query.answers == ["Эта кнопка уже не работает."]
    assert names(events()) == ["start", "abandon"]


def test_start_over_expired_salon_abandons_it_first(bot, events, clock):
    start(bot)
    old = current()
    clock.now += rules.IDLE_TIMEOUT_S + 5

    start(bot, user_id=WATCHER)

    assert current().host.user_id == WATCHER
    assert names(events()) == ["start", "abandon", "start"]
    assert events()[1]["session"] == old.session_id and events()[1]["data"]["reason"] == "timeout"


def test_player_can_quit(bot, events):
    start(bot)
    asyncio.run(guru_salon.stop_salon(message(bot, "гуру лаб стоп")))

    assert current() is None
    assert events()[-1]["event"] == "abandon" and events()[-1]["data"]["reason"] == "quit"
    assert textui.ABANDON_NOTES["quit"] in bot.edits[-1].text


def test_only_player_or_admin_can_quit(bot, events):
    start(bot)

    stranger = message(bot, "гуру лаб стоп", user_id=WATCHER)
    asyncio.run(guru_salon.stop_salon(stranger))
    assert current() is not None and "только мадам Зоя" in stranger.replies[0]

    asyncio.run(guru_salon.stop_salon(message(bot, "гуру лаб стоп", user_id=ADMIN_ID)))
    assert current() is None


def test_quit_without_salon(bot, events):
    msg = message(bot, "гуру лаб стоп")
    asyncio.run(guru_salon.stop_salon(msg))
    assert "и так закрыт" in msg.replies[0]
    assert events() == []


def test_shutdown_logs_unfinished_parties(bot, events):
    start(bot)
    session = current()

    async def run_and_stop():
        task = asyncio.ensure_future(guru_salon.guru_salon_timeout_loop(bot, interval=3600))
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(run_and_stop())

    assert current() is None
    record = events()[-1]
    assert record["event"] == "abandon" and record["session"] == session.session_id
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
