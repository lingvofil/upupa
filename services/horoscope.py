"""Гороскоп из канала @neural_horo.

Канал раз в день публикует один пост с двенадцатью блоками вида
«♐️Стрелец: …», разделёнными пустой строкой. Бот читает публичную ленту
``t.me/s/neural_horo`` (она статична, браузер не нужен) и отдаёт блок
нужного знака из последнего поста целиком, ничего не переписывая.
"""

from __future__ import annotations

import asyncio
import html
import logging
import re
import time
from dataclasses import dataclass

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, ReplyParameters
from bs4 import BeautifulSoup

from infrastructure.media_io import download_url_bytes


HOROSCOPE_CHANNEL = "neural_horo"
HOROSCOPE_FEED_URL = f"https://t.me/s/{HOROSCOPE_CHANNEL}"
HOROSCOPE_CALLBACK_PREFIX = "horo:"

_FEED_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36"
    ),
}
_FEED_MAX_BYTES = 5 * 1024 * 1024
_FEED_TIMEOUT_SECONDS = 20.0
# Пост выходит раз в сутки утром; кэш избавляет от запроса в Telegram на
# каждое нажатие кнопки, но свежий пост подхватывается быстро.
_CACHE_TTL_SECONDS = 15 * 60
# Один и тот же знак в одном чате не повторяем чаще этого — защита от
# многократных нажатий одной кнопки.
_REPEAT_COOLDOWN_SECONDS = 20.0

MENU_TEXT = "🔮 Тыкай свой знак, щас узнаешь, какая хуйня тебя ждет"
UNAVAILABLE_TEXT = "🔮 Звезды молчат: не смог достать гароскоп. Попробуй попозже"
SIGN_MISSING_TEXT = "🔮 Для этого знака звезды сегодня ничего не нашептали"


@dataclass(frozen=True)
class ZodiacSign:
    key: str
    emoji: str
    name: str
    forms: frozenset[str]


ZODIAC_SIGNS: tuple[ZodiacSign, ...] = (
    ZodiacSign("aries", "♈️", "Овен", frozenset({"овен", "овна", "овну", "овны", "овнов", "овнам"})),
    ZodiacSign("taurus", "♉️", "Телец", frozenset({"телец", "тельца", "тельцу", "тельцы", "тельцов", "тельцам"})),
    ZodiacSign("gemini", "♊️", "Близнецы", frozenset({"близнецы", "близнец", "близнеца", "близнецу", "близнецов", "близнецам"})),
    ZodiacSign("cancer", "♋️", "Рак", frozenset({"рак", "рака", "раку", "раки", "раков", "ракам"})),
    ZodiacSign("leo", "♌️", "Лев", frozenset({"лев", "льва", "льву", "львы", "львов", "львам"})),
    ZodiacSign("virgo", "♍️", "Дева", frozenset({"дева", "девы", "деве", "деву", "дев", "девам"})),
    ZodiacSign("libra", "♎️", "Весы", frozenset({"весы", "весов", "весам"})),
    ZodiacSign("scorpio", "♏️", "Скорпион", frozenset({"скорпион", "скорпиона", "скорпиону", "скорпионы", "скорпионов", "скорпионам"})),
    ZodiacSign("sagittarius", "♐️", "Стрелец", frozenset({"стрелец", "стрельца", "стрельцу", "стрельцы", "стрельцов", "стрельцам"})),
    ZodiacSign("capricorn", "♑️", "Козерог", frozenset({"козерог", "козерога", "козерогу", "козероги", "козерогов", "козерогам"})),
    ZodiacSign("aquarius", "♒️", "Водолей", frozenset({"водолей", "водолея", "водолею", "водолеи", "водолеев", "водолеям"})),
    ZodiacSign("pisces", "♓️", "Рыбы", frozenset({"рыбы", "рыба", "рыбе", "рыб", "рыбам"})),
)
SIGNS_BY_KEY = {sign.key: sign for sign in ZODIAC_SIGNS}
_SIGN_BY_NAME = {sign.name.lower(): sign for sign in ZODIAC_SIGNS}
_SIGN_BY_FORM = {form: sign for sign in ZODIAC_SIGNS for form in sign.forms}
_SIGN_BY_SYMBOL = {sign.emoji.rstrip("️"): sign for sign in ZODIAC_SIGNS}

# «♐️Стрелец: текст» — эмодзи необязателен, после двоеточия бывает \xa0.
_BLOCK_HEADER_RE = re.compile(
    r"^\s*(?P<emoji>[♈-♓]️?)?\s*"
    r"(?P<name>" + "|".join(sign.name for sign in ZODIAC_SIGNS) + r")\s*:\s*(?P<body>.*)$",
    re.IGNORECASE,
)
_COMMAND_WORD = "гороскоп"
_TODAY_SUFFIXES = (("на", "сегодня"), ("сегодня",))
_TRAILING_PUNCTUATION = "!?.,…;:"


@dataclass(frozen=True)
class SignForecast:
    sign: ZodiacSign
    emoji: str
    body: str

    @property
    def plain_text(self) -> str:
        return f"{self.emoji}{self.sign.name}: {self.body}"

    @property
    def html_text(self) -> str:
        return f"{self.emoji}<b>{self.sign.name}</b>: {html.escape(self.body, quote=False)}"


@dataclass(frozen=True)
class HoroscopeCommand:
    """Разобранная команда: ``sign is None`` — показать меню знаков."""

    sign: ZodiacSign | None


# ----------------------------------------------------------------------------
# Разбор команды
# ----------------------------------------------------------------------------

def _match_sign_token(token: str) -> ZodiacSign | None:
    token = token.strip(_TRAILING_PUNCTUATION)
    sign = _SIGN_BY_FORM.get(token)
    if sign is not None:
        return sign
    return _SIGN_BY_SYMBOL.get(token.rstrip("️"))


def parse_horoscope_command(text: str | None) -> HoroscopeCommand | None:
    """Распознать «гороскоп», «гороскоп стрелец», «упупа, гороскоп для рыб».

    Посторонний текст после слова «гороскоп» («гороскоп это хуйня») командой
    не считается, чтобы бот не влезал в обычный разговор.
    """
    if not text:
        return None
    normalized = text.lower().replace("ё", "е")
    tokens = [token.strip(_TRAILING_PUNCTUATION) for token in normalized.split()]
    tokens = [token for token in tokens if token]
    if tokens and tokens[0] == "упупа":
        tokens = tokens[1:]
    if not tokens or tokens[0] != _COMMAND_WORD:
        return None

    rest = tokens[1:]
    if rest and rest[0] == "для":
        rest = rest[1:]
    for suffix in _TODAY_SUFFIXES:
        if len(rest) >= len(suffix) and tuple(rest[-len(suffix):]) == suffix:
            rest = rest[: -len(suffix)]
            break

    if not rest:
        return HoroscopeCommand(sign=None)
    if len(rest) == 1:
        sign = _match_sign_token(rest[0])
        if sign is not None:
            return HoroscopeCommand(sign=sign)
    return None


def is_horoscope_command(text: str | None) -> bool:
    return parse_horoscope_command(text) is not None


# ----------------------------------------------------------------------------
# Разбор ленты канала
# ----------------------------------------------------------------------------

def parse_horoscope_post(text: str | None) -> dict[str, SignForecast]:
    """Разобрать текст поста на блоки знаков.

    Блок начинается строкой «♐️Стрелец: …» и заканчивается пустой строкой или
    заголовком следующего знака, так что подпись канала после последнего блока
    в прогноз не попадёт.
    """
    forecasts: dict[str, SignForecast] = {}
    if not text:
        return forecasts

    current_sign: ZodiacSign | None = None
    current_emoji = ""
    current_lines: list[str] = []

    def flush() -> None:
        if current_sign is None:
            return
        body = " ".join(" ".join(current_lines).split())
        if body and current_sign.key not in forecasts:
            forecasts[current_sign.key] = SignForecast(
                sign=current_sign,
                emoji=current_emoji or current_sign.emoji,
                body=body,
            )

    for raw_line in text.splitlines():
        line = raw_line.replace("\xa0", " ")
        header = _BLOCK_HEADER_RE.match(line)
        if header:
            flush()
            current_sign = _SIGN_BY_NAME[header["name"].lower()]
            current_emoji = header["emoji"] or ""
            current_lines = [header["body"]]
            continue
        if not line.strip():
            flush()
            current_sign = None
            current_lines = []
            continue
        if current_sign is not None:
            current_lines.append(line)
    flush()
    return forecasts


def _post_text(post) -> str | None:
    text_node = post.select_one(".tgme_widget_message_text")
    if text_node is None:
        return None
    for line_break in text_node.find_all("br"):
        line_break.replace_with("\n")
    return text_node.get_text()


def parse_horoscope_feed(feed_html: str) -> dict[str, SignForecast]:
    """Блок каждого знака из самого свежего поста, где этот знак есть.

    В ленте ``t.me/s`` посты идут от старых к новым, поэтому читаем с конца.
    """
    soup = BeautifulSoup(feed_html, "html.parser")
    forecasts: dict[str, SignForecast] = {}
    for post in reversed(soup.select(".tgme_widget_message")):
        for key, forecast in parse_horoscope_post(_post_text(post)).items():
            forecasts.setdefault(key, forecast)
        if len(forecasts) == len(ZODIAC_SIGNS):
            break
    return forecasts


# ----------------------------------------------------------------------------
# Загрузка с кэшем
# ----------------------------------------------------------------------------

_cache: dict[str, SignForecast] = {}
_cache_fetched_at: float | None = None
_cache_lock = asyncio.Lock()


async def _download_feed() -> str:
    raw = await download_url_bytes(
        HOROSCOPE_FEED_URL,
        headers=_FEED_HEADERS,
        timeout=_FEED_TIMEOUT_SECONDS,
        max_bytes=_FEED_MAX_BYTES,
    )
    return raw.decode("utf-8", errors="replace")


async def get_forecasts(*, now: float | None = None) -> dict[str, SignForecast]:
    """Свежие прогнозы; при сбое Telegram — последние удачно скачанные."""
    global _cache, _cache_fetched_at
    current = time.monotonic() if now is None else now
    if _cache and _cache_fetched_at is not None and current - _cache_fetched_at < _CACHE_TTL_SECONDS:
        return _cache

    async with _cache_lock:
        if _cache and _cache_fetched_at is not None and current - _cache_fetched_at < _CACHE_TTL_SECONDS:
            return _cache
        try:
            feed_html = await _download_feed()
            forecasts = await asyncio.to_thread(parse_horoscope_feed, feed_html)
        except Exception as error:
            logging.warning("[horoscope] не удалось скачать %s: %s", HOROSCOPE_FEED_URL, error)
            return _cache
        if not forecasts:
            logging.warning("[horoscope] в ленте %s не нашлось блоков знаков", HOROSCOPE_FEED_URL)
            return _cache
        _cache = forecasts
        _cache_fetched_at = current
        return _cache


def reset_cache() -> None:
    global _cache, _cache_fetched_at, _cache_lock
    _cache = {}
    _cache_fetched_at = None
    _cache_lock = asyncio.Lock()
    _recent_posts.clear()


# ----------------------------------------------------------------------------
# Telegram: меню и ответы
# ----------------------------------------------------------------------------

def build_signs_keyboard() -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(
            text=f"{sign.emoji} {sign.name}",
            callback_data=f"{HOROSCOPE_CALLBACK_PREFIX}{sign.key}",
        )
        for sign in ZODIAC_SIGNS
    ]
    rows = [buttons[index:index + 3] for index in range(0, len(buttons), 3)]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def sign_from_callback_data(data: str | None) -> ZodiacSign | None:
    if not data or not data.startswith(HOROSCOPE_CALLBACK_PREFIX):
        return None
    return SIGNS_BY_KEY.get(data[len(HOROSCOPE_CALLBACK_PREFIX):])


_recent_posts: dict[tuple[int, str], float] = {}


def claim_sign_post(chat_id: int, sign: ZodiacSign, *, now: float | None = None) -> bool:
    """False, если этот знак уже только что показывали в этом чате."""
    current = time.monotonic() if now is None else now
    cutoff = current - _REPEAT_COOLDOWN_SECONDS
    for key in [key for key, posted_at in _recent_posts.items() if posted_at <= cutoff]:
        _recent_posts.pop(key, None)
    key = (int(chat_id), sign.key)
    if key in _recent_posts:
        return False
    _recent_posts[key] = current
    return True


def release_sign_post(chat_id: int, sign: ZodiacSign) -> None:
    _recent_posts.pop((int(chat_id), sign.key), None)


async def forecast_reply_text(sign: ZodiacSign) -> str:
    forecasts = await get_forecasts()
    if not forecasts:
        return UNAVAILABLE_TEXT
    forecast = forecasts.get(sign.key)
    if forecast is None:
        return SIGN_MISSING_TEXT
    return forecast.html_text


async def handle_horoscope_message(message) -> None:
    command = parse_horoscope_command(message.text)
    if command is None:
        return
    if command.sign is None:
        await message.reply(MENU_TEXT, reply_markup=build_signs_keyboard())
        return
    await message.reply(await forecast_reply_text(command.sign), parse_mode="HTML")


async def handle_horoscope_callback(query) -> None:
    sign = sign_from_callback_data(query.data)
    menu_message = query.message
    if sign is None or menu_message is None:
        await query.answer()
        return

    chat_id = menu_message.chat.id
    if not claim_sign_post(chat_id, sign):
        await query.answer(f"{sign.emoji} {sign.name} только что был, глянь выше")
        return

    await query.answer()
    try:
        text = await forecast_reply_text(sign)
        await query.bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode="HTML",
            reply_parameters=ReplyParameters(
                message_id=menu_message.message_id,
                allow_sending_without_reply=True,
            ),
        )
    except Exception:
        release_sign_post(chat_id, sign)
        raise
