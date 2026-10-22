import asyncio
from types import SimpleNamespace

import pytest

from tests import test_smoke_imports  # noqa: F401  (env + heavy-library mocks)


OWN_DREAMS = [
    "мне снилось что я летал над Москвой",
    "Мне сегодня приснился кот в сапогах",
    "видел сон, будто я сдаю экзамен голым",
    "видела во сне бывшего с арбузом",
    "Приснилось, что я опять в армии",
    "мне всю ночь снились какие-то крысы в пиджаках",
    "увидел во сне своего начальника",
    "мне снится каждую ночь одно и то же: лифт падает",
    "во сне я был пиратом",
    "у меня был такой сон: иду по лесу",
    "ты мне приснилась сегодня в шубе",
    "Снилось мне, что я женюсь на табуретке",
    "мне приснилось что я летаю, к чему это?",
    "короче сон: я в школе",
    "в моём сне был дракон",
    "мне был сон про бабушку",
    "я сегодня видел такой сон про бабушку",
]

NOT_OWN_DREAMS = [
    "тебе снилось что-нибудь?",
    "ему приснилось, что он миллионер",
    "мне такое и не снилось",
    "даже в страшном сне не приснится",
    "пусть тебе приснится котик",
    "чтобы мне приснилось что-нибудь хорошее",
    "сон — это важно",
    "я плохо сплю",
    "мне нужен СНИЛС",
    "ты видел сон?",
    "мне давно ничего не снилось",
    "приснилось ли тебе что-то",
    "мне кажется, тебе снилось",
    "твой сон про кота дурацкий",
    "живу как во сне я",
    "сонник миллера",
    "приснится же такое",
    "вы видели сон",
]


@pytest.mark.parametrize("text", OWN_DREAMS)
def test_own_dream_mentions_are_detected(text):
    from AI.dream_book import is_dream_mention, is_dream_teaser

    assert is_dream_mention(text)
    assert not is_dream_teaser(text)


@pytest.mark.parametrize("text", NOT_OWN_DREAMS)
def test_foreign_negated_and_idiomatic_dreams_are_ignored(text):
    from AI.dream_book import is_dream_mention

    assert not is_dream_mention(text)


@pytest.mark.parametrize("text", [
    "Пацаны, мне такая дичь приснилась",
    "мне снилось",
    "Мне сегодня такой сон приснился, пиздец",
    "мне снилось что-то странное",
])
def test_announcement_without_content_is_a_teaser(text):
    from AI.dream_book import is_dream_teaser

    assert is_dream_teaser(text)


def test_prompt_contains_dream_author_and_detailed_mocking_contract():
    from AI.dream_book import build_dream_prompt

    prompt = build_dream_prompt("мне снилось, что я ем кактус в метро", "Детектор")

    assert "мне снилось, что я ем кактус в метро" in prompt
    assert "Детектор" in prompt
    assert "2–4 САМЫХ ярких образа" in prompt
    assert "Местоимения (я, меня, мне" in prompt
    assert "40–110 слов" in prompt
    assert "Прогноз:" not in prompt
    # Конкретные примеры авторитетов модель тащила в каждый ответ.
    for stock_character in ("Зин", "Фрейд", "Ванг", "Миллер"):
        assert stock_character not in prompt
    assert "без постоянных персонажей" in prompt
    assert "мат" in prompt
    assert "без Markdown" in prompt


def test_pronoun_images_and_extra_symbols_are_dropped():
    from AI.dream_book import _clean_interpretation

    raw = (
        "▪️ Меня — значит, ты эгоцентричный долбоёб.\n"
        "▪️ «Я» (сновидец) — тоже к нарциссизму.\n"
        "▪️ Велосипед — к тому, что ты всю жизнь крутишь педали вхолостую.\n"
        "▪️ Гудок — Фрейд после литра говорит: хочешь, чтобы тебя наконец услышали.\n"
        "- Колесо - к сдутым амбициям.\n"
        "▪️ Сжатый воздух — у тебя в голове его дохуя.\n"
        "▪️ Бог — даже он с тебя охуел.\n"
        "Итог: ты сам себе гудок, прогноз — проколешься к пятнице."
    )

    cleaned = _clean_interpretation(raw)

    assert "Меня —" not in cleaned
    assert "«Я»" not in cleaned
    assert "▪️ Велосипед" in cleaned
    assert "▪️ Гудок" in cleaned
    assert "- Колесо" in cleaned
    assert "▪️ Сжатый воздух" in cleaned
    assert "Бог —" not in cleaned
    assert cleaned.endswith("Итог: ты сам себе гудок, прогноз — проколешься к пятнице.")


def test_prompt_truncates_very_long_dreams():
    from AI.dream_book import _MAX_DREAM_CHARS, build_dream_prompt

    prompt = build_dream_prompt("я " + "очень " * 2000, "Детектор")

    assert len(prompt) < _MAX_DREAM_CHARS + 3000


def _message(text, *, user_id=123, chat_id=77, forward_origin=None, automatic_forward=None):
    replies = []
    actions = []

    async def reply(value):
        replies.append(value)

    async def send_chat_action(**kwargs):
        actions.append(kwargs)

    message = SimpleNamespace(
        message_id=1,
        text=text,
        caption=None,
        chat=SimpleNamespace(id=chat_id, type="group"),
        from_user=SimpleNamespace(id=user_id, is_bot=False, first_name="Детектор"),
        forward_origin=forward_origin,
        is_automatic_forward=automatic_forward,
        bot=SimpleNamespace(send_chat_action=send_chat_action),
        reply=reply,
    )
    return message, replies


@pytest.fixture
def dream_book(monkeypatch):
    import AI.dream_book as module

    module._last_interpretation.clear()
    module._pending_dreams.clear()
    calls = []

    async def fake_generate(prompt, chat_id):
        calls.append(prompt)
        return "▪️ **Кактус** — к колючему разговору с бухгалтерией.\n\n\n## Итог: ты долбоёб."

    monkeypatch.setattr(module, "generate_with_active_model", fake_generate)
    module.calls = calls
    yield module
    module._last_interpretation.clear()
    module._pending_dreams.clear()


def test_dream_message_gets_cleaned_interpretation_reply(dream_book):
    message, replies = _message("мне снилось, что я ем кактус в метро")

    assert asyncio.run(dream_book.maybe_interpret_dream(message)) is True

    assert len(dream_book.calls) == 1
    assert "кактус в метро" in dream_book.calls[0]
    assert replies == [
        "🔮 Сонник Упупы\n\n▪️ Кактус — к колючему разговору с бухгалтерией.\n\nИтог: ты долбоёб."
    ]


def test_ordinary_message_is_not_interpreted(dream_book):
    message, replies = _message("пойдём сегодня в кино")

    assert asyncio.run(dream_book.maybe_interpret_dream(message)) is False
    assert dream_book.calls == []
    assert replies == []


def test_bot_messages_and_automatic_channel_forwards_are_ignored(dream_book):
    bot_message, bot_replies = _message("мне снилось, что я кит")
    bot_message.from_user.is_bot = True
    channel_post, channel_replies = _message(
        "Мне снилось, что я кит, блядь",
        user_id=777000,
        automatic_forward=True,
        forward_origin=SimpleNamespace(type="channel", chat=SimpleNamespace(title="Упупа"), author_signature=None),
    )
    forwarded_bot, forwarded_bot_replies = _message(
        "мне снилось, что я кит",
        forward_origin=SimpleNamespace(
            type="user",
            sender_user=SimpleNamespace(id=999, is_bot=True, first_name="Упупа"),
        ),
    )

    for message in (bot_message, channel_post, forwarded_bot):
        assert asyncio.run(dream_book.maybe_interpret_dream(message)) is False
    assert dream_book.calls == []
    assert bot_replies == channel_replies == forwarded_bot_replies == []


def test_forwarded_dream_is_interpreted_as_original_authors_dream(dream_book):
    message, replies = _message(
        "Вот например мне снился сегодня сон. На велосипеде у меня был гудок.",
        forward_origin=SimpleNamespace(
            type="user",
            sender_user=SimpleNamespace(id=456, is_bot=False, first_name="Тукан"),
        ),
    )

    assert asyncio.run(dream_book.maybe_interpret_dream(message)) is True

    prompt = dream_book.calls[0]
    assert "по имени Тукан рассказал свой сон" in prompt
    assert "Этот сон переслал в чат Детектор" in prompt
    assert "толкуй сон Тукан" in prompt
    assert "На велосипеде у меня был гудок" in prompt
    assert len(replies) == 1


def test_forwarded_own_message_has_no_forward_note(dream_book):
    message, _ = _message(
        "мне снилось, что я кит",
        forward_origin=SimpleNamespace(
            type="user",
            sender_user=SimpleNamespace(id=123, is_bot=False, first_name="Детектор"),
        ),
    )

    assert asyncio.run(dream_book.maybe_interpret_dream(message)) is True
    assert "по имени Детектор рассказал свой сон" in dream_book.calls[0]
    assert "переслал" not in dream_book.calls[0]


def test_forwarded_author_name_for_hidden_users_chats_and_channels():
    from AI.dream_book import _forwarded_author

    forwarder = SimpleNamespace(id=123)
    hidden = SimpleNamespace(type="hidden_user", sender_user_name="Скрытный Кот")
    chat = SimpleNamespace(type="chat", sender_chat=SimpleNamespace(title="Группа"), author_signature=None)
    channel = SimpleNamespace(type="channel", chat=SimpleNamespace(title="Канал"), author_signature="Админ")

    assert _forwarded_author(hidden, forwarder) == "Скрытный Кот"
    assert _forwarded_author(chat, forwarder) == "Группа"
    assert _forwarded_author(channel, forwarder) == "Админ"


def test_probability_controls_interpretation(dream_book):
    message, replies = _message("мне снилось, что я кит")

    assert asyncio.run(dream_book.maybe_interpret_dream(message, probability=0)) is False
    assert asyncio.run(
        dream_book.maybe_interpret_dream(message, probability=0.5, rng=lambda: 0.9)
    ) is False
    assert dream_book.calls == []

    assert asyncio.run(
        dream_book.maybe_interpret_dream(message, probability=0.5, rng=lambda: 0.1)
    ) is True
    assert len(replies) == 1


def test_same_user_is_on_cooldown_but_other_user_is_not(dream_book):
    first, _ = _message("мне снилось, что я кит")
    again, again_replies = _message("а ещё мне снилось, что я дельфин")
    other, other_replies = _message("а мне снилось, что я акула", user_id=456)

    assert asyncio.run(dream_book.maybe_interpret_dream(first)) is True
    assert asyncio.run(dream_book.maybe_interpret_dream(again)) is False
    assert asyncio.run(dream_book.maybe_interpret_dream(other)) is True
    assert again_replies == []
    assert len(other_replies) == 1


def test_failed_generation_does_not_reply_or_burn_cooldown(dream_book, monkeypatch):
    async def broken_generate(prompt, chat_id):
        raise RuntimeError("model is down")

    monkeypatch.setattr(dream_book, "generate_with_active_model", broken_generate)
    message, replies = _message("мне снилось, что я кит")

    assert asyncio.run(dream_book.maybe_interpret_dream(message)) is False
    assert replies == []
    assert dream_book._last_interpretation == {}


def test_teaser_waits_for_the_story_from_the_same_author(dream_book):
    teaser, teaser_replies = _message("Пацаны, мне сегодня такая дичь приснилась")
    stranger, _ = _message("и что там было?", user_id=456)
    short, short_replies = _message("короче")
    story, story_replies = _message("я был в школе, а директор оказался огромным гусем с портфелем")

    assert asyncio.run(dream_book.maybe_interpret_dream(teaser)) is False
    assert asyncio.run(dream_book.maybe_interpret_dream(stranger)) is False
    assert asyncio.run(dream_book.maybe_interpret_dream(short)) is False
    assert asyncio.run(dream_book.maybe_interpret_dream(story)) is True

    assert teaser_replies == short_replies == []
    assert len(story_replies) == 1
    assert len(dream_book.calls) == 1
    assert "такая дичь приснилась" in dream_book.calls[0]
    assert "директор оказался огромным гусем" in dream_book.calls[0]
    assert dream_book._pending_dreams == {}


def test_question_teaser_does_not_wait_for_followups(dream_book):
    question, _ = _message("мне снилось, что ли?")
    later, later_replies = _message("ладно, пошёл работать, всем хорошего вечера")

    assert asyncio.run(dream_book.maybe_interpret_dream(question)) is False
    assert asyncio.run(dream_book.maybe_interpret_dream(later)) is False
    assert later_replies == []
    assert dream_book._pending_dreams == {}


def test_expired_teaser_is_forgotten(dream_book, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(dream_book.time, "monotonic", lambda: clock[0])
    teaser, _ = _message("мне такая хрень приснилась")
    story, story_replies = _message("я был в школе, а директор оказался огромным гусем с портфелем")

    assert asyncio.run(dream_book.maybe_interpret_dream(teaser)) is False
    clock[0] += dream_book._TEASER_TTL_SECONDS + 1
    assert asyncio.run(dream_book.maybe_interpret_dream(story)) is False
    assert story_replies == []
    assert dream_book._pending_dreams == {}


def test_expired_state_is_pruned_when_maps_grow(dream_book, monkeypatch):
    monkeypatch.setattr(dream_book, "_STATE_PRUNE_THRESHOLD", 2)
    now = 10_000.0
    for user_id in range(5):
        dream_book._last_interpretation[(77, user_id)] = now - dream_book._COOLDOWN_SECONDS - 1
        dream_book._pending_dreams[(77, user_id)] = dream_book._PendingDream(
            created_at=now - dream_book._TEASER_TTL_SECONDS - 1,
        )
    dream_book._last_interpretation[(77, 99)] = now
    dream_book._pending_dreams[(77, 99)] = dream_book._PendingDream(created_at=now)

    dream_book._prune_state(now)

    assert list(dream_book._last_interpretation) == [(77, 99)]
    assert list(dream_book._pending_dreams) == [(77, 99)]


def test_pipeline_interprets_dream_before_random_reactions(monkeypatch):
    from features import dialog_pipeline as pipeline

    pipeline.situational_summary._recent_chat_messages.clear()
    pipeline.situational_summary._seen_message_ids.clear()
    pipeline.chat_settings["77"] = {
        "dialog_enabled": True,
        "reactions_enabled": True,
        "emoji_enabled": False,
        "ai_prob": 1.0,
    }
    seen = []

    async def noop(*args, **kwargs):
        return None

    async def fake_dream(message, *, probability):
        seen.append(probability)
        return True

    async def forbidden_situational(chat_id):
        raise AssertionError("random reaction must not run after dream interpretation")

    monkeypatch.setattr(pipeline, "save_user_message", noop)
    monkeypatch.setattr(pipeline, "record_participant_message", lambda message: None)
    monkeypatch.setattr(pipeline, "track_message_statistics", noop)
    monkeypatch.setattr(pipeline, "add_chat", lambda *args: None)
    monkeypatch.setattr(pipeline.dream_book, "maybe_interpret_dream", fake_dream)
    monkeypatch.setattr(pipeline, "_generate_live_situational_reaction", forbidden_situational)

    message, _ = _message("мне снилось, что я кит")
    message.message_id = 9101
    message.chat.title = "Test chat"
    message.chat.username = None
    message.from_user.full_name = "Детектор"
    message.from_user.username = "detector"
    message.voice = None

    assert asyncio.run(pipeline.process_random_reactions_once(message)) is True
    assert seen == [1.0]


def test_pipeline_respects_disabled_reactions_and_dream_probability(monkeypatch):
    from features import dialog_pipeline as pipeline

    pipeline.situational_summary._recent_chat_messages.clear()
    pipeline.situational_summary._seen_message_ids.clear()
    seen = []

    async def noop(*args, **kwargs):
        return None

    async def fake_dream(message, *, probability):
        seen.append(probability)
        return False

    monkeypatch.setattr(pipeline, "save_user_message", noop)
    monkeypatch.setattr(pipeline, "record_participant_message", lambda message: None)
    monkeypatch.setattr(pipeline, "track_message_statistics", noop)
    monkeypatch.setattr(pipeline, "add_chat", lambda *args: None)
    monkeypatch.setattr(pipeline.dream_book, "maybe_interpret_dream", fake_dream)
    monkeypatch.setattr(pipeline.random_reactions.random, "random", lambda: 0.99)

    def run(message_id, settings):
        pipeline.chat_settings["77"] = settings
        message, _ = _message("мне снилось, что я кит")
        message.message_id = message_id
        message.chat.title = "Test chat"
        message.chat.username = None
        message.from_user.full_name = "Детектор"
        message.from_user.username = "detector"
        message.voice = None
        return asyncio.run(pipeline.process_random_reactions_once(message))

    assert run(9201, {"reactions_enabled": False, "emoji_enabled": False}) is False
    assert seen == []

    assert run(9202, {"reactions_enabled": True, "emoji_enabled": False, "dream_prob": 0.05}) is False
    assert seen == [0.05]


def test_dream_probability_is_configurable_in_settings_menu():
    from features.interactive_settings import DEFAULT_PROBS, REACTION_TYPES

    assert REACTION_TYPES["dream_prob"] == "🔮 Сонник"
    assert DEFAULT_PROBS["dream_prob"] == 1.0


def test_help_mentions_dream_book():
    from prompts.help_texts import HELP_DICT

    assert "сонник" in HELP_DICT["talking"]
