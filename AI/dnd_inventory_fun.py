"""Funny stackable inventory extensions for Upupa DnD."""
from __future__ import annotations

from copy import deepcopy
import re

from aiogram import BaseMiddleware


TRANSFER_RE = re.compile(
    r"^(?:упупа\s+)?(?:передать|отдать)\s+(?:(\d+)\s+)?(.+?)\s*$",
    re.I,
)

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
Артефакты — редкая, но штатная награда прямо ВО ВРЕМЯ игры, а не декоративная строчка эпилога.
Ориентир для обычной полной кампании — примерно 1–2 новых артефакта на всю группу, только за действительно значимый трофей,
победу, находку, сделку или последствие решения. Не раздавай их по таймеру и не выдумывай без сюжетной причины.
Как только герой фактически получает такой уникальный наследуемый предмет, в ЭТОМ ЖЕ ответе обязательно добавляй
[ITEM:ADD;PLAYER:123;NAME:название;KIND:artifact]. Не откладывай выдачу до эпилога: эпилог не меняет инвентарь.
""".strip()


class InventoryTransferError(ValueError):
    pass


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


def _ensure_artifact_awards(session) -> dict[str, list[str]]:
    awards = getattr(session, "artifact_awards", None)
    if not isinstance(awards, dict):
        awards = {}
        session.artifact_awards = awards
    for key, values in list(awards.items()):
        if not isinstance(values, list):
            awards[str(key)] = []
    return awards


def _artifact_snapshot(session) -> set[tuple[str, str]]:
    result = set()
    for player, items in (getattr(session, "inventories", {}) or {}).items():
        for item in items or []:
            if _kind(item) == "artifact" and _name(item):
                result.add((str(player), _name(item).casefold()))
    return result


def _record_new_artifact_awards(session, before: set[tuple[str, str]]) -> None:
    awards = _ensure_artifact_awards(session)
    after = _artifact_snapshot(session)
    for player, normalized_name in sorted(after - before):
        item_name = next(
            (
                _name(item)
                for item in (getattr(session, "inventories", {}) or {}).get(player, [])
                if _kind(item) == "artifact" and _name(item).casefold() == normalized_name
            ),
            normalized_name,
        )
        bucket = awards.setdefault(player, [])
        if not any(str(value).casefold() == item_name.casefold() for value in bucket):
            bucket.append(item_name)


def _artifact_progress_line(session) -> str:
    awards = _ensure_artifact_awards(session)
    names = [str(name) for values in awards.values() for name in values if str(name).strip()]
    count = len(names)
    try:
        scene_count = int(getattr(session, "scene_count", 0) or 0)
    except (TypeError, ValueError):
        scene_count = 0
    if count == 0 and scene_count >= 4:
        return (
            "- Новых артефактов этой кампании пока 0. Не выдавай подарок из воздуха, но при ближайшем действительно "
            "заслуженном уникальном трофее обязательно оформи его KIND:artifact сразу в сцене."
        )
    if count == 0:
        return "- Новых артефактов этой кампании пока 0; не форсируй их до сюжетно заслуженного момента."
    return f"- Новых артефактов этой кампании: {count} ({', '.join(names[-3:])}). Не раздавай новые ради квоты."


def _inventory_context(campaign, session) -> str:
    campaign._ensure(session)
    out = []
    for key, items in session.inventories.items():
        names = [format_inventory_item(item) for item in items]
        names = [name for name in names if name]
        if names:
            out.append(f"- ID {key}: {', '.join(names)}")
    out.append(_artifact_progress_line(session))
    return "\n".join(out)


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


def _normalize_item_query(value: str) -> str:
    return " ".join(str(value or "").strip().casefold().split())


def parse_transfer_command(text: str | None) -> tuple[int, str] | None:
    match = TRANSFER_RE.match(str(text or "").strip())
    if not match:
        return None
    quantity = int(match.group(1) or 1)
    if quantity <= 0:
        return None
    quantity = min(quantity, 999)
    item_name = str(match.group(2) or "").strip()
    if len(item_name) >= 2 and item_name[0] == item_name[-1] and item_name[0] in {'"', "'", "«", "“"}:
        item_name = item_name[1:-1].strip()
    return (quantity, item_name) if item_name else None


def _item_aliases(item) -> set[str]:
    aliases = {_normalize_item_query(_name(item))}
    if isinstance(item, dict):
        for key in ("few", "many", "plural"):
            value = _normalize_item_query(item.get(key))
            if value:
                aliases.add(value)
    return {value for value in aliases if value}


def _find_transfer_item_index(items, query: str) -> int | None:
    needle = _normalize_item_query(query)
    exact = [index for index, item in enumerate(items or []) if needle in _item_aliases(item)]
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        raise InventoryTransferError("Нашлось несколько вещей с таким названием — уточни.")
    fuzzy = []
    if len(needle) >= 3:
        for index, item in enumerate(items or []):
            aliases = _item_aliases(item)
            if any(needle in alias or alias in needle for alias in aliases):
                fuzzy.append(index)
    if len(fuzzy) == 1:
        return fuzzy[0]
    if len(fuzzy) > 1:
        raise InventoryTransferError("Нашлось несколько похожих вещей — напиши название точнее.")
    return None


def transfer_between_inventories(inventories, sender_id: int, target_id: int, item_query: str, quantity: int = 1) -> tuple[str, str]:
    sender = str(int(sender_id))
    target = str(int(target_id))
    if sender == target:
        raise InventoryTransferError("Самому себе передавать бессмысленно.")
    try:
        quantity = int(quantity)
    except (TypeError, ValueError):
        quantity = 1
    if quantity < 1:
        raise InventoryTransferError("Количество должно быть больше нуля.")

    source_items = inventories.setdefault(sender, [])
    source_index = _find_transfer_item_index(source_items, item_query)
    if source_index is None:
        raise InventoryTransferError(f"В инвентаре нет «{item_query}».")

    source_item = source_items[source_index]
    item_name = _name(source_item)
    item_kind = _kind(source_item)
    available = _quantity(source_item)
    if quantity > available:
        raise InventoryTransferError(f"Столько нет: «{format_inventory_item(source_item)}».")
    if item_kind == "artifact" and quantity != 1:
        raise InventoryTransferError("Артефакт уникальный — его можно передать только целиком.")

    target_items = inventories.setdefault(target, [])
    target_index = _find_item_index(target_items, item_name)
    if item_kind == "artifact" and target_index is not None:
        raise InventoryTransferError("У получателя уже есть такой артефакт.")

    moved = dict(source_item) if isinstance(source_item, dict) else {"name": item_name, "kind": item_kind}
    moved["name"] = item_name
    moved["kind"] = item_kind
    if quantity > 1:
        moved["quantity"] = quantity
    else:
        moved.pop("quantity", None)

    remaining = available - quantity
    if remaining <= 0:
        source_items.pop(source_index)
    else:
        source_items[source_index] = _stack_item(source_item, name=item_name, quantity=remaining, fields={})

    if target_index is None:
        target_items.append(moved)
    elif item_kind != "artifact":
        fields = {
            "KIND": item_kind,
            "FEW": moved.get("few") if isinstance(moved, dict) else None,
            "MANY": moved.get("many") if isinstance(moved, dict) else None,
            "PLURAL": moved.get("plural") if isinstance(moved, dict) else None,
        }
        target_items[target_index] = _stack_item(
            target_items[target_index],
            name=item_name,
            quantity=_quantity(target_items[target_index]) + quantity,
            fields={key: value for key, value in fields.items() if value},
        )

    return format_inventory_item(moved), item_kind


def _sync_archived_artifacts(history: dict) -> None:
    history["artifacts"] = [
        deepcopy(item)
        for item in (history.get("inventory") or [])
        if _kind(item) == "artifact"
    ]


def transfer_inventory(dnd, chat_id: int, sender_id: int, target_id: int, item_query: str, quantity: int = 1) -> tuple[str, str, str]:
    from AI import dnd_campaign as campaign

    campaign._load_archive(dnd)
    session = dnd.dnd_sessions.get(int(chat_id))
    sender_key = str(int(sender_id))
    target_key = str(int(target_id))

    if session is not None and getattr(session, "mode", None) == "participants":
        participants = getattr(session, "participants", {}) or {}
        sender_active = sender_key in participants
        target_active = target_key in participants
        if sender_active or target_active:
            if not sender_active or not target_active:
                raise InventoryTransferError("Пока идёт кампания, передавать игровой инвентарь можно только между её участниками.")
            campaign._ensure(session)
            display, item_kind = transfer_between_inventories(
                session.inventories,
                sender_id,
                target_id,
                item_query,
                quantity,
            )
            dnd.persist_dnd_sessions()
            return display, item_kind, "session"

    sender_history = campaign._player_history(chat_id, sender_id)
    target_history = campaign._player_history(chat_id, target_id)
    if not sender_history:
        raise InventoryTransferError("У тебя нет сохранённого D&D-инвентаря в этом чате.")
    if not target_history:
        raise InventoryTransferError("У получателя ещё нет сохранённого D&D-персонажа в этом чате.")

    inventories = {
        sender_key: sender_history.setdefault("inventory", []),
        target_key: target_history.setdefault("inventory", []),
    }
    display, item_kind = transfer_between_inventories(
        inventories,
        sender_id,
        target_id,
        item_query,
        quantity,
    )
    sender_history["inventory"] = inventories[sender_key]
    target_history["inventory"] = inventories[target_key]
    _sync_archived_artifacts(sender_history)
    _sync_archived_artifacts(target_history)
    campaign._save_archive(dnd)
    return display, item_kind, "archive"


class DndInventoryTransferMiddleware(BaseMiddleware):
    """Handle explicit inventory transfers before DnD state/action middleware."""

    async def __call__(self, handler, event, data):
        parsed = parse_transfer_command(getattr(event, "text", None))
        if parsed is None:
            return await handler(event, data)

        chat = getattr(event, "chat", None)
        sender = getattr(event, "from_user", None)
        replied = getattr(event, "reply_to_message", None)
        target = getattr(replied, "from_user", None) if replied else None
        if chat is None or sender is None or not hasattr(event, "answer"):
            return await handler(event, data)
        if target is None:
            await event.answer("↪️ Ответь командой «передать <предмет>» на сообщение того, кому отдаёшь вещь.")
            return None
        if getattr(target, "is_bot", False):
            await event.answer("🤖 Боту инвентарь не нужен.")
            return None
        if int(target.id) == int(sender.id):
            await event.answer("🎒 Самому себе передавать бессмысленно.")
            return None

        from AI import dnd

        quantity, item_query = parsed
        try:
            display, item_kind, source = transfer_inventory(
                dnd,
                int(chat.id),
                int(sender.id),
                int(target.id),
                item_query,
                quantity,
            )
        except InventoryTransferError as exc:
            await event.answer(f"🎒 {exc}")
            return None

        target_name = getattr(target, "first_name", None) or getattr(target, "full_name", None) or "получателю"
        icon = "✨" if item_kind == "artifact" else "🎒"
        suffix = ""
        if source == "archive":
            suffix = (
                "\nСохранённый инвентарь обновлён вне егры. В новую отдельную кампанию автоматически наследуются только ✨ артефакты; "
                "обычный лут целиком возвращается при продолжении прошлой кампании."
            )
        await event.answer(f"{icon} Передано {target_name}: {display}.{suffix}")
        return None


def configure_dnd_inventory_transfer(dnd_router) -> None:
    """Register transfer handling before state commands and action collection."""
    if getattr(dnd_router, "_upupa_dnd_inventory_transfer_configured", False):
        return
    dnd_router.message.outer_middleware(DndInventoryTransferMiddleware())
    dnd_router._upupa_dnd_inventory_transfer_configured = True


def apply_stackable_metadata(campaign, original_apply, session, text):
    """Preserve campaign metadata behavior while stacking repeated ordinary ITEM tags."""
    campaign._ensure(session)
    _ensure_artifact_awards(session)
    before = _snapshot_inventory(session)
    before_artifacts = _artifact_snapshot(session)
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

    _record_new_artifact_awards(session, before_artifacts)
    return cleaned, notices


def install_fun_inventory() -> None:
    """Install inventory extensions once, without changing the base campaign module."""
    from AI import dnd_campaign as campaign
    from AI import dnd_state_commands as state_commands

    if getattr(campaign, "_upupa_fun_inventory_installed", False):
        return

    original_ensure = campaign._ensure

    def ensure(session):
        original_ensure(session)
        _ensure_artifact_awards(session)

    campaign._ensure = ensure

    original_state = campaign._state

    def state(session):
        row = original_state(session)
        _ensure_artifact_awards(session)
        row["artifact_awards"] = session.artifact_awards
        return row

    campaign._state = state

    original_apply = campaign._apply_metadata

    def apply_metadata(session, text):
        return apply_stackable_metadata(campaign, original_apply, session, text)

    campaign._apply_metadata = apply_metadata
    campaign._inventory_context = lambda session: _inventory_context(campaign, session)
    if FUN_INVENTORY_RULES not in campaign.RULES:
        campaign.RULES = f"{campaign.RULES}\n{FUN_INVENTORY_RULES}"
    state_commands._inventory_items = render_inventory_lines

    original_render_inventory = state_commands.render_inventory

    def render_inventory(dnd, chat_id, user_id):
        text = original_render_inventory(dnd, chat_id, user_id)
        return text + (
            "\n\n↪️ Передача: ответь на сообщение игрока «передать <название>». "
            "Для стака можно, например, «передать 2 штрафа»."
        )

    state_commands.render_inventory = render_inventory
    campaign._upupa_fun_inventory_installed = True
