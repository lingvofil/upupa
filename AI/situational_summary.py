"""Короткие ситуативные вставки для случайных реакций чата.

Реакция должна быть короткой, но синтаксически законченной микросводкой
последних реплик: например ``произошёл внезапный срач`` или
``происходит спор о кальяне``.

Для этой реакции используется отдельный короткий буфер живых сообщений чата.
Он не зависит от ``conversation_history`` диалогового режима, куда обычная
болтовня без обращения к боту не попадает.
"""

from collections import deque
import logging
import re
from typing import Awaitable, Callable, Mapping, Sequence


_RESULT_RE = re.compile(
    r"^(происходит|произош[её]л|произошла|произошло)\s+(.+?)[.!?]?$",
    re.IGNORECASE,
)
_TOKEN_RE = re.compile(r"^[а-яёa-z0-9-]+$", re.IGNORECASE)
_DANGLING_MODIFIER_RE = re.compile(
    r"(?:ый|ий|ой|ая|яя|ое|ее|ые|ие|ого|его|ому|ему|ым|им|ую|юю|ых|их)$",
    re.IGNORECASE,
)

_STOP_WORDS = {
    "ага", "без", "блин", "блядь", "будет", "была", "были", "было", "быть",
    "вам", "вас", "весь", "вот", "вроде", "где", "давай", "даже", "для", "его",
    "если", "есть", "ещё", "зачем", "или", "как", "какая", "какие", "какой",
    "когда", "который", "кто", "куда", "лишь", "меня", "мне", "может", "можно",
    "мой", "над", "нам", "нас", "наш", "него", "нее", "неё", "нет", "них",
    "нужно", "она", "они", "оно", "очень", "под", "пока", "потом", "почему",
    "при", "про", "просто", "раз", "сам", "себя", "сейчас", "так", "там", "тебе",
    "тебя", "тем", "теперь", "тоже", "только", "тот", "тут", "твой", "уже", "упупа",
    "хоть", "чего", "чем", "что", "чтобы", "эта", "эти", "это", "этот", "ещe",
}

# Последние формулировки именно этой реакции — защита от локального зацикливания модели.
_recent_event_words: dict[str, deque[str]] = {}
# Отдельный живой контекст для ситуативной реакции.
_recent_chat_messages: dict[str, deque[dict]] = {}
# Защита от двойного process_random_reactions для одного Telegram message_id.
_seen_message_ids: dict[str, deque[int]] = {}

_RECENT_LIMIT = 20
_CONTEXT_LIMIT = 12
_SEEN_MESSAGE_LIMIT = 100
_MAX_EVENT_WORDS = 4


def _recent_for_chat(chat_id: int | str) -> deque[str]:
    key = str(chat_id)
    if key not in _recent_event_words:
        _recent_event_words[key] = deque(maxlen=_RECENT_LIMIT)
    return _recent_event_words[key]


def _context_for_chat(chat_id: int | str) -> deque[dict]:
    key = str(chat_id)
    if key not in _recent_chat_messages:
        _recent_chat_messages[key] = deque(maxlen=_CONTEXT_LIMIT)
    return _recent_chat_messages[key]


def _seen_for_chat(chat_id: int | str) -> deque[int]:
    key = str(chat_id)
    if key not in _seen_message_ids:
        _seen_message_ids[key] = deque(maxlen=_SEEN_MESSAGE_LIMIT)
    return _seen_message_ids[key]


def _message_author(message) -> str:
    user = getattr(message, "from_user", None)
    if not user:
        return "Участник"
    return (
        getattr(user, "full_name", None)
        or getattr(user, "first_name", None)
        or getattr(user, "username", None)
        or "Участник"
    )


def _register_incoming_message(message) -> bool:
    """Запоминает человеческую реплику и возвращает False для повторного вызова того же message_id."""
    chat = getattr(message, "chat", None)
    chat_id = getattr(chat, "id", None)
    if chat_id is None:
        return True

    message_id = getattr(message, "message_id", None)
    if message_id is not None:
        seen = _seen_for_chat(chat_id)
        if message_id in seen:
            logging.debug(
                "[situational-summary] Повторная обработка message_id=%s в чате %s пропущена",
                message_id,
                chat_id,
            )
            return False
        seen.append(message_id)

    user = getattr(message, "from_user", None)
    if user and getattr(user, "is_bot", False):
        return True

    text = (getattr(message, "text", None) or getattr(message, "caption", None) or "").strip()
    if text:
        _context_for_chat(chat_id).append(
            {
                "role": "user",
                "name": _message_author(message),
                "content": text,
            }
        )
        logging.debug(
            "[situational-summary] Контекст чата %s: %s сообщений",
            chat_id,
            len(_context_for_chat(chat_id)),
        )

    return True


def _usable_messages(history: Sequence[Mapping]) -> list[Mapping]:
    return [msg for msg in history if str(msg.get("content", "")).strip()]


def _normalize_model_result(text: str | None) -> tuple[str, str] | None:
    """Проверяет короткий законченный контракт и возвращает (фраза, ключ повтора)."""
    if not text:
        return None

    cleaned = text.strip().strip("*`_\"' «»").strip()
    match = _RESULT_RE.fullmatch(cleaned)
    if not match:
        return None

    prefix = match.group(1).lower().replace("произошел", "произошёл")
    payload = " ".join(match.group(2).strip().split()).lower()
    words = payload.split()

    if not 1 <= len(words) <= _MAX_EVENT_WORDS:
        return None
    if any(not _TOKEN_RE.fullmatch(word) for word in words):
        return None
    if words[0] in _STOP_WORDS:
        return None

    # Главная защита от ответов вида «произошёл странный».
    if len(words) == 1 and _DANGLING_MODIFIER_RE.search(words[0]):
        return None

    phrase = f"{prefix} {payload}"
    return phrase, phrase


def _format_event(phrase: str) -> str:
    # Сохраняем прежний Markdown-курсив ситуативной вставки.
    return f"*{phrase}*"


def _build_prompt(
    focus_messages: Sequence[Mapping],
    recent_summaries: Sequence[str] = (),
) -> str:
    lines = []
    for msg in focus_messages:
        author = msg.get("name") or ("Бот" if msg.get("role") == "assistant" else "Участник")
        text = str(msg.get("content", "")).strip()
        lines.append(f"{author}: {text}")

    context = "\n".join(lines)
    recent_block = "\n".join(f"- {item}" for item in recent_summaries[-5:]) or "- нет"
    return f"""
Ты делаешь короткую абсурдную, но ОСМЫСЛЕННУЮ микросводку происходящего в групповом чате.

Формат ответа — одна фраза без кавычек и без Markdown, от 2 до 5 слов целиком.
Начни её одним из вариантов:
- происходит ...
- произошёл ...
- произошла ...
- произошло ...

Правила:
- после первого слова дай законченную формулировку события из 1–4 слов;
- формулировка должна реально вытекать из последних реплик, а не быть случайным словом из них;
- если используешь прилагательное, обязательно допиши существительное: нельзя «произошёл странный», можно «произошёл странный спор»;
- согласуй род: «произошёл спор», «произошла ссора», «произошло примирение»;
- предпочитай конкретное действие, конфликт, смену темы, социальный жест или абсурдный итог разговора;
- можно быть грубым, нелепым и циничным, если это органично контексту;
- не объясняй ответ, не задавай вопросов, не называй автора без необходимости;
- не повторяй недавние микросводки.

Последние реплики:
---
{context}
---

Недавние микросводки, которых надо избегать:
{recent_block}

Микросводка:
""".strip()


async def generate_absurd_situational_reaction(
    chat_id: int,
    history: Sequence[Mapping],
    generate_with_model: Callable[..., Awaitable[str]],
    *,
    rng=None,
) -> str | None:
    """Сгенерировать короткую осмысленную ситуативную микросводку."""
    del rng  # оставлено в сигнатуре для совместимости со старыми тестами/вызовами

    usable = _usable_messages(history)
    if not usable:
        logging.info("[situational-summary] Нет текстового контекста для чата %s", chat_id)
        return None

    # Одной свежей реплики уже достаточно, но модель всегда видит до пяти последних сообщений.
    focus_messages = usable[-5:]
    recent = _recent_for_chat(chat_id)
    prompt = _build_prompt(focus_messages, list(recent))

    try:
        raw = await generate_with_model(
            prompt,
            chat_id,
            temperature=0.9,
            max_tokens=24,
        )
        logging.info("[situational-summary] Ответ модели для чата %s: %r", chat_id, raw)
        normalized = _normalize_model_result(raw)
        if not normalized:
            logging.info(
                "[situational-summary] Модель нарушила контракт для чата %s; реакция пропущена",
                chat_id,
            )
            return None

        phrase, repeat_key = normalized
        if repeat_key in recent:
            logging.info(
                "[situational-summary] Модель повторила недавнюю микросводку %r",
                repeat_key,
            )
            return None

        recent.append(repeat_key)
        return _format_event(phrase)
    except Exception as exc:
        logging.error(
            "[situational-summary] Ошибка генерации для чата %s: %s",
            chat_id,
            exc,
            exc_info=True,
        )
        return None


def install_into_random_reactions(random_reactions_module) -> None:
    """Подменяет ситуативную вставку и делает обработку одного Telegram-сообщения идемпотентной."""
    if getattr(random_reactions_module, "_situational_summary_patch_installed", False):
        return

    original_process_random_reactions = random_reactions_module.process_random_reactions

    async def patched_process_random_reactions(message, *args, **kwargs):
        # handlers/dialog.py и process_general_message исторически вызывали этот пайплайн дважды.
        # Первым вызовом сохраняем реплику и выполняем реакции, второй для того же message_id пропускаем.
        if not _register_incoming_message(message):
            return False
        return await original_process_random_reactions(message, *args, **kwargs)

    async def patched_generate_situational_reaction(chat_id: int) -> str | None:
        # Основной источник — отдельный буфер живой болтовни. conversation_history оставляем
        # только как fallback для старых/прямых сценариев вызова.
        history = list(_context_for_chat(chat_id))
        if not history:
            history = random_reactions_module.conversation_history.get(str(chat_id), [])
        return await generate_absurd_situational_reaction(
            chat_id,
            history,
            random_reactions_module.generate_with_model,
        )

    random_reactions_module.process_random_reactions = patched_process_random_reactions
    random_reactions_module.generate_situational_reaction = patched_generate_situational_reaction
    random_reactions_module._situational_summary_patch_installed = True
