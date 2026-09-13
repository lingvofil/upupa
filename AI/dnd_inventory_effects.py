"""Optional flavor and situational effects for Upupa DnD inventory items."""
from __future__ import annotations

from AI import dnd_inventory_fun as inventory_fun


INVENTORY_EFFECTS_MARKER = "ХАРАКТЕРИСТИКИ ПРЕДМЕТОВ УПУПЫ"
INVENTORY_EFFECT_RULES = f"""
{INVENTORY_EFFECTS_MARKER}: у нового предмета или артефакта можно указать короткую характеристику, если она делает вещь
понятнее, полезнее или смешнее. Не выдумывай характеристику только ради заполнения поля.
Для стака с естественно накапливающимся шуточным свойством используй BONUS как бонус ЗА ОДНУ единицу и TRAIT как
короткое название свойства после предлога «к». Например:
[ITEM:ADD;PLAYER:123;NAME:ложка;QTY:5;FEW:ложки;MANY:ложек;KIND:item;BONUS:1;TRAIT:прожорливости]
В инвентаре это отобразится как «5 ложек — +5 к прожорливости». BONUS держи небольшим целым числом от -9 до +9.
Для свойства, которое не нужно умножать на количество, используй EFFECT, например:
[ITEM:ADD;PLAYER:123;NAME:Перстень мокрого барона;KIND:artifact;EFFECT:звенит рядом с болотной нечистью]
TRAIT и EFFECT должны быть очень короткими, без точек с запятой и служебных тегов.
Это не прямые модификаторы стандартных STR/DEX/CON/INT/WIS/CHA и не число, которое код автоматически прибавляет к d20.
Если характеристика предмета действительно релевантна сцене, можешь учитывать её повествовательно или обоснованно дать
преимущество/помеху существующей проверке. Не превращай шуточный «+5 к прожорливости» в универсальный боевой бонус.
""".strip()


def _clean_text(value, *, limit: int) -> str:
    text = " ".join(str(value or "").replace("[", "").replace("]", "").split()).strip(" ;")
    return text[:limit].rstrip()


def _bonus_per_unit(item) -> int | None:
    if not isinstance(item, dict):
        return None
    try:
        value = int(item.get("bonus_per_unit"))
    except (TypeError, ValueError):
        return None
    if value == 0:
        return None
    return max(-9, min(9, value))


def _trait(item) -> str:
    if not isinstance(item, dict):
        return ""
    return _clean_text(item.get("trait"), limit=60)


def _effect(item) -> str:
    if not isinstance(item, dict):
        return ""
    return _clean_text(item.get("effect"), limit=100)


def format_inventory_effect(item) -> str:
    """Render optional item metadata without treating it as a core d20 modifier."""
    parts = []
    bonus = _bonus_per_unit(item)
    trait = _trait(item)
    if bonus is not None and trait:
        total = bonus * inventory_fun._quantity(item)
        parts.append(f"{total:+d} к {trait}")
    effect = _effect(item)
    if effect:
        parts.append(effect)
    return "; ".join(parts)


def format_inventory_entry(item) -> str:
    base = inventory_fun.format_inventory_item(item)
    if not base:
        return ""
    effect = format_inventory_effect(item)
    return f"{base} — {effect}" if effect else base


def render_inventory_lines(items) -> list[str]:
    result = []
    for item in items or []:
        text = format_inventory_entry(item)
        if text:
            result.append(("✨ " if inventory_fun._kind(item) == "artifact" else "• ") + text)
    return result


def _parse_bonus(value) -> int | None:
    try:
        bonus = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    if bonus == 0:
        return None
    return max(-9, min(9, bonus))


def _apply_effect_fields(item: dict, fields: dict) -> bool:
    changed = False
    bonus = _parse_bonus(fields.get("BONUS"))
    trait = _clean_text(fields.get("TRAIT"), limit=60)
    effect = _clean_text(fields.get("EFFECT"), limit=100)

    if bonus is not None and trait:
        if item.get("bonus_per_unit") != bonus or item.get("trait") != trait:
            item["bonus_per_unit"] = bonus
            item["trait"] = trait
            changed = True
    if effect and item.get("effect") != effect:
        item["effect"] = effect
        changed = True
    return changed


def _valid_players(session) -> set[str]:
    result = set()
    for participant in (getattr(session, "participants", {}) or {}).values():
        try:
            result.add(str(int(participant["user_id"])))
        except (KeyError, TypeError, ValueError):
            continue
    return result


def _refresh_notice(notices: list[str], session, player: str, item: dict) -> None:
    participant = (getattr(session, "participants", {}) or {}).get(player, {})
    player_name = participant.get("name") or f"игрок {player}"
    base = inventory_fun.format_inventory_item(item)
    full = format_inventory_entry(item)
    if not base or base == full:
        return
    replacements = {
        f"🎒 {player_name} получает: {base}": f"🎒 {player_name} получает: {full}",
        f"🎒 {player_name}: теперь {base}": f"🎒 {player_name}: теперь {full}",
    }
    for index, notice in enumerate(notices):
        if notice in replacements:
            notices[index] = replacements[notice]


def apply_item_effect_metadata(campaign, original_apply, session, text):
    """Decorate ITEM:ADD results after the existing inventory mechanics have applied the tag."""
    cleaned, notices = original_apply(session, text)
    valid_players = _valid_players(session)

    for match in campaign.META_RE.finditer(str(text or "")):
        kind, payload = match.group(1).upper(), match.group(2)
        if kind != "ITEM":
            continue
        head, fields = campaign._parse_fields(payload)
        if str(head or "").upper() != "ADD":
            continue
        if not any(fields.get(key) for key in ("BONUS", "TRAIT", "EFFECT")):
            continue

        player = str(fields.get("PLAYER") or "")
        item_name = str(fields.get("NAME") or "").strip()
        if not player.isdigit() or not item_name or (valid_players and player not in valid_players):
            continue

        inventory = (getattr(session, "inventories", {}) or {}).get(player, [])
        index = inventory_fun._find_item_index(inventory, item_name)
        if index is None:
            continue
        current = inventory[index]
        if isinstance(current, dict):
            item = current
        else:
            item = {"name": inventory_fun._name(current), "kind": inventory_fun._kind(current)}
            inventory[index] = item
        if _apply_effect_fields(item, fields):
            _refresh_notice(notices, session, player, item)

    return cleaned, notices


def _inventory_context(campaign, session) -> str:
    campaign._ensure(session)
    out = []
    for key, items in session.inventories.items():
        names = [format_inventory_entry(item) for item in items]
        names = [name for name in names if name]
        if names:
            out.append(f"- ID {key}: {', '.join(names)}")
    out.append(inventory_fun._artifact_progress_line(session))
    return "\n".join(out)


def install_dnd_inventory_effects(dnd) -> None:
    """Install optional inventory properties after stacking and reliability wrappers."""
    from AI import dnd_campaign as campaign
    from AI import dnd_state_commands as state_commands

    if getattr(campaign, "_upupa_dnd_inventory_effects_installed", False):
        return

    original_apply = campaign._apply_metadata

    def apply_metadata(session, text):
        return apply_item_effect_metadata(campaign, original_apply, session, text)

    campaign._apply_metadata = apply_metadata
    campaign._inventory_context = lambda session: _inventory_context(campaign, session)
    state_commands._inventory_items = render_inventory_lines

    if INVENTORY_EFFECTS_MARKER not in campaign.RULES:
        campaign.RULES = f"{campaign.RULES}\n{INVENTORY_EFFECT_RULES}"
    if INVENTORY_EFFECTS_MARKER not in dnd.DND_SYSTEM_PROMPT:
        dnd.DND_SYSTEM_PROMPT = f"{dnd.DND_SYSTEM_PROMPT.rstrip()}\n\n{INVENTORY_EFFECT_RULES}"

    campaign._upupa_dnd_inventory_effects_installed = True
