"""Fairly rotate proactive individual DnD turns between living participants."""
from __future__ import annotations

import logging
import random
import re


SPOTLIGHT_MARKER = "ОЧЕРЕДЬ ФОКУСА DND УПУПЫ"
SPOTLIGHT_RULES = f"""
{SPOTLIGHT_MARKER}: индивидуальная инициатива должна равномерно переходить между живыми героями.
Если ниже указан «СЛЕДУЮЩИЙ ФОКУС», то следующий ОДИНОЧНЫЙ проактивный ход обязан принадлежать именно этому ID:
адресный INPUT, CHECK, PLAYER_ATTACK, CINEMATIC_ATTACK или личный POLL. Не отдавай два таких хода подряд одному герою,
пока есть другие живые участники. Общий INPUT/POLL всей партии очередь не расходует. SAVE, атака врага и другие вынужденные
реакции тоже не расходуют очередь: персонаж не теряет инициативу за то, что на него свалился потолок.
После двух индивидуальных инициатив подряд предпочитай общий сюжетный эпизод/ход партии, чтобы история снова собрала всех.
Голосование используй для настоящей общей развилки, а не как меню каждого микродействия и не как замену обычному ходу.
""".strip()

_ACTION_RE = re.compile(r"\[ACTION:([A-Z_]+)([^\]]*)\]", re.I | re.S)
_TARGETS_RE = re.compile(r"(?:^|;)TARGETS:([0-9,\s]+)(?=;|$)", re.I)
_TYPE_RE = re.compile(r"(?:^|;)TYPE:([A-Z_]+)(?=;|$)", re.I)
_PROACTIVE_ACTIONS = {"INPUT", "POLL", "ROLL", "PLAYER_ATTACK", "CINEMATIC_ATTACK"}


def _ensure(session) -> None:
    if not isinstance(getattr(session, "spotlight_order", None), list):
        session.spotlight_order = []
    try:
        session.spotlight_cursor = max(0, int(getattr(session, "spotlight_cursor", 0) or 0))
    except (TypeError, ValueError):
        session.spotlight_cursor = 0
    try:
        session.spotlight_individual_streak = max(0, int(getattr(session, "spotlight_individual_streak", 0) or 0))
    except (TypeError, ValueError):
        session.spotlight_individual_streak = 0
    try:
        session.spotlight_decisions_since_poll = max(0, int(getattr(session, "spotlight_decisions_since_poll", 0) or 0))
    except (TypeError, ValueError):
        session.spotlight_decisions_since_poll = 0
    last = getattr(session, "spotlight_last_player", None)
    try:
        session.spotlight_last_player = int(last) if last is not None else None
    except (TypeError, ValueError):
        session.spotlight_last_player = None


def _participant_ids(session) -> list[int]:
    result = []
    for item in (getattr(session, "participants", {}) or {}).values():
        try:
            user_id = int(item["user_id"])
        except (KeyError, TypeError, ValueError):
            continue
        if user_id not in result:
            result.append(user_id)
    return result


def _living_ids(session) -> list[int]:
    participants = _participant_ids(session)
    sheets = getattr(session, "character_sheets", {}) or {}
    if not isinstance(sheets, dict) or not sheets:
        return participants
    living = []
    for user_id in participants:
        sheet = sheets.get(str(user_id))
        if not isinstance(sheet, dict):
            living.append(user_id)
            continue
        try:
            hp = int(sheet.get("hp", 0))
        except (TypeError, ValueError):
            hp = 0
        if hp > 0 and str(sheet.get("status") or "alive").casefold() != "dead":
            living.append(user_id)
    return living


def _ensure_order(session) -> list[int]:
    _ensure(session)
    participants = _participant_ids(session)
    current = []
    for raw in session.spotlight_order:
        try:
            user_id = int(raw)
        except (TypeError, ValueError):
            continue
        if user_id in participants and user_id not in current:
            current.append(user_id)
    missing = [user_id for user_id in participants if user_id not in current]
    if not current and missing:
        random.shuffle(missing)
    current.extend(missing)
    session.spotlight_order = current
    if current:
        session.spotlight_cursor %= len(current)
    else:
        session.spotlight_cursor = 0
    return current


def next_spotlight(session) -> int | None:
    order = _ensure_order(session)
    living = set(_living_ids(session))
    if not order or not living:
        return None
    start = int(session.spotlight_cursor) % len(order)
    for offset in range(len(order)):
        candidate = order[(start + offset) % len(order)]
        if candidate in living:
            return candidate
    return None


def _advance_after(session, user_id: int) -> None:
    order = _ensure_order(session)
    if order and int(user_id) in order:
        session.spotlight_cursor = (order.index(int(user_id)) + 1) % len(order)
    session.spotlight_last_player = int(user_id)
    session.spotlight_individual_streak = int(session.spotlight_individual_streak) + 1
    session.spotlight_decisions_since_poll = int(session.spotlight_decisions_since_poll) + 1


def _note_group_decision(session, *, poll: bool = False) -> None:
    _ensure(session)
    session.spotlight_individual_streak = 0
    if poll:
        session.spotlight_decisions_since_poll = 0
    else:
        session.spotlight_decisions_since_poll = int(session.spotlight_decisions_since_poll) + 1


def _targets(suffix: str) -> list[int]:
    match = _TARGETS_RE.search(";" + str(suffix or "").strip(";") + ";")
    if not match:
        return []
    result = []
    for token in match.group(1).split(","):
        token = token.strip()
        if token.isdigit() and int(token) not in result:
            result.append(int(token))
    return result


def _roll_type(suffix: str) -> str:
    match = _TYPE_RE.search(";" + str(suffix or "").strip(";") + ";")
    return match.group(1).upper() if match else "CHECK"


def _action_info(response: str) -> tuple[str | None, str, list[int]]:
    match = _ACTION_RE.search(str(response or ""))
    if not match:
        return None, "", []
    action = match.group(1).upper()
    suffix = match.group(2) or ""
    return action, suffix, _targets(suffix)


def _replace_or_add_single_target(response: str, user_id: int) -> str:
    """Set TARGETS on the first action tag without touching POLL option payloads."""
    text = str(response or "")
    match = _ACTION_RE.search(text)
    if not match:
        return text
    action = match.group(1).upper()
    suffix = match.group(2) or ""
    normalized = ";" + suffix.strip(";")
    target_match = re.search(r";TARGETS:[0-9,\s]+(?=;|$)", normalized, re.I)
    if target_match:
        normalized = normalized[: target_match.start()] + f";TARGETS:{int(user_id)}" + normalized[target_match.end() :]
    elif action != "POLL":
        normalized += f";TARGETS:{int(user_id)}"
    return text[: match.start()] + f"[ACTION:{action}{normalized}]" + text[match.end() :]


def enforce_spotlight(session, response: str) -> tuple[str, int | None, bool]:
    """Return response with a fair target, consumed player ID, and whether it was rewritten."""
    _ensure(session)
    action, suffix, targets = _action_info(response)
    if action not in _PROACTIVE_ACTIONS:
        return str(response or ""), None, False

    if action == "ROLL" and _roll_type(suffix) == "SAVE":
        return str(response or ""), None, False

    # Untargeted INPUT/POLL are genuinely collective. Multi-target actions are
    # also treated as collective and do not consume one hero's spotlight.
    if action in {"INPUT", "POLL"} and len(targets) != 1:
        _note_group_decision(session, poll=action == "POLL")
        return str(response or ""), None, False
    if len(targets) > 1:
        _note_group_decision(session, poll=action == "POLL")
        return str(response or ""), None, False

    expected = next_spotlight(session)
    if expected is None:
        return str(response or ""), None, False

    # CHECK/attacks without TARGETS would otherwise become a race to type
    # «кидаю». Make them deterministic. Addressed INPUT/POLL are rotated too.
    current = targets[0] if targets else None
    rewritten = current != expected
    guarded = _replace_or_add_single_target(response, expected) if rewritten else str(response or "")
    _advance_after(session, expected)
    return guarded, expected, rewritten


def _spotlight_context(session) -> str:
    _ensure(session)
    expected = next_spotlight(session)
    if expected is None:
        return ""
    participant = (getattr(session, "participants", {}) or {}).get(str(expected), {})
    name = participant.get("name") or f"ID {expected}"
    extra = ""
    if int(session.spotlight_individual_streak) >= 2:
        extra += "\nУже было два индивидуальных хода подряд: сейчас особенно предпочтителен общий INPUT/POLL или общий сюжетный бит."
    if int(session.spotlight_decisions_since_poll) < 2:
        extra += "\nНедавно уже было голосование: не создавай новое без действительно новой общей развилки."
    return (
        f"\n{SPOTLIGHT_MARKER}: СЛЕДУЮЩИЙ ФОКУС — ID {expected} ({name}). "
        "Если следующий проактивный ход индивидуальный, TARGETS должен указывать именно этот ID."
        + extra
    )


def _restore(session, data) -> None:
    row = data if isinstance(data, dict) else {}
    session.spotlight_order = list(row.get("spotlight_order") or [])
    session.spotlight_cursor = row.get("spotlight_cursor", 0)
    session.spotlight_individual_streak = row.get("spotlight_individual_streak", 0)
    session.spotlight_decisions_since_poll = row.get("spotlight_decisions_since_poll", 0)
    session.spotlight_last_player = row.get("spotlight_last_player")
    _ensure(session)


def install_dnd_spotlight(dnd, *, state_policy) -> None:
    """Install persistent spotlight state and hard target rotation."""
    from AI import dnd_campaign as campaign

    if getattr(dnd, "_upupa_dnd_spotlight_installed", False):
        return

    state_policy.add_ensure_hook(_ensure)
    state_policy.add_state_field("spotlight_order", lambda session: list(getattr(session, "spotlight_order", []) or []))
    state_policy.add_state_field("spotlight_cursor", lambda session: int(getattr(session, "spotlight_cursor", 0) or 0))
    state_policy.add_state_field(
        "spotlight_individual_streak", lambda session: int(getattr(session, "spotlight_individual_streak", 0) or 0)
    )
    state_policy.add_state_field(
        "spotlight_decisions_since_poll", lambda session: int(getattr(session, "spotlight_decisions_since_poll", 0) or 0)
    )
    state_policy.add_state_field("spotlight_last_player", lambda session: getattr(session, "spotlight_last_player", None))
    state_policy.add_restore_hook(_restore)

    if SPOTLIGHT_MARKER not in dnd.DND_SYSTEM_PROMPT:
        dnd.DND_SYSTEM_PROMPT = dnd.DND_SYSTEM_PROMPT.rstrip() + "\n\n" + SPOTLIGHT_RULES

    original_context = campaign._campaign_context

    def campaign_context(dnd_module, session):
        return original_context(dnd_module, session) + "\n" + SPOTLIGHT_RULES + _spotlight_context(session)

    campaign._campaign_context = campaign_context

    original_parse = dnd.parse_and_execute_turn

    async def parse_turn(bot, chat_id, response):
        session = dnd.dnd_sessions.get(chat_id)
        if session and dnd._is_participant_mode(session):
            guarded, expected, rewritten = enforce_spotlight(session, response)
            if rewritten:
                logging.warning(
                    "DnD rotated individual spotlight chat_id=%s expected_user_id=%s",
                    chat_id,
                    expected,
                )
            response = guarded
            dnd.persist_dnd_sessions()
        return await original_parse(bot, chat_id, response)

    dnd.parse_and_execute_turn = parse_turn

    original_finalize = dnd.finalize_group_actions

    async def finalize_group_actions(bot, chat_id, prompt_message_id):
        session = dnd.dnd_sessions.get(chat_id)
        if session and dnd._is_participant_mode(session):
            targets = list(getattr(session, "action_target_user_ids", []) or [])
            actions = list((getattr(session, "pending_actions", {}) or {}).values())
            if not targets and len(actions) == 1:
                try:
                    actor = int(actions[0].get("user_id"))
                except (TypeError, ValueError):
                    actor = None
                if actor is not None and actor in _living_ids(session):
                    _advance_after(session, actor)
            elif not targets and len(actions) > 1:
                _note_group_decision(session)
            dnd.persist_dnd_sessions()
        return await original_finalize(bot, chat_id, prompt_message_id)

    dnd.finalize_group_actions = finalize_group_actions
    dnd._upupa_dnd_spotlight_installed = True


__all__ = [
    "SPOTLIGHT_MARKER",
    "SPOTLIGHT_RULES",
    "enforce_spotlight",
    "next_spotlight",
    "install_dnd_spotlight",
]
