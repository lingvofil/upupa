"""Optional flavor and situational effects for Upupa DnD inventory items."""
from __future__ import annotations

from AI import dnd_inventory_fun as inventory_fun


INVENTORY_EFFECTS_MARKER = "ХАРАКТЕРИСТИКИ ПРЕДМЕТОВ УПУПЫ"
INVENTORY_EFFECT_VERSION = 1
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

_GENERIC_ITEM_EFFECTS = (
    "пригодится в самый неподходящий момент",
    "повышает уверенность в сомнительных планах",
    "выглядит бесполезно ровно до момента, когда понадобится",
    "официально считается частью плана, какого именно — неизвестно",
)
_GENERIC_ARTIFACT_EFFECTS = (
    "явно хранит больше истории, чем объясняет",
    "подозрительно реагирует на серьёзные неприятности",
    "слишком важен, чтобы просто валяться в кармане",
    "ведёт себя так, будто у него есть собственный план",
)


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


def _parse_bonus(value) -> int | None:
    try:
        bonus = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    if bonus == 0:
        return None
    return max(-9, min(9, bonus))


def _effect_is_checked(item) -> bool:
    if not isinstance(item, dict):
        return False
    try:
        return int(item.get("effect_version", 0) or 0) >= INVENTORY_EFFECT_VERSION
    except (TypeError, ValueError):
        return False


def _has_explicit_effect(item) -> bool:
    return bool((_bonus_per_unit(item) is not None and _trait(item)) or _effect(item))


def _pick_by_name(name: str, options: tuple[str, ...]) -> str:
    normalized = str(name or "").casefold()
    score = sum((index + 1) * ord(char) for index, char in enumerate(normalized))
    return options[score % len(options)]


def _contains_any(value: str, needles: tuple[str, ...]) -> bool:
    return any(needle in value for needle in needles)


def _legacy_effect_fields(item) -> dict[str, object]:
    """Give pre-effect inventories a deterministic, harmless flavor property."""
    name = inventory_fun._name(item)
    normalized = name.casefold()
    kind = inventory_fun._kind(item)
    quantity = inventory_fun._quantity(item)

    if kind == "artifact":
        if _contains_any(normalized, ("кольц", "перст", "амул", "талисман", "медальон")):
            effect = "подозрительно отзывается на магическую хрень"
        elif _contains_any(normalized, ("меч", "нож", "кинжал", "топор", "молот", "дубин", "оруж")):
            effect = "делает угрозы заметно убедительнее"
        elif "ключ" in normalized:
            effect = "открывает что-то важное, но явно не бесплатно"
        elif _contains_any(normalized, ("книг", "свит", "дневник", "тетрад")):
            effect = "знает больше, чем прилично рассказывать вслух"
        else:
            effect = _pick_by_name(name, _GENERIC_ARTIFACT_EFFECTS)
        return {"EFFECT": effect}

    if _contains_any(
        normalized,
        ("ложк", "вилк", "тарел", "еда", "пицц", "бургер", "хлеб", "сыр", "колбас", "арбуз", "банан", "яблок"),
    ):
        return {"BONUS": 1, "TRAIT": "прожорливости"}
    if _contains_any(normalized, ("пиво", "ром", "водк", "вино", "бутыл", "алког")):
        return {"BONUS": 1, "TRAIT": "сомнительным решениям"}
    if _contains_any(normalized, ("штраф", "долг", "кредит", "квитанц", "счёт", "счет")):
        return {"BONUS": -1, "TRAIT": "финансовому благополучию"}
    if _contains_any(normalized, ("пизд", "подзат", "синяк", "шиш", "рана", "травм")):
        return {"BONUS": 1, "TRAIT": "травматическому опыту"}
    if _contains_any(normalized, ("носок", "ботин", "тапок", "кроссов", "туфл", "штан", "трус")):
        return {"BONUS": 1, "TRAIT": "гардеробному превосходству"}
    if _contains_any(normalized, ("ключ", "отмыч", "отвёртк", "отвертк", "лопат", "верёвк", "веревк", "скотч")):
        return {"BONUS": 1, "TRAIT": "бытовой находчивости"}
    if quantity > 1:
        return {"BONUS": 1, "TRAIT": "коллекционерству"}
    return {"EFFECT": _pick_by_name(name, _GENERIC_ITEM_EFFECTS)}


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


def backfill_legacy_inventory_item(item) -> tuple[dict | object, bool]:
    """Upgrade one pre-effects inventory entry without changing its gameplay math."""
    if _effect_is_checked(item):
        return item, False

    if isinstance(item, dict):
        upgraded = item
    else:
        name = inventory_fun._name(item)
        if not name:
            return item, False
        upgraded = {"name": name, "kind": inventory_fun._kind(item)}

    changed = upgraded is not item
    if not _has_explicit_effect(upgraded):
        changed = _apply_effect_fields(upgraded, _legacy_effect_fields(upgraded)) or changed
    if upgraded.get("effect_version") != INVENTORY_EFFECT_VERSION:
        upgraded["effect_version"] = INVENTORY_EFFECT_VERSION
        changed = True
    return upgraded, changed


def backfill_inventory_items(items) -> bool:
    """Upgrade a mutable inventory list in place and report whether it changed."""
    if not isinstance(items, list):
        return False
    changed = False
    for index, current in enumerate(list(items)):
        upgraded, item_changed = backfill_legacy_inventory_item(current)
        if upgraded is not current:
            items[index] = upgraded
        changed = item_changed or changed
    return changed


def backfill_archive_data(archive) -> bool:
    """Upgrade persistent player inventories and historical campaign snapshots."""
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
        if isinstance(players, dict):
            for history in players.values():
                if not isinstance(history, dict):
                    continue
                changed = backfill_inventory_items(history.get("inventory")) or changed
                changed = backfill_inventory_items(history.get("artifacts")) or changed
        for campaign_row in chat.get("campaigns") or []:
            if not isinstance(campaign_row, dict):
                continue
            inventories = campaign_row.get("inventories") or {}
            if not isinstance(inventories, dict):
                continue
            for items in inventories.values():
                changed = backfill_inventory_items(items) or changed
    return changed


def format_inventory_effect(item) -> str:
    """Render optional item metadata without treating it as a core d20 modifier."""
    if not _effect_is_checked(item) and not _has_explicit_effect(item):
        item, _changed = backfill_legacy_inventory_item(item)

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
    backfill_inventory_items(items)
    result = []
    for item in items or []:
        text = format_inventory_entry(item)
        if text:
            result.append(("✨ " if inventory_fun._kind(item) == "artifact" else "• ") + text)
    return result


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
    before = inventory_fun._snapshot_inventory(session)
    cleaned, notices = original_apply(session, text)
    valid_players = _valid_players(session)

    for match in campaign.META_RE.finditer(str(text or "")):
        kind, payload = match.group(1).upper(), match.group(2)
        if kind != "ITEM":
            continue
        head, fields = campaign._parse_fields(payload)
        if str(head or "").upper() != "ADD":
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

        has_requested_effect = any(fields.get(key) for key in ("BONUS", "TRAIT", "EFFECT"))
        existed_before = (player, item_name.casefold()) in before
        if existed_before and not has_requested_effect and not _effect_is_checked(item):
            upgraded, _changed = backfill_legacy_inventory_item(item)
            if upgraded is not item:
                inventory[index] = upgraded
                item = upgraded
        else:
            item["effect_version"] = INVENTORY_EFFECT_VERSION

        if has_requested_effect and _apply_effect_fields(item, fields):
            _refresh_notice(notices, session, player, item)

    return cleaned, notices


def _inventory_context(campaign, session) -> str:
    campaign._ensure(session)
    out = []
    for key, items in session.inventories.items():
        backfill_inventory_items(items)
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

    campaign._load_archive(dnd)
    if backfill_archive_data(getattr(campaign, "_archive", None)):
        campaign._save_archive(dnd)

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
