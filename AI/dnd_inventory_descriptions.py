"""Guarantee a short visible characteristic for every DnD inventory item."""
from __future__ import annotations

from AI import dnd_inventory_effects as effects
from AI import dnd_inventory_fun as inventory_fun


INVENTORY_DESCRIPTION_MARKER = "ОБЯЗАТЕЛЬНЫЕ ХАРАКТЕРИСТИКИ ИНВЕНТАРЯ УПУПЫ"
INVENTORY_DESCRIPTION_RULES = f"""
{INVENTORY_DESCRIPTION_MARKER}: КАЖДЫЙ новый ITEM:ADD должен содержать короткую характеристику, которая будет видна
рядом с названием предмета в инвентаре. Не оставляй новый предмет только с NAME/KIND.
Если у стака естественно накапливается шуточное свойство, используй BONUS + TRAIT. Во всех остальных случаях обязательно
укажи EFFECT: 2–10 слов о том, чем вещь примечательна, полезна, подозрительна или смешна в контексте её получения.
EFFECT не обязан быть механическим бонусом и не должен выдумывать прямой модификатор d20. Для обычной безделушки это может
быть просто короткая характерная деталь. Сохраняй характеристику лаконичной и без точки с запятой.
""".strip()


def _described_copy(item):
    """Return a display-only copy with a deterministic characteristic when missing."""
    if effects._has_explicit_effect(item):
        return item
    if isinstance(item, dict):
        described = dict(item)
    else:
        name = inventory_fun._name(item)
        if not name:
            return item
        described = {
            "name": name,
            "kind": inventory_fun._kind(item),
            "quantity": inventory_fun._quantity(item),
        }
    effects._apply_effect_fields(described, effects._legacy_effect_fields(described))
    return described


def format_inventory_entry(item) -> str:
    """Render a characteristic without mutating the persisted inventory object."""
    return effects.format_inventory_entry(_described_copy(item))


def render_inventory_lines(items) -> list[str]:
    result = []
    for item in items or []:
        text = format_inventory_entry(item)
        if text:
            prefix = "✨ " if inventory_fun._kind(item) == "artifact" else "• "
            result.append(prefix + text)
    return result


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


def apply_missing_item_descriptions(campaign, session, text, cleaned, notices):
    """Refresh ITEM:ADD notices with a fallback characteristic without changing stored items."""
    valid_players = effects._valid_players(session)
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
        described = _described_copy(inventory[index])
        if isinstance(described, dict):
            effects._refresh_notice(notices, session, player, described)

    return cleaned, notices


def configure_inventory_description_rules(dnd, *, campaign=None) -> None:
    """Compose the mandatory-description contract after the older optional effect rules."""
    if campaign is None:
        from AI import dnd_campaign as campaign

    if INVENTORY_DESCRIPTION_MARKER not in campaign.RULES:
        campaign.RULES = f"{campaign.RULES}\n{INVENTORY_DESCRIPTION_RULES}"
    if INVENTORY_DESCRIPTION_MARKER not in dnd.DND_SYSTEM_PROMPT:
        dnd.DND_SYSTEM_PROMPT = f"{dnd.DND_SYSTEM_PROMPT.rstrip()}\n\n{INVENTORY_DESCRIPTION_RULES}"


def install_dnd_inventory_descriptions(*, metadata_policy) -> None:
    """Install display fallback for acquisition notices; persistence remains unchanged."""
    from AI import dnd_campaign as campaign

    if getattr(campaign, "_upupa_dnd_inventory_descriptions_installed", False):
        return

    def postprocess(session, text, cleaned, notices):
        return apply_missing_item_descriptions(campaign, session, text, cleaned, notices)

    metadata_policy.add_postprocessor(postprocess)
    campaign._upupa_dnd_inventory_descriptions_installed = True
