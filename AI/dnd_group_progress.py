"""Guard participant group turns against acknowledgment-only stalls."""
from __future__ import annotations

import logging
import re


GROUP_PROGRESS_MARKER = "ПРОГРЕСС ГРУППОВОГО ХОДА DND УПУПЫ"
GROUP_ACTION_KIND = "GROUP_ACTION_CONTINUATION"
GROUP_PROGRESS_CORRECTION_KIND = "GROUP_PROGRESS_CORRECTION"

_ACTION_RE = re.compile(r"\[ACTION:([^;\]]+)([^\]]*)\]", re.I | re.S)
_INSPECTION_RE = re.compile(
    r"\b(осмотр|осматри|огляд|ищ|поиск|смотр|прислуш|изуч|развед|провер|обыск|замет)\w*",
    re.I,
)
_DEFER_RE = re.compile(
    r"\b(осматривай|осматривайтесь|думай|думайте|ищи|ищите|смотри|смотрите|"
    r"решай|решайте|продолжай|продолжайте|можете\s+осмотреть)\w*",
    re.I,
)
_CONCRETE_FEEDBACK_RE = re.compile(
    r"\b(замеч|обнаруж|наход|увид|видн|слыш|услыш|чувству|запах|след|улика|"
    r"двер|проход|надпис|щель|шорох|пусто|стен|предмет|оказыва|выясн|понима|"
    r"получ|добира|вход|выход|откры|закры|лома|чин|отвеч|отказыва|соглаша|"
    r"бер[её]т|теря|меня|сдвига|переход|срабаты|уда[её]т|провал|останав|"
    r"появ|исчез|достига|узна|реагир)\w*",
    re.I,
)
_ACK_ONLY_RE = re.compile(
    r"\b(принято|понял|да-да|делаете|пытаетесь|начинаете|продолжаете|можете|"
    r"думайте|решайте|что\s+дальше|ход\s+за\s+вами|ваш\s+ход|действуйте)\b",
    re.I,
)

GROUP_PROGRESS_RULES = f"""{GROUP_PROGRESS_MARKER}.
Каждый коллективный ход должен дать игрокам наблюдаемую обратную связь на их заявки.
Если несколько героев осматриваются, ищут, слушают или изучают окружение, нельзя просто подтвердить намерение словами
«осматривайтесь», «думайте», «ищите» и снова открыть тот же общий INPUT.
Если бросок не нужен — назови конкретную обнаруженную деталь, отсутствие находок, изменение позиции, реакцию NPC или
другой фактический результат. Если исход неопределён и провал имеет цену — назначь адресный ROLL тому, кто это делает.
Не допускай третьего подряд общего INPUT без материального изменения информации, цели, позиции или ситуации партии.
Продвижение не обязано быть экшеном или катастрофой: новая улика, понятная пустота, разговор, путь, смена цели или
конкретная реакция мира тоже считаются полноценным прогрессом.
""".strip()


def _ensure(session) -> None:
    try:
        session.group_input_streak = max(
            0,
            int(getattr(session, "group_input_streak", 0) or 0),
        )
    except (TypeError, ValueError):
        session.group_input_streak = 0


def _restore(session, data) -> None:
    row = data if isinstance(data, dict) else {}
    session.group_input_streak = row.get("group_input_streak", 0)
    _ensure(session)


def _action_kind(response: str) -> tuple[str | None, str]:
    match = _ACTION_RE.search(str(response or ""))
    if not match:
        return None, ""
    return match.group(1).upper(), match.group(2) or ""


def _response_body(response: str) -> str:
    return _ACTION_RE.sub("", str(response or ""), count=1).strip()


def _group_action_count(source_prompt: str) -> int:
    block = _group_action_block(source_prompt)
    return sum(1 for line in block.splitlines() if line.lstrip().startswith("- "))


def _minimum_resolution_words(source_prompt: str) -> int:
    count = max(1, _group_action_count(source_prompt))
    return min(100, 24 + 18 * count)


def _group_action_block(source_prompt: str) -> str:
    text = str(source_prompt or "")
    marker = "Игроки заявили действия одновременно:\n"
    if marker not in text:
        return ""
    tail = text.split(marker, 1)[1]
    for stop in (
        "\nСначала явно учти",
        "\nСначала учти",
        "\nРЕЖИССЁР СЦЕНЫ:",
        "\n\nДИСЦИПЛИНА",
    ):
        if stop in tail:
            tail = tail.split(stop, 1)[0]
    return tail.strip()[:2400]


def _is_untargeted_group_input(response: str) -> bool:
    action, suffix = _action_kind(response)
    return action == "INPUT" and "TARGETS:" not in suffix.upper()


def inspection_was_deferred(source_prompt: str, response: str) -> bool:
    actions = _group_action_block(source_prompt)
    if not actions or not _INSPECTION_RE.search(actions):
        return False
    if not _is_untargeted_group_input(response):
        return False

    body = _response_body(response)
    if _CONCRETE_FEEDBACK_RE.search(body):
        return False

    normalized = " ".join(body.split())
    return bool(_DEFER_RE.search(normalized)) or len(normalized) < 90


def group_resolution_was_noop(source_prompt: str, response: str) -> bool:
    """Detect acknowledgement/rephrasing that returns control without consequence."""
    actions = _group_action_block(source_prompt)
    if not actions or not _is_untargeted_group_input(response):
        return False

    body = _response_body(response)
    if _CONCRETE_FEEDBACK_RE.search(body):
        return False

    normalized = " ".join(body.split())
    if not normalized:
        return True

    words = re.findall(r"\b[\wЁёА-Яа-я-]+\b", normalized)
    minimum_words = _minimum_resolution_words(source_prompt)

    # A multi-actor group resolution that immediately returns INPUT with only a
    # handful of words cannot have shown concrete consequences for everyone.
    # This catches the live failure mode:
    # "Детектор рвёт когти..., Чудо за ним... Не тормозите" -> next group turn.
    if len(words) < minimum_words:
        return True

    return len(normalized) < 220 and bool(
        _ACK_ONLY_RE.search(normalized) or _DEFER_RE.search(normalized)
    )


def progress_correction_reason(session, pending: dict, response: str) -> str | None:
    source_kind = str(pending.get("source_request_kind") or "").upper()
    if source_kind != GROUP_ACTION_KIND:
        return None

    source_prompt = str(pending.get("source_prompt") or "")
    if inspection_was_deferred(source_prompt, response):
        return "inspection-without-feedback"
    if group_resolution_was_noop(source_prompt, response):
        return "group-action-without-consequence"

    _ensure(session)
    if (
        int(session.group_input_streak) >= 2
        and _is_untargeted_group_input(response)
    ):
        return "third-consecutive-group-input"
    return None


def _correction_prompt(pending: dict, response: str, reason: str) -> str:
    actions = _group_action_block(str(pending.get("source_prompt") or ""))
    return (
        "СЛУЖЕБНАЯ КОРРЕКЦИЯ ГРУППОВОГО ХОДА. Предыдущий ответ не продвинул игру. "
        "Не повторяй и не комментируй ошибочный ответ. Сначала РАЗРЕШИ каждую исходную заявку: "
        "для каждого участника явно покажи «действие -> последствие», а не просто перефразируй намерение. "
        "Для осмотра/поиска: если бросок не нужен — сообщи конкретную новую деталь, улику, отсутствие находок "
        "или другую фактическую обратную связь; если исход реально неопределён и провал имеет цену — дай адресный "
        "ACTION:ROLL соответствующему игроку. Нельзя отвечать только «осматривайтесь», «думайте», «ищите» и снова "
        "возвращать тот же общий ход. Если это уже третий общий INPUT подряд, материально измени ситуацию, цель, "
        "доступную информацию или позицию партии; ещё один общий INPUT допустим только после такого явного продвижения. "
        "Не добавляй случайную катастрофу ради темпа. До 100 слов.\n"
        f"ПРИЧИНА КОРРЕКЦИИ: {reason}\n"
        f"ИСХОДНЫЕ ЗАЯВКИ:\n{actions or 'см. текущий structured state'}\n"
        f"НЕУДАЧНЫЙ ОТВЕТ:\n{_response_body(response)[:1200]}"
    )


def _update_streak(session, pending: dict, response: str) -> None:
    _ensure(session)
    source_kind = str(pending.get("source_request_kind") or "").upper()
    if source_kind not in {GROUP_ACTION_KIND, GROUP_PROGRESS_CORRECTION_KIND}:
        return
    if _is_untargeted_group_input(response):
        session.group_input_streak += 1
    else:
        session.group_input_streak = 0


def install_dnd_group_progress(dnd, *, state_policy=None) -> None:
    """Install the group-progress gate outside all state-mutating parse wrappers."""
    if getattr(dnd, "_upupa_dnd_group_progress_installed", False):
        return

    if state_policy is not None:
        state_policy.add_ensure_hook(_ensure)
        state_policy.add_state_field(
            "group_input_streak",
            lambda session: int(getattr(session, "group_input_streak", 0) or 0),
        )
        state_policy.add_restore_hook(_restore)

    if GROUP_PROGRESS_MARKER not in dnd.DND_SYSTEM_PROMPT:
        dnd.DND_SYSTEM_PROMPT = (
            dnd.DND_SYSTEM_PROMPT.rstrip()
            + "\n\n"
            + GROUP_PROGRESS_RULES
        )

    original_parse = dnd.parse_and_execute_turn

    async def parse_turn(bot, chat_id, response):
        session = dnd.dnd_sessions.get(chat_id)
        pending = (
            dict(getattr(session, "pending_generated_result", {}) or {})
            if session is not None
            else {}
        )
        if session is not None:
            reason = progress_correction_reason(session, pending, response)
            if reason:
                from AI.dnd_result_recovery import (
                    continue_pending_generation,
                    transition_to_generation_request,
                )

                logging.warning(
                    "DnD correcting stalled group resolution chat_id=%s reason=%s streak=%s",
                    chat_id,
                    reason,
                    getattr(session, "group_input_streak", 0),
                )
                if transition_to_generation_request(
                    session,
                    _correction_prompt(pending, response, reason),
                    kind=GROUP_PROGRESS_CORRECTION_KIND,
                ):
                    dnd.persist_dnd_sessions()
                    completed = await continue_pending_generation(dnd, bot, session)
                    if not completed:
                        raise RuntimeError(
                            "DnD group progress correction generation failed"
                        )
                    return None

            _update_streak(session, pending, response)
            dnd.persist_dnd_sessions()

        return await original_parse(bot, chat_id, response)

    dnd.parse_and_execute_turn = parse_turn
    dnd._upupa_dnd_group_progress_installed = True


__all__ = [
    "GROUP_PROGRESS_RULES",
    "_minimum_resolution_words",
    "group_resolution_was_noop",
    "inspection_was_deferred",
    "progress_correction_reason",
    "install_dnd_group_progress",
]
