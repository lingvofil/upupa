"""Gender choice and hard story-grounding rules for participant DnD."""
from __future__ import annotations

import logging
import re

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


GENDER_STEP = "gender"
GENDER_OPTIONS = ("мужской", "женский")
_GENDER_LABELS = {
    "мужской": "♂️ Мужской",
    "женский": "♀️ Женский",
}
_GROUNDING_MARKER = "ЖЁСТКОЕ ОГРАНИЧЕНИЕ СЮЖЕТА"
_OLD_META_RULE = (
    "Не злоупотребляй четвёртой стеной, мастером игры, двойниками, слоями реальности и симуляциями. "
    "Это допустимо лишь если выбранный сюжет прямо мета-ориентирован или как редкий подготовленный поворот."
)
GROUNDING_RULES = (
    f"{_GROUNDING_MARKER}: не уводи историю в метафизику и не ломай реальность ради твиста. "
    "Запрещены разрывы/трещины/слои реальности, альтернативные и параллельные реальности, двойники или копии героев, "
    "симуляции, мир-внутри-игры, персонаж-Мастер игры, четвёртая стена и объяснение событий через «всё было ненастоящим». "
    "Не используй эти тропы даже как редкий неожиданный поворот. Фантастика и магия допустимы как обычные правила мира, "
    "но странности должны иметь локальную причинность внутри этого мира: люди, существа, магия, техника, культ, преступление, "
    "катастрофа, политика, природа и последствия решений героев."
)

_METAPHYSICAL_PATTERNS = (
    re.compile(r"\bдвойн\w*", re.I),
    re.compile(r"\b(?:копи[яи]|дубликат\w*)\b.{0,30}\b(?:геро\w*|персонаж\w*|участник\w*)", re.I),
    re.compile(r"\b(?:разрыв\w*|разлом\w*|трещин\w*|сло[йя])\b.{0,40}\bреальност\w*", re.I),
    re.compile(r"\b(?:альтернативн\w*|параллельн\w*)\b.{0,30}\b(?:реальност\w*|мир\w*)", re.I),
    re.compile(r"\bсимуляц\w*|\bвиртуальн\w*.{0,20}\bреальност\w*", re.I),
    re.compile(r"\bчетв[её]рт\w*.{0,10}\bстен\w*|\bмета[- ]?игр\w*", re.I),
    re.compile(r"\bмастер\w*.{0,12}\bигр\w*|\bdungeon\s+master\b", re.I),
)


def _normalize_gender(value) -> str | None:
    normalized = " ".join(str(value or "").strip().casefold().split())
    if normalized in {"мужской", "мужчина", "муж", "male", "man", "м"}:
        return "мужской"
    if normalized in {"женский", "женщина", "жен", "female", "woman", "ж"}:
        return "женский"
    return None


def _gender_keyboard(user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[
            InlineKeyboardButton(
                text=_GENDER_LABELS[value],
                callback_data=f"dnd:prof:{int(user_id)}:{GENDER_STEP}:{index}",
            )
            for index, value in enumerate(GENDER_OPTIONS)
        ]]
    )


def _metaphysical_plot(text: str) -> bool:
    return any(pattern.search(str(text or "")) for pattern in _METAPHYSICAL_PATTERNS)


def install_dnd_profile_gender_grounding() -> None:
    """Patch campaign mechanics after the base campaign layer has been configured."""
    from AI import dnd
    from AI import dnd_campaign as campaign
    from AI import dnd_state_commands as state_commands

    if getattr(campaign, "_upupa_dnd_profile_gender_grounding_installed", False):
        return

    campaign.PROFILE_LABELS[GENDER_STEP] = "пол"
    state_commands._PROFILE_LABELS[GENDER_STEP] = "Пол"

    campaign.RULES = campaign.RULES.replace(_OLD_META_RULE, GROUNDING_RULES)
    if _OLD_META_RULE in dnd.DND_SYSTEM_PROMPT:
        dnd.DND_SYSTEM_PROMPT = dnd.DND_SYSTEM_PROMPT.replace(_OLD_META_RULE, GROUNDING_RULES)
    elif _GROUNDING_MARKER not in dnd.DND_SYSTEM_PROMPT:
        dnd.DND_SYSTEM_PROMPT = dnd.DND_SYSTEM_PROMPT.rstrip() + "\n\n" + GROUNDING_RULES

    original_generate_profile_options = campaign._generate_profile_options

    async def generate_profile_options(dnd_module, session, user_id, step, exclude=None):
        if step != GENDER_STEP:
            return await original_generate_profile_options(
                dnd_module,
                session,
                user_id,
                step,
                exclude=exclude,
            )
        campaign._ensure(session)
        options = list(GENDER_OPTIONS)
        session.profile_options.setdefault(str(int(user_id)), {})[GENDER_STEP] = options
        if dnd_module.dnd_sessions.get(session.chat_id) is session:
            dnd_module.persist_dnd_sessions()
        return options

    campaign._generate_profile_options = generate_profile_options

    original_profile_keyboard = campaign._profile_keyboard

    def profile_keyboard(user_id, step, options):
        if step == GENDER_STEP:
            return _gender_keyboard(user_id)
        return original_profile_keyboard(user_id, step, options)

    campaign._profile_keyboard = profile_keyboard

    original_profile_choice_text = campaign._profile_choice_text

    def profile_choice_text(step, options, *, heading="🎭 Выбери"):
        if step == GENDER_STEP:
            return f"{heading} пол:"
        return original_profile_choice_text(step, options, heading=heading)

    campaign._profile_choice_text = profile_choice_text

    original_profile_text = campaign._profile_text

    def profile_text(profile):
        text = original_profile_text(profile)
        gender = _normalize_gender((profile or {}).get(GENDER_STEP))
        return text + (f"; пол — {gender}" if gender else "")

    campaign._profile_text = profile_text

    original_missing_profiles = campaign._missing_profiles

    def missing_profiles(session):
        campaign._ensure(session)
        missing = []
        for participant in session.participants.values():
            key = str(int(participant["user_id"]))
            profile = session.character_profiles.get(key) or {}
            if not campaign._profile_complete(profile) or not _normalize_gender(profile.get(GENDER_STEP)):
                missing.append(participant.get("name") or key)
        return missing

    campaign._missing_profiles = missing_profiles

    original_lobby_text = campaign._lobby_text

    def lobby_text(session):
        campaign._ensure(session)
        roster = "\n".join(
            ("✅" if name not in missing_profiles(session) else "🧩") + " " + name
            for participant in session.participants.values()
            for name in [participant.get("name") or "Игрок"]
        ) or "Пока никто не записался."
        return (
            f"👥 Игра с участниками чата.\nВедущий: {session.starter_name}\n\n"
            f"Участники:\n{roster}\n\n"
            "После «Участвовать» выбери образ, сильную сторону, слабость, особый приём и пол. "
            "Затем ведущий выбирает сюжет."
        )

    campaign._lobby_text = lobby_text
    dnd._lobby_text = lobby_text

    original_profile_prompt = campaign._profile_prompt

    async def profile_prompt(dnd_module, callback, session):
        user_id = int(callback.from_user.id)
        key = str(user_id)
        campaign._ensure(session)
        current = session.character_profiles.get(key) or {}
        if campaign._profile_complete(current) and not _normalize_gender(current.get(GENDER_STEP)):
            options = await campaign._generate_profile_options(dnd_module, session, user_id, GENDER_STEP)
            await callback.message.answer(
                campaign._profile_choice_text(GENDER_STEP, options, heading="🎭 Осталось выбрать"),
                reply_markup=campaign._profile_keyboard(user_id, GENDER_STEP, options),
            )
            dnd_module.persist_dnd_sessions()
            return
        return await original_profile_prompt(dnd_module, callback, session)

    campaign._profile_prompt = profile_prompt

    original_profile_callback = campaign._profile_callback

    async def profile_callback(callback, dnd_module):
        session = dnd_module.dnd_sessions.get(callback.message.chat.id) if callback.message else None
        parts = str(callback.data or "").split(":")
        user_id = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
        action = parts[3] if len(parts) > 3 else ""
        token = parts[4] if len(parts) > 4 else ""

        if not session or session.mode != "participants" or session.state != "LOBBY":
            return await original_profile_callback(callback, dnd_module)
        if int(callback.from_user.id) != user_id or str(user_id) not in session.participants:
            return await original_profile_callback(callback, dnd_module)

        if action == "reuse":
            old = campaign._player_history(session.chat_id, user_id) or {}
            profile = dict(old.get("profile") or {})
            if campaign._profile_complete(profile) and not _normalize_gender(profile.get(GENDER_STEP)):
                session.character_profiles[str(user_id)] = profile
                session.profile_options.pop(str(user_id), None)
                dnd_module.persist_dnd_sessions()
                await callback.answer("Вернул. Осталось выбрать пол.")
                options = await campaign._generate_profile_options(dnd_module, session, user_id, GENDER_STEP)
                await callback.message.edit_text(
                    campaign._profile_choice_text(GENDER_STEP, options, heading="🎭 Теперь выбери"),
                    reply_markup=campaign._profile_keyboard(user_id, GENDER_STEP, options),
                )
                return

        if action == "special" and token != "regen":
            options = list(session.profile_options.get(str(user_id), {}).get(action) or [])
            if campaign._options_are_valid(options):
                try:
                    value = options[int(token)]
                except (ValueError, IndexError):
                    return await original_profile_callback(callback, dnd_module)
                profile = session.character_profiles.setdefault(str(user_id), {})
                profile[action] = value
                dnd_module.persist_dnd_sessions()
                await callback.answer("Записал.")
                gender_options = await campaign._generate_profile_options(
                    dnd_module, session, user_id, GENDER_STEP
                )
                await callback.message.edit_text(
                    campaign._profile_choice_text(GENDER_STEP, gender_options, heading="🎭 Теперь выбери"),
                    reply_markup=campaign._profile_keyboard(user_id, GENDER_STEP, gender_options),
                )
                return

        if action == GENDER_STEP:
            try:
                gender = GENDER_OPTIONS[int(token)]
            except (ValueError, IndexError):
                await callback.answer("Кнопка протухла.")
                return
            profile = session.character_profiles.setdefault(str(user_id), {})
            profile[GENDER_STEP] = gender
            session.profile_options.pop(str(user_id), None)
            dnd_module.persist_dnd_sessions()
            await callback.answer("Записал.")
            if campaign._profile_complete(profile):
                await callback.message.edit_text("✅ Персонаж готов: " + campaign._profile_text(profile))
                await campaign._refresh_lobby(session, callback.bot)
                return
            step = next((item for item in campaign.PROFILE_STEPS if not profile.get(item)), "style")
            next_options = await campaign._generate_profile_options(dnd_module, session, user_id, step)
            await callback.message.edit_text(
                campaign._profile_choice_text(step, next_options, heading="🎭 Теперь выбери"),
                reply_markup=campaign._profile_keyboard(user_id, step, next_options),
            )
            return

        return await original_profile_callback(callback, dnd_module)

    campaign._profile_callback = profile_callback

    original_campaign_context = campaign._campaign_context

    def campaign_context(dnd_module, session):
        base = original_campaign_context(dnd_module, session)
        if _GROUNDING_MARKER in base:
            return base
        return base + "\nСЮЖЕТНЫЕ ОГРАНИЧЕНИЯ:\n" + GROUNDING_RULES

    campaign._campaign_context = campaign_context

    original_plot_generation_prompt = campaign._plot_generation_prompt

    def plot_generation_prompt(attempt):
        return original_plot_generation_prompt(attempt) + "\n" + GROUNDING_RULES

    campaign._plot_generation_prompt = plot_generation_prompt
    campaign.FORBIDDEN_PLOT_PATTERNS = tuple(campaign.FORBIDDEN_PLOT_PATTERNS) + _METAPHYSICAL_PATTERNS

    original_parse_turn = dnd.parse_and_execute_turn

    async def parse_turn(bot, chat_id, response):
        session = dnd.dnd_sessions.get(chat_id)
        candidate = str(response or "")
        if session and dnd._is_participant_mode(session):
            for attempt in range(3):
                story = campaign.ACTION_RE.sub("", campaign.META_RE.sub("", candidate)).strip()
                if not _metaphysical_plot(story):
                    break
                logging.warning(
                    "DnD metaphysical draft rejected chat_id=%s attempt=%s",
                    chat_id,
                    attempt + 1,
                )
                conversation = getattr(session, "conversation", None)
                if (
                    isinstance(conversation, list)
                    and conversation
                    and conversation[-1].get("role") == "assistant"
                    and conversation[-1].get("content") == candidate
                ):
                    conversation.pop()
                rewrite_prompt = (
                    "Предыдущий черновик нарушил жёсткое ограничение сюжета. Перепиши продолжение этой же сцены "
                    "без разрывов реальности, двойников, параллельных миров, симуляций, четвёртой стены и мета-твистов. "
                    "Сохрани конкретные действия и последствия, но объясняй их только причинностью внутри мира. "
                    "Верни нормальный игровой кусок и корректный ACTION-тег.\n" + GROUNDING_RULES
                )
                candidate = await dnd.generate_session_response(session, rewrite_prompt)
            else:
                candidate = (
                    "Ситуация остаётся конкретной и материальной: герои видят перед собой последствия своих решений "
                    "и могут выбрать следующий ход.\n[ACTION:INPUT]"
                )
        return await original_parse_turn(bot, chat_id, candidate)

    dnd.parse_and_execute_turn = parse_turn
    campaign._upupa_dnd_profile_gender_grounding_installed = True
