"""Narrative continuity and combat-pacing guards for participant DnD."""
from __future__ import annotations

import difflib
import logging
import re


WORLD_VARIETY_RULES = (
    "ПРОСТРАНСТВО СЦЕН: заметное большинство эпизодов — ориентир около двух из трёх, когда причинность позволяет — "
    "разворачивай под открытым небом или в больших открытых пространствах. Чередуй улицы, площади, крыши, дворы, рынки, "
    "поля, лесные просеки, берега, мосты, сады, порты, карьеры, развалины, склоны, ярмарки и другие места, где окружение "
    "можно активно использовать в действии. Не делай палатки, шатры, вентиляцию, тесные коридоры, шахты, подвалы и "
    "технические тоннели локацией по умолчанию: используй их только когда туда реально ведут действия игроков или сюжет. "
    "Не телепортируй партию наружу ради правила — меняй пространство естественно при переходе между сценами."
)

SCENE_MOMENTUM_RULES = (
    "СМЕНА ДЕКОРАЦИЙ И ЭКШЕН: сравни ближайшую сцену с 2–3 предыдущими. Если партия уже два эпизода подряд находится "
    "в одном и том же типе пространства или решает один и тот же вид проблемы, при ближайшей естественной возможности "
    "измени хотя бы два элемента: место, масштаб, цель, способ давления или доступное окружение. Экшен создавай прежде "
    "всего через движение и изменение ситуации: погоню, толпу, пожар, обвал, шторм, транспорт, высоту, публичное событие, "
    "соревнование, дедлайн, рушащийся путь, перемещение по городу или природе. Не подменяй динамику очередной волной врагов. "
    "Чередуй архетипы угроз: социальные, человеческие, природные, абсурдные, средовые, загадочные и боевые. Роботы, дроны, "
    "киберживотные, стража и одинаковые монстры не являются универсальным источником конфликта и не должны повторяться "
    "сцену за сценой без прямой причинности. Новая декорация должна вытекать из действий игроков или развития сюжета, "
    "а не быть случайным телепортом."
)

PACING_RULES = (
    "Темп партии важнее затяжной боёвки. Между двумя решениями игроков допускай не более одной непосредственной "
    "атаки NPC. После неё верни инициативу партии: INPUT, осмысленный CHECK или короткий выбор. Не создавай новые волны "
    "врагов только чтобы продлить бой. Схватка должна продвигать сюжет, открывать путь, менять обстановку или быстро "
    "заканчиваться, а не превращаться в отдельную бесконечную арену. Решение, выбранное игроками в только что завершённом "
    "опросе, обязательно: сначала покажи прямое следствие именно этого решения; нельзя подменять его старым действием. "
    + WORLD_VARIETY_RULES
    + SCENE_MOMENTUM_RULES
)

POLL_DECISION_GUARD = (
    "ВАЖНО: результат голосования ниже — обязательное текущее решение партии. Следующая сцена ДОЛЖНА начинаться с его "
    "реализации или прямого немедленного последствия. Не возвращайся вместо этого к предыдущему плану, предмету, генератору, "
    "врагу или локации только потому, что они ярче в старой истории."
)

_ACTION_RE = re.compile(r"\[ACTION:([^;\]]+)(?:;[^\]]*)?\]", re.I | re.S)
_ENEMY_ATTACK_RE = re.compile(r"\[ACTION:ENEMY_ATTACK(?:;[^\]]*)?\]", re.I | re.S)
_POLL_ACTION_RE = re.compile(r"\[ACTION:POLL(?P<body>[^\]]*)\]", re.I | re.S)
_PLAYER_WINDOW_ACTIONS = {"INPUT", "POLL", "ROLL", "PLAYER_ATTACK", "CINEMATIC_ATTACK"}
_POLL_CONTINUATION_KINDS = {"POLL_CONTINUATION", "POLL_REPEAT_CORRECTION"}


def _normalized_text(value) -> str:
    return " ".join(
        re.findall(r"[a-zа-яё0-9]+", str(value or "").casefold().replace("ё", "е"))
    )


def _scene_similarity(left, right) -> float:
    a, b = _normalized_text(left), _normalized_text(right)
    if not a or not b:
        return 0.0
    sequence = difflib.SequenceMatcher(None, a, b).ratio()
    wa, wb = set(a.split()), set(b.split())
    union = wa | wb
    jaccard = len(wa & wb) / len(union) if union else 0.0
    return max(sequence, jaccard)


def _poll_payload(response: str) -> tuple[str, list[str]] | None:
    text = str(response or "")
    match = _POLL_ACTION_RE.search(text)
    if not match:
        return None
    body = match.group("body") or ""
    marker = re.search(r"(?:^|;)OPTIONS:(.*)$", body, re.I | re.S)
    if not marker:
        return None
    options = [item.strip() for item in marker.group(1).split(";") if item.strip()]
    if len(options) < 2:
        return None
    scene = _POLL_ACTION_RE.sub("", text, count=1).strip()
    return scene, options


def is_repeated_resolved_poll(response: str, resolved_poll) -> bool:
    if not isinstance(resolved_poll, dict):
        return False
    payload = _poll_payload(response)
    if payload is None:
        return False
    scene, options = payload
    previous_options = [
        str(item).strip() for item in (resolved_poll.get("options") or []) if str(item).strip()
    ]
    if len(previous_options) < 2:
        return False
    normalized_options = sorted(_normalized_text(item) for item in options)
    normalized_previous = sorted(_normalized_text(item) for item in previous_options)
    if normalized_options != normalized_previous:
        return False
    previous_scene = str(resolved_poll.get("scene_text") or "").strip()
    return _scene_similarity(scene, previous_scene) >= 0.58


def _repeat_poll_correction_prompt(resolved_poll: dict, repeated_response: str) -> str:
    return (
        "СЛУЖЕБНАЯ КОРРЕКЦИЯ. Ты повторил голосование, которое партия уже завершила. "
        "Не показывай тот же выбор снова и не переигрывай предыдущую сцену. "
        "Начни с нового прямого последствия уже принятого решения и продвинь историю. "
        "Если нужен новый выбор, он должен быть про ДРУГУЮ ситуацию с другими вариантами; "
        "иначе верни INPUT или уместный ROLL. До 100 слов.\n"
        f"УЖЕ РАЗРЕШЁННЫЙ ИТОГ: {resolved_poll.get('outcome') or 'решение принято'}\n"
        f"ПРЕДЫДУЩАЯ СЦЕНА: {resolved_poll.get('scene_text') or 'не указана'}\n"
        f"ОШИБОЧНЫЙ ПОВТОР: {str(repeated_response or '')[:1200]}"
    )


def _force_repeat_poll_to_input(response: str) -> str:
    return _POLL_ACTION_RE.sub("[ACTION:INPUT]", str(response or ""), count=1)


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
    scenes = [str(x).strip() for x in (getattr(session, "scene_log", None) or [])[-3:] if str(x).strip()]
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
        if session:
            pending = getattr(session, "pending_generated_result", {}) or {}
            source_kind = str(pending.get("source_request_kind") or "").upper()
            resolved_poll = getattr(session, "last_resolved_poll", None)
            if (
                source_kind in _POLL_CONTINUATION_KINDS
                and is_repeated_resolved_poll(response, resolved_poll)
            ):
                logging.warning(
                    "DnD blocked repeated resolved poll chat_id=%s source_kind=%s",
                    chat_id,
                    source_kind,
                )
                if source_kind == "POLL_CONTINUATION":
                    from AI.dnd_result_recovery import (
                        continue_pending_generation,
                        transition_to_generation_request,
                    )

                    correction_prompt = _repeat_poll_correction_prompt(
                        resolved_poll,
                        response,
                    )
                    if transition_to_generation_request(
                        session,
                        correction_prompt,
                        kind="POLL_REPEAT_CORRECTION",
                    ):
                        dnd.persist_dnd_sessions()
                        completed = await continue_pending_generation(dnd, bot, session)
                        if not completed:
                            raise RuntimeError(
                                "DnD repeated poll correction generation failed"
                            )
                        return None
                response = _force_repeat_poll_to_input(response)

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
    "WORLD_VARIETY_RULES",
    "SCENE_MOMENTUM_RULES",
    "PACING_RULES",
    "POLL_DECISION_GUARD",
    "guard_enemy_attack_chain",
    "is_repeated_resolved_poll",
    "install_dnd_pacing",
]
