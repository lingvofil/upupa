"""Абсурдные ситуативные вставки для случайных реакций чата.

Вместо длинной «кинематографичной» ремарки бот выдаёт микросводку:
``происходит <слово>`` или ``произошёл <слово>``.
Часть ответов осмысленно резюмирует ситуацию через LLM, часть намеренно
подхватывает одно содержательное слово из последних реплик.

Для этой реакции используется отдельный короткий буфер живых сообщений чата.
Он не зависит от ``conversation_history`` диалогового режима, куда обычная
болтовня без обращения к боту не попадает.
"""

from collections import deque
import logging
import random
import re
from typing import Awaitable, Callable, Iterable, Mapping, Sequence


_WORD_RE = re.compile(r"\b[а-яёa-z][а-яёa-z0-9-]{2,31}\b", re.IGNORECASE)
_RESULT_RE = re.compile(
    r"^(происходит|произош[её]л)\s+([а-яёa-z][а-яёa-z0-9-]{1,31})[.!?]?$",
    re.IGNORECASE,
)

# Не даём алгоритмическому режиму превращаться в «произошёл который».
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

# Последние слова именно этой реакции — защита от локального зацикливания модели.
_recent_event_words: dict[str, deque[str]] = {}
# Отдельный живой контекст для ситуативной реакции.
_recent_chat_messages: dict[str, deque[dict]] = {}
# Защита от двойного process_random_reactions для одного Telegram message_id.
_seen_message_ids: dict[str, deque[int]] = {}

_RECENT_LIMIT = 20
_CONTEXT_LIMIT = 12
_SEEN_MESSAGE_LIMIT = 100
_DIRECT_WORD_PROBABILITY = 0.42


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


def _extract_candidate_words(messages: Iterable[Mapping]) -> list[str]:
    """Берёт содержательные слова из человеческих реплик, сохраняя свежесть."""
    words: list[str] = []
    for msg in messages:
        if msg.get("role") == "assistant":
            continue
        text = str(msg.get("content", "")).lower()
        for word in _WORD_RE.findall(text):
            word = word.strip("-")
            if len(word) < 3 or word in _STOP_WORDS or word.isdigit():
                continue
            words.append(word)
    return words


def _normalize_model_result(text: str | None) -> tuple[str, str] | None:
    """Проверяет строгий двухсловный контракт и возвращает (фраза, слово)."""
    if not text:
        return None

    cleaned = text.strip().strip("*`_\"' «»").strip()
    match = _RESULT_RE.fullmatch(cleaned)
    if not match:
        return None

    prefix = match.group(1).lower().replace("произошел", "произошёл")
    word = match.group(2).lower()
    if word in _STOP_WORDS:
        return None
    return f"{prefix} {word}", word


def _format_event(prefix: str, word: str) -> str:
    # Сохраняем прежний Markdown-курсив ситуативной вставки.
    return f"*{prefix} {word}*"


def _pick_direct_word(
    chat_id: int,
    focus_messages: Sequence[Mapping],
    rng=random,
) -> tuple[str, str] | None:
    candidates = _extract_candidate_words(focus_messages)
    if not candidates:
        return None

    # Не повторяем недавние события, пока есть свежие слова.
    recent = set(_recent_for_chat(chat_id))
    fresh = [word for word in candidates if word not in recent]
    pool = fresh or candidates

    # Ограничиваемся последними словами: абсурд должен всё же цепляться за текущий чат.
    word = rng.choice(pool[-25:])
    prefix = rng.choice(("происходит", "произошёл"))
    return f"{prefix} {word}", word


def _build_prompt(focus_messages: Sequence[Mapping]) -> str:
    lines = []
    for msg in focus_messages:
        author = msg.get("name") or ("Бот" if msg.get("role") == "assistant" else "Участник")
        text = str(msg.get("content", "")).strip()
        lines.append(f"{author}: {text}")

    context = "\n".join(lines)
    return f"""
Ты делаешь абсурдную микросводку происходящего в групповом чате.

Ответ должен состоять РОВНО из двух слов и соответствовать одному шаблону:
происходит СЛОВО
произошёл СЛОВО

Правила для СЛОВО:
- ровно одно слово, без пояснений и продолжения;
- чаще кратко назови происходящее: действие, явление, тип ситуации или социальный жест;
- иногда намеренно возьми буквально одно заметное слово из последних реплик, даже если результат абсурден;
- допускаются разговорные, грубые и нелепые слова, если они органичны этому чату;
- не пиши имена авторов как пояснение и не добавляй знаки оформления;
- не повторяй формулировки из инструкции.

Последние реплики:
---
{context}
---

Два слова:
""".strip()


async def generate_absurd_situational_reaction(
    chat_id: int,
    history: Sequence[Mapping],
    generate_with_model: Callable[..., Awaitable[str]],
    *,
    rng=random,
) -> str | None:
    """Сгенерировать короткое ``происходит/произошёл + одно слово``."""
    usable = _usable_messages(history)
    if not usable:
        logging.info("[situational-summary] Нет текстового контекста для чата %s", chat_id)
        return None

    # Одной свежей реплики уже достаточно: абсурдная вставка не требует полноценного диалога.
    focus_messages = usable[-5:]
    recent = _recent_for_chat(chat_id)

    # Значимая доля вставок вообще не требует LLM: берём слово прямо из живой речи.
    if rng.random() < _DIRECT_WORD_PROBABILITY:
        direct = _pick_direct_word(chat_id, focus_messages, rng=rng)
        if direct:
            phrase, word = direct
            recent.append(word)
            logging.info("[situational-summary] Прямое слово для чата %s: %s", chat_id, phrase)
            prefix, _ = phrase.split(maxsplit=1)
            return _format_event(prefix, word)

    prompt = _build_prompt(focus_messages)
    try:
        raw = await generate_with_model(
            prompt,
            chat_id,
            temperature=1.05,
            max_tokens=12,
        )
        logging.info("[situational-summary] Ответ модели для чата %s: %r", chat_id, raw)
        normalized = _normalize_model_result(raw)
        if normalized:
            phrase, word = normalized
            if word not in recent:
                recent.append(word)
                prefix, _ = phrase.split(maxsplit=1)
                return _format_event(prefix, word)
            logging.info("[situational-summary] Модель повторила недавнее слово %r", word)
    except Exception as exc:
        logging.error(
            "[situational-summary] Ошибка генерации для чата %s: %s",
            chat_id,
            exc,
            exc_info=True,
        )

    # Модель нарушила формат / повторилась / упала — всё равно выдаём локальный абсурд.
    direct = _pick_direct_word(chat_id, focus_messages, rng=rng)
    if not direct:
        return None
    phrase, word = direct
    recent.append(word)
    prefix, _ = phrase.split(maxsplit=1)
    return _format_event(prefix, word)


def install_into_random_reactions(random_reactions_module) -> None:
    """Подменяет ситуативную вставку и делает обработку одного Telegram-сообщения идемпотентной."""
    if getattr(random_reactions_module, "_situational_summary_patch_installed", False):
        return

    original_process_random_reactions = random_reactions_module.process_random_reactions

    async def patched_process_random_reactions(message, *args, **kwargs):
        # handlers/dialog.py и process_general_message исторически вызывают этот пайплайн дважды.
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
