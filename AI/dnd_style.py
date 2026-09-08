"""Runtime style layer for DnD without changing mechanical semantics."""

from __future__ import annotations

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

СВОБОДНЫЕ ХОДЫ ПАРТИИ — ПРИОРИТЕТ. ACTION:INPUT должен появляться примерно в половине
сюжетных эпизодов, ориентир — около каждого второго хода. Если ситуацию можно интересно
разрулить фантазией игроков без обязательного броска или выбора из готовых вариантов,
ВСЕГДА предпочитай ACTION:INPUT. Не делай длинную цепочку ROLL/POLL: после одного-двух
таких эпизодов дай свободный ACTION:INPUT. POLL используй только когда реально нужны
несколько заранее сформулированных альтернатив, ROLL — только когда важен неопределённый исход.
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
    ("История", "Иstoreя"),
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


def _styled_bot(bot):
    return bot if isinstance(bot, _StyledBotProxy) else _StyledBotProxy(bot)


def configure_dnd_style() -> None:
    """Install DnD-only style wrappers. Safe to call more than once."""
    from AI import dnd

    if getattr(dnd, "_upupa_dnd_style_configured", False):
        return

    if DND_STYLE_MARKER not in dnd.DND_SYSTEM_PROMPT:
        dnd.DND_SYSTEM_PROMPT = dnd.DND_SYSTEM_PROMPT.rstrip() + "\n\n" + DND_STYLE_INSTRUCTION

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
