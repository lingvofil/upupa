import asyncio
from types import SimpleNamespace

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from AI import dnd_lobby_controls as controls


def _session(state="LOBBY"):
    return SimpleNamespace(
        chat_id=-100123,
        mode="participants",
        state=state,
        participants={
            "1": {"user_id": 1, "name": "Алиса"},
            "2": {"user_id": 2, "name": "Боря"},
        },
        character_profiles={"1": {"style": "старый образ"}, "2": {"style": "образ Бори"}},
        profile_options={"1": {"style": ["старый вариант"]}},
        heritage={"1": {"adventures": ["x"]}},
        inventories={"1": [{"name": "ключ"}]},
        reputations={"1": ["сомнительная репутация"]},
    )


class FakeDnd:
    def __init__(self, session):
        self.dnd_sessions = {session.chat_id: session}
        self.persist_calls = 0

    def persist_dnd_sessions(self):
        self.persist_calls += 1


class FakeMessage:
    def __init__(self, chat_id):
        self.chat = SimpleNamespace(id=chat_id)
        self.answers = []

    async def answer(self, text, reply_markup=None):
        self.answers.append((text, reply_markup))


class FakeCallback:
    def __init__(self, session, user_id=1):
        self.message = FakeMessage(session.chat_id)
        self.from_user = SimpleNamespace(id=user_id)
        self.bot = SimpleNamespace()
        self.answers = []

    async def answer(self, text=None, **kwargs):
        self.answers.append((text, kwargs))


def test_add_controls_places_leave_and_rebuild_before_start_and_is_idempotent():
    base = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🙋 Участвовать", callback_data="dnd:lobby:join")],
            [InlineKeyboardButton(text="▶️ Выбрать сюжет", callback_data="dnd:lobby:start")],
        ]
    )

    first = controls._add_controls(base)
    second = controls._add_controls(first)
    callbacks = [button.callback_data for row in second.inline_keyboard for button in row]

    assert callbacks == [
        "dnd:lobby:join",
        "dnd:lobby:rebuild",
        "dnd:lobby:leave",
        "dnd:lobby:start",
    ]
    assert callbacks.count("dnd:lobby:rebuild") == 1
    assert callbacks.count("dnd:lobby:leave") == 1


def test_leave_campaign_removes_participant_and_pregame_character_state():
    session = _session()
    dnd = FakeDnd(session)
    callback = FakeCallback(session)
    refreshed = []

    async def refresh_lobby(target_session, bot):
        refreshed.append((target_session, bot))

    campaign = SimpleNamespace(_refresh_lobby=refresh_lobby)

    asyncio.run(controls._leave_campaign(callback, dnd, campaign))

    assert "1" not in session.participants
    assert "2" in session.participants
    for attr in ("character_profiles", "profile_options", "heritage", "inventories", "reputations"):
        assert "1" not in getattr(session, attr)
    assert dnd.persist_calls == 1
    assert refreshed and refreshed[0][0] is session
    assert callback.answers[0][0] == "Вышел из кампании."


def test_rebuild_character_clears_profile_and_starts_with_gender_choice():
    session = _session()
    dnd = FakeDnd(session)
    callback = FakeCallback(session)
    refreshed = []
    generated = ["мужской", "женский"]

    def ensure(target_session):
        assert target_session is session

    async def refresh_lobby(target_session, bot):
        refreshed.append((target_session, bot))

    async def generate_profile_options(_dnd, target_session, user_id, step):
        assert target_session is session
        assert user_id == 1
        assert step == "gender"
        assert target_session.character_profiles["1"] == {}
        assert "1" not in target_session.profile_options
        target_session.profile_options["1"] = {"gender": list(generated)}
        return list(generated)

    def choice_text(step, options, heading):
        assert step == "gender"
        assert options == generated
        return f"{heading}: " + " | ".join(options)

    def profile_keyboard(user_id, step, options):
        return (user_id, step, tuple(options))

    campaign = SimpleNamespace(
        _ensure=ensure,
        _refresh_lobby=refresh_lobby,
        _generate_profile_options=generate_profile_options,
        _profile_choice_text=choice_text,
        _profile_keyboard=profile_keyboard,
    )

    asyncio.run(controls._rebuild_character(callback, dnd, campaign))

    assert session.character_profiles["1"] == {}
    assert session.profile_options["1"]["gender"] == generated
    assert dnd.persist_calls == 1
    assert refreshed and refreshed[0][0] is session
    assert callback.answers[0][0] == "Пересобираю персонажа."
    assert callback.message.answers[0][0].startswith("♻️ Пересобираем. Выбери")
    assert callback.message.answers[0][1] == (1, "gender", tuple(generated))


def test_gender_context_is_added_to_later_profile_generation():
    session = SimpleNamespace(character_profiles={"7": {"gender": "женский"}})

    prompt = controls._with_gender_context("base prompt", session, 7, "style")

    assert "Пол персонажа уже выбран: женский" in prompt
    assert "согласуй слова по роду" in prompt
    assert controls._with_gender_context("gender prompt", session, 7, "gender") == "gender prompt"


def test_gender_context_is_not_added_before_gender_is_chosen():
    session = SimpleNamespace(character_profiles={"7": {}})

    assert controls._with_gender_context("base prompt", session, 7, "style") == "base prompt"


def test_lobby_controls_are_blocked_after_campaign_start():
    session = _session(state="WAITING_ACTION")
    dnd = FakeDnd(session)
    callback = FakeCallback(session)
    campaign = SimpleNamespace()

    asyncio.run(controls._leave_campaign(callback, dnd, campaign))

    assert "1" in session.participants
    assert dnd.persist_calls == 0
    text, kwargs = callback.answers[0]
    assert "уже началась" in text
    assert kwargs.get("show_alert") is True
