"""Салон «Гуру»: Telegram-слой (флаг, личка, паузы, партия, зрители, таймаут, журнал)."""

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
        self.cleared = []
        self.actions = []
        self._next_id = 1000

    async def send_message(self, chat_id, text, **kwargs):
        self._next_id += 1
        self.sent.append(SimpleNamespace(chat_id=chat_id, text=text, message_id=self._next_id, **kwargs))
        return SimpleNamespace(message_id=self._next_id)

    async def edit_message_text(self, **kwargs):
        self.edits.append(SimpleNamespace(**kwargs))

    async def edit_message_reply_markup(self, **kwargs):
        self.cleared.append(SimpleNamespace(**kwargs))

    async def send_chat_action(self, **kwargs):
        self.actions.append(SimpleNamespace(**kwargs))


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
def pauses(monkeypatch):
    recorded = []

    async def fake_sleep(seconds):
        recorded.append(seconds)

    monkeypatch.setattr(guru_salon, "_sleep", fake_sleep)
    return recorded


@pytest.fixture
def bot(monkeypatch, flags, clock, pauses):
    monkeypatch.setattr(guru_salon, "_registry", rules.SessionRegistry())
    monkeypatch.setattr(guru_salon, "_rng", random.Random(3))
    monkeypatch.setattr(guru_salon, "_reports", {})
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


def first_action(prefix, chat_id=CHAT):
    return next(a.id for a in rules.available_actions(current(chat_id)) if a.id.startswith(prefix))


def serve_client(bot, chat_id=CHAT, confidence="maybe"):
    press(bot, "c:stop", chat_id=chat_id)
    press(bot, "go", chat_id=chat_id)
    for phase in ("who", "what", "when"):
        press(bot, first_action(f"s:{phase}:", chat_id), chat_id=chat_id)
    press(bot, f"a:{confidence}", chat_id=chat_id)


def next_client(bot, chat_id=CHAT):
    if current(chat_id) is not None:
        press(bot, "next", chat_id=chat_id)


def play_shift(bot, chat_id=CHAT):
    for _ in range(3):
        serve_client(bot, chat_id=chat_id)
        next_client(bot, chat_id=chat_id)


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


@pytest.mark.parametrize("text", ["гуру", "салон", "гуру лаба", "лаба вкл гуру", None])
def test_other_text_passes_through(flags, text):
    assert not _accepts(guru_salon.router.message, message(FakeBot(), text))


def test_router_is_registered_before_dialog():
    from handlers import ROUTERS, dialog

    assert ROUTERS.index(guru_salon.router) < ROUTERS.index(dialog.router)


def test_works_in_private_chat_with_the_bot(bot, events):
    assert _accepts(guru_salon.router.message, message(bot, "гуру лаб", chat_id=PLAYER))
    start(bot, chat_id=PLAYER)
    play_shift(bot, chat_id=PLAYER)
    assert current(PLAYER) is None
    assert names(events())[0] == "start" and names(events())[-1] == "finish"


# --- партия и паузы ---------------------------------------------------------

def test_start_sends_intro_and_story_in_beats(bot, events, pauses):
    start(bot)

    session = current()
    assert session.host.user_id == PLAYER
    intro, *story = bot.sent
    assert "открывает салон" in intro.text and "легенды салона" in intro.text
    assert story[0].text.startswith("Клиент 1/3") and "🚪" in story[0].text
    assert len(story) == 1 + len(session.client.story.told)
    assert all(m.reply_markup is None for m in story[:-1]) and story[-1].reply_markup is not None
    assert session.message_id == story[-1].message_id and "Что уточнить" in story[-1].text
    told = len(session.client.story.told)
    assert told * rules.PACE_TELL[0] <= sum(pauses) <= told * sum(rules.PACE_TELL)
    assert bot.actions and bot.actions[0].action == "typing"
    (record,) = events()
    assert record["event"] == "start" and record["data"]["via"] == "command" and record["data"]["rules"] == rules.RULES_VERSION


def test_question_removes_old_buttons_and_answers_after_a_pause(bot, events, pauses):
    start(bot)
    session = current()
    old = session.message_id
    pauses.clear()
    question = session.client.story.questions[0]

    press(bot, f"c:{question.id}")

    assert bot.cleared[-1].message_id == old
    assert rules.PACE_ANSWER[0] <= sum(pauses) <= sum(rules.PACE_ANSWER)
    reply = bot.sent[-1]
    assert reply.text.startswith(f"— Ты: «{question.label}»") and reply.reply_markup is not None
    assert session.message_id == reply.message_id
    record = events()[-1]
    assert record["event"] == "action" and record["data"]["kind"] == "question"


def test_divination_edits_ritual_into_omen(bot, events, pauses):
    start(bot)
    press(bot, "c:stop")
    pauses.clear()
    sent_before = len(bot.sent)

    press(bot, "m:tarot")

    assert len(bot.sent) == sent_before + 1 and "колод" in bot.sent[-1].text
    edit = bot.edits[-1]
    assert edit.message_id == bot.sent[-1].message_id and "Расклад" in edit.text and "Что озвучить" in edit.text
    assert edit.reply_markup is not None and rules.PACE_RITUAL[0] <= sum(pauses) <= sum(rules.PACE_RITUAL)
    assert current().message_id == bot.sent[-1].message_id


def test_long_pause_keeps_typing_indicator_alive(bot, pauses):
    session = rules.new_session(session_id="ab12cd34ef56", chat_id=CHAT, player_id=PLAYER, player_name="Зоя",
                                rng=random.Random(1), now=0)
    asyncio.run(guru_salon._pause(bot, session, 6.0))
    assert pauses == [guru_salon.TYPING_REFRESH_S, 2.0] and len(bot.actions) == 2


def test_voiced_interpretation_shows_reaction_and_status(bot, events):
    start(bot)
    press(bot, "c:stop")
    press(bot, "m:tarot")
    tag = current().client.pending.offered[0]

    press(bot, f"i:{tag}")

    reply = bot.sent[-1]
    assert reply.text.startswith("— Ты:") and reply.text.count("\n") >= 2
    record = events()[-1]
    assert record["data"]["kind"] == "reading" and record["data"]["voiced"] == tag


def test_constructor_edits_one_message(bot, events):
    start(bot)
    press(bot, "c:stop")
    press(bot, "go")
    constructor = current().message_id
    sent_before = len(bot.sent)

    press(bot, first_action("s:who:"))
    press(bot, first_action("s:what:"))

    assert len(bot.sent) == sent_before
    assert bot.edits[-1].message_id == constructor and "Когда?" in bot.edits[-1].text


def test_full_party_logs_everything(bot, events, clock):
    start(bot)
    session = current()
    for _ in range(3):
        clock.now += 60
        serve_client(bot)
        next_client(bot)

    records = events()
    kinds = names(records)
    assert kinds[0] == "start" and kinds[-1] == "finish" and kinds.count("client_reveal") == 3
    assert {record["session"] for record in records} == {session.session_id}
    finish = records[-1]["data"]
    for key in ("max_streak", "legend_left", "hits", "partials", "misses", "exposures", "methods_used", "duration_s"):
        assert key in finish
    summary = bot.sent[-1]
    assert "Хотите ещё одну смену?" in summary.text
    labels = [button.text for row in summary.reply_markup.inline_keyboard for button in row]
    assert labels == [textui.AGAIN_YES, textui.AGAIN_NO]


def test_client_result_offers_report_and_next(bot, events):
    start(bot)
    serve_client(bot)
    session = current()
    assert session is not None and session.client.phase == "done"
    result = bot.sent[-1]
    labels = [button.text for row in result.reply_markup.inline_keyboard for button in row]
    assert labels == [textui.REPORT_BUTTON, "🚪 Следующий клиент"]
    assert "📜" in result.text and "Легенда" in result.text and "Разбор" not in result.text


def test_report_button_shows_details_once(bot, events):
    start(bot)
    session = current()
    serve_client(bot)
    data = textui.report_data(session.session_id, 0)

    first = callback(bot, data, user_id=WATCHER, message_id=55)
    asyncio.run(guru_salon.salon_callback(first))
    assert "📋 Разбор приёма" in bot.sent[-1].text and bot.sent[-1].reply_to_message_id == 55

    second = callback(bot, data, message_id=55)
    asyncio.run(guru_salon.salon_callback(second))
    assert "уже показан" in second.answers[0]


def test_next_client_arrives_on_button(bot, events):
    start(bot)
    serve_client(bot)
    first_story = current().client.story.id
    press(bot, "next")
    assert current().client.story.id != first_story and current().client.phase == "clarify"
    assert any(m.text.startswith("Клиент 2/3") for m in bot.sent)


def test_spectator_cannot_play_but_is_counted(bot, events):
    start(bot)
    step = current().step
    query = press(bot, "c:stop", user_id=WATCHER)
    assert "мадам Зоя" in query.answers[0] and current().step == step
    assert names(events()) == ["start", "spectator"] and events()[-1]["user_id"] == WATCHER


def test_double_click_plays_once(bot, events):
    start(bot)
    session = current()
    question = session.client.story.questions[0]
    data = textui.encode(rules.build_view(session), f"c:{question.id}")
    first = callback(bot, data, message_id=session.message_id)
    second = callback(bot, data, message_id=session.message_id)
    asyncio.run(guru_salon.salon_callback(first))
    asyncio.run(guru_salon.salon_callback(second))
    assert names(events()) == ["start", "action"]
    assert second.answers == ["Эта кнопка уже не работает."]


def test_second_salon_in_chat_is_rejected(bot, events):
    start(bot)
    start(bot, user_id=WATCHER)
    assert current().host.user_id == PLAYER and "занят" in bot.sent[-1].text
    assert events()[-1]["event"] == "start_rejected"


def test_replay_yes_starts_new_party_and_no_closes(bot, events):
    start(bot)
    old = current().session_id
    play_shift(bot)

    yes = callback(bot, textui.again_data(old, "yes"), user_id=WATCHER)
    asyncio.run(guru_salon.salon_callback(yes))
    assert current().host.user_id == WATCHER
    answer, started = events()[-2:]
    assert answer["event"] == "replay_answer" and answer["data"]["answer"] == "yes"
    assert started["data"]["via"] == "replay" and started["data"]["previous"] == old

    no = callback(bot, textui.again_data(old, "no"), message_id=77)
    asyncio.run(guru_salon.salon_callback(no))
    assert events()[-1]["data"]["answer"] == "no" and bot.cleared[-1].message_id == 77


# --- брошенные партии -------------------------------------------------------

def test_idle_salon_is_abandoned_after_fifteen_minutes(bot, events, clock):
    start(bot)
    press(bot, "c:stop")
    session = current()

    clock.now += rules.IDLE_TIMEOUT_S - 1
    assert asyncio.run(guru_salon.sweep_idle_salons(bot)) == 0
    clock.now += 1
    assert asyncio.run(guru_salon.sweep_idle_salons(bot)) == 1

    assert current() is None
    record = events()[-1]
    assert record["event"] == "abandon" and record["session"] == session.session_id
    data = record["data"]
    assert data["reason"] == "timeout" and data["idle_s"] == 900 and data["phase"] == "divine"
    assert bot.cleared[-1].message_id == session.message_id
    assert textui.ABANDON_NOTES["timeout"] in bot.sent[-1].text and "брошен" in bot.sent[-1].text


def test_old_buttons_do_nothing_after_timeout(bot, events, clock):
    start(bot)
    session = current()
    data = textui.encode(rules.build_view(session), "c:stop")
    clock.now += rules.IDLE_TIMEOUT_S
    asyncio.run(guru_salon.sweep_idle_salons(bot))
    query = callback(bot, data, message_id=session.message_id)
    asyncio.run(guru_salon.salon_callback(query))
    assert query.answers == ["Эта кнопка уже не работает."]
    assert names(events()) == ["start", "abandon"]


def test_stop_during_a_pause_interrupts_the_story(bot, events, monkeypatch):
    start_session = None

    async def stop_midway(seconds):
        nonlocal start_session
        session = current()
        if session is not None:
            start_session = session
            await guru_salon.stop_salon(message(bot, "гуру лаб стоп"))

    monkeypatch.setattr(guru_salon, "_sleep", stop_midway)
    start(bot)

    assert current() is None and start_session is not None
    assert not any(start_session.client.story.told[-1] in m.text for m in bot.sent)
    assert names(events()) == ["start", "abandon"]


def test_start_over_expired_salon_abandons_it_first(bot, events, clock):
    start(bot)
    old = current()
    clock.now += rules.IDLE_TIMEOUT_S + 5
    start(bot, user_id=WATCHER)
    assert current().host.user_id == WATCHER
    assert names(events()) == ["start", "abandon", "start"] and events()[1]["session"] == old.session_id


def test_player_can_quit(bot, events):
    start(bot)
    asyncio.run(guru_salon.stop_salon(message(bot, "гуру лаб стоп")))
    assert current() is None and events()[-1]["data"]["reason"] == "quit"
    assert textui.ABANDON_NOTES["quit"] in bot.sent[-1].text


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
    assert "и так закрыт" in msg.replies[0] and events() == []


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
    record = events()[-1]
    assert current() is None and record["session"] == session.session_id and record["data"]["reason"] == "restart"


def test_failed_send_does_not_leave_zombie_salon(bot, events, monkeypatch):
    from aiogram.exceptions import TelegramForbiddenError

    async def refuse(*args, **kwargs):
        raise TelegramForbiddenError(method=None, message="bot was kicked")

    monkeypatch.setattr(bot, "send_message", refuse)
    start(bot)
    assert current() is None
    assert names(events()) == ["start", "abandon"] and events()[-1]["data"]["reason"] == "send_failed"


def test_failed_send_mid_game_abandons_instead_of_hanging(bot, events, monkeypatch):
    from aiogram.exceptions import TelegramForbiddenError

    start(bot)

    async def refuse(*args, **kwargs):
        raise TelegramForbiddenError(method=None, message="bot was kicked")

    monkeypatch.setattr(bot, "send_message", refuse)
    press(bot, "c:stop")
    assert current() is None and events()[-1]["data"]["reason"] == "send_failed"
