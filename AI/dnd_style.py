"""Runtime style layer for DnD without changing mechanical semantics."""

from __future__ import annotations

import logging
import random
import re

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


DND_STYLE_MARKER = "СТИЛЬ УПУПЫ: КРИВАЯ ЕГРА"
DND_STORY_MAX_WORDS = 70
DND_STYLE_INSTRUCTION = f"""
{DND_STYLE_MARKER}.
Пиши весь художественный текст намеренно криво и по-падонковски: используй эрративы,
фонетически узнаваемые орфографические ошибки, простые опечатки, кривое согласование,
разговорный сленг и мат. Нормальное слово «игра» по возможности пиши «егра»,
«игроки» — «егроки», «участники» — «учаснеги». Не превращай текст в нечитаемый набор букв:
смысл сцены и варианты действий должны оставаться понятными.

Будь заметно грубее: подкалывай партию и конкретных персонажей по ситуации, но не заканчивай
каждое сообщение одной и той же дежурной обзывалкой. Сарказм, мат и издёвка должны быть частью
конкретной сцены, а не копипастой. Не используй оскорбления по защищённым признакам.
Не поясняй, что специально делаешь ошибки: для этого мастера безграмотность — нормальная речь.

ДЛИНА: обычно 40–60 слов художественного текста, жёсткий максимум 70 слов. Пиши плотнее:
не пересказывай только что случившееся, не повторяй решения игроков и не разжёвывай очевидные
последствия. Обычно достаточно одного-двух коротких абзацев плюс технический тег.

СВОБОДНЫЕ ХОДЫ ПАРТИИ — ВАЖНЫ, НО НЕ ДВА ПОДРЯД. ACTION:INPUT может появляться часто,
но не чаще чем через один игровой эпизод. НИКОГДА не ставь ACTION:INPUT, если предыдущий
технический тег мастера тоже был ACTION:INPUT — это относится и к общему ходу партии, и к
адресному INPUT с TARGETS. После INPUT следующий эпизод должен завершаться ROLL или POLL,
если игра не заканчивается через END. Если ситуацию можно интересно разрулить фантазией игроков,
предпочитай ACTION:INPUT только при соблюдении этого ограничения. Не делай длинную цепочку ROLL/POLL:
после одного-двух таких эпизодов снова можно дать свободный ACTION:INPUT. POLL используй только когда
реально нужны несколько заранее сформулированных альтернатив, ROLL — только когда важен неопределённый исход.

БАЛАНС БРОСКОВ: MODE:NORMAL — штатный режим и должен использоваться заметно чаще всего,
ориентир примерно 70–80% бросков. ADVANTAGE и DISADVANTAGE — редкие ситуационные исключения,
а не украшение каждого броска. Назначай их только когда в ТЕКУЩЕЙ сцене есть конкретный фактор:
явная помощь/подготовка/позиционное преимущество либо конкретная помеха/состояние/опасное окружение.
Не ставь DISADVANTAGE просто потому, что у персонажа вообще есть слабость: она должна буквально
мешать именно этому действию здесь и сейчас. Аналогично сильная сторона не обязана автоматически
давать ADVANTAGE. Не выдавай модифицированный MODE несколько бросков подряд. Примеры тегов из
базовой инструкции показывают только синтаксис и НЕ задают желаемую частоту режимов.
""".strip()


_REPLACEMENTS = (
    ("Игра", "Егра"),
    ("игра", "егра"),
    ("Игроки", "Егроки"),
    ("игроки", "егроки"),
    ("Игрок", "Егрок"),
    ("игрок", "егрок"),
    ("Участники", "Учаснеги"),
    ("участники", "учаснеги"),
    ("участников", "учаснегов"),
    ("участникам", "учаснегам"),
    ("история", "исторея"),
    ("История", "Исторея"),
    ("действия", "дейсвия"),
    ("действие", "дейсвие"),
    ("Действия", "Дейсвия"),
    ("голосование", "галасавание"),
    ("Голосование", "Галасавание"),
    ("выбор", "выбар"),
    ("Выбор", "Выбар"),
    ("Пишите", "Пешите"),
    ("пишите", "пешите"),
    ("Пиши", "Пеши"),
    ("пиши", "пеши"),
    ("может", "можит"),
    ("Может", "Можит"),
    ("только", "тока"),
    ("Только", "Тока"),
    ("сначала", "сночала"),
    ("Сначала", "Сночала"),
    ("начать", "начять"),
    ("завершить", "завиршить"),
    ("запустить", "запустить, бля,"),
    ("решайте", "решайте, дебилы,"),
    ("продолжай", "прадалжай"),
)

_INSULT_SUFFIXES = (
    " Ну чо, дегенераты.",
    " Шевелитесь, мудилы.",
    " Думайте, кретины.",
    " Не тормозите, долбоёбы.",
    " Решайте уже, герои хуевы.",
    " Ну давайте, мастера катастроф.",
    " Чо встали, стратеги из ларька.",
    " Соберитесь, цирк уехал без вас.",
    " Пошевелите извилинами, если нашли.",
    " Давайте, легенды районного масштаба.",
    " Решение будет или опять коллективный ступор?",
    " Ну же, специалисты по плохим идеям.",
    " Не тупим, у мира и без вас проблем хватает.",
    " Шевелитесь, пока сюжет не сдох.",
    " Выбирайте, пока здравый смысл не вернулся.",
    " Ну чо, академики хуёвых решений.",
)

_last_insult_by_key: dict[str, str] = {}

_ACTION_TAG_RE = re.compile(r"\[ACTION:([A-Z]+)(?:[;\]])", flags=re.IGNORECASE)
_FULL_ACTION_TAG_RE = re.compile(r"\[ACTION:.*?\]", flags=re.IGNORECASE | re.DOTALL)
_INPUT_TAG_RE = re.compile(
    r"\[ACTION:INPUT(?:;TARGETS:([0-9,\s]+))?\]",
    flags=re.IGNORECASE,
)
_ROLL_MODE_RE = re.compile(
    r"(\[ACTION:ROLL\b[^\]]*?\bMODE:)(NORMAL|ADVANTAGE|DISADVANTAGE)(?=;|\])",
    flags=re.IGNORECASE | re.DOTALL,
)


def _pick_insult_suffix(key=None) -> str:
    state_key = str(key) if key is not None else "__global__"
    previous = _last_insult_by_key.get(state_key)
    candidates = [suffix for suffix in _INSULT_SUFFIXES if suffix != previous]
    suffix = random.choice(candidates or list(_INSULT_SUFFIXES))
    _last_insult_by_key[state_key] = suffix
    return suffix


def errative_text(text: str, *, add_insult: bool = False, taunt_key=None) -> str:
    """Apply readable padonak-style distortion and at most one varied taunt."""
    if not text:
        return text
    result = str(text)
    for source, target in _REPLACEMENTS:
        result = result.replace(source, target)
    already_taunted = any(suffix.strip() in result for suffix in _INSULT_SUFFIXES)
    if (
        add_insult
        and not already_taunted
        and not any(label in result for label in ("КРИТИЧЕСКАЯ УДАЧА", "КРИТИЧЕСКАЯ НЕУДАЧА"))
    ):
        result += _pick_insult_suffix(taunt_key)
    return result


def _compact_request_text(text: str) -> str:
    """Normalize legacy 100-word hints to the current compact DnD contract."""
    result = str(text or "")
    result = result.replace(
        "СТРОГО до 100 слов",
        "обычно 40–60 слов, СТРОГО не больше 70 слов",
    )
    result = result.replace(
        "не более 100 слов",
        "обычно 40–60 слов, максимум 70 слов",
    )
    result = result.replace(
        "до 100 слов",
        "обычно 40–60 слов, максимум 70 слов",
    )
    return result


def _ensure_style_instruction(prompt: str) -> str:
    result = _compact_request_text(prompt).rstrip()
    if DND_STYLE_MARKER not in result:
        result += "\n\n" + DND_STYLE_INSTRUCTION
    return result


def _compact_story_response(text: str) -> str:
    """Keep user-facing story text under the hard cap while preserving the action tag."""
    source = str(text or "").strip()
    action_match = _FULL_ACTION_TAG_RE.search(source)
    action_tag = action_match.group(0) if action_match else ""
    story = _FULL_ACTION_TAG_RE.sub("", source).strip()
    words = story.split()
    if len(words) <= DND_STORY_MAX_WORDS:
        return source

    sentences = re.split(r"(?<=[.!?…])\s+", story)
    kept = []
    kept_words = 0
    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue
        sentence_words = len(sentence.split())
        if kept_words + sentence_words > DND_STORY_MAX_WORDS:
            break
        kept.append(sentence)
        kept_words += sentence_words

    if kept:
        compact_story = " ".join(kept).strip()
    else:
        compact_story = " ".join(words[:DND_STORY_MAX_WORDS]).rstrip(" ,;:") + "…"

    if action_tag:
        return f"{compact_story}\n{action_tag}".strip()
    return compact_story


def _replace_last_assistant_content(session, raw_text: str, compact_text: str) -> None:
    if raw_text == compact_text:
        return
    conversation = getattr(session, "conversation", None)
    if not isinstance(conversation, list):
        return
    for item in reversed(conversation):
        if not isinstance(item, dict) or item.get("role") != "assistant":
            continue
        if item.get("content") == raw_text:
            item["content"] = compact_text
        return


def _action_kind(text: str | None) -> str | None:
    match = _ACTION_TAG_RE.search(str(text or ""))
    return match.group(1).upper() if match else None


def _roll_mode(text: str | None) -> str | None:
    match = _ROLL_MODE_RE.search(str(text or ""))
    return match.group(2).upper() if match else None


def _recent_roll_modes(conversation, *, limit: int = 4) -> list[str]:
    modes = []
    for item in reversed(list(conversation or [])):
        if not isinstance(item, dict) or item.get("role") != "assistant":
            continue
        mode = _roll_mode(item.get("content"))
        if not mode:
            continue
        modes.append(mode)
        if len(modes) >= limit:
            break
    return list(reversed(modes))


def _replace_roll_mode(text: str, mode: str) -> str:
    return _ROLL_MODE_RE.sub(lambda match: match.group(1) + mode, str(text), count=1)


def _balance_roll_mode(session, text: str, *, history=None) -> str:
    """Keep non-normal roll modes exceptional even if the model overproduces them."""
    requested = _roll_mode(text)
    if requested not in {"ADVANTAGE", "DISADVANTAGE"}:
        return text
    recent = _recent_roll_modes(
        history if history is not None else getattr(session, "conversation", None),
        limit=4,
    )
    if not recent:
        return text

    recent_modified = sum(mode != "NORMAL" for mode in recent)
    should_normalize = (
        recent[-1] != "NORMAL"
        or requested in recent
        or recent_modified >= 2
    )
    if not should_normalize:
        return text

    logging.info(
        "DnD roll mode balanced chat_id=%s requested=%s recent=%s applied=NORMAL",
        getattr(session, "chat_id", None),
        requested,
        recent,
    )
    return _replace_roll_mode(text, "NORMAL")


def _last_assistant_action(conversation) -> str | None:
    for item in reversed(list(conversation or [])):
        if not isinstance(item, dict) or item.get("role") != "assistant":
            continue
        action = _action_kind(item.get("content"))
        if action:
            return action
    return None


def _fallback_non_input_poll(text: str) -> str:
    """Last-resort deterministic guard if the model ignores two correction prompts."""
    input_match = _INPUT_TAG_RE.search(str(text or ""))
    targets = input_match.group(1).strip() if input_match and input_match.group(1) else ""
    clean_text = re.sub(r"\[ACTION:.*?\]", "", str(text or "")).strip()
    target_field = f";TARGETS:{targets}" if targets else ""
    tag = (
        f"[ACTION:POLL{target_field};OPTIONS:Действовать осторожно;"
        "Рискнуть и полезть напролом]"
    )
    return _compact_story_response(f"{clean_text}\n{tag}".strip())


async def _generate_without_consecutive_input(original_generate, session, prompt: str) -> str:
    """Generate a compact turn while guarding INPUT cadence and roll-mode balance."""
    conversation = getattr(session, "conversation", None)
    previous_action = _last_assistant_action(conversation or [])

    async def generate_once(request: str, *, force_style: bool = False) -> str:
        history_before = list(conversation) if isinstance(conversation, list) else []
        if force_style or isinstance(conversation, list):
            prepared_request = _ensure_style_instruction(request)
        else:
            prepared_request = _compact_request_text(request)
        raw_result = await original_generate(session, prepared_request)
        compact_result = _compact_story_response(raw_result)
        balanced_result = _balance_roll_mode(session, compact_result, history=history_before)
        _replace_last_assistant_content(session, raw_result, balanced_result)
        return balanced_result

    result = await generate_once(prompt)
    if previous_action != "INPUT" or _action_kind(result) != "INPUT":
        return result

    logging.info(
        "DnD rejected consecutive ACTION:INPUT chat_id=%s",
        getattr(session, "chat_id", None),
    )
    correction_prompt = (
        "Предыдущий технический ход уже был ACTION:INPUT, а ты снова выдал ACTION:INPUT. "
        "Так нельзя. Перепиши ближайший сюжетный эпизод без нового свободного хода партии. "
        "Заверши его только ACTION:ROLL или ACTION:POLL; если это настоящий финал — ACTION:END. "
        "Не упоминай это исправление и не используй ACTION:INPUT ни с TARGETS, ни без TARGETS."
    )
    for _attempt in range(2):
        corrected = await generate_once(correction_prompt, force_style=True)
        if _action_kind(corrected) != "INPUT":
            return corrected
        result = corrected

    logging.warning(
        "DnD model ignored consecutive INPUT guard; using fallback poll chat_id=%s",
        getattr(session, "chat_id", None),
    )
    return _fallback_non_input_poll(result)


class _StyledBotProxy:
    def __init__(self, bot):
        self._bot = bot

    async def send_message(self, chat_id, text, **kwargs):
        return await self._bot.send_message(
            chat_id,
            errative_text(text, add_insult=True, taunt_key=chat_id),
            **kwargs,
        )

    def __getattr__(self, name):
        return getattr(self._bot, name)

    def __eq__(self, other):
        if isinstance(other, _StyledBotProxy):
            other = other._bot
        return self._bot == other

    def __repr__(self):
        return repr(self._bot)


def _styled_bot(bot):
    return bot if isinstance(bot, _StyledBotProxy) else _StyledBotProxy(bot)


def configure_dnd_style() -> None:
    """Install DnD-only style wrappers. Safe to call more than once."""
    from AI import dnd
    from AI.dnd_completion import configure_dnd_completion

    configure_dnd_completion(dnd.dnd_router)
    if getattr(dnd, "_upupa_dnd_style_configured", False):
        return

    dnd.DND_SYSTEM_PROMPT = _ensure_style_instruction(dnd.DND_SYSTEM_PROMPT)

    original_generate_session_response = dnd.generate_session_response

    async def guarded_generate_session_response(session, prompt: str) -> str:
        return await _generate_without_consecutive_input(
            original_generate_session_response,
            session,
            prompt,
        )

    dnd.generate_session_response = guarded_generate_session_response

    original_with_scene_direction = dnd.with_scene_direction

    def styled_with_scene_direction(session, prompt: str) -> str:
        result = original_with_scene_direction(session, _compact_request_text(prompt))
        return _ensure_style_instruction(result)

    dnd.with_scene_direction = styled_with_scene_direction

    # Keep text distortion local, but let _StyledBotProxy own the single automatic
    # taunt. Previously these helpers added one taunt and the proxy added another.
    original_action_prompt_text = dnd._action_prompt_text
    dnd._action_prompt_text = lambda session: errative_text(
        original_action_prompt_text(session), add_insult=False
    )

    original_lobby_text = dnd._lobby_text
    dnd._lobby_text = lambda session: errative_text(
        original_lobby_text(session), add_insult=False
    )

    original_poll_question = dnd._poll_question
    dnd._poll_question = lambda session, targets: errative_text(
        original_poll_question(session, targets), add_insult=False
    )

    original_outcome_from_counts = dnd._outcome_from_counts
    dnd._outcome_from_counts = lambda options, counts: errative_text(
        original_outcome_from_counts(options, counts), add_insult=False
    )

    def styled_mode_keyboard() -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🎲 Абстрактная исторея",
                        callback_data="dnd:mode:abstract",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="👥 С учаснегами чата",
                        callback_data="dnd:mode:participants",
                    )
                ],
            ]
        )

    dnd._mode_keyboard = styled_mode_keyboard

    for function_name in (
        "parse_and_execute_turn",
        "open_action_window",
        "finalize_poll",
        "finalize_group_actions",
        "_restore_mode_prompt",
        "_restore_lobby_prompt",
        "_restore_backstory_prompt",
        "_restore_action_prompt",
    ):
        original = getattr(dnd, function_name)

        async def wrapped(bot, *args, __original=original, **kwargs):
            return await __original(_styled_bot(bot), *args, **kwargs)

        setattr(dnd, function_name, wrapped)

    dnd._upupa_dnd_style_configured = True
