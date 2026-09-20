"""Pregame participant controls for leaving and rebuilding DnD characters."""
from __future__ import annotations

from aiogram import F
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


_REBUILD_CALLBACK = "dnd:lobby:rebuild"
_LEAVE_CALLBACK = "dnd:lobby:leave"
_GENDER_STEP = "gender"


def _add_controls(markup: InlineKeyboardMarkup) -> InlineKeyboardMarkup:
    rows = [list(row) for row in markup.inline_keyboard]
    callbacks = {
        button.callback_data
        for row in rows
        for button in row
        if getattr(button, "callback_data", None)
    }
    if _REBUILD_CALLBACK in callbacks or _LEAVE_CALLBACK in callbacks:
        return markup

    controls = [
        [InlineKeyboardButton(text="♻️ Пересобрать персонажа", callback_data=_REBUILD_CALLBACK)],
        [InlineKeyboardButton(text="🚪 Выйти из кампании", callback_data=_LEAVE_CALLBACK)],
    ]
    start_index = next(
        (
            index
            for index, row in enumerate(rows)
            if any(getattr(button, "callback_data", None) == "dnd:lobby:start" for button in row)
        ),
        len(rows),
    )
    rows[start_index:start_index] = controls
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _clear_participant_session_state(session, user_id: int) -> None:
    key = str(int(user_id))
    for attr in (
        "character_profiles",
        "profile_options",
        "heritage",
        "inventories",
        "reputations",
    ):
        value = getattr(session, attr, None)
        if isinstance(value, dict):
            value.pop(key, None)
    rebuild_users = getattr(session, "rebuild_inventory_users", None)
    if isinstance(rebuild_users, list):
        session.rebuild_inventory_users = [value for value in rebuild_users if str(value) != key]


def _with_gender_context(prompt: str, session, user_id: int, step: str) -> str:
    if step == _GENDER_STEP:
        return prompt
    profiles = getattr(session, "character_profiles", {}) or {}
    profile = profiles.get(str(int(user_id))) or {}
    gender = str(profile.get(_GENDER_STEP) or "").strip()
    if not gender:
        return prompt
    return (
        prompt
        + f"\nПол персонажа уже выбран: {gender}. "
        "Обязательно учитывай его при формулировке вариантов и согласуй слова по роду там, где это уместно."
    )


async def _leave_campaign(callback, dnd, campaign) -> None:
    if not callback.message:
        await callback.answer("Кнопка потерялась.")
        return
    session = dnd.dnd_sessions.get(callback.message.chat.id)
    if not session or session.mode != "participants" or session.state != "LOBBY":
        await callback.answer("Кампания уже началась — выйти через лобби поздно.", show_alert=True)
        return

    user_id = int(callback.from_user.id)
    key = str(user_id)
    if key not in session.participants:
        await callback.answer("Ты и так не участвуешь.", show_alert=True)
        return

    session.participants.pop(key, None)
    _clear_participant_session_state(session, user_id)
    dnd.persist_dnd_sessions()
    await callback.answer("Вышел из кампании.")
    await campaign._refresh_lobby(session, callback.bot)


async def _rebuild_character(callback, dnd, campaign) -> None:
    if not callback.message:
        await callback.answer("Кнопка потерялась.")
        return
    session = dnd.dnd_sessions.get(callback.message.chat.id)
    if not session or session.mode != "participants" or session.state != "LOBBY":
        await callback.answer("Кампания уже началась — персонаж зафиксирован.", show_alert=True)
        return

    user_id = int(callback.from_user.id)
    key = str(user_id)
    if key not in session.participants:
        await callback.answer("Сначала нажми «Участвовать».", show_alert=True)
        return

    campaign._ensure(session)
    campaign._preserve_inventory_for_rebuild(session, user_id)
    session.character_profiles[key] = {}
    session.profile_options.pop(key, None)
    dnd.persist_dnd_sessions()
    await callback.answer("Пересобираю персонажа.")
    await campaign._refresh_lobby(session, callback.bot)

    options = await campaign._generate_profile_options(dnd, session, user_id, _GENDER_STEP)
    await callback.message.answer(
        campaign._profile_choice_text(
            _GENDER_STEP,
            options,
            heading="♻️ Пересобираем. Выбери",
        ),
        reply_markup=campaign._profile_keyboard(user_id, _GENDER_STEP, options),
    )


def install_dnd_lobby_controls(dnd_router) -> None:
    """Add lobby buttons and callbacks once, after campaign mechanics are configured."""
    if getattr(dnd_router, "_upupa_dnd_lobby_controls_configured", False):
        return

    from AI import dnd
    from AI import dnd_campaign as campaign
    from AI.dnd_profile_gender_grounding import install_dnd_profile_gender_grounding

    # Gender/profile extensions patch the campaign helpers that the lobby uses.
    install_dnd_profile_gender_grounding()

    original_profile_generation_prompt = campaign._profile_generation_prompt

    def profile_generation_prompt(session, user_id, step, exclude=None):
        prompt = original_profile_generation_prompt(session, user_id, step, exclude=exclude)
        return _with_gender_context(prompt, session, user_id, step)

    campaign._profile_generation_prompt = profile_generation_prompt

    original_profile_prompt = campaign._profile_prompt

    async def profile_prompt(dnd_module, callback, session):
        user_id = int(callback.from_user.id)
        key = str(user_id)
        campaign._ensure(session)
        current = session.character_profiles.get(key) or {}
        old = campaign._player_history(session.chat_id, user_id) or {}
        old_profile = old.get("profile") or {}

        # Preserve the explicit "reuse old character / rebuild" choice when a
        # complete previous profile exists. Any genuinely new or partially rebuilt
        # character starts with gender so all generated traits can use it.
        if not current.get(_GENDER_STEP) and (current or not campaign._profile_complete(old_profile)):
            options = await campaign._generate_profile_options(dnd_module, session, user_id, _GENDER_STEP)
            await callback.message.answer(
                campaign._profile_choice_text(_GENDER_STEP, options),
                reply_markup=campaign._profile_keyboard(user_id, _GENDER_STEP, options),
            )
            dnd_module.persist_dnd_sessions()
            return

        if current.get(_GENDER_STEP) and not campaign._profile_complete(current):
            step = next((item for item in campaign.PROFILE_STEPS if not current.get(item)), "style")
            options = await campaign._generate_profile_options(dnd_module, session, user_id, step)
            await callback.message.answer(
                campaign._profile_choice_text(step, options),
                reply_markup=campaign._profile_keyboard(user_id, step, options),
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

        valid_lobby = bool(
            session
            and session.mode == "participants"
            and session.state == "LOBBY"
            and int(callback.from_user.id) == user_id
            and str(user_id) in session.participants
        )
        if not valid_lobby:
            return await original_profile_callback(callback, dnd_module)

        if action == "edit":
            campaign._preserve_inventory_for_rebuild(session, user_id)
            session.character_profiles[str(user_id)] = {}
            session.profile_options.pop(str(user_id), None)
            dnd_module.persist_dnd_sessions()
            await callback.answer("Генерирую.")
            options = await campaign._generate_profile_options(dnd_module, session, user_id, _GENDER_STEP)
            await callback.message.edit_text(
                campaign._profile_choice_text(_GENDER_STEP, options),
                reply_markup=campaign._profile_keyboard(user_id, _GENDER_STEP, options),
            )
            return

        # The gender patch historically appended gender after the final "special"
        # step. In the new gender-first flow it is already present, so finish the
        # profile here instead of asking for gender a second time.
        profile = session.character_profiles.get(str(user_id)) or {}
        if action == "special" and token != "regen" and profile.get(_GENDER_STEP):
            options = list(session.profile_options.get(str(user_id), {}).get(action) or [])
            if not campaign._options_are_valid(options):
                await callback.answer("Старая кнопка. Генерирую свежие варианты.")
                await campaign._show_profile_step(dnd_module, callback, session, user_id, action, regenerate=False)
                return
            try:
                value = options[int(token)]
            except (ValueError, IndexError):
                await callback.answer("Вариант пропал. Сгенерирую свежие.")
                await campaign._show_profile_step(dnd_module, callback, session, user_id, action, regenerate=True)
                return
            profile[action] = value
            session.profile_options.pop(str(user_id), None)
            dnd_module.persist_dnd_sessions()
            await callback.answer("Записал.")
            await callback.message.edit_text("✅ Персонаж готов: " + campaign._profile_text(profile))
            await campaign._refresh_lobby(session, callback.bot)
            return

        return await original_profile_callback(callback, dnd_module)

    campaign._profile_callback = profile_callback

    original_lobby_text = campaign._lobby_text

    def lobby_text(session):
        return original_lobby_text(session).replace(
            "После «Участвовать» выбери образ, сильную сторону, слабость, особый приём и пол.",
            "После «Участвовать» сначала выбери пол, затем образ, сильную сторону, слабость и особый приём.",
        )

    campaign._lobby_text = lobby_text
    dnd._lobby_text = lobby_text

    original_lobby_keyboard = campaign._lobby_keyboard

    def lobby_keyboard(session=None):
        return _add_controls(original_lobby_keyboard(session))

    campaign._lobby_keyboard = lobby_keyboard
    dnd._lobby_keyboard = lambda: lobby_keyboard()

    async def leave_callback(callback):
        await _leave_campaign(callback, dnd, campaign)

    async def rebuild_callback(callback):
        await _rebuild_character(callback, dnd, campaign)

    dnd_router.callback_query.register(leave_callback, F.data == _LEAVE_CALLBACK)
    dnd_router.callback_query.register(rebuild_callback, F.data == _REBUILD_CALLBACK)
    dnd_router._upupa_dnd_lobby_controls_configured = True
