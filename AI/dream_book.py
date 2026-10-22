"""Сонник Упупы: издевательское подетальное толкование снов участников.

Реакция срабатывает без команды, когда человек рассказывает свой сон
("мне снилось", "приснился", "видел во сне", "у меня был сон" и т.п.).
Чужие сны ("тебе снилось"), отрицания и идиомы ("мне такое и не снилось"),
пожелания ("пусть мне приснится") отсеиваются. Если сообщение — только
анонс ("мне такая дичь приснилась"), Упупа ждёт следующие сообщения того же
автора и толкует их вместе с анонсом. Пересланный сон толкуется от имени
автора оригинала; автопересылки постов канала и пересланные сообщения ботов
пропускаются.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import logging
import random
import re
import time

from AI.whoparody import generate_with_active_model, send_long_message
from infrastructure.ai.execution import ai_feature


DREAM_HEADER = "🔮 Сонник Упупы"

_COOLDOWN_SECONDS = 180.0
_TEASER_TTL_SECONDS = 180.0
_TEASER_MAX_FOLLOWUPS = 4
_FOLLOWUP_MIN_CONTENT_WORDS = 4
_MAX_DREAM_CHARS = 3000

# (chat_id, user_id) -> monotonic time of the last interpretation.
_last_interpretation: dict[tuple[int, int], float] = {}


@dataclass
class _PendingDream:
    created_at: float
    parts: list[str] = field(default_factory=list)
    followups: int = 0


# (chat_id, user_id) -> анонс сна, ожидающий продолжения.
_pending_dreams: dict[tuple[int, int], _PendingDream] = {}


_WORD_RE = re.compile(r"[а-яёa-z]+(?:-[а-яёa-z]+)*")
_SENTENCE_END_RE = re.compile(r"[.!?…\n]")

# сниться: снился/снилась/снилось/снились, снится/снятся/сниться,
# приснился..., наснилось. Будущее "приснится" почти всегда пожелание или
# вопрос, поэтому не считается рассказом. "СНИЛС" сюда не попадает.
_DREAM_VERB_RE = re.compile(
    r"\b(?:(?:при|на)?сни(?:лся|лась|лось|лись|лося)|сни(?:тся|ться)|снятся)\b"
)
# видеть сон: "видел сон", "видела сегодня такой сон", "вижу во сне".
_SEEING_RE = re.compile(
    r"\b(?:у?видел[аи]?|вижу)\b(?:\s+[а-яё-]+){0,3}?\s+(?:сон|сны|во\s+сне|в\s+сне|сновидени[ея])\b"
)
# "во сне я", "в моём сне", "у меня был сон", "мне был такой сон".
_FIRST_PERSON_SLEEP_RE = re.compile(
    r"\bво\s+сне\s+(?:я|мне|меня|у\s+меня)\b"
    r"|\bв\s+(?:моем|моих|своем|своих)\s+сн(?:е|ах)\b"
    r"|\b(?:мне|у\s+меня)\s+(?:[а-яё-]+\s+){0,2}?был\w?\s+(?:[а-яё-]+\s+){0,2}?(?:сон|сновидени[ея])\b"
)
# "сон про...", "сон, будто...", "сон: ..." — рассказ без глагола.
_DREAM_STORY_RE = re.compile(
    r"\bсон\s*,?\s*(?:про\b|о\s+том\b|будто\b|как\s+будто\b|словно\b|что\s+будто\b|:)"
)

_FIRST_PERSON_DATIVE = {"мне", "нам"}
_OTHER_DATIVE = {"тебе", "вам", "ему", "ей", "им", "нему", "ней", "ним"}
_OTHER_SUBJECT = {"ты", "вы", "он", "она", "они"}
_OTHER_POSSESSIVE = {
    "твой", "твоем", "твои", "твоих", "твоей", "ваш", "вашем", "ваши", "его", "ее", "их",
}
# Отрицания и пожелания перед глаголом: "и не снилось", "пусть приснится".
_BLOCKING_WORDS = {"не", "ни", "чтоб", "чтобы", "пусть", "пускай", "хочу", "хотел", "хотела"}
_PRONOUN_WINDOW = 4

# Слова, которые не являются содержанием сна: служебные, анонсные, вводные.
_NON_CONTENT_WORDS = {
    "мне", "нам", "меня", "мой", "моем", "моих", "свой", "своем", "я", "мы", "у", "в", "во",
    "и", "а", "но", "что", "как", "так", "вот", "это", "этот", "эта", "то", "там", "тут",
    "сон", "сны", "сне", "снах", "сна", "сновидение", "был", "была", "было", "были",
    "видел", "видела", "видели", "увидел", "увидела", "увидели", "вижу",
    "сегодня", "ночью", "вчера", "сейчас", "щас", "опять", "снова", "всю", "ночь", "утром",
    "недавно", "давно", "иногда", "часто", "постоянно", "каждую",
    "такой", "такая", "такое", "такие", "такую", "такого", "таком", "какой", "какая", "какое",
    "какой-то", "какая-то", "какое-то", "какие-то", "что-то", "кое-что", "одно", "одна",
    "дичь", "хрень", "херня", "херню", "хуйня", "хуйню", "жесть", "пиздец", "пиздецки",
    "странный", "странная", "странное", "странные", "странную", "страшный", "страшная",
    "страшное", "страшные", "ебанутый", "ебанутое", "ебанутая", "кошмар", "кошмары",
    "прикол", "ужас", "ужасный", "ужасное", "интересный", "интересное", "охуенный", "охуенное",
    "короче", "вообще", "кароче", "бля", "блять", "блин", "епт", "ребят", "ребята", "пацаны",
    "народ", "чат", "слушайте", "прикиньте", "расскажу", "щас", "сейчас", "мда", "капец",
    "просто", "очень", "прям", "прямо", "вот-вот", "типа", "будто", "словно", "про",
}


def _normalize(text: str) -> str:
    return (text or "").lower().replace("ё", "е")


def _words_before(text: str, position: int) -> list[str]:
    """Words of the current sentence before ``position``, nearest last."""
    start = 0
    for match in _SENTENCE_END_RE.finditer(text, 0, position):
        start = match.end()
    return _WORD_RE.findall(text[start:position])[-_PRONOUN_WINDOW:]


def _first_word_after(text: str, position: int) -> str | None:
    match = _WORD_RE.search(text, position)
    if not match or _SENTENCE_END_RE.search(text, position, match.start()):
        return None
    return match.group(0)


def _blocked(before: list[str]) -> bool:
    return any(word in _BLOCKING_WORDS for word in before[-2:]) or any(
        word in {"чтоб", "чтобы", "пусть", "пускай"} for word in before
    )


def _dream_verb_is_own(text: str, match: re.Match) -> bool:
    before = _words_before(text, match.start())
    if _blocked(before):
        return False
    # Решает ближайшее местоимение-адресат: "ты мне приснилась" — свой сон,
    # "мне кажется, тебе снилось" — чужой.
    for word in reversed(before):
        if word in _FIRST_PERSON_DATIVE:
            return True
        if word in _OTHER_DATIVE:
            return False
    after = _first_word_after(text, match.end())
    if after in _OTHER_DATIVE or after == "ли":
        return False
    return True


def _seeing_is_own(text: str, match: re.Match) -> bool:
    before = _words_before(text, match.start())
    if _blocked(before):
        return False
    verb_end = match.start() + len(match.group(0).split()[0])
    if _first_word_after(text, verb_end) == "ли":
        return False
    for word in reversed(before):
        if word in {"я", "мы"}:
            return True
        if word in _OTHER_SUBJECT:
            return False
    return True


def _story_is_own(text: str, match: re.Match) -> bool:
    before = _words_before(text, match.start())
    return not any(
        word in _OTHER_POSSESSIVE or word in _OTHER_DATIVE or word in _OTHER_SUBJECT
        for word in before
    )


def is_dream_mention(text: str | None) -> bool:
    """True, когда автор сообщения рассказывает (или анонсирует) свой сон."""
    normalized = _normalize(text)
    if not normalized:
        return False

    for match in _DREAM_VERB_RE.finditer(normalized):
        if _dream_verb_is_own(normalized, match):
            return True
    for match in _SEEING_RE.finditer(normalized):
        if _seeing_is_own(normalized, match):
            return True
    for match in _FIRST_PERSON_SLEEP_RE.finditer(normalized):
        if "как" not in _words_before(normalized, match.start())[-1:]:
            return True
    for match in _DREAM_STORY_RE.finditer(normalized):
        if _story_is_own(normalized, match):
            return True
    return False


def dream_content_words(text: str | None) -> list[str]:
    """Значимые слова сна без триггера, анонсных и служебных слов."""
    normalized = _normalize(text)
    words = _WORD_RE.findall(normalized)
    return [
        word for word in words
        if len(word) >= 3
        and word not in _NON_CONTENT_WORDS
        and not _DREAM_VERB_RE.fullmatch(word)
    ]


def is_dream_teaser(text: str | None) -> bool:
    """Анонс без содержания: "мне сегодня такая дичь приснилась"."""
    return is_dream_mention(text) and not dream_content_words(text)


DREAM_PROMPT = """Ты — «Сонник Упупы»: потомственный толкователь снов с дипломом из подземного перехода,
циничный, злой, саркастичный и матерящийся. Участник Telegram-чата по имени {name} рассказал свой сон.{forward_note}

Твоя задача — растолковать этот сон по деталям, коротко и максимально издевательски:
1. Выбери 2–4 САМЫХ ярких образа сна: существа, персонажи, предметы, места, нелепые события —
   только то, что реально есть в тексте. Местоимения (я, меня, мне, мы, он, они и т.п.), самого
   сновидца, время («сегодня», «ночью»), слово «сон» и общие слова («что-то», «там») НЕ толкуй никогда.
2. Каждому образу — одна строка «▪️ <образ> — <толкование>»: одна хлёсткая фраза до 15 слов.
   Иногда, не в каждой строке, ссылайся на выдуманные авторитеты (сонник бабы Зины из третьего
   подъезда, Фрейд после литра, Ванга по телефону). Издевайся над тем, что образ говорит о сновидце.
3. Последняя строка «Итог: ...» — одно предложение: диагноз сновидцу и нелепый прогноз.

Тон: сарказм, ирония, издёвка, русский мат, но мат должен быть смешным, а не через каждое слово.
Без травли по национальности, ориентации и болезням. Если во сне умирают близкие —
стебись над абсурдом сюжета, а не над горем.
Формат: обычный текст без Markdown — никаких звёздочек, решёток и жирного шрифта.
Без вступлений вроде «Итак» или «Давай разберём», не пересказывай сон.
Объём: 40–110 слов на весь ответ, даже если сон длинный. Короче — смешнее. Если деталей почти нет —
растолкуй то немногое, что есть, и поиздевайся над тем, что сновидец не умеет рассказывать сны.

Сон, рассказанный {name}:
---
{dream}
---
"""


_FORWARD_NOTE = (
    "\nЭтот сон переслал в чат {forwarded_by} — сон не его, толкуй сон {name}. "
    "Над {forwarded_by} можешь мимоходом поиздеваться за то, что таскает в чат чужие сны."
)


def build_dream_prompt(
    dream_text: str,
    author_name: str | None,
    *,
    forwarded_by: str | None = None,
) -> str:
    dream = (dream_text or "").strip()
    if len(dream) > _MAX_DREAM_CHARS:
        dream = dream[:_MAX_DREAM_CHARS].rstrip() + "…"
    name = (author_name or "аноним").strip() or "аноним"
    forward_note = _FORWARD_NOTE.format(forwarded_by=forwarded_by, name=name) if forwarded_by else ""
    return DREAM_PROMPT.format(name=name, dream=dream, forward_note=forward_note)


_MARKDOWN_HEADING_RE = re.compile(r"(?m)^[ \t]*#{1,6}[ \t]*")


_MAX_SYMBOLS = 4
_SYMBOL_LINE_RE = re.compile(r"^[ \t]*(?:▪️?|•|-|\*)[ \t]*(?P<image>[^\n]+?)[ \t]+[—–-][ \t]+\S")
# Образы, которые толковать бессмысленно: местоимения, сам сновидец, время, "сон".
_TRIVIAL_IMAGE_WORDS = {
    "я", "меня", "мне", "мной", "мною", "ты", "тебя", "тебе", "тобой", "он", "его", "ему", "него",
    "нему", "нем", "им", "ним", "она", "ее", "ей", "нее", "ней", "мы", "нас", "нам", "нами", "вы",
    "вас", "вам", "вами", "они", "их", "них", "ними", "себя", "себе", "собой", "сам", "сама", "сами",
    "мой", "моя", "мое", "мои", "моего", "моей", "твой", "свой", "своя", "свои", "это", "этот", "эта",
    "то", "тот", "та", "там", "тут", "здесь", "что-то", "кто-то", "сон", "сне", "сны", "сегодня",
    "ночью", "ночь", "вчера", "утром", "сновидец", "сновидца", "сновидцу",
}


def _is_trivial_image(image: str) -> bool:
    words = _WORD_RE.findall(_normalize(image))
    return not words or all(word in _TRIVIAL_IMAGE_WORDS for word in words)


def _trim_symbols(text: str) -> str:
    """Выбрасывает толкования местоимений и лишние образы сверх _MAX_SYMBOLS."""
    kept: list[str] = []
    symbols = 0
    for line in text.splitlines():
        match = _SYMBOL_LINE_RE.match(line)
        if match:
            if _is_trivial_image(match.group("image")) or symbols >= _MAX_SYMBOLS:
                continue
            symbols += 1
        kept.append(line)
    return "\n".join(kept)


def _clean_interpretation(text: str) -> str:
    cleaned = _MARKDOWN_HEADING_RE.sub("", text or "")
    cleaned = cleaned.replace("**", "").replace("__", "")
    cleaned = _trim_symbols(cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


@ai_feature("сонник")
async def interpret_dream(
    dream_text: str,
    chat_id: int,
    author_name: str | None,
    *,
    forwarded_by: str | None = None,
) -> str | None:
    """Генерирует толкование сна; None, если модель промолчала или упала."""
    prompt = build_dream_prompt(dream_text, author_name, forwarded_by=forwarded_by)
    try:
        response = await generate_with_active_model(prompt, chat_id)
    except Exception as exc:
        logging.warning("[dream_book] генерация толкования для чата %s упала: %s", chat_id, exc)
        return None
    cleaned = _clean_interpretation(response)
    return cleaned or None


def _message_text(message) -> str:
    return (getattr(message, "text", None) or getattr(message, "caption", None) or "").strip()


_SKIP = object()


def _forwarded_author(origin, forwarder) -> str | None | object:
    """Автор пересланного сна: имя, None для собственного сообщения, _SKIP для бота."""
    sender_user = getattr(origin, "sender_user", None)
    if sender_user is not None:
        if getattr(sender_user, "is_bot", False):
            return _SKIP
        if getattr(sender_user, "id", None) == getattr(forwarder, "id", None):
            return None
        return getattr(sender_user, "first_name", None) or getattr(sender_user, "full_name", None) or "аноним"
    for name in (
        getattr(origin, "author_signature", None),
        getattr(origin, "sender_user_name", None),
        getattr(getattr(origin, "sender_chat", None), "title", None),
        getattr(getattr(origin, "chat", None), "title", None),
    ):
        if name:
            return name
    return "аноним"


_STATE_PRUNE_THRESHOLD = 2000


def _prune_state(now: float) -> None:
    """Drop expired cooldowns/teasers once the in-memory maps grow large."""
    if len(_last_interpretation) > _STATE_PRUNE_THRESHOLD:
        for key, last in list(_last_interpretation.items()):
            if now - last >= _COOLDOWN_SECONDS:
                _last_interpretation.pop(key, None)
    if len(_pending_dreams) > _STATE_PRUNE_THRESHOLD:
        for key, pending in list(_pending_dreams.items()):
            if now - pending.created_at > _TEASER_TTL_SECONDS:
                _pending_dreams.pop(key, None)


def _on_cooldown(key: tuple[int, int], now: float) -> bool:
    last = _last_interpretation.get(key)
    return last is not None and now - last < _COOLDOWN_SECONDS


def _take_pending_followup(key: tuple[int, int], text: str, now: float) -> str | None:
    """Добавляет сообщение к ожидающему анонсу; возвращает весь сон, когда он набрался."""
    pending = _pending_dreams.get(key)
    if pending is None:
        return None
    if now - pending.created_at > _TEASER_TTL_SECONDS:
        _pending_dreams.pop(key, None)
        return None

    pending.parts.append(text)
    pending.followups += 1
    story = "\n".join(pending.parts)
    if len(dream_content_words(story)) >= _FOLLOWUP_MIN_CONTENT_WORDS:
        _pending_dreams.pop(key, None)
        return story
    if pending.followups >= _TEASER_MAX_FOLLOWUPS:
        _pending_dreams.pop(key, None)
    return None


async def maybe_interpret_dream(message, *, probability: float = 1.0, rng=random.random) -> bool:
    """Толкует сон из сообщения; True, если толкование отправлено."""
    user = getattr(message, "from_user", None)
    # Автопересылка поста канала в группу обсуждения — не рассказ участника.
    if user is None or getattr(user, "is_bot", False) or getattr(message, "is_automatic_forward", False):
        return False
    text = _message_text(message)
    if not text:
        return False

    author_name = getattr(user, "first_name", None)
    forwarded_by = None
    origin = getattr(message, "forward_origin", None)
    if origin is not None:
        original_author = _forwarded_author(origin, user)
        if original_author is _SKIP:
            return False
        if original_author is not None:
            author_name, forwarded_by = original_author, author_name

    key = (int(message.chat.id), int(user.id))
    now = time.monotonic()
    _prune_state(now)

    dream = _take_pending_followup(key, text, now)
    if dream is None:
        if not is_dream_mention(text) or _on_cooldown(key, now):
            return False
        if probability <= 0 or rng() >= probability:
            return False
        if not dream_content_words(text):
            # "мне снилось, что ли?" — вопрос, а не анонс рассказа.
            if text.rstrip().endswith("?"):
                return False
            _pending_dreams[key] = _PendingDream(created_at=now, parts=[text])
            logging.info("[dream_book] анонс сна в чате %s, ждём продолжение от %s", key[0], key[1])
            return False
        dream = text

    _last_interpretation[key] = now
    try:
        await message.bot.send_chat_action(chat_id=message.chat.id, action="typing")
    except Exception:
        logging.debug("[dream_book] send_chat_action failed", exc_info=True)

    interpretation = await interpret_dream(dream, message.chat.id, author_name, forwarded_by=forwarded_by)
    if not interpretation:
        _last_interpretation.pop(key, None)
        return False

    await send_long_message(message, f"{DREAM_HEADER}\n\n{interpretation}")
    return True
