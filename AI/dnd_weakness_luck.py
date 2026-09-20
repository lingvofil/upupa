"""Deliberate weakness complications that earn bounded luck tokens."""
from __future__ import annotations

import copy
import re

from aiogram import BaseMiddleware


WEAKNESS_LUCK_MARKER = "СЛАБОСТЬ ЗА УДАЧУ DND УПУПЫ"
MAX_LUCK_TOKENS = 1
MAX_LUCK_EARNED_PER_ADVENTURE = 2

_WEAKNESS_RE = re.compile(r"\[WEAKNESS:([^\]]*)\]", re.I)
_ACTION_RE = re.compile(r"\[ACTION:([A-Z_]+)([^\]]*)\]", re.I | re.S)
_TARGETS_RE = re.compile(r"(?:^|;)TARGETS:([0-9,\s]+)(?=;|$)", re.I)
_MODE_RE = re.compile(r"(?:^|;)MODE:([A-Z_]+)(?=;|$)", re.I)

_WEAKNESS_PREFIXES = (
    "играю слабость",
    "использую слабость",
    "поддаюсь слабости",
    "поддаюсь своей слабости",
)
_LUCK_COMMANDS = {
    "трачу удачу",
    "использую удачу",
    "трачу жетон удачи",
    "использую жетон удачи",
}

WEAKNESS_LUCK_RULES = f"""
{WEAKNESS_LUCK_MARKER}.
Игрок может ОСОЗНАННО сыграть слабость своего профиля. Код помечает такие заявки отдельно.
Никакого жетона за простое упоминание слабости нет: нужна реальная дополнительная цена именно из-за неё.

Если заявка действительно воплощает указанную слабость и должна потребовать бросок/атаку, добавь:
[WEAKNESS:ROLL;PLAYER:123;COMPLICATION:жадность заставляет полезть первым]
непосредственно перед ACTION:ROLL или ACTION:PLAYER_ATTACK. Код сам ухудшит MODE на одну ступень:
ADVANTAGE → NORMAL, NORMAL → DISADVANTAGE. Если бросок уже с DISADVANTAGE, дополнительной цены нет и жетона не будет.
Не добавляй помеху вручную сверх этого тега.

Если осложнение происходит сразу и бросок не нужен, используй только один реальный кодовый COST:
[WEAKNESS:PAYOFF;PLAYER:123;COST:DANGER_PLUS_1;COMPLICATION:болтовня выдаёт позицию]
[WEAKNESS:PAYOFF;PLAYER:123;COST:PROGRESS_MINUS_1;COMPLICATION:одержимость загадкой уводит от цели]
[WEAKNESS:PAYOFF;PLAYER:123;COST:CONDITION;NAME:растерян после провокации;EFFECT:SOCIAL_DISADVANTAGE;
CLEAR:товарищ приводит в чувство;USES:1;COMPLICATION:гордость заставляет сорваться]
Для CONDITION доступны только стандартные CONDITION EFFECTS проекта.
DANGER_PLUS_1 допустим только при активной незаполненной шкале опасности/старой угрозе.
PROGRESS_MINUS_1 допустим только при активной шкале прогресса выше нуля.

PAYOFF означает, что осложнение уже реально произошло в этой сцене. Не ставь его за декоративный стыд, реплику без последствий
или обычный провал, который случился бы и без слабости. Код применит цену и только после этого выдаст жетон.
Баланс: максимум 1 жетон удачи в запасе и максимум 2 заработанных за приключение.
Жетон тратится игроком ДО броска отдельной фразой и улучшает MODE на одну ступень:
DISADVANTAGE → NORMAL, NORMAL → ADVANTAGE. Уже имеющий ADVANTAGE бросок усилить нельзя.
""".strip()


def _clean(value, limit=220):
    return " ".join(str(value or "").replace("[", "").replace("]", "").split()).strip(" ;")[:limit]


def _normalize_text(value):
    return " ".join(str(value or "").strip().casefold().replace("ё", "е").split()).rstrip(" .,!?:;")


def _parse_fields(raw):
    parts = [part.strip() for part in str(raw or "").split(";") if part.strip()]
    head = parts[0].upper() if parts else ""
    fields = {}
    for part in parts[1:]:
        key, sep, value = part.partition(":")
        if sep and value.strip():
            fields[key.strip().upper()] = value.strip()
    return head, fields


def _ensure(session):
    if not isinstance(getattr(session, "luck_tokens", None), dict):
        session.luck_tokens = {}
    if not isinstance(getattr(session, "weakness_luck_earned", None), dict):
        session.weakness_luck_earned = {}
    if not isinstance(getattr(session, "weakness_luck_spent", None), dict):
        session.weakness_luck_spent = {}
    if not isinstance(getattr(session, "pending_weakness_invocations", None), dict):
        session.pending_weakness_invocations = {}


def _restore(session, data):
    row = data if isinstance(data, dict) else {}
    session.luck_tokens = {
        str(key): max(0, min(MAX_LUCK_TOKENS, int(value or 0)))
        for key, value in (row.get("luck_tokens") or {}).items()
        if str(key).lstrip("-").isdigit()
    }
    session.weakness_luck_earned = {
        str(key): max(0, min(MAX_LUCK_EARNED_PER_ADVENTURE, int(value or 0)))
        for key, value in (row.get("weakness_luck_earned") or {}).items()
        if str(key).lstrip("-").isdigit()
    }
    session.weakness_luck_spent = {
        str(key): max(0, int(value or 0))
        for key, value in (row.get("weakness_luck_spent") or {}).items()
        if str(key).lstrip("-").isdigit()
    }
    session.pending_weakness_invocations = {
        str(key): dict(value)
        for key, value in (row.get("pending_weakness_invocations") or {}).items()
        if str(key).lstrip("-").isdigit() and isinstance(value, dict)
    }
    _ensure(session)


def _profile_weakness(session, user_id):
    profile = (getattr(session, "character_profiles", {}) or {}).get(str(int(user_id)), {})
    return _clean(profile.get("weakness"), 120) if isinstance(profile, dict) else ""


def _can_earn(session, user_id):
    _ensure(session)
    key = str(int(user_id))
    return (
        int(session.luck_tokens.get(key, 0) or 0) < MAX_LUCK_TOKENS
        and int(session.weakness_luck_earned.get(key, 0) or 0) < MAX_LUCK_EARNED_PER_ADVENTURE
    )


def _award_luck(session, user_id, complication, *, prevalidated=False):
    if not prevalidated and not _can_earn(session, user_id):
        return None
    _ensure(session)
    key = str(int(user_id))
    session.luck_tokens[key] = min(MAX_LUCK_TOKENS, int(session.luck_tokens.get(key, 0) or 0) + 1)
    session.weakness_luck_earned[key] = min(
        MAX_LUCK_EARNED_PER_ADVENTURE,
        int(session.weakness_luck_earned.get(key, 0) or 0) + 1,
    )
    who = (getattr(session, "participants", {}) or {}).get(key, {}).get("name") or f"игрок {key}"
    reason = _clean(complication, 180) or "слабость реально осложнила ситуацию"
    return f"🍀 {who} получает жетон удачи 1/1: {reason}."


def _spend_luck(session, user_id):
    _ensure(session)
    key = str(int(user_id))
    if int(session.luck_tokens.get(key, 0) or 0) <= 0:
        return False
    session.luck_tokens[key] = int(session.luck_tokens.get(key, 0) or 0) - 1
    session.weakness_luck_spent[key] = int(session.weakness_luck_spent.get(key, 0) or 0) + 1
    return True


def _worsen_mode(mode):
    value = str(mode or "NORMAL").upper()
    if value == "ADVANTAGE":
        return "NORMAL"
    if value == "NORMAL":
        return "DISADVANTAGE"
    return None


def _improve_mode(mode):
    value = str(mode or "NORMAL").upper()
    if value == "DISADVANTAGE":
        return "NORMAL"
    if value == "NORMAL":
        return "ADVANTAGE"
    return None


def _targets(suffix):
    match = _TARGETS_RE.search(";" + str(suffix or "").strip(";") + ";")
    if not match:
        return []
    return [int(value) for value in match.group(1).split(",") if value.strip().isdigit()]


def _mode(suffix):
    match = _MODE_RE.search(";" + str(suffix or "").strip(";") + ";")
    value = match.group(1).upper() if match else "NORMAL"
    return value if value in {"NORMAL", "ADVANTAGE", "DISADVANTAGE"} else "NORMAL"


def _set_mode_in_action(response, new_mode):
    text = str(response or "")
    match = _ACTION_RE.search(text)
    if not match:
        return text
    action = match.group(1).upper()
    suffix = (match.group(2) or "").strip(";")
    normalized = f";{suffix}" if suffix else ""
    existing = re.search(r";MODE:[A-Z_]+(?=;|$)", normalized, re.I)
    if existing:
        normalized = normalized[:existing.start()] + f";MODE:{new_mode}" + normalized[existing.end():]
    else:
        normalized += f";MODE:{new_mode}"
    return text[:match.start()] + f"[ACTION:{action}{normalized}]" + text[match.end():]


def _action_reply(dnd, event):
    if dnd._is_group_action_reply(event):
        return True
    from AI.dnd_any_bot_reply import is_any_bot_action_reply
    return bool(is_any_bot_action_reply(event))


def _weakness_invoked(text):
    normalized = _normalize_text(text)
    return any(
        normalized == prefix or normalized.startswith(prefix + " ") or normalized.startswith(prefix + " —")
        for prefix in _WEAKNESS_PREFIXES
    )


def _append_weakness_note(action, weakness):
    return (
        str(action or "").rstrip()
        + "\nТЕХНИЧЕСКИ: игрок сознательно играет слабость профиля «"
        + str(weakness)
        + "». Жетон допустим только за дополнительное реальное осложнение по правилам WEAKNESS."
    )


def _pending_context(session):
    _ensure(session)
    lines = []
    for player, row in session.pending_weakness_invocations.items():
        if not isinstance(row, dict):
            continue
        lines.append(
            f"- ID {player}: слабость «{row.get('weakness')}»; заявка: {row.get('action')}"
        )
    if not lines:
        return ""
    return (
        "\n\n"
        + WEAKNESS_LUCK_RULES
        + "\nСЕЙЧАС СОЗНАТЕЛЬНО ИГРАЮТ СЛАБОСТЬ:\n"
        + "\n".join(lines)
    )


def _apply_roll_tag(session, response):
    _ensure(session)
    text = str(response or "")
    action_match = _ACTION_RE.search(text)
    if not action_match or action_match.group(1).upper() not in {"ROLL", "PLAYER_ATTACK"}:
        return _WEAKNESS_RE.sub(lambda m: "" if _parse_fields(m.group(1))[0] == "ROLL" else m.group(0), text), None

    targets = _targets(action_match.group(2) or "")
    if len(targets) != 1:
        return text, None
    user_id = int(targets[0])
    key = str(user_id)
    pending = session.pending_weakness_invocations.get(key)
    if not isinstance(pending, dict) or not _can_earn(session, user_id):
        return _WEAKNESS_RE.sub(lambda m: "" if _parse_fields(m.group(1))[0] == "ROLL" else m.group(0), text), None

    selected = None
    for match in _WEAKNESS_RE.finditer(text):
        head, fields = _parse_fields(match.group(1))
        if head == "ROLL" and str(fields.get("PLAYER") or "") == key:
            selected = (match, fields)
            break
    if selected is None:
        return text, None

    match, fields = selected
    worsened = _worsen_mode(_mode(action_match.group(2) or ""))

    def strip_same_player_tags(tag_match):
        tag_head, tag_fields = _parse_fields(tag_match.group(1))
        if str(tag_fields.get("PLAYER") or "") == key and tag_head in {"ROLL", "PAYOFF"}:
            return ""
        return tag_match.group(0)

    stripped = _WEAKNESS_RE.sub(strip_same_player_tags, text)
    if worsened is None:
        return stripped, None

    complication = _clean(fields.get("COMPLICATION"), 180) or f"слабость «{pending.get('weakness')}» мешает действию"
    guarded = _set_mode_in_action(stripped, worsened)
    return guarded, {"player": user_id, "complication": complication, "mode": worsened}


def _active_clock(session, kind):
    from AI import dnd_scene_clocks as clocks

    clocks._ensure(session)
    for row in session.scene_clocks.values():
        if str(row.get("kind") or "").upper() == str(kind).upper() and not row.get("full"):
            return row
    return None


def _apply_payoff_cost(session, user_id, fields):
    from AI import dnd_conditions as conditions
    from AI import dnd_scene_clocks as clocks

    cost = str(fields.get("COST") or "").upper()
    complication = _clean(fields.get("COMPLICATION"), 180)
    if cost == "DANGER_PLUS_1":
        row = _active_clock(session, "DANGER")
        if row is not None:
            old = int(row.get("value", 0) or 0)
            clock_id = str(row.get("id") or "")
            notice = clocks._delta_clock(
                session,
                {"ID": clock_id, "DELTA": "1", "CAUSE": complication or "осложнение слабости"},
            )
            current = (getattr(session, "scene_clocks", {}) or {}).get(clock_id) or {}
            applied = int(current.get("value", 0) or 0) > old
            return applied, notice if applied else None
        threat = getattr(session, "threat", None)
        if isinstance(threat, dict) and threat.get("name"):
            old = int(threat.get("level", 0) or 0)
            maximum = max(1, int(threat.get("max", 6) or 6))
            if old < maximum:
                threat["level"] = old + 1
                return True, (
                    f"⚠️ {threat.get('name')}: {old + 1}/{maximum} — "
                    f"{complication or 'осложнение слабости'}."
                )
        return False, None

    if cost == "PROGRESS_MINUS_1":
        row = _active_clock(session, "PROGRESS")
        if row is None or int(row.get("value", 0) or 0) <= 0:
            return False, None
        old = int(row.get("value", 0) or 0)
        clock_id = str(row.get("id") or "")
        notice = clocks._delta_clock(
            session,
            {"ID": clock_id, "DELTA": "-1", "CAUSE": complication or "осложнение слабости"},
        )
        current = (getattr(session, "scene_clocks", {}) or {}).get(clock_id) or {}
        applied = int(current.get("value", 0) or 0) < old
        return applied, notice if applied else None

    if cost == "CONDITION":
        effect = str(fields.get("EFFECT") or "").upper()
        if effect not in conditions.EFFECTS:
            return False, None
        conditions._ensure(session)
        existing = (session.conditions or {}).get(str(int(user_id)), []) or []
        if any(str(row.get("effect") or "").upper() == effect for row in existing):
            return False, None
        name = _clean(fields.get("NAME"), 80) or "осложнение слабости"
        clear = _clean(fields.get("CLEAR"), 180) or "осмысленно устранить причину или получить помощь"
        payload = {
            "PLAYER": str(int(user_id)),
            "NAME": name,
            "EFFECT": effect,
            "CLEAR": clear,
        }
        if str(fields.get("USES") or "").isdigit():
            payload["USES"] = str(fields["USES"])
        elif str(fields.get("SCENES") or "").isdigit():
            payload["SCENES"] = str(fields["SCENES"])
        elif effect == "NEXT_ROLL_DISADVANTAGE":
            payload["USES"] = "1"
        elif effect != "ACTION_TO_CLEAR":
            payload["SCENES"] = "1"
        notice = conditions._add(session, payload)
        return bool(notice), notice
    return False, None


def apply_weakness_metadata(session, original_text, cleaned, notices):
    _ensure(session)
    extra = []
    for match in _WEAKNESS_RE.finditer(str(original_text or "")):
        head, fields = _parse_fields(match.group(1))
        if head != "PAYOFF":
            continue
        player = str(fields.get("PLAYER") or "")
        if not player.isdigit():
            continue
        user_id = int(player)
        if player not in session.pending_weakness_invocations or not _can_earn(session, user_id):
            continue
        applied, cost_notice = _apply_payoff_cost(session, user_id, fields)
        if not applied:
            continue
        if cost_notice:
            extra.append(cost_notice)
        award = _award_luck(
            session,
            user_id,
            fields.get("COMPLICATION"),
            prevalidated=True,
        )
        if award:
            extra.append(award)
        session.pending_weakness_invocations.pop(player, None)
    return _WEAKNESS_RE.sub("", str(cleaned or "")).strip(), list(notices or []) + extra


def _luck_line(session, user_id):
    _ensure(session)
    key = str(int(user_id))
    tokens = int(session.luck_tokens.get(key, 0) or 0)
    earned = int(session.weakness_luck_earned.get(key, 0) or 0)
    return f"🍀 Жетон удачи: {tokens}/{MAX_LUCK_TOKENS} · заработано {earned}/{MAX_LUCK_EARNED_PER_ADVENTURE} за приключение"


def _reset_adventure(session):
    _ensure(session)
    session.pending_weakness_invocations = {}
    for row in (getattr(session, "participants", {}) or {}).values():
        try:
            key = str(int(row.get("user_id")))
        except (TypeError, ValueError):
            continue
        session.luck_tokens[key] = 0
        session.weakness_luck_earned[key] = 0
        session.weakness_luck_spent[key] = 0


class WeaknessLuckMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        from AI import dnd

        chat = getattr(event, "chat", None)
        user = getattr(event, "from_user", None)
        if chat is None or user is None:
            return await handler(event, data)
        session = dnd.dnd_sessions.get(int(chat.id))
        if not session or not dnd._is_participant_mode(session):
            return await handler(event, data)

        _ensure(session)
        user_id = int(user.id)
        text = getattr(event, "text", None) or getattr(event, "caption", None) or ""
        normalized = _normalize_text(text)

        if session.state == "WAITING_ROLL" and normalized in _LUCK_COMMANDS:
            roll = getattr(session, "pending_roll", None) or {}
            if not dnd._can_user_act(session, user_id, roll.get("target_user_ids") or []):
                await event.answer("Этот бросок не твой.")
                return None
            key = str(user_id)
            if int(session.luck_tokens.get(key, 0) or 0) <= 0:
                await event.answer("🍀 Жетона удачи нет.")
                return None
            improved = _improve_mode(roll.get("mode", "NORMAL"))
            if improved is None:
                await event.answer("🍀 Бросок уже с преимуществом — жетон не трачу.")
                return None
            if not _spend_luck(session, user_id):
                await event.answer("🍀 Жетона удачи нет.")
                return None
            roll["mode"] = improved
            session.pending_roll = roll
            dnd.persist_dnd_sessions()
            await event.answer(
                "🍀 Жетон потрачен: "
                + ("помеха снята. Теперь обычный бросок." if improved == "NORMAL" else "теперь бросок с преимуществом.")
            )
            return None

        if session.state != "WAITING_ACTION" or not _action_reply(dnd, event):
            return await handler(event, data)

        invoked = _weakness_invoked(text)
        weakness = _profile_weakness(session, user_id) if invoked else ""
        reward_available = bool(invoked and weakness and _can_earn(session, user_id))

        result = await handler(event, data)

        current = dnd.dnd_sessions.get(int(chat.id))
        if not current or getattr(current, "state", None) != "WAITING_ACTION":
            return result
        _ensure(current)
        key = str(user_id)
        action_row = (getattr(current, "pending_actions", {}) or {}).get(key)
        if not isinstance(action_row, dict):
            return result
        if reward_available:
            current.pending_weakness_invocations[key] = {
                "weakness": weakness,
                "action": _clean(text, 300),
                "scene": int(getattr(current, "scene_count", 0) or 0),
            }
            action_row["action"] = _append_weakness_note(action_row.get("action"), weakness)
        else:
            current.pending_weakness_invocations.pop(key, None)
        dnd.persist_dnd_sessions()
        if invoked and weakness and not reward_available:
            await event.answer("🍀 Слабость сыграть можно, но новый жетон сейчас не помещается в лимит.")
        return result


def install_dnd_weakness_luck(dnd, dnd_router, *, state_policy, metadata_policy):
    from AI import dnd_campaign as campaign
    from AI import dnd_combat as combat
    from AI import dnd_state_commands as state_commands

    if getattr(dnd, "_upupa_dnd_weakness_luck_installed", False):
        return

    state_policy.add_ensure_hook(_ensure)
    state_policy.add_state_field("luck_tokens", lambda s: dict(getattr(s, "luck_tokens", {}) or {}))
    state_policy.add_state_field(
        "weakness_luck_earned",
        lambda s: dict(getattr(s, "weakness_luck_earned", {}) or {}),
    )
    state_policy.add_state_field(
        "weakness_luck_spent",
        lambda s: dict(getattr(s, "weakness_luck_spent", {}) or {}),
    )
    state_policy.add_state_field(
        "pending_weakness_invocations",
        lambda s: copy.deepcopy(getattr(s, "pending_weakness_invocations", {}) or {}),
    )
    state_policy.add_restore_hook(_restore)
    metadata_policy.add_postprocessor(apply_weakness_metadata)

    original_context = campaign._campaign_context

    def campaign_context(dnd_module, session):
        text = original_context(dnd_module, session)
        if not dnd_module._is_participant_mode(session):
            return text
        lines = []
        for key in (getattr(session, "participants", {}) or {}):
            weakness = _profile_weakness(session, int(key))
            if weakness:
                lines.append(f"- ID {key}: слабость «{weakness}»; {_luck_line(session, int(key))}")
        return text + (("\n" + WEAKNESS_LUCK_RULES + "\nТЕКУЩАЯ УДАЧА:\n" + "\n".join(lines)) if lines else "")

    campaign._campaign_context = campaign_context

    original_start_story = campaign._start_story

    async def start_story(dnd_module, bot, session, plot, continuation=False, message=None):
        _reset_adventure(session)
        dnd_module.persist_dnd_sessions()
        return await original_start_story(dnd_module, bot, session, plot, continuation, message)

    campaign._start_story = start_story

    original_generate = dnd.generate_session_response

    async def generate_session_response(session, prompt):
        _ensure(session)
        enriched = str(prompt or "")
        context = _pending_context(session)
        if context and getattr(session, "state", None) == "RESOLVING":
            enriched += context
        return await original_generate(session, enriched)

    dnd.generate_session_response = generate_session_response

    original_parse = dnd.parse_and_execute_turn

    async def parse_turn(bot, chat_id, response):
        session = dnd.dnd_sessions.get(chat_id)
        if not session or not dnd._is_participant_mode(session):
            return await original_parse(bot, chat_id, response)

        _ensure(session)
        guarded, roll_reward = _apply_roll_tag(session, response)
        result = await original_parse(bot, chat_id, guarded)

        if roll_reward:
            pending_roll = getattr(session, "pending_roll", None)
            if isinstance(pending_roll, dict):
                targets = [int(x) for x in pending_roll.get("target_user_ids") or []]
                if int(roll_reward["player"]) in targets:
                    pending_roll["weakness_luck_reward"] = dict(roll_reward)
                    dnd.persist_dnd_sessions()
        session.pending_weakness_invocations = {}
        dnd.persist_dnd_sessions()
        return result

    dnd.parse_and_execute_turn = parse_turn

    original_resolve_roll = combat._resolve_player_roll

    async def resolve_player_roll(dnd_module, message, session):
        pending = getattr(session, "pending_roll", None) or {}
        reward = copy.deepcopy(pending.get("weakness_luck_reward")) if isinstance(pending, dict) else None
        eligible = False
        if isinstance(reward, dict):
            eligible = (
                int(reward.get("player", -1)) == int(message.from_user.id)
                and dnd_module._can_user_act(
                    session,
                    int(message.from_user.id),
                    pending.get("target_user_ids") or [],
                )
            )
        await original_resolve_roll(dnd_module, message, session)
        if eligible and getattr(session, "pending_roll", None) is not pending:
            award = _award_luck(session, int(message.from_user.id), reward.get("complication"))
            if award:
                dnd_module.persist_dnd_sessions()
                await message.answer(award)

    combat._resolve_player_roll = resolve_player_roll

    original_render_hero = state_commands.render_hero

    def render_hero(dnd_module, chat_id, user_id, user_name=None):
        text = original_render_hero(dnd_module, chat_id, user_id, user_name)
        session = dnd_module.dnd_sessions.get(int(chat_id))
        if session is None or not dnd_module._is_participant_mode(session):
            return text
        return text + "\n\n" + _luck_line(session, user_id)

    state_commands.render_hero = render_hero

    original_archive = campaign._archive_campaign

    def archive_campaign(dnd_module, session, finale, epilogue):
        original_archive(dnd_module, session, finale, epilogue)
        chat = campaign._chat_history(session.chat_id, True)
        campaigns = chat.get("campaigns") or []
        if campaigns:
            campaigns[-1]["weakness_luck"] = {
                "tokens": dict(getattr(session, "luck_tokens", {}) or {}),
                "earned": dict(getattr(session, "weakness_luck_earned", {}) or {}),
                "spent": dict(getattr(session, "weakness_luck_spent", {}) or {}),
            }
            campaign._save_archive(dnd_module)

    campaign._archive_campaign = archive_campaign

    dnd_router.message.outer_middleware(WeaknessLuckMiddleware())
    dnd._upupa_dnd_weakness_luck_installed = True


__all__ = [
    "WEAKNESS_LUCK_MARKER",
    "WEAKNESS_LUCK_RULES",
    "MAX_LUCK_TOKENS",
    "MAX_LUCK_EARNED_PER_ADVENTURE",
    "apply_weakness_metadata",
    "install_dnd_weakness_luck",
]
