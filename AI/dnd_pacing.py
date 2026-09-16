"""Narrative continuity and combat-pacing guards for participant DnD."""
from __future__ import annotations

import logging
import re


PACING_RULES = (
    "Темп партии важнее затяжной боёвки. Между двумя решениями игроков допускай не более одной непосредственной "
    "атаки NPC. После неё верни инициативу партии: INPUT, осмысленный CHECK или короткий выбор. Не создавай новые волны "
    "врагов только чтобы продлить бой. Схватка должна продвигать сюжет, открывать путь, менять обстановку или быстро "
    "заканчиваться, а не превращаться в отдельную бесконечную арену. Решение, выбранное игроками в только что завершённом "
    "опросе, обязательно: сначала покажи прямое следствие именно этого решения; нельзя подменять его старым действием."
)

POLL_DECISION_GUARD = (
    "ВАЖНО: результат голосования ниже — обязательное текущее решение партии. Следующая сцена ДОЛЖНА начинаться с его "
    "реализации или прямого немедленного последствия. Не возвращайся вместо этого к предыдущему плану, предмету, генератору, "
    "врагу или локации только потому, что они ярче в старой истории."
)

_ACTION_RE = re.compile(r"\[ACTION:([^;\]]+)(?:;[^\]]*)?\]", re.I | re.S)
_ENEMY_ATTACK_RE = re.compile(r"\[ACTION:ENEMY_ATTACK(?:;[^\]]*)?\]", re.I | re.S)
_PLAYER_WINDOW_ACTIONS = {"INPUT", "POLL", "ROLL", "PLAYER_ATTACK", "CINEMATIC_ATTACK"}


def guard_enemy_attack_chain(response: str, already_attacked: bool) -> tuple[str, bool, bool]:
    """Allow one enemy attack, then turn a consecutive one into player agency."""
    text = str(response or "")
    match = _ACTION_RE.search(text)
    if not match:
        return text, already_attacked, False
    action = match.group(1).upper()
    if action == "ENEMY_ATTACK":
        if already_attacked:
            return _ENEMY_ATTACK_RE.sub("[ACTION:INPUT]", text, count=1), False, True
        return text, True, False
    if action in _PLAYER_WINDOW_ACTIONS:
        return text, False, False
    return text, already_attacked, False


def _recent_scene_context(session) -> str:
    scenes = [str(x).strip() for x in (getattr(session, "scene_log", None) or [])[-2:] if str(x).strip()]
    if not scenes:
        return ""
    body = "\n---\n".join(scenes)
    return (
        "\nПОСЛЕДНИЕ ПОДТВЕРЖДЁННЫЕ СЦЕНЫ (это уже случилось; не переигрывай их):\n"
        + body[-1800:]
    )


def install_dnd_pacing(dnd) -> None:
    from AI import dnd_campaign as campaign

    if getattr(dnd, "_upupa_dnd_pacing_installed", False):
        return

    if PACING_RULES not in dnd.DND_SYSTEM_PROMPT:
        dnd.DND_SYSTEM_PROMPT = dnd.DND_SYSTEM_PROMPT.rstrip() + "\n\n" + PACING_RULES

    original_context = campaign._campaign_context

    def campaign_context(dnd_module, session):
        return original_context(dnd_module, session) + _recent_scene_context(session) + "\nТЕМП И ПРИЧИННОСТЬ:\n" + PACING_RULES

    campaign._campaign_context = campaign_context

    original_generate = dnd.generate_session_response

    async def generate(session, prompt):
        value = str(prompt or "")
        if "Результат:" in value and ("Выбор сделан:" in value or "Тишина" in value):
            value += "\n\n" + POLL_DECISION_GUARD
        return await original_generate(session, value)

    dnd.generate_session_response = generate

    original_parse = dnd.parse_and_execute_turn

    async def parse_turn(bot, chat_id, response):
        session = dnd.dnd_sessions.get(chat_id)
        if session and dnd._is_participant_mode(session):
            guarded, next_flag, blocked = guard_enemy_attack_chain(
                response,
                bool(getattr(session, "_enemy_attack_since_player_window", False)),
            )
            session._enemy_attack_since_player_window = next_flag
            if blocked:
                logging.warning("DnD blocked consecutive enemy attack chat_id=%s", chat_id)
            response = guarded
        return await original_parse(bot, chat_id, response)

    dnd.parse_and_execute_turn = parse_turn
    dnd._upupa_dnd_pacing_installed = True


__all__ = [
    "PACING_RULES",
    "POLL_DECISION_GUARD",
    "guard_enemy_attack_chain",
    "install_dnd_pacing",
]
