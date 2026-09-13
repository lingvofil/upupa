"""Gender profile choice and hard story-grounding rules for participant DnD."""
from __future__ import annotations

import json
import random
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


def _profile_from_generated_payload(campaign, raw):
    """Upgrade the old four-field auto-profile payload with a normalized gender."""
    text = campaign.ACTION_RE.sub("", campaign.META_RE.sub("", str(raw or ""))).strip()
    fenced = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.I | re.S).strip()
    try:
        data = json.loads(fenced) if fenced.startswith("{") else None
    except (TypeError, ValueError, json.JSONDecodeError):
        data = None
    if not isinstance(data, dict):
        return None

    legacy_steps = ("style", "strength", "weakness", "special")
    profile = {
        step: campaign._clean_generated_value(data.get(step), max_chars=100)
        for step in legacy_steps
    }
    if not all(profile.values()):
        return None
    profile[GENDER_STEP] = _normalize_gender(data.get(GENDER_STEP)) or random.choice(GENDER_OPTIONS)
    return profile


def install_dnd_profile_gender_grounding() -> None:
    """Patch campaign mechanics after the base campaign layer has been configured."""
    from AI import dnd
    from AI import dnd_campaign as campaign
    from AI import dnd_state_commands as state_commands

    if getattr(campaign, "_upupa_dnd_profile_gender_grounding_installed", False):
        return

    legacy_steps = tuple(step for step in campaign.PROFILE_STEPS if step != GENDER_STEP)
    campaign.PROFILE_STEPS = legacy_steps + (GENDER_STEP,)
    campaign.PROFILE_LABELS[GENDER_STEP] = "пол"
    campaign.EMERGENCY_PROFILE_OPTIONS[GENDER_STEP] = GENDER_OPTIONS
    state_commands._PROFILE_LABELS[GENDER_STEP] = "Пол"

    campaign.RULES = campaign.RULES.replace(_OLD_META_RULE, GROUNDING_RULES)
    if _OLD_META_RULE in dnd.DND_SYSTEM_PROMPT:
        dnd.DND_SYSTEM_PROMPT = dnd.DND_SYSTEM_PROMPT.replace(_OLD_META_RULE, GROUNDING_RULES)
    elif _GROUNDING_MARKER not in dnd.DND_SYSTEM_PROMPT:
        dnd.DND_SYSTEM_PROMPT = dnd.DND_SYSTEM_PROMPT.rstrip() + "\n\n" + GROUNDING_RULES

    original_options_are_valid = campaign._options_are_valid

    def options_are_valid(options, *, expected=campaign.PROFILE_OPTION_COUNT, similarity_limit=0.82):
        if tuple(options or ()) == GENDER_OPTIONS:
            return True
        return original_options_are_valid(
            options,
            expected=expected,
            similarity_limit=similarity_limit,
        )

    campaign._options_are_valid = options_are_valid

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

    original_parse_generated_profile = campaign._parse_generated_profile

    def parse_generated_profile(raw):
        profile = original_parse_generated_profile(raw)
        if profile:
            gender = _normalize_gender(profile.get(GENDER_STEP))
            if gender:
                profile[GENDER_STEP] = gender
                return profile
        return _profile_from_generated_payload(campaign, raw)

    campaign._parse_generated_profile = parse_generated_profile

    def legacy_profile_complete(profile) -> bool:
        return bool(profile and all(profile.get(step) for step in legacy_steps))

    original_profile_prompt = campaign._profile_prompt

    async def profile_prompt(dnd_module, callback, session):
        user_id = int(callback.from_user.id)
        key = str(user_id)
        campaign._ensure(session)
        current = session.character_profiles.get(key) or {}

        if legacy_profile_complete(current) and not _normalize_gender(current.get(GENDER_STEP)):
            options = await campaign._generate_profile_options(dnd_module, session, user_id, GENDER_STEP)
            await callback.message.answer(
                campaign._profile_choice_text(GENDER_STEP, options),
                reply_markup=campaign._profile_keyboard(user_id, GENDER_STEP, options),
            )
            dnd_module.persist_dnd_sessions()
            return

        old = campaign._player_history(session.chat_id, user_id) or {}
        old_profile = dict(old.get("profile") or {})
        if legacy_profile_complete(old_profile) and not _normalize_gender(old_profile.get(GENDER_STEP)):
            campaign._apply_heritage(session, user_id)
            session.character_profiles[key] = old_profile
            options = await campaign._generate_profile_options(dnd_module, session, user_id, GENDER_STEP)
            await callback.message.answer(
                "🎭 Старый персонаж сохранён. Осталось выбрать пол.",
                reply_markup=campaign._profile_keyboard(user_id, GENDER_STEP, options),
            )
            dnd_module.persist_dnd_sessions()
            return

        return await original_profile_prompt(dnd_module, callback, session)

    campaign._profile_prompt = profile_prompt

    original_auto_profile = campaign._auto_profile

    async def auto_profile(dnd_module, session, user_id):
        old = campaign._player_history(session.chat_id, user_id) or {}
        old_profile = dict(old.get("profile") or {})
        if legacy_profile_complete(old_profile) and not _normalize_gender(old_profile.get(GENDER_STEP)):
            campaign._apply_heritage(
                session,
                user_id,
                continuation=bool(getattr(session, "continuation_mode", False)),
            )
            old_profile[GENDER_STEP] = random.choice(GENDER_OPTIONS)
            session.character_profiles[str(int(user_id))] = old_profile
            return old_profile
        return await original_auto_profile(dnd_module, session, user_id)

    campaign._auto_profile = auto_profile

    original_lobby_text = campaign._lobby_text

    def lobby_text(session):
        text = original_lobby_text(session)
        return text.replace(
            "выбери образ, сильную сторону, слабость и особый приём.",
            "выбери образ, сильную сторону, слабость, особый приём и пол.",
        )

    campaign._lobby_text = lobby_text
    dnd._lobby_text = lobby_text

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

    campaign._upupa_dnd_profile_gender_grounding_installed = True
