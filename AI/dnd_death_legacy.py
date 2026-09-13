"""Keep dead DnD heroes in history without resurrecting them in later parties."""
from __future__ import annotations

from datetime import datetime, timezone


GENDER_STEP = "gender"


def history_is_dead(campaign, chat_id: int, user_id: int) -> bool:
    history = campaign._player_history(chat_id, user_id) or {}
    return bool(history.get("dead"))


async def _start_fresh_profile(campaign, dnd, callback, session, user_id: int, *, edit=False) -> None:
    key = str(int(user_id))
    campaign._ensure(session)
    session.character_profiles[key] = {}
    session.profile_options.pop(key, None)
    options = await campaign._generate_profile_options(dnd, session, user_id, GENDER_STEP)
    text = campaign._profile_choice_text(
        GENDER_STEP,
        options,
        heading="☠️ Прошлый герой погиб. Новый персонаж — выбери",
    )
    markup = campaign._profile_keyboard(user_id, GENDER_STEP, options)
    dnd.persist_dnd_sessions()
    if edit:
        await callback.message.edit_text(text, reply_markup=markup)
    else:
        await callback.message.answer(text, reply_markup=markup)


def install_dnd_death_legacy(dnd_router) -> None:
    from AI import dnd
    from AI import dnd_campaign as campaign
    from AI import dnd_combat as combat
    from AI import dnd_state_commands as state_commands

    if getattr(campaign, "_upupa_dnd_death_legacy_installed", False):
        return

    # Archive the final life/death state after the combat layer has written its
    # normal campaign/player history. This keeps the old hero inspectable but
    # gives the next lobby an explicit tombstone flag.
    original_archive = campaign._archive_campaign

    def archive_campaign(dnd_module, session, finale, epilogue):
        original_archive(dnd_module, session, finale, epilogue)
        chat = campaign._chat_history(session.chat_id, True)
        players = chat.setdefault("players", {})
        dead_ids = []
        for participant in (getattr(session, "participants", {}) or {}).values():
            user_id = int(participant["user_id"])
            key = str(user_id)
            sheet = (getattr(session, "character_sheets", {}) or {}).get(key)
            dead = bool(
                isinstance(sheet, dict)
                and (
                    str(sheet.get("status") or "").casefold() == "dead"
                    or int(sheet.get("hp", 1) or 0) <= 0
                )
            )
            row = players.setdefault(key, {})
            row["dead"] = dead
            if dead:
                row["died_at"] = datetime.now(timezone.utc).isoformat()
                dead_ids.append(user_id)
            else:
                row.pop("died_at", None)
        campaigns = chat.get("campaigns") or []
        if campaigns:
            campaigns[-1]["dead_user_ids"] = dead_ids
        campaign._save_archive(dnd_module)

    campaign._archive_campaign = archive_campaign

    # A dead hero contributes no profile, equipment, artifacts, reputation or
    # character-specific heritage to a new hero. Historical records remain in
    # the archive and can still be inspected through state/history commands.
    original_apply_heritage = campaign._apply_heritage

    def apply_heritage(session, user_id, continuation=False):
        old = campaign._player_history(session.chat_id, user_id)
        if not old or not old.get("dead"):
            return original_apply_heritage(session, user_id, continuation=continuation)
        campaign._ensure(session)
        key = str(int(user_id))
        session.heritage[key] = {
            "adventures": [],
            "reputation": [],
            "artifacts": [],
            "previous_hero_dead": True,
        }
        session.reputations[key] = []
        session.inventories.pop(key, None)
        # Return only the tombstone metadata: callers that normally inherit
        # old['profile'] are therefore forced to generate a new character.
        return {"name": old.get("name"), "dead": True, "died_at": old.get("died_at")}

    campaign._apply_heritage = apply_heritage

    # The gender-first lobby wrapper checks raw history before delegating to the
    # base profile prompt, so explicitly divert dead heroes into a fresh profile.
    original_profile_prompt = campaign._profile_prompt

    async def profile_prompt(dnd_module, callback, session):
        user_id = int(callback.from_user.id)
        key = str(user_id)
        current = (getattr(session, "character_profiles", {}) or {}).get(key) or {}
        if history_is_dead(campaign, session.chat_id, user_id) and not current:
            await _start_fresh_profile(campaign, dnd_module, callback, session, user_id)
            return
        return await original_profile_prompt(dnd_module, callback, session)

    campaign._profile_prompt = profile_prompt

    # A stale "reuse" button from an old lobby/restart must not resurrect a hero.
    original_profile_callback = campaign._profile_callback

    async def profile_callback(callback, dnd_module):
        session = dnd_module.dnd_sessions.get(callback.message.chat.id) if callback.message else None
        parts = str(callback.data or "").split(":")
        user_id = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
        action = parts[3] if len(parts) > 3 else ""
        if (
            session
            and action == "reuse"
            and user_id
            and int(callback.from_user.id) == user_id
            and history_is_dead(campaign, session.chat_id, user_id)
        ):
            await callback.answer("Этот герой погиб. Собираем нового.")
            await _start_fresh_profile(campaign, dnd_module, callback, session, user_id, edit=True)
            return
        return await original_profile_callback(callback, dnd_module)

    campaign._profile_callback = profile_callback

    # Never inherit the old standard-array assignment for a dead character even
    # if a newly generated profile happens to match the previous text.
    original_history_stats = combat._history_stats

    def history_stats(campaign_module, session, user_id):
        if history_is_dead(campaign_module, session.chat_id, user_id):
            return None
        return original_history_stats(campaign_module, session, user_id)

    combat._history_stats = history_stats

    # Historical hero views should make the tombstone explicit instead of
    # looking like an active reusable character sheet.
    original_render_hero = state_commands.render_hero

    def render_hero(dnd_module, chat_id, user_id, user_name=None):
        text = original_render_hero(dnd_module, chat_id, user_id, user_name)
        if dnd_module.dnd_sessions.get(int(chat_id)) is None and history_is_dead(campaign, chat_id, user_id):
            text += "\n☠️ Этот персонаж погиб в завершённой партии. В новой игре будет создан новый герой."
        return text

    state_commands.render_hero = render_hero
    campaign._upupa_dnd_death_legacy_installed = True
