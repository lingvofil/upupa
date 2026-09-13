"""Refine generic legacy DnD inventory effects into concrete item properties."""
from __future__ import annotations

import hashlib

from AI import dnd_inventory_fun as inventory_fun


OLD_PLACEHOLDER_EFFECTS = frozenset(
    {
        "пригодится в самый неподходящий момент",
        "повышает уверенность в сомнительных планах",
        "выглядит бесполезно ровно до момента, когда понадобится",
        "официально считается частью плана, какого именно — неизвестно",
        "явно хранит больше истории, чем объясняет",
        "подозрительно реагирует на серьёзные неприятности",
        "слишком важен, чтобы просто валяться в кармане",
        "ведёт себя так, будто у него есть собственный план",
    }
)

_ARTIFACT_TRIGGERS = (
    "нагревается рядом с",
    "тихо вибрирует возле",
    "слегка звенит при появлении",
    "заметно тяжелеет около",
    "пахнет озоном рядом с",
    "холодеет при приближении",
    "начинает царапать карман возле",
    "тихо гудит в присутствии",
)
_ARTIFACT_TARGETS = (
    "скрытого прохода или тайника",
    "человека, который врёт",
    "опасности, которую все почему-то игнорируют",
    "сильной магии",
    "чужой жадности",
    "совсем плохой, но эффектной идеи",
    "вещи, которую лучше не трогать",
    "того, кто собирается нарушить договорённость",
)
_ITEM_PREFIXES = (
    "неожиданно полезен при",
    "подозрительно хорошо подходит для",
    "иногда выручает при",
    "оказывается кстати во время",
    "работает лучше ожидаемого при",
    "годится для нецелевого применения при",
    "неожиданно удобен для",
    "может спасти ситуацию при",
)
_ITEM_TARGETS = (
    "импровизированном ремонте",
    "побеге через неудобное место",
    "торге с упрямым NPC",
    "поиске спрятанной мелочи",
    "отвлечении внимания",
    "маскировке на ходу",
    "подготовке сомнительной ловушки",
    "попытке выкрутиться без нормального плана",
)


def _contains_any(value: str, needles: tuple[str, ...]) -> bool:
    return any(needle in value for needle in needles)


def _themed_effect(name: str, kind: str) -> str | None:
    normalized = str(name or "").casefold()

    if _contains_any(normalized, ("корон", "диадем", "тиар", "венец")):
        return "делает владельца убедительнее, когда тот изображает важную персону"
    if _contains_any(normalized, ("череп", "кость", "костя", "зуб", "клык", "лап", "когт", "перо")):
        return "дёргается рядом с существами, которые явно что-то недоговаривают"
    if _contains_any(normalized, ("глаз", "око", "линз", "монокл", "очки")):
        return "помогает заметить деталь, которую остальные предпочли пропустить"
    if _contains_any(normalized, ("монет", "жетон", "медал", "талер", "рубл", "деньг")):
        return "звенит перед особенно сомнительными сделками"
    if _contains_any(normalized, ("карт", "компас", "атлас", "схем")):
        return "уверенно указывает направление, не обещая, что туда стоило идти"
    if _contains_any(normalized, ("кубок", "чаш", "бокал", "кружк", "фляг", "бутыл")):
        return "меняет вкус содержимого, когда рядом кто-то врёт"
    if _contains_any(normalized, ("колокол", "колоколь", "бубен", "свист", "флейт", "дуд")):
        return "сам подаёт голос, когда вокруг становится подозрительно тихо"
    if _contains_any(normalized, ("зеркал", "стекл", "оскол")):
        return "помогает заметить следы и пятна, которые обычным взглядом легко пропустить"
    if _contains_any(normalized, ("плащ", "мант", "перчат", "сапог", "ботин", "шлем", "доспех")):
        return "в критический момент ведёт себя чуть надёжнее, чем выглядит"
    if _contains_any(normalized, ("цеп", "верёв", "верев", "канат", "крюк")):
        return "неожиданно хорошо держит то, что по всем законам уже должно было сорваться"
    if _contains_any(normalized, ("свеч", "фонар", "ламп", "факел")):
        return "горит ярче рядом с хорошо спрятанными проходами"
    if _contains_any(normalized, ("маск", "лиц", "портрет")):
        return "помогает убедительнее изображать того, кем владелец вообще не является"

    return None


def _stable_indices(name: str, kind: str) -> tuple[int, int]:
    payload = f"{kind}:{str(name or '').casefold()}".encode("utf-8")
    digest = hashlib.sha256(payload).digest()
    return digest[0], digest[1]


def concrete_effect(item) -> str:
    """Return a stable, concrete situational property for a legacy placeholder."""
    name = inventory_fun._name(item)
    kind = inventory_fun._kind(item)
    themed = _themed_effect(name, kind)
    if themed:
        return themed

    first, second = _stable_indices(name, kind)
    if kind == "artifact":
        return f"{_ARTIFACT_TRIGGERS[first % len(_ARTIFACT_TRIGGERS)]} {_ARTIFACT_TARGETS[second % len(_ARTIFACT_TARGETS)]}"
    return f"{_ITEM_PREFIXES[first % len(_ITEM_PREFIXES)]} {_ITEM_TARGETS[second % len(_ITEM_TARGETS)]}"


def refine_inventory_item(item) -> bool:
    """Replace only an exact old fallback effect, preserving authored properties."""
    if not isinstance(item, dict):
        return False
    current = " ".join(str(item.get("effect") or "").split())
    if current not in OLD_PLACEHOLDER_EFFECTS:
        return False
    replacement = concrete_effect(item)
    if not replacement or replacement == current:
        return False
    item["effect"] = replacement
    return True


def refine_inventory_items(items) -> bool:
    if not isinstance(items, list):
        return False
    changed = False
    for item in items:
        changed = refine_inventory_item(item) or changed
    return changed


def refine_archive_data(archive) -> bool:
    """Replace v1 placeholder effects throughout saved DnD history."""
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
                changed = refine_inventory_items(history.get("inventory")) or changed
                changed = refine_inventory_items(history.get("artifacts")) or changed
        for campaign_row in chat.get("campaigns") or []:
            if not isinstance(campaign_row, dict):
                continue
            inventories = campaign_row.get("inventories") or {}
            if isinstance(inventories, dict):
                for items in inventories.values():
                    changed = refine_inventory_items(items) or changed
    return changed


def refine_session_inventory(session) -> bool:
    inventories = getattr(session, "inventories", {}) or {}
    if not isinstance(inventories, dict):
        return False
    changed = False
    for items in inventories.values():
        changed = refine_inventory_items(items) or changed
    return changed


def install_dnd_inventory_effect_refinement(dnd) -> None:
    """Replace v1 generic placeholders in persisted and restored inventories."""
    from AI import dnd_campaign as campaign

    if getattr(campaign, "_upupa_dnd_inventory_effect_refinement_installed", False):
        return

    campaign._load_archive(dnd)
    if refine_archive_data(getattr(campaign, "_archive", None)):
        campaign._save_archive(dnd)

    active_changed = False
    for session in (getattr(dnd, "dnd_sessions", {}) or {}).values():
        active_changed = refine_session_inventory(session) or active_changed
    if active_changed:
        dnd.persist_dnd_sessions()

    original_restore_state = campaign._restore_state

    def restore_state(session, data):
        original_restore_state(session, data)
        refine_session_inventory(session)

    campaign._restore_state = restore_state
    campaign._upupa_dnd_inventory_effect_refinement_installed = True
