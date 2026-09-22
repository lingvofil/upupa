"""Mechanical ability bonuses granted by selected DnD artifacts."""
from __future__ import annotations

import logging

from AI import dnd_inventory_fun as inventory_fun


ARTIFACT_STATS_MARKER = "АРТЕФАКТЫ И ХАРАКТЕРИСТИКИ DND УПУПЫ"
ABILITY_KEYS = ("STR", "DEX", "CON", "INT", "WIS", "CHA")
ABILITY_LABELS = {
    "STR": "Сила",
    "DEX": "Ловкость",
    "CON": "Телосложение",
    "INT": "Интеллект",
    "WIS": "Мудрость",
    "CHA": "Харизма",
}
ARTIFACT_STAT_RULES = f"""
{ARTIFACT_STATS_MARKER}: некоторые значимые артефакты могут реально усиливать одну базовую характеристику героя.
Не каждый артефакт обязан давать цифру: ориентир — не чаще примерно одного из трёх и только когда бонус тематически понятен.
Для такого артефакта добавь к ITEM:ADD поля STAT:STR/DEX/CON/INT/WIS/CHA и STAT_BONUS:1 или 2.
+1 — обычный редкий бонус, +2 — только для действительно сильного/сюжетно важного артефакта. Один артефакт усиливает максимум
одну характеристику. Например:
[ITEM:ADD;PLAYER:123;NAME:Сапоги крысиных тоннелей;KIND:artifact;EFFECT:сами находят устойчивую опору;STAT:DEX;STAT_BONUS:1]
Код сам применяет бонус к текущей характеристике, проверкам, спасброскам и атакам, которые от неё зависят. Итоговое значение
характеристики никогда не становится выше 20. Не вписывай этот бонус отдельно в d20 и не дублируй его через ADVANTAGE.
При передаче или потере артефакта бонус переходит вместе с ним или исчезает.
""".strip()


def _artifact_stat(item) -> tuple[str, int] | None:
    if not isinstance(item, dict) or inventory_fun._kind(item) != "artifact":
        return None
    ability = str(item.get("stat") or "").upper()
    if ability not in ABILITY_KEYS:
        return None
    try:
        bonus = int(item.get("stat_bonus") or 0)
    except (TypeError, ValueError):
        return None
    if bonus <= 0:
        return None
    return ability, min(2, bonus)


def _base_stats(sheet) -> dict[str, int] | None:
    if not isinstance(sheet, dict):
        return None
    raw = sheet.get("base_stats")
    if not isinstance(raw, dict) or any(key not in raw for key in ABILITY_KEYS):
        raw = sheet.get("stats")
        if not isinstance(raw, dict) or any(key not in raw for key in ABILITY_KEYS):
            return None
        try:
            sheet["base_stats"] = {key: int(raw[key]) for key in ABILITY_KEYS}
        except (TypeError, ValueError):
            return None
    try:
        return {key: int(sheet["base_stats"][key]) for key in ABILITY_KEYS}
    except (KeyError, TypeError, ValueError):
        return None


def artifact_bonuses(session, user_id: int) -> dict[str, int]:
    totals = {key: 0 for key in ABILITY_KEYS}
    items = (getattr(session, "inventories", {}) or {}).get(str(int(user_id)), [])
    for item in items or []:
        parsed = _artifact_stat(item)
        if parsed is None:
            continue
        ability, bonus = parsed
        totals[ability] += bonus
    return {key: value for key, value in totals.items() if value > 0}


def sync_character_sheet(session, user_id: int) -> bool:
    sheets = getattr(session, "character_sheets", {}) or {}
    sheet = sheets.get(str(int(user_id)))
    base = _base_stats(sheet)
    if base is None:
        return False
    bonuses = artifact_bonuses(session, user_id)
    effective = {
        key: min(20, int(base[key]) + int(bonuses.get(key, 0)))
        for key in ABILITY_KEYS
    }
    effective_bonuses = {key: effective[key] - base[key] for key in ABILITY_KEYS if effective[key] > base[key]}
    changed = sheet.get("stats") != effective or sheet.get("artifact_stat_bonuses") != effective_bonuses
    sheet["stats"] = effective
    sheet["artifact_stat_bonuses"] = effective_bonuses
    return changed


def sync_party(session) -> bool:
    changed = False
    for key in list((getattr(session, "character_sheets", {}) or {}).keys()):
        try:
            user_id = int(key)
        except (TypeError, ValueError):
            continue
        changed = sync_character_sheet(session, user_id) or changed
    return changed


def _apply_stat_fields(item: dict, fields: dict) -> bool:
    if not isinstance(item, dict) or inventory_fun._kind(item) != "artifact":
        return False
    ability = str(fields.get("STAT") or "").upper()
    if ability not in ABILITY_KEYS:
        return False
    try:
        bonus = int(fields.get("STAT_BONUS") or 0)
    except (TypeError, ValueError):
        return False
    if bonus <= 0:
        return False
    bonus = min(2, bonus)
    changed = item.get("stat") != ability or item.get("stat_bonus") != bonus
    item["stat"] = ability
    item["stat_bonus"] = bonus
    return changed


def apply_artifact_stat_metadata(campaign, session, text, cleaned, notices):
    """Attach STAT metadata after the ordinary ITEM pipeline created the artifact."""
    from AI import dnd_inventory_effects as effects

    valid_players = set()
    for participant in (getattr(session, "participants", {}) or {}).values():
        try:
            valid_players.add(str(int(participant["user_id"])))
        except (KeyError, TypeError, ValueError):
            continue

    for match in campaign.META_RE.finditer(str(text or "")):
        kind, payload = match.group(1).upper(), match.group(2)
        if kind != "ITEM":
            continue
        head, fields = campaign._parse_fields(payload)
        if str(head or "").upper() != "ADD" or not fields.get("STAT") or not fields.get("STAT_BONUS"):
            continue
        player = str(fields.get("PLAYER") or "")
        item_name = str(fields.get("NAME") or "").strip()
        if not player.isdigit() or not item_name or (valid_players and player not in valid_players):
            continue
        inventory = (getattr(session, "inventories", {}) or {}).get(player, [])
        index = inventory_fun._find_item_index(inventory, item_name)
        if index is None or not isinstance(inventory[index], dict):
            continue
        if _apply_stat_fields(inventory[index], fields):
            effects._refresh_notice(notices, session, player, inventory[index])

    return cleaned, notices


def _format_bonus_suffix(item) -> str:
    parsed = _artifact_stat(item)
    if parsed is None:
        return ""
    ability, bonus = parsed
    return f"{bonus:+d} к {ABILITY_LABELS[ability]}"


def _artifact_bonus_line(session, user_id: int) -> str | None:
    sheet = (getattr(session, "character_sheets", {}) or {}).get(str(int(user_id))) or {}
    bonuses = sheet.get("artifact_stat_bonuses") if isinstance(sheet, dict) else None
    if not isinstance(bonuses, dict) or not bonuses:
        return None
    chunks = [f"{ABILITY_LABELS[key]} +{int(bonuses[key])}" for key in ABILITY_KEYS if bonuses.get(key)]
    return "✨ От артефактов: " + ", ".join(chunks)


def install_dnd_artifact_stats(dnd, *, metadata_policy, state_policy=None) -> None:
    """Install artifact stat metadata, effective stat sync, transfer sync and UI."""
    from AI import dnd_campaign as campaign
    from AI import dnd_combat as combat
    from AI import dnd_inventory_effects as effects
    from AI import dnd_state_commands as state_commands

    if getattr(dnd, "_upupa_dnd_artifact_stats_installed", False):
        return

    if state_policy is None:
        state_policy = getattr(campaign, "_upupa_dnd_campaign_state_policy", None)
    if state_policy is None:
        from AI.dnd_campaign_state import DndCampaignStatePolicy, configure_dnd_campaign_state

        state_policy = configure_dnd_campaign_state(
            campaign,
            DndCampaignStatePolicy(
                campaign._ensure,
                campaign._state,
                campaign._restore_state,
            ),
        )

    if ARTIFACT_STATS_MARKER not in campaign.RULES:
        campaign.RULES = campaign.RULES.rstrip() + "\n" + ARTIFACT_STAT_RULES
    if ARTIFACT_STATS_MARKER not in dnd.DND_SYSTEM_PROMPT:
        dnd.DND_SYSTEM_PROMPT = dnd.DND_SYSTEM_PROMPT.rstrip() + "\n\n" + ARTIFACT_STAT_RULES

    def postprocess(session, text, cleaned, notices):
        cleaned, notices = apply_artifact_stat_metadata(campaign, session, text, cleaned, notices)
        changed = sync_party(session)
        if changed or "STAT_BONUS:" in str(text or "").upper():
            dnd.persist_dnd_sessions()
        return cleaned, notices

    metadata_policy.add_postprocessor(postprocess)

    original_format_effect = effects.format_inventory_effect

    def format_inventory_effect(item):
        base = original_format_effect(item)
        stat = _format_bonus_suffix(item)
        return "; ".join(part for part in (base, stat) if part)

    effects.format_inventory_effect = format_inventory_effect

    original_initialize = combat.initialize_party_combat

    async def initialize_party_combat(dnd_module, bot, session):
        result = await original_initialize(dnd_module, bot, session)
        changed = sync_party(session)
        if changed:
            dnd_module.persist_dnd_sessions()
            lines = []
            for participant in (getattr(session, "participants", {}) or {}).values():
                try:
                    user_id = int(participant["user_id"])
                except (KeyError, TypeError, ValueError):
                    continue
                line = _artifact_bonus_line(session, user_id)
                if line:
                    lines.append(f"• {participant.get('name') or user_id}: {line.removeprefix('✨ ')}")
            if lines:
                await bot.send_message(session.chat_id, "✨ Наследуемые артефакты усиливают характеристики:\n" + "\n".join(lines))
        return result

    combat.initialize_party_combat = initialize_party_combat

    original_auto_profile = campaign._auto_profile

    async def auto_profile(dnd_module, session, user_id):
        profile = await original_auto_profile(dnd_module, session, user_id)
        if sync_character_sheet(session, int(user_id)):
            dnd_module.persist_dnd_sessions()
        return profile

    campaign._auto_profile = auto_profile

    original_transfer = inventory_fun.transfer_inventory

    def transfer_inventory(dnd_module, chat_id, sender_id, target_id, item_query, quantity=1):
        result = original_transfer(dnd_module, chat_id, sender_id, target_id, item_query, quantity)
        session = dnd_module.dnd_sessions.get(int(chat_id))
        if session is not None and result[2] == "session" and sync_party(session):
            dnd_module.persist_dnd_sessions()
        return result

    inventory_fun.transfer_inventory = transfer_inventory

    state_policy.add_restore_hook(lambda session, _data: sync_party(session))

    original_archive = campaign._archive_campaign

    def archive_campaign(dnd_module, session, finale, epilogue):
        original_archive(dnd_module, session, finale, epilogue)
        chat = campaign._chat_history(session.chat_id, True)
        players = chat.get("players") or {}
        for participant in (getattr(session, "participants", {}) or {}).values():
            try:
                user_id = int(participant["user_id"])
            except (KeyError, TypeError, ValueError):
                continue
            sheet = (getattr(session, "character_sheets", {}) or {}).get(str(user_id)) or {}
            base = _base_stats(sheet)
            history = players.get(str(user_id))
            if base is not None and isinstance(history, dict) and isinstance(history.get("sheet"), dict):
                history["sheet"]["stats"] = dict(base)
        campaign._save_archive(dnd_module)

    campaign._archive_campaign = archive_campaign

    original_render_hero = state_commands.render_hero

    def render_hero(dnd_module, chat_id, user_id, user_name=None):
        text = original_render_hero(dnd_module, chat_id, user_id, user_name)
        session = dnd_module.dnd_sessions.get(int(chat_id))
        if session is None:
            return text
        line = _artifact_bonus_line(session, user_id)
        return text + ("\n" + line if line else "")

    state_commands.render_hero = render_hero

    # Existing live sessions can gain the new semantics without a restart.
    changed = False
    for session in (getattr(dnd, "dnd_sessions", {}) or {}).values():
        changed = sync_party(session) or changed
    if changed:
        dnd.persist_dnd_sessions()

    dnd._upupa_dnd_artifact_stats_installed = True
    logging.info("DnD artifact stat bonuses installed")


__all__ = [
    "ARTIFACT_STAT_RULES",
    "artifact_bonuses",
    "sync_character_sheet",
    "sync_party",
    "apply_artifact_stat_metadata",
    "install_dnd_artifact_stats",
]
