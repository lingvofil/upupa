"""Keep dead DnD heroes in history without resurrecting them in later parties."""
from __future__ import annotations

from datetime import datetime, timezone


GENDER_STEP = "gender"


def _sheet_is_dead(sheet) -> bool:
    if not isinstance(sheet, dict):
        return False
    status = str(sheet.get("status") or "").casefold()
    try:
        hp = int(sheet.get("hp", 1) or 0)
    except (TypeError, ValueError):
        hp = 1
    return status == "dead" or hp <= 0


def backfill_death_flags(archive) -> bool:
    """Infer latest player death state from archived per-campaign combat sheets."""
    if not isinstance(archive, dict):
        return False
    chats = archive.get("chats")
    if not isinstance(chats, dict):
        return False

    changed = False
    for chat in chats.values():
        if not isinstance(chat, dict):
            continue
        players = chat.get("players") or {}
        campaigns = chat.get("campaigns") or []
        if not isinstance(players, dict) or not isinstance(campaigns, list):
            continue

        latest = {}
        for campaign_row in campaigns:
            if not isinstance(campaign_row, dict):
                continue
            sheets = campaign_row.get("character_sheets") or {}
            if isinstance(sheets, dict):
                for user_id, sheet in sheets.items():
                    latest[str(user_id)] = _sheet_is_dead(sheet)
            for user_id in campaign_row.get("dead_user_ids") or []:
                latest[str(user_id)] = True

        for user_id, dead in latest.items():
            row = players.get(str(user_id))
            if not isinstance(row, dict):
                continue
            if bool(row.get("dead")) != bool(dead):
                row["dead"] = bool(dead)
                changed = True
            if not dead and "died_at" in row:
                row.pop("died_at", None)
                changed = True
    return changed


def history_is_dead(campaign, chat_id: int, user_id: int) -> bool:
    history = campaign._player_history(chat_id, user_id) or {}
    return bool(history.get("dead"))


def _uses_active_inventory(dnd, chat_id: int, user_id: int) -> bool:
    session = dnd.dnd_sessions.get(int(chat_id))
    if not session or getattr(session, "mode", None) != "participants":
        return False
    participants = getattr(session, "participants", {}) or {}
    return str(int(user_id)) in participants


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
    from AI import dnd_inventory_fun as inventory_fun
    from AI import dnd_state_commands as state_commands

    if getattr(campaign, "_upupa_dnd_death_legacy_installed", False):
        return

    campaign._load_archive(dnd)
    if backfill_death_flags(getattr(campaign, "_archive", None)):
        campaign._save_archive(dnd)

    # Archive the final life/death state after the combat layer has written its
    # normal campaign/player history. This keeps the old hero inspectable but
    # gives the next lobby an explicit tombstone flag.
    original_archive = campaign._archive_campaign

    def archive_campaign(dnd_module, session, finale, epilogue):
        original_archive(dnd_module, session, finale, epilogue)
        chat = campaign._chat_history(session.chat_id, True)
        players = chat.setdefault("players", {})
        dead_ids = []
        now = datetime.now(timezone.utc).isoformat()
        for participant in (getattr(session, "participants", {}) or {}).values():
            user_id = int(participant["user_id"])
            key = str(user_id)
            sheet = (getattr(session, "character_sheets", {}) or {}).get(key)
            dead = _sheet_is_dead(sheet)
            row = players.setdefault(key, {})
            row["dead"] = dead
            if dead:
                row["died_at"] = now
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
        # Return only tombstone metadata: callers that normally inherit
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

    # The corpse inventory stays in the archive as historical evidence, but it
    # is not a usable player inventory. A newly created active hero gets a fresh
    # session inventory and is therefore unaffected by this tombstone guard.
    original_render_inventory = state_commands.render_inventory

    def render_inventory(dnd_module, chat_id, user_id):
        if (
            not _uses_active_inventory(dnd_module, chat_id, user_id)
            and history_is_dead(campaign, chat_id, user_id)
        ):
            return (
                "🎒 Инвентарь недоступен: этот персонаж погиб.\n"
                "Его вещи остались только в архиве приключения и больше не являются игровым имуществом."
            )
        return original_render_inventory(dnd_module, chat_id, user_id)

    state_commands.render_inventory = render_inventory

    original_transfer_inventory = inventory_fun.transfer_inventory

    def transfer_inventory(dnd_module, chat_id, sender_id, target_id, item_query, quantity=1):
        session = dnd_module.dnd_sessions.get(int(chat_id))
        participants = (
            getattr(session, "participants", {}) or {}
            if session is not None and getattr(session, "mode", None) == "participants"
            else {}
        )
        sender_key = str(int(sender_id))
        target_key = str(int(target_id))

        # If either side belongs to the current participant campaign, preserve
        # the normal active-session rules (including the requirement that both
        # sides are participants). The old death flag belongs to the previous
        # hero and must not block the newly created active character.
        if sender_key in participants or target_key in participants:
            return original_transfer_inventory(
                dnd_module,
                chat_id,
                sender_id,
                target_id,
                item_query,
                quantity,
            )

        if history_is_dead(campaign, chat_id, sender_id):
            raise inventory_fun.InventoryTransferError(
                "Инвентарь погибшего героя недоступен: передавать его вещи нельзя."
            )
        if history_is_dead(campaign, chat_id, target_id):
            raise inventory_fun.InventoryTransferError(
                "Последний герой получателя погиб: передавать вещи в его архив нельзя. Сначала нужен новый герой."
            )
        return original_transfer_inventory(
            dnd_module,
            chat_id,
            sender_id,
            target_id,
            item_query,
            quantity,
        )

    inventory_fun.transfer_inventory = transfer_inventory

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
