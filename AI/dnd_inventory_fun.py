"""Funny stackable inventory extensions for Upupa DnD."""
from __future__ import annotations

from copy import deepcopy


FUN_INVENTORY_RULES = """
Инвентарь хранит не только полезный лут, но и смешные следы реально случившихся событий.
Если последствия действия достаточно характерные, чтобы потом было смешно их вспомнить, иногда сохраняй их обычным KIND:item:
полученный пизд, подзатыльник, штраф, проклятие, чужой носок, позорный жетон и другой уместный трофей.
Не превращай в предмет каждую реплику: такой трофей должен следовать из реально описанного события.
Одинаковые обычные предметы стакаются автоматически, поэтому при повторении используй ТО ЖЕ NAME.
Для счётных русских приколов можно передать формы FEW и MANY. Пример:
[ITEM:ADD;PLAYER:123;NAME:пизд;FEW:пизда;MANY:пиздов;KIND:item]
После пяти таких реальных событий инвентарь покажет «5 пиздов».
Уникальные KIND:artifact не стакаются и по-прежнему должны быть действительно уникальными вещами.
""".strip()


def _quantity(item) -> int:
    if not isinstance(item, dict):
        return 1
    try:
        return max(1, int(item.get("quantity", 1)))
    except (TypeError, ValueError):
        return 1


def _name(item) -> str:
    if isinstance(item, dict):
        return str(item.get("name") or "").strip()
    return str(item or "").strip()


def _kind(item) -> str:
    if isinstance(item, dict):
        return str(item.get("kind") or "item").casefold()
    return "item"


def _count_form(quantity: int, one: str, few: str | None, many: str | None) -> str:
    if quantity == 1:
        return one
    mod100 = quantity % 100
    mod10 = quantity % 10
    if mod10 == 1 and mod100 != 11:
        return one
    if mod10 in {2, 3, 4} and mod100 not in {12, 13, 14} and few:
        return few
    if many:
        return many
    return ""


def format_inventory_item(item) -> str:
    """Render one inventory entry, including a natural Russian stack form when supplied."""
    name = _name(item)
    if not name:
        return ""
    quantity = _quantity(item)
    if quantity <= 1:
        return name
    if isinstance(item, dict):
        form = _count_form(
            quantity,
            name,
            str(item.get("few") or "").strip() or None,
            str(item.get("many") or item.get("plural") or "").strip() or None,
        )
        if form:
            return f"{quantity} {form}"
    return f"{name} ×{quantity}"


def render_inventory_lines(items) -> list[str]:
    result = []
    for item in items or []:
        text = format_inventory_item(item)
        if text:
            result.append(("✨ " if _kind(item) == "artifact" else "• ") + text)
    return result


def _inventory_context(campaign, session) -> str:
    campaign._ensure(session)
    out = []
    for key, items in session.inventories.items():
        names = [format_inventory_item(item) for item in items]
        names = [name for name in names if name]
        if names:
            out.append(f"- ID {key}: {', '.join(names)}")
    return "\n".join(out) or "- нет"


def _snapshot_inventory(session) -> dict[tuple[str, str], dict]:
    snapshot = {}
    for player, items in (getattr(session, "inventories", {}) or {}).items():
        for item in items or []:
            name = _name(item)
            if not name:
                continue
            snapshot[(str(player), name.casefold())] = {
                "item": deepcopy(item),
                "quantity": _quantity(item),
                "kind": _kind(item),
            }
    return snapshot


def _valid_players(session) -> set[str]:
    participants = getattr(session, "participants", None) or {}
    result = set()
    for participant in participants.values():
        try:
            result.add(str(int(participant["user_id"])))
        except (KeyError, TypeError, ValueError):
            continue
    return result


def _find_item_index(items, item_name: str) -> int | None:
    needle = item_name.casefold()
    for index, item in enumerate(items or []):
        if _name(item).casefold() == needle:
            return index
    return None


def _stack_item(base_item, *, name: str, quantity: int, fields: dict) -> dict:
    if isinstance(base_item, dict):
        item = dict(base_item)
    else:
        item = {"name": name, "kind": "item"}
    item["name"] = name
    item["kind"] = str(item.get("kind") or fields.get("KIND") or "item").lower()
    item["quantity"] = max(1, int(quantity))
    if fields.get("FEW"):
        item["few"] = fields["FEW"]
    if fields.get("MANY"):
        item["many"] = fields["MANY"]
    elif fields.get("PLURAL"):
        item["many"] = fields["PLURAL"]
    return item


def apply_stackable_metadata(campaign, original_apply, session, text):
    """Preserve campaign metadata behavior while stacking repeated ordinary ITEM tags."""
    campaign._ensure(session)
    before = _snapshot_inventory(session)
    cleaned, notices = original_apply(session, text)
    valid_players = _valid_players(session)
    deltas: dict[tuple[str, str], dict] = {}

    for match in campaign.META_RE.finditer(str(text or "")):
        kind, payload = match.group(1).upper(), match.group(2)
        if kind != "ITEM":
            continue
        head, fields = campaign._parse_fields(payload)
        player = fields.get("PLAYER", "")
        item_name = str(fields.get("NAME") or "").strip()
        if not player.isdigit() or not item_name or (valid_players and player not in valid_players):
            continue
        key = (player, item_name.casefold())
        current = deltas.setdefault(key, {"add": 0, "remove": 0, "fields": {}, "name": item_name})
        current["fields"].update(fields)
        if head.upper() == "ADD":
            current["add"] += 1
        elif head.upper() == "REMOVE":
            current["remove"] += 1

    for key, delta in deltas.items():
        player, _ = key
        fields = delta["fields"]
        item_name = delta["name"]
        previous = before.get(key)
        previous_kind = previous.get("kind") if previous else None
        requested_kind = str(fields.get("KIND") or previous_kind or "item").casefold()
        if requested_kind == "artifact" or previous_kind == "artifact":
            continue

        previous_quantity = previous.get("quantity", 0) if previous else 0
        desired_quantity = max(0, previous_quantity + delta["add"] - delta["remove"])
        inventory = session.inventories.setdefault(player, [])
        index = _find_item_index(inventory, item_name)

        if desired_quantity <= 0:
            if index is not None:
                inventory.pop(index)
            continue

        if index is not None:
            base_item = inventory[index]
        elif previous is not None:
            base_item = previous["item"]
        else:
            base_item = {"name": item_name, "kind": requested_kind}
        stacked = _stack_item(base_item, name=item_name, quantity=desired_quantity, fields=fields)
        if index is None:
            inventory.append(stacked)
        else:
            inventory[index] = stacked

        if desired_quantity > 1 and desired_quantity != previous_quantity:
            participant = (getattr(session, "participants", {}) or {}).get(player, {})
            player_name = participant.get("name") or f"игрок {player}"
            display = format_inventory_item(stacked)
            notices[:] = [
                notice
                for notice in notices
                if notice not in {
                    f"🎒 {player_name} получает: {item_name}",
                    f"🎒 {player_name} теряет: {item_name}",
                }
            ]
            notices.append(f"🎒 {player_name}: теперь {display}")

    return cleaned, notices


def install_fun_inventory() -> None:
    """Install inventory extensions once, without changing the base campaign module."""
    from AI import dnd_campaign as campaign
    from AI import dnd_state_commands as state_commands

    if getattr(campaign, "_upupa_fun_inventory_installed", False):
        return

    original_apply = campaign._apply_metadata

    def apply_metadata(session, text):
        return apply_stackable_metadata(campaign, original_apply, session, text)

    campaign._apply_metadata = apply_metadata
    campaign._inventory_context = lambda session: _inventory_context(campaign, session)
    if FUN_INVENTORY_RULES not in campaign.RULES:
        campaign.RULES = f"{campaign.RULES}\n{FUN_INVENTORY_RULES}"
    state_commands._inventory_items = render_inventory_lines
    campaign._upupa_fun_inventory_installed = True
