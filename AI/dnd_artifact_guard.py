"""Prevent participant characters from taking artifacts away from each other through story metadata."""
from __future__ import annotations

from AI import dnd_inventory_fun as inventory_fun


ARTIFACT_GUARD_MARKER = "ЗАЩИТА АРТЕФАКТОВ ОТ ОТБОРА"
ARTIFACT_GUARD_RULES = f"""
{ARTIFACT_GUARD_MARKER}: один герой не может украсть, отобрать, вырвать, присвоить или иным недобровольным способом
забрать KIND:artifact у другого героя. Такие попытки могут быть частью сцены, но владелец артефакта не меняется и
ITEM:REMOVE/ITEM:ADD для передачи чужого артефакта между героями писать нельзя.
Добровольная передача артефакта возможна только через явную пользовательскую команду владельца «передать ...»/«отдать ...».
Обычные KIND:item этим правилом не защищены.
""".strip()


def _artifact_owner(session, item_name: str) -> str | None:
    needle = str(item_name or "").strip().casefold()
    if not needle:
        return None
    for player, items in (getattr(session, "inventories", {}) or {}).items():
        for item in items or []:
            if inventory_fun._kind(item) == "artifact" and inventory_fun._name(item).casefold() == needle:
                return str(player)
    return None


def _item_tags(campaign, text: str) -> list[dict]:
    result = []
    for match in campaign.META_RE.finditer(str(text or "")):
        kind, payload = match.group(1).upper(), match.group(2)
        if kind != "ITEM":
            continue
        head, fields = campaign._parse_fields(payload)
        result.append(
            {
                "span": match.span(),
                "action": str(head or "").upper(),
                "player": str(fields.get("PLAYER") or ""),
                "name": str(fields.get("NAME") or "").strip(),
                "kind": str(fields.get("KIND") or "item").casefold(),
            }
        )
    return result


def strip_artifact_theft(campaign, session, text: str) -> tuple[str, list[tuple[str, str]]]:
    """Remove metadata that would move an existing artifact to another participant."""
    source = str(text or "")
    tags = _item_tags(campaign, source)
    blocked_spans: set[tuple[int, int]] = set()
    blocked: list[tuple[str, str]] = []

    for tag in tags:
        if tag["action"] != "ADD" or not tag["player"] or not tag["name"]:
            continue
        owner = _artifact_owner(session, tag["name"])
        if owner is None or owner == tag["player"]:
            continue
        # Existing artifact already belongs to another hero: never clone or move it by story metadata.
        blocked_spans.add(tag["span"])
        blocked.append((owner, tag["name"]))
        for candidate in tags:
            if (
                candidate["action"] == "REMOVE"
                and candidate["player"] == owner
                and candidate["name"].casefold() == tag["name"].casefold()
            ):
                blocked_spans.add(candidate["span"])

    if not blocked_spans:
        return source, []

    pieces = []
    cursor = 0
    for start, end in sorted(blocked_spans):
        pieces.append(source[cursor:start])
        cursor = end
    pieces.append(source[cursor:])
    return "".join(pieces), blocked


def apply_artifact_guard(campaign, original_apply, session, text):
    guarded_text, blocked = strip_artifact_theft(campaign, session, text)
    cleaned, notices = original_apply(session, guarded_text)
    participants = getattr(session, "participants", {}) or {}
    seen = set()
    for owner, item_name in blocked:
        key = (owner, item_name.casefold())
        if key in seen:
            continue
        seen.add(key)
        owner_name = (participants.get(owner) or {}).get("name") or f"игрок {owner}"
        notices.append(f"✨ {item_name} остаётся у {owner_name}: чужие артефакты нельзя отбирать.")
    return cleaned, notices


def install_dnd_artifact_guard(dnd) -> None:
    from AI import dnd_campaign as campaign

    if getattr(campaign, "_upupa_dnd_artifact_guard_installed", False):
        return

    original_apply = campaign._apply_metadata

    def apply_metadata(session, text):
        return apply_artifact_guard(campaign, original_apply, session, text)

    campaign._apply_metadata = apply_metadata
    if ARTIFACT_GUARD_MARKER not in campaign.RULES:
        campaign.RULES = f"{campaign.RULES}\n{ARTIFACT_GUARD_RULES}"
    if ARTIFACT_GUARD_MARKER not in dnd.DND_SYSTEM_PROMPT:
        dnd.DND_SYSTEM_PROMPT = f"{dnd.DND_SYSTEM_PROMPT.rstrip()}\n\n{ARTIFACT_GUARD_RULES}"
    campaign._upupa_dnd_artifact_guard_installed = True
