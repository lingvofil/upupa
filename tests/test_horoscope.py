"""Гороскоп из @neural_horo: команда, разбор ленты, кнопки знаков."""

import asyncio
from types import SimpleNamespace

import pytest

from tests import test_smoke_imports  # noqa: F401  (env + heavy-library mocks)

from services import horoscope
from services.horoscope import (
    HOROSCOPE_CALLBACK_PREFIX,
    SIGNS_BY_KEY,
    ZODIAC_SIGNS,
    build_signs_keyboard,
    claim_sign_post,
    get_forecasts,
    handle_horoscope_callback,
    handle_horoscope_message,
    parse_horoscope_command,
    parse_horoscope_feed,
    parse_horoscope_post,
    sign_from_callback_data,
)


SAGITTARIUS_TEXT = (
    "♐️Стрелец: День хорош для того, чтобы орать как бешеная лошадь. "
    "У вас может появиться желание сожрать свою обувь. Сегодня вы можете "
    "столкнуться с проблемами, такими как метеоризм и задержка в развитии. "
    "Вероятен легкий флирт с представителями кошачьей расы."
)


def _emoji(symbol: str) -> str:
    # Так t.me/s отдаёт эмодзи знака: картинка-фон и сам символ внутри <b>.
    return (
        "<i class=\"emoji\" style=\"background-image:url('//telegram.org/img/emoji/40/E29990.png')\">"
        f"<b>{symbol}</b></i>"
    )


def _post(number: int, blocks: list[str], *, footer: str = "") -> str:
    body = "<br/><br/>".join(blocks)
    if footer:
        body += f"<br/><br/>{footer}"
    return (
        f'<div class="tgme_widget_message" data-post="neural_horo/{number}">'
        f'<div class="tgme_widget_message_text js-message_text" dir="auto">{body}</div>'
        "</div>"
    )


def _full_post_blocks(prefix: str) -> list[str]:
    return [
        f"{_emoji(sign.emoji)}<b>{sign.name}</b>: {prefix} для знака {sign.name}."
        for sign in ZODIAC_SIGNS
    ]


def _feed(*posts: str) -> str:
    return f"<html><body><section>{''.join(posts)}</section></body></html>"


def _latest_post_blocks() -> list[str]:
    blocks = _full_post_blocks("Свежий прогноз")
    index = [sign.key for sign in ZODIAC_SIGNS].index("sagittarius")
    blocks[index] = (
        f"{_emoji('♐️')}<b>Стрелец</b>: День хорош для того, чтобы орать как бешеная лошадь. "
        "У вас может появиться желание сожрать свою обувь. Сегодня вы можете "
        "столкнуться с проблемами, такими как метеоризм и задержка в развитии. "
        "Вероятен легкий флирт с представителями кошачьей расы."
    )
    return blocks


FEED_HTML = _feed(
    _post(2827, _full_post_blocks("Вчерашний прогноз")),
    _post(2828, _latest_post_blocks(), footer="@neural_horo — подписывайся"),
)


@pytest.fixture(autouse=True)
def _fresh_horoscope_state():
    horoscope.reset_cache()
    yield
    horoscope.reset_cache()


# ---------------------------------------------------------------------------
# Команда
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text", ["гороскоп", "Гороскоп", "гороскоп!", "  гороскоп  ", "упупа гороскоп", "упупа, гороскоп", "гороскоп на сегодня"])
def test_bare_command_opens_menu(text):
    command = parse_horoscope_command(text)
    assert command is not None
    assert command.sign is None


@pytest.mark.parametrize(
    ("text", "key"),
    [
        ("гороскоп стрелец", "sagittarius"),
        ("Гороскоп Стрелец", "sagittarius"),
        ("гороскоп для стрельца", "sagittarius"),
        ("гороскоп стрелец на сегодня", "sagittarius"),
        ("гороскоп рыбы", "pisces"),
        ("гороскоп рыба", "pisces"),
        ("гороскоп для рыб", "pisces"),
        ("гороскоп овен", "aries"),
        ("гороскоп для овна", "aries"),
        ("гороскоп лев", "leo"),
        ("гороскоп для льва", "leo"),
        ("гороскоп весы", "libra"),
        ("гороскоп водолей?", "aquarius"),
        ("гороскоп ♐️", "sagittarius"),
        ("гороскоп ♐", "sagittarius"),
        ("упупа гороскоп козерог", "capricorn"),
    ],
)
def test_command_with_sign_picks_that_sign(text, key):
    command = parse_horoscope_command(text)
    assert command is not None
    assert command.sign is SIGNS_BY_KEY[key]


@pytest.mark.parametrize(
    "text",
    [
        None,
        "",
        "гороскопы",
        "мой гороскоп",
        "гороскоп это хуйня",
        "гороскоп ракета",
        "гороскоп стрелец и рыбы",
        "читал гороскоп стрелец",
    ],
)
def test_ordinary_talk_is_not_a_command(text):
    assert parse_horoscope_command(text) is None


# ---------------------------------------------------------------------------
# Разбор ленты
# ---------------------------------------------------------------------------

def test_feed_returns_block_from_latest_post_verbatim():
    forecasts = parse_horoscope_feed(FEED_HTML)

    assert set(forecasts) == {sign.key for sign in ZODIAC_SIGNS}
    assert forecasts["sagittarius"].plain_text == SAGITTARIUS_TEXT
    assert forecasts["aries"].plain_text == "♈️Овен: Свежий прогноз для знака Овен."


def test_channel_signature_after_last_block_is_not_part_of_forecast():
    forecasts = parse_horoscope_feed(FEED_HTML)

    assert forecasts["pisces"].plain_text == "♓️Рыбы: Свежий прогноз для знака Рыбы."


def test_sign_missing_in_latest_post_falls_back_to_previous_post():
    latest = [block for block in _full_post_blocks("Свежий") if "Дева" not in block]
    feed = _feed(_post(1, _full_post_blocks("Старый")), _post(2, latest))

    forecasts = parse_horoscope_feed(feed)

    assert forecasts["virgo"].body == "Старый для знака Дева."
    assert forecasts["leo"].body == "Свежий для знака Лев."


def test_post_parser_handles_nbsp_bold_emoji_and_missing_emoji():
    text = (
        "♈️Овен:\xa0 Сегодня звезды советуют вам  завести знакомство.\n\n"
        "Телец: Будьте осторожны\n\n"
        "#реклама"
    )

    forecasts = parse_horoscope_post(text)

    assert forecasts["aries"].plain_text == "♈️Овен: Сегодня звезды советуют вам завести знакомство."
    # Эмодзи нет в посте — подставляем канонический, чтобы блок выглядел одинаково.
    assert forecasts["taurus"].plain_text == "♉️Телец: Будьте осторожны"
    assert set(forecasts) == {"aries", "taurus"}


def test_feed_without_horoscope_blocks_is_empty():
    assert parse_horoscope_feed(_feed(_post(1, ["Просто пост без гороскопа"]))) == {}


def test_html_text_bolds_sign_and_escapes_body():
    forecasts = parse_horoscope_post("♐️Стрелец: купите <трактор> & пейте")

    assert forecasts["sagittarius"].html_text == "♐️<b>Стрелец</b>: купите &lt;трактор&gt; &amp; пейте"


# ---------------------------------------------------------------------------
# Кэш ленты
# ---------------------------------------------------------------------------

def test_feed_is_cached_between_requests(monkeypatch):
    calls = []

    async def fake_download():
        calls.append(1)
        return FEED_HTML

    monkeypatch.setattr(horoscope, "_download_feed", fake_download)

    async def scenario():
        first = await get_forecasts(now=1000.0)
        second = await get_forecasts(now=1000.0 + 60)
        third = await get_forecasts(now=1000.0 + 16 * 60)
        return first, second, third

    first, second, third = asyncio.run(scenario())

    assert first["sagittarius"].plain_text == SAGITTARIUS_TEXT
    assert second is first
    assert len(calls) == 2  # третий запрос — после истечения кэша
    assert third["sagittarius"].plain_text == SAGITTARIUS_TEXT


def test_stale_cache_survives_telegram_failure(monkeypatch):
    async def ok_download():
        return FEED_HTML

    async def broken_download():
        raise RuntimeError("t.me лежит")

    async def scenario():
        monkeypatch.setattr(horoscope, "_download_feed", ok_download)
        await get_forecasts(now=0.0)
        monkeypatch.setattr(horoscope, "_download_feed", broken_download)
        return await get_forecasts(now=10_000.0)

    forecasts = asyncio.run(scenario())

    assert forecasts["sagittarius"].plain_text == SAGITTARIUS_TEXT


def test_failure_without_cache_returns_empty(monkeypatch):
    async def broken_download():
        raise RuntimeError("t.me лежит")

    monkeypatch.setattr(horoscope, "_download_feed", broken_download)

    assert asyncio.run(get_forecasts()) == {}


# ---------------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------------

def test_keyboard_has_all_twelve_signs_three_per_row():
    keyboard = build_signs_keyboard()

    rows = keyboard.inline_keyboard
    assert [len(row) for row in rows] == [3, 3, 3, 3]
    buttons = [button for row in rows for button in row]
    assert [button.text for button in buttons] == [f"{sign.emoji} {sign.name}" for sign in ZODIAC_SIGNS]
    for button, sign in zip(buttons, ZODIAC_SIGNS):
        assert button.callback_data.startswith(HOROSCOPE_CALLBACK_PREFIX)
        assert len(button.callback_data.encode("utf-8")) <= 64
        assert sign_from_callback_data(button.callback_data) is sign


def test_unknown_callback_data_is_ignored():
    assert sign_from_callback_data("horo:ophiuchus") is None
    assert sign_from_callback_data("help:main") is None
    assert sign_from_callback_data(None) is None


class _FakeMessage:
    def __init__(self, text):
        self.text = text
        self.replies = []

    async def reply(self, text, **kwargs):
        self.replies.append((text, kwargs))


def test_bare_command_replies_with_sign_menu():
    message = _FakeMessage("гороскоп")

    asyncio.run(handle_horoscope_message(message))

    [(text, kwargs)] = message.replies
    assert text == horoscope.MENU_TEXT
    assert kwargs["reply_markup"].inline_keyboard == build_signs_keyboard().inline_keyboard


def test_command_with_sign_replies_with_forecast(monkeypatch):
    async def fake_download():
        return FEED_HTML

    monkeypatch.setattr(horoscope, "_download_feed", fake_download)
    message = _FakeMessage("гороскоп стрелец")

    asyncio.run(handle_horoscope_message(message))

    [(text, kwargs)] = message.replies
    assert text == SAGITTARIUS_TEXT.replace("Стрелец", "<b>Стрелец</b>", 1)
    assert kwargs["parse_mode"] == "HTML"


def test_command_reports_unavailable_feed(monkeypatch):
    async def broken_download():
        raise RuntimeError("t.me лежит")

    monkeypatch.setattr(horoscope, "_download_feed", broken_download)
    message = _FakeMessage("гороскоп стрелец")

    asyncio.run(handle_horoscope_message(message))

    assert message.replies[0][0] == horoscope.UNAVAILABLE_TEXT


class _FakeBot:
    def __init__(self):
        self.sent = []

    async def send_message(self, **kwargs):
        self.sent.append(kwargs)


class _FakeQuery:
    def __init__(self, data, bot, *, chat_id=-100, message_id=77):
        self.data = data
        self.bot = bot
        self.message = SimpleNamespace(chat=SimpleNamespace(id=chat_id), message_id=message_id)
        self.answers = []

    async def answer(self, text=None, **kwargs):
        self.answers.append(text)


def test_button_sends_forecast_as_reply_to_menu(monkeypatch):
    async def fake_download():
        return FEED_HTML

    monkeypatch.setattr(horoscope, "_download_feed", fake_download)
    bot = _FakeBot()
    query = _FakeQuery("horo:sagittarius", bot)

    asyncio.run(handle_horoscope_callback(query))

    [sent] = bot.sent
    assert sent["chat_id"] == -100
    assert sent["text"] == SAGITTARIUS_TEXT.replace("Стрелец", "<b>Стрелец</b>", 1)
    assert sent["parse_mode"] == "HTML"
    assert sent["reply_parameters"].message_id == 77
    assert sent["reply_parameters"].allow_sending_without_reply is True
    assert query.answers == [None]


def test_repeated_button_press_does_not_spam_chat(monkeypatch):
    async def fake_download():
        return FEED_HTML

    monkeypatch.setattr(horoscope, "_download_feed", fake_download)
    bot = _FakeBot()

    async def scenario():
        await handle_horoscope_callback(_FakeQuery("horo:sagittarius", bot))
        repeat = _FakeQuery("horo:sagittarius", bot)
        await handle_horoscope_callback(repeat)
        await handle_horoscope_callback(_FakeQuery("horo:pisces", bot))
        await handle_horoscope_callback(_FakeQuery("horo:sagittarius", bot, chat_id=-200))
        return repeat

    repeat = asyncio.run(scenario())

    assert [sent["chat_id"] for sent in bot.sent] == [-100, -100, -200]
    assert "только что был" in repeat.answers[0]


def test_sign_cooldown_expires():
    sign = SIGNS_BY_KEY["leo"]

    assert claim_sign_post(1, sign, now=0.0)
    assert not claim_sign_post(1, sign, now=5.0)
    assert claim_sign_post(1, sign, now=30.0)


def test_failed_send_releases_cooldown(monkeypatch):
    async def fake_download():
        return FEED_HTML

    monkeypatch.setattr(horoscope, "_download_feed", fake_download)

    class BrokenBot:
        async def send_message(self, **kwargs):
            raise RuntimeError("telegram недоступен")

    with pytest.raises(RuntimeError):
        asyncio.run(handle_horoscope_callback(_FakeQuery("horo:leo", BrokenBot())))

    assert claim_sign_post(-100, SIGNS_BY_KEY["leo"])


# ---------------------------------------------------------------------------
# Подключение и справка
# ---------------------------------------------------------------------------

def test_horoscope_is_routed_before_dialog():
    import handlers
    from handlers import media_search

    names = [router.name for router in handlers.ROUTERS]
    assert names.index("media_search") < names.index("dialog")
    message_handlers = [handler.callback.__name__ for handler in media_search.router.message.handlers]
    callback_handlers = [handler.callback.__name__ for handler in media_search.router.callback_query.handlers]
    assert "handle_horoscope_command" in message_handlers
    assert "horoscope_callback_handler" in callback_handlers


def test_help_advertises_horoscope():
    from prompts.help_texts import HELP_DICT

    assert "<code>гороскоп</code>" in HELP_DICT["content"]
    assert "<code>гороскоп [знак]</code>" in HELP_DICT["content"]
    assert "@neural_horo" in HELP_DICT["content"]
