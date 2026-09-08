"""Runtime style layer for DnD without changing mechanical semantics."""

from __future__ import annotations

import logging
import re

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


DND_STYLE_MARKER = "СТИЛЬ УПУПЫ: КРИВАЯ ЕГРА"
DND_STYLE_INSTRUCTION = f"""
{DND_STYLE_MARKER}.
Пиши весь художественный текст намеренно криво и по-падонковски: используй эрративы,
фонетически узнаваемые орфографические ошибки, простые опечатки, кривое согласование,
разговорный сленг и мат. Нормальное слово «игра» по возможности пиши «егра»,
«игроки» — «егроки», «участники» — «учаснеги». Не превращай текст в нечитаемый набор букв:
смысл сцены и варианты действий должны оставаться понятными.

Будь заметно грубее: почти в каждом сюжетном сообщении подкалывай или оскорбляй партию
либо конкретного персонажа короткой репликой вроде «дегенераты», «долбоёбы», «мудилы»,
«кретины», «идиоты», если это уместно по сцене. Не используй оскорбления по защищённым признакам.
Не поясняй, что специально делаешь ошибки: для этого мастера безграмотность — нормальная речь.

СВОБОДНЫЕ ХОДЫ ПАРТИИ — ВАЖНЫ, НО НЕ ДВА ПОДРЯД. ACTION:INPUT может появляться часто,
но не чаще чем через один игровой эпизод. НИКОГДА не ставь ACTION:INPUT, если предыдущий
технический тег мастера тоже был ACTION:INPUT — это относится и к общему ходу партии, и к
адресному INPUT с TARGETS. После INPUT следующий эпизод должен завершаться ROLL или POLL,
если игра не заканчивается через END. Если ситуацию можно интересно разрулить фантазией игроков,
предпочитай ACTION:INPUT только при соблюдении этого ограничения. Не делай длинную цепочку ROLL/POLL:
после одного-двух таких эпизодов снова можно дать свободный ACTION:INPUT. POLL используй только когда
реально нужны несколько заранее сформулированных альтернатив, ROLL — только когда важен неопределённый исход.
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
)

_ACTION_TAG_RE = re.compile(r"\[ACTION:([A-Z]+)(?:[;\]])", flags=re.IGNORECASE)
_INPUT_TAG_RE = re.compile(
    r"\[ACTION:INPUT(?:;TARGETS:([0-9,\s]+))?\]",
    flags=re.IGNORECASE,
)


def errative_text(text: str, *, add_insult: bool = False) -> str:
    """Apply a readable padonak-style distortion while preserving mechanics."""
    if not text:
        return text
    result = str(text)
    for source, target in _REPLACEMENTS:
        result = result.replace(source, target)
    if add_insult and not any(
        label in result for label in ("КРИТИЧЕСКАЯ УДАЧА", "КРИТИЧЕСКАЯ НЕУДАЧА")
    ):
        suffix = _INSULT_SUFFIXES[len(result) % len(_INSULT_SUFFIXES)]
        if suffix.strip() not in result:
            result += suffix
    return result


def _action_kind(text: str | None) -> str | None:
    match = _ACTION_TAG_RE.search(str(text or ""))
    return match.group(1).upper() if match else None


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
    return f"{clean_text}\n{tag}".strip()


async def _generate_without_consecutive_input(original_generate, session, prompt: str) -> str:
    """Generate a turn while guaranteeing that INPUT never follows INPUT."""
    previous_action = _last_assistant_action(getattr(session, "conversation", []))
    result = await original_generate(session, prompt)
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
        corrected = await original_generate(session, correction_prompt)
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
            errative_text(text, add_insult=True),
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

    if DND_STYLE_MARKER not in dnd.DND_SYSTEM_PROMPT:
        dnd.DND_SYSTEM_PROMPT = dnd.DND_SYSTEM_PROMPT.rstrip() + "\n\n" + DND_STYLE_INSTRUCTION

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
        result = original_with_scene_direction(session, prompt)
        if DND_STYLE_MARKER not in result:
            result += "\n\n" + DND_STYLE_INSTRUCTION
        return result

    dnd.with_scene_direction = styled_with_scene_direction

    original_action_prompt_text = dnd._action_prompt_text
    dnd._action_prompt_text = lambda session: errative_text(
        original_action_prompt_text(session), add_insult=True
    )

    original_lobby_text = dnd._lobby_text
    dnd._lobby_text = lambda session: errative_text(
        original_lobby_text(session), add_insult=True
    )

    original_poll_question = dnd._poll_question
    dnd._poll_question = lambda session, targets: errative_text(
        original_poll_question(session, targets), add_insult=False
    )

    original_outcome_from_counts = dnd._outcome_from_counts
    dnd._outcome_from_counts = lambda options, counts: errative_text(
        original_outcome_from_counts(options, counts), add_insult=True
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
