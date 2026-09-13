"""Hard inventory acquisition rules and a repair pass for participant DnD."""
from __future__ import annotations

import logging
import re


INVENTORY_RELIABILITY_MARKER = "ЖЁСТКОЕ ПРАВИЛО ИНВЕНТАРЯ"
INVENTORY_RELIABILITY_RULES = f"""
{INVENTORY_RELIABILITY_MARKER}: если текст текущего ответа подтверждает, что герой ФАКТИЧЕСКИ получил, взял, украл,
подобрал, купил, выменял, забрал, получил в подарок, оставил себе или иным образом сохранил предмет, в ЭТОМ ЖЕ ответе
ОБЯЗАТЕЛЬНО добавь [ITEM:ADD;PLAYER:id;NAME:название;KIND:item]. Это относится и к совершенно бытовому или смешному
хламу: ложкам, носкам, штрафам, проклятиям, полученным пиздам и другим памятным последствиям. Если попытка провалилась и
предмет фактически не получен, тег не добавляй. Если предмет потерян, отдан, сломан без возможности использования или
израсходован — добавь ITEM:REMOVE.
Если за один эпизод герой получает несколько одинаковых предметов, используй QTY, например:
[ITEM:ADD;PLAYER:123;NAME:ложка;QTY:3;FEW:ложки;MANY:ложек;KIND:item]. При повторном получении используй то же NAME,
чтобы предмет стакавался. Для счётных приколов также используй QTY:5, если герой реально получил пять единиц сразу.
Если уникальная значимая вещь становится собственностью героя, используй KIND:artifact. В нормальной полной кампании
естественно давай партии возможности получить несколько обычных предметов и примерно 1–2 действительно заслуженных
артефакта, но не выдавай лут из воздуха ради квоты. Коллекционерские привычки персонажа должны работать буквально:
если герой систематически тырит ложки и кража удалась, ложка обязана оказаться в инвентаре.
""".strip()

_ITEM_TAG_RE = re.compile(r"\[ITEM:(ADD|REMOVE);([^\]]*)\]", re.I)
_QTY_RE = re.compile(r";QTY:(\d+)", re.I)
_LOOT_SIGNAL_RE = re.compile(
    r"(?:\bвзял\w*|\bбер[её]т\w*|\bzабрал\w*|\bподобрал\w*|\bполучил\w*|\bнаш[её]л\w*|"
    r"\bукрал\w*|\bстыр\w*|\bутащ\w*|\bприсво\w*|\bкупил\w*|\bвымен\w*|\bподар\w*|"
    r"\bтрофе\w*|\bартефакт\w*|\bложк\w*|\bкарман\w*|\bинвентар\w*|\bштраф\w*|"
    r"\bпроклят\w*|\bпизд\w*)",
    re.I,
)


def _expand_quantity_tags(text: str) -> str:
    """Expand QTY metadata so the existing stacker can apply it without changing base parsing."""
    source = str(text or "")

    def repl(match: re.Match) -> str:
        full = match.group(0)
        qty_match = _QTY_RE.search(full)
        if not qty_match:
            return full
        try:
            qty = max(1, min(25, int(qty_match.group(1))))
        except (TypeError, ValueError):
            qty = 1
        clean = _QTY_RE.sub("", full)
        if re.search(r";KIND:artifact(?:;|\])", clean, re.I):
            qty = 1
        return "".join(clean for _ in range(qty))

    return _ITEM_TAG_RE.sub(repl, source)


def _participant_ids(session) -> set[str]:
    result = set()
    for participant in (getattr(session, "participants", {}) or {}).values():
        try:
            result.add(str(int(participant["user_id"])))
        except (KeyError, TypeError, ValueError):
            continue
    return result


def _should_audit(session, prompt: str, response: str) -> bool:
    if getattr(session, "mode", None) != "participants":
        return False
    if "[ACTION:" not in str(response or "").upper():
        return False
    if _ITEM_TAG_RE.search(str(response or "")):
        return False
    return bool(_LOOT_SIGNAL_RE.search(f"{prompt}\n{response}"))


def _audit_prompt(campaign, session, prompt: str, response: str) -> str:
    roster = []
    for participant in (getattr(session, "participants", {}) or {}).values():
        if participant.get("user_id") is None:
            continue
        roster.append(f"- ID {int(participant['user_id'])}: {participant.get('name') or 'игрок'}")
    try:
        inventory = campaign._inventory_context(session)
    except Exception:
        inventory = "- неизвестно"
    return (
        "СЛУЖЕБНАЯ ПРОВЕРКА ИНВЕНТАРЯ. Это не игровой ход и не продолжение сюжета.\n"
        "Проверь уже написанный ответ ведущего и верни ТОЛЬКО отсутствующие ITEM-теги для изменений инвентаря, "
        "которые этот ответ уже однозначно подтвердил. Ничего не придумывай и не меняй исход сцены.\n"
        "Если герой лишь попытался взять/украсть предмет и провалился — ничего не добавляй. Если фактически получил даже "
        "обычную ложку, мусор или смешной трофей — добавь. Для нескольких одинаковых единиц используй QTY. "
        "Значимый уникальный предмет можно отметить KIND:artifact. Потерянное/отданное/израсходованное — ITEM:REMOVE.\n"
        "Если изменений нет, верни ровно NONE.\n\n"
        f"УЧАСТНИКИ:\n{chr(10).join(roster) or '- нет'}\n"
        f"ТЕКУЩИЙ ИНВЕНТАРЬ:\n{inventory}\n\n"
        f"ИСХОДНЫЙ ЗАПРОС/ДЕЙСТВИЯ:\n{str(prompt)[-3500:]}\n\n"
        f"УЖЕ НАПИСАННЫЙ ОТВЕТ:\n{str(response)[:4500]}\n"
    )


def _validated_item_tags(campaign, session, audit_text: str) -> list[str]:
    valid_players = _participant_ids(session)
    tags = []
    for match in campaign.META_RE.finditer(str(audit_text or "")):
        kind, payload = match.group(1).upper(), match.group(2)
        if kind != "ITEM":
            continue
        head, fields = campaign._parse_fields(payload)
        action = str(head or "").upper()
        player = str(fields.get("PLAYER") or "")
        name = str(fields.get("NAME") or "").strip()
        if action not in {"ADD", "REMOVE"} or player not in valid_players or not name:
            continue
        item_kind = str(fields.get("KIND") or "item").lower()
        if item_kind not in {"item", "artifact"}:
            item_kind = "item"
        try:
            qty = max(1, min(25, int(fields.get("QTY", 1))))
        except (TypeError, ValueError):
            qty = 1
        if item_kind == "artifact":
            qty = 1
        parts = [f"[ITEM:{action}", f"PLAYER:{player}", f"NAME:{name}"]
        if qty > 1:
            parts.append(f"QTY:{qty}")
        if action == "ADD":
            for key in ("FEW", "MANY", "PLURAL"):
                value = str(fields.get(key) or "").strip()
                if value:
                    parts.append(f"{key}:{value}")
            parts.append(f"KIND:{item_kind}")
        tags.append(";".join(parts) + "]")
    return tags


def _insert_tags_before_action(campaign, response: str, tags: list[str]) -> str:
    if not tags:
        return response
    block = "\n".join(tags)
    match = campaign.ACTION_RE.search(str(response or ""))
    if not match:
        return str(response or "").rstrip() + "\n" + block
    before = str(response)[:match.start()].rstrip()
    after = str(response)[match.start():].lstrip()
    return f"{before}\n{block}\n{after}".strip()


def _restore_gemini_state(dnd, session) -> None:
    if getattr(session, "active_model", None) != "gemini" or not hasattr(dnd, "model"):
        return
    history = []
    for item in getattr(session, "conversation", None) or []:
        if not isinstance(item, dict) or item.get("content") is None:
            continue
        role = "model" if item.get("role") in {"assistant", "model"} else "user"
        history.append({"role": role, "parts": [str(item["content"])]})
    session.chat_session = dnd.model.start_chat(chat_id=session.chat_id, history=history)


async def _audit_missing_inventory_tags(dnd, campaign, original_generate, session, prompt: str, response: str) -> str:
    if not _should_audit(session, prompt, response):
        return response
    conversation = getattr(session, "conversation", None)
    before = len(conversation) if isinstance(conversation, list) else None
    try:
        audit = await original_generate(session, _audit_prompt(campaign, session, prompt, response))
        tags = _validated_item_tags(campaign, session, audit)
        if tags:
            logging.info(
                "DnD inventory repair chat_id=%s tags=%s",
                getattr(session, "chat_id", None),
                tags,
            )
            return _insert_tags_before_action(campaign, response, tags)
        return response
    except Exception:
        logging.exception("DnD inventory audit failed chat_id=%s", getattr(session, "chat_id", None))
        return response
    finally:
        if before is not None and isinstance(conversation, list) and len(conversation) > before:
            del conversation[before:]
            try:
                dnd.persist_dnd_sessions()
            except Exception:
                logging.exception("DnD inventory audit state restore failed")
            try:
                _restore_gemini_state(dnd, session)
            except Exception:
                logging.exception("DnD inventory audit Gemini restore failed")


def install_dnd_inventory_reliability(dnd) -> None:
    """Make confirmed item acquisition mechanically reliable for participant campaigns."""
    from AI import dnd_campaign as campaign

    if getattr(campaign, "_upupa_dnd_inventory_reliability_installed", False):
        return

    original_context = campaign._campaign_context

    def campaign_context(dnd_module, session):
        base = original_context(dnd_module, session)
        if INVENTORY_RELIABILITY_MARKER in base:
            return base
        return base + "\nИНВЕНТАРНЫЕ ПРАВИЛА:\n" + INVENTORY_RELIABILITY_RULES

    campaign._campaign_context = campaign_context
    if INVENTORY_RELIABILITY_MARKER not in campaign.RULES:
        campaign.RULES = f"{campaign.RULES}\n{INVENTORY_RELIABILITY_RULES}"
    if INVENTORY_RELIABILITY_MARKER not in dnd.DND_SYSTEM_PROMPT:
        dnd.DND_SYSTEM_PROMPT = f"{dnd.DND_SYSTEM_PROMPT.rstrip()}\n\n{INVENTORY_RELIABILITY_RULES}"

    original_apply_metadata = campaign._apply_metadata

    def apply_metadata(session, text):
        return original_apply_metadata(session, _expand_quantity_tags(text))

    campaign._apply_metadata = apply_metadata

    original_generate = dnd.generate_session_response

    async def generate_with_inventory_audit(session, prompt: str) -> str:
        response = await original_generate(session, prompt)
        return await _audit_missing_inventory_tags(
            dnd,
            campaign,
            original_generate,
            session,
            prompt,
            response,
        )

    dnd.generate_session_response = generate_with_inventory_audit
    campaign._upupa_dnd_inventory_reliability_installed = True
