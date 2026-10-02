"""Fail-fast text generation for DnD without long Gemini retry storms."""
from __future__ import annotations

import asyncio
import logging
import re
import threading
import time
from typing import Any

from google import genai
from google.genai import types as genai_types

from core.settings import (
    GEMINI_KEYS_POOL,
    GROQ_API_KEY,
    MODEL_QUEUE_DEFAULT,
    MODEL_QUEUE_SPECIAL,
    SPECIAL_CHAT_ID,
)
from infrastructure.ai.clients import groq_ai
from infrastructure.ai.execution import ai_execution_lane, ai_feature, run_ai_provider_call
from infrastructure.ai.execution import _extract_token_usage
from AI.dnd_ai_budget import (
    DndAIBudgetExhausted, DndCallGuard, ai_call_guard, ai_call_purpose,
    current_ai_purpose, current_call_guard, current_turn_budget, dnd_turn_budget,
)


DND_GEMINI_HTTP_TIMEOUT_MS = 12_000
DND_GEMINI_GOVERNOR_TIMEOUT_SECONDS = 15.0
DND_GEMINI_QUEUE_TIMEOUT_SECONDS = 5.0
DND_GEMINI_ATTEMPTS = 2
DND_GEMINI_CIRCUIT_SECONDS = 45.0
DND_GEMINI_INPUT_MAX_CHARS = 20_000
DND_GEMINI_SYSTEM_MAX_CHARS = 8_000
DND_GEMINI_RECENT_HISTORY_MAX_CHARS = 4_000
DND_GEMINI_CURRENT_PROMPT_MAX_CHARS = 8_000
DND_GEMINI_RECENT_MESSAGES = 4
DND_GROQ_FALLBACK_TIMEOUT_SECONDS = 18.0
DND_GROQ_HTTP_TIMEOUT_SECONDS = 15.0
DND_GROQ_RATE_LIMIT_RETRY_CAP_SECONDS = 6.0
DND_GROQ_RATE_LIMIT_RETRY_PADDING_SECONDS = 0.15
DND_AUX_HTTP_TIMEOUT_MS = 12_000
DND_AUX_GOVERNOR_TIMEOUT_SECONDS = 14.0
DND_AUX_QUEUE_TIMEOUT_SECONDS = 2.0
DND_AUX_GROQ_TIMEOUT_SECONDS = 10.0
DND_AUX_GROQ_MAX_PROMPT_CHARS = 7_000
DND_AUX_GROQ_MAX_TOKENS = 500
DND_AUX_GROQ_TEMPERATURE = 0.1
DND_SERVICE_INPUT_MAX_CHARS = 5_000
DND_SERVICE_MAX_TOKENS = 500
DND_COMPACT_SYSTEM_MAX_CHARS = 10_000
# Groq's on-demand tier for the current fallback model is capped at 8k TPM.
# Cyrillic DnD history can tokenize much denser than Latin text, so keep the
# normal fallback comfortably below that ceiling and retry once even smaller.
DND_FALLBACK_PROMPT_MAX_CHARS = 12_000
DND_FALLBACK_RETRY_PROMPT_MAX_CHARS = 7_000
DND_FALLBACK_RECENT_MESSAGES = 4
DND_GROQ_FALLBACK_MAX_TOKENS = 1800
DND_GROQ_FALLBACK_RETRY_MAX_TOKENS = 1800
DND_GROQ_FALLBACK_TEMPERATURE = 0.55
DND_FALLBACK_CONTINUITY_GUARD = (
    "АВАРИЙНЫЙ РЕЖИМ DND. Блок CURRENT REQUEST ниже — главный источник истины. "
    "Сначала разреши заявленные действия игроков и продолжи ровно текущую сцену. "
    "Не вводи нового врага, локацию, катастрофу или сюжетную ветку только ради разнообразия "
    "или указания РЕЖИССЁР СЦЕНЫ. РЕЖИССЁР СЦЕНЫ задаёт подачу, а не заменяет причинность. "
    "Не игнорируй действия игроков даже если часть старой истории сокращена."
)
DND_DIRECT_CONTINUITY_GUARD = (
    "DND MEMORY V2. CURRENT REQUEST содержит актуальный structured state и важнее старой истории. "
    "Используй несколько последних реплик только для литературной связности; не отменяй ими "
    "подтверждённые позиции, HP, предметы, состояния и последствия."
)


_state_lock = threading.Lock()
_key_cursor = 0
_gemini_circuit_until: dict[int | None, float] = {}
_client_cache: dict[tuple[str, int], genai.Client] = {}


class DndGeminiCircuitOpen(RuntimeError):
    """Gemini was recently degraded, so DnD skips it briefly."""


_COMPACT_CORE = """ВЕДУЩИЙ УПУПЫ. Текущая заявка и подтверждённая MEMORY V2 важнее старого текста.
Не меняй результаты кубиков, HP, позиции, вещи, ресурсы, смерть и уже выполненные действия.
Разрешай сначала каждую текущую заявку: новое конкретное последствие, не подтверждение получения сообщения.
Не выдумывай успех, поражение или лут для ремонта ответа. Сохраняй способ и исполнителя действия.
Пиши грамотно, живо, с ситуационным сарказмом; не повторяй обзывалку, не оскорбляй защищённые признаки.
Никаких симуляций, разрывов реальности, двойников и мета-твистов без основания сюжета.
MODE:NORMAL штатный; преимущество/помеха только по конкретному фактору. RISK:LOW/MEDIUM/HIGH/EXTREME даёт DC 8/11/14/17.
Лечить, тратить special/удачу и одноразовые ресурсы решает их владелец; расчёт и расход выполняет код.
Позицию подтверждай [POSITION:SET;PLAYER:id;LOCATION:место;DETAIL:деталь], не перемещай героя без действия.
Цель завершена только по фактам: [MISSION:SUCCESS;EVIDENCE:что выполнено] или [MISSION:FAILURE;EVIDENCE:причина], затем [ACTION:END].
Время приключения и очередь задаёт сохранённое состояние; лимит сцен сам по себе не победа.
THREAT совместимости: [THREAT:опасность;DELTA:1;CAUSE:факт]. Репутация [REP:ADD;PLAYER:id;TEXT:заслуженная репутация].
Новые факты только из текущих последствий, не из примеров. Неизвестная позиция [POSITION:CLEAR;PLAYER:id].
В значимой сцене установи 2–4 интерактивных объекта: [SCENE:UPSERT;ID:объект;NAME:имя;STATE:состояние;DETAIL:возможность]. Не создавай смертельное удобство задним числом.
""".strip()
_COMPACT_MECHANICS = {
    "battle": """БОЙ И ОКРУЖЕНИЕ. Живого врага нельзя победить описанием в обход HP.
Редкий смертельный трюк только с существующим доступным OBJECT, не обычным ударом:
[ACTION:CINEMATIC_ATTACK;TARGETS:id;ENEMY:имя;POWER:HIGH;HP:28;AC:14;ABILITY:STR;DC:16;MODE:NORMAL;OBJECT:id;METHOD:физический трюк;REASON:действие].
Провал трюка не снимает HP. До броска не описывай попадание/победу.
[SCENE:UPSERT;ID:объект;NAME:имя;STATE:состояние;DETAIL:возможность], [SCENE:UPDATE;ID:объект;STATE:изменение;AVAILABLE:0].
Не добавляй удобный смертельный объект задним числом. Намерение [INTENT:SET;ENEMY:имя;ACTION:угроза;TARGETS:id;DANGER:DEADLY;DETAIL:факт].
Дай окно INPUT/POLL вмешательства; сначала разреши действие/бросок, потом [INTENT:CLEAR;REASON:сорвано] или последствие. Не замещай сорванное намерение бесплатной атакой.""",
    "items": """ПРЕДМЕТЫ. DESCRIPTION кратко: что это и почему принадлежит герою. QTY 1..25, уникальный artifact QTY:1; одинаковое NAME стакается.
TRAIT/BONUS -9..9 за единицу — шуточное свойство, не бонус d20. EFFECT — короткое свойство без тегов/точек с запятой.
Редкий тематический artifact может STAT:STR/DEX/CON/INT/WIS/CHA;STAT_BONUS:1/2; код применит один бонус, не дублируй ADVANTAGE.
Для активной вещи MECH:ADVANTAGE_MOVE/ADVANTAGE_SOCIAL/ADVANTAGE_PERCEPTION/ADVANTAGE_COMBAT/CLEAR_CONDITION/CREATE_EXIT/CLOCK_PUSH/CLOCK_COMPLETE/CLEAR_ACCUSATION;
CHARGES:1..3;REQUIRE:NONE/CONFESS_FEAR;COST:NONE/DANGER_PLUS_1/DROP_ITEM/GAIN_CONDITION/NEXT_ACCUSATION_SELF.
GAIN_CONDITION задаёт COST_NAME/COST_EFFECT/COST_CLEAR. Для шкалы POWER:1..3;CLOCK_ID или CLOCK_KIND. Не выдумывай второй эффект.
Долг обвинения храни до [ITEMFACT:RESOLVE;PLAYER:id;KIND:NEXT_ACCUSATION_SELF]. Передача/использование проверяются кодом.""",
    "clocks": """ШКАЛЫ. До двух: [CLOCK:SET;ID:id;NAME:имя;KIND:PROGRESS/DANGER/NEUTRAL;VALUE:0;MAX:4;WHEN_FULL:конкретное событие]. MAX 2..8.
[CLOCK:DELTA;ID:id;DELTA:1;CAUSE:действие], [CLOCK:COMPLETE;ID:id;CAUSE:прямое решение], [CLOCK:CLEAR;ID:id].
Успех с ценой может продвинуть обе шкалы; не считай сообщения/минуты и не требуй набить шкалу при найденном прямом решении.""",
    "effects": """СОСТОЯНИЯ. [CONDITION:ADD;PLAYER:id;NAME:имя;EFFECT:эффект;CLEAR:способ;SCENES:2;USES:1], [CONDITION:REMOVE;PLAYER:id;NAME:имя].
EFFECT:MOVE_DISADVANTAGE/SOCIAL_DISADVANTAGE/PERCEPTION_DISADVANTAGE/COMBAT_DISADVANTAGE/NEXT_ROLL_DISADVANTAGE/ACTION_TO_CLEAR.
SCENES совместимости считает ответы мастера; новые эффекты: TICK:action/window/round/scene;TICKS:1..12. round только в бою; scene меняет [SCENE:BEGIN;ID:новое-место]. USES — подходящие броски, расход считает код. Низкое HP не дополнительная помеха.
ROLL DOMAIN:MOVE/SOCIAL/PERCEPTION/COMBAT/OTHER; не накладывай помеху второй раз.
Осознанная слабость с ценой: [WEAKNESS:ROLL;PLAYER:id;COMPLICATION:причина] перед проверкой/атакой, либо [WEAKNESS:PAYOFF;PLAYER:id;COST:DANGER_PLUS_1/PROGRESS_MINUS_1/CONDITION;COMPLICATION:цена]. CONDITION задаёт NAME/EFFECT/CLEAR. Без цены нет жетона.""",
    "world": """МИР И NPC. В NPC доступны AFFECTED:id, ATTITUDE:эмоция, OBLIGATION_KIND:NPC_OWES_PLAYERS/PLAYERS_OWE_NPC/MUTUAL/NONE,
OBLIGATION:долг, WANTS:цель, UNRESOLVED:незакрытый вопрос. Обновляй того же NPC после реального изменения; NONE закрывает поле.
Эмоция и долг независимы. Архивный кандидат возвращается естественно, максимум один; не импортируй других старых NPC.
Позиции, объекты, долг и последствия из structured state обязательны. Соцграф игроков — мягкий фон, не факт мира.""",
}


def build_compact_system(session, prompt: str = "") -> str:
    """Versioned protocol plus relevant mechanics; never clip its middle."""
    from AI.dnd_turn_contract import TURN_CONTRACT, turn_contract

    contract = turn_contract(session) or TURN_CONTRACT
    text = str(prompt).casefold()
    active = {
        "battle": bool(getattr(session, "enemy_combatants", None) or getattr(session, "scene_objects", None))
                  or any(word in text for word in ("атак", "удар", "враг", "бой", "трюк", "enemy", "cinematic")),
        "items": bool(getattr(session, "inventories", None))
                 or any(word in text for word in ("предмет", "вещ", "беру", "взять", "куп", "артефакт", "item")),
        "clocks": bool(getattr(session, "scene_clocks", None))
                  or any(word in text for word in ("clock", "шкала", "тревог", "погон", "прогресс")),
        "effects": bool(getattr(session, "conditions", None) or getattr(session, "weakness_luck_pending", None))
                   or any(word in text for word in ("condition", "weakness", "слабост", "ранен", "оглуш")),
        "world": True,
    }
    sections = [contract, _COMPACT_CORE]
    sections.extend(_COMPACT_MECHANICS[name].strip() for name, enabled in active.items() if enabled)
    objects = getattr(session, "scene_objects", None) or {}
    needs_scene_rules = getattr(session, "mode", None) == "participants" and (
        not objects or any(
            isinstance(row, dict) and row.get("available", True) and not row.get("interactions")
            for row in objects.values()
        )
    )
    if needs_scene_rules:
        from AI.dnd_scene_rules import RULES_PROTOCOL
        sections.append(RULES_PROTOCOL)
    enemies = getattr(session, "enemy_combatants", None) or {}
    enemy_rules = getattr(session, "local_enemy_rules", None) or {}
    if any(isinstance(row, dict) and row.get("status") != "dead" and int(row.get("hp", 0)) > 0
           and key not in enemy_rules for key, row in enemies.items()):
        from AI.dnd_scene_rules import ENEMY_RULES_PROTOCOL
        sections.append(ENEMY_RULES_PROTOCOL)
    if objects and not getattr(session, "local_wait_rule", None):
        from AI.dnd_scene_rules import WAIT_RULES_PROTOCOL
        sections.append(WAIT_RULES_PROTOCOL)
    if getattr(session, "mode", None) != "participants":
        sections.append("АБСТРАКТНЫЙ РЕЖИМ: если участников с ID нет, не выдумывай ID; TARGETS можно опустить.")
    result = "\n\n".join(sections)
    if len(result) > DND_COMPACT_SYSTEM_MAX_CHARS:
        raise DndAIBudgetExhausted("Контракт DnD превышает безопасный бюджет; заявка сохранена.")
    return result


async def _bounded_worker(func, *args, timeout_seconds: float, **kwargs):
    guard = DndCallGuard(timeout_seconds)
    with ai_call_guard(guard):
        try:
            remaining = guard.remaining()
            return await asyncio.wait_for(asyncio.to_thread(func, *args, **kwargs), timeout=remaining)
        except BaseException:
            guard.cancelled.set()
            raise


def _call_admission(prompt: str, max_tokens: int, *, optional=False):
    budget = current_turn_budget()
    return budget.reserve(prompt, max_tokens, optional=optional) if budget is not None else None


def _start_provider_call(guard, reservation, *, provider, model):
    guard.remaining()
    if reservation is not None:
        reservation.start()
    logging.info("DnD AI dispatch purpose=%s provider=%s model=%s turn_id=%s",
                 current_ai_purpose(), provider, model,
                 getattr(current_turn_budget(), "turn_id", ""))


def _get_client(api_key: str, timeout_ms: int) -> genai.Client:
    key = (api_key, int(timeout_ms))
    with _state_lock:
        client = _client_cache.get(key)
        if client is None:
            client = genai.Client(
                api_key=api_key,
                http_options=genai_types.HttpOptions(timeout=int(timeout_ms)),
            )
            _client_cache[key] = client
        return client


def _model_queue(chat_id: int | None) -> list[str]:
    if chat_id is not None and str(chat_id) == str(SPECIAL_CHAT_ID):
        queue = MODEL_QUEUE_SPECIAL
    else:
        queue = MODEL_QUEUE_DEFAULT
    return [str(name).removeprefix("models/") for name in queue if name]


def _attempt_pairs(chat_id: int | None, attempts: int) -> list[tuple[str, str]]:
    keys = list(GEMINI_KEYS_POOL)
    models = _model_queue(chat_id)
    if not keys or not models or attempts <= 0:
        return []

    global _key_cursor
    with _state_lock:
        start = _key_cursor % len(keys)
        _key_cursor = (start + max(1, attempts)) % len(keys)

    model_span = min(2, len(models))
    return [
        (keys[(start + index) % len(keys)], models[index % model_span])
        for index in range(attempts)
    ]


def _circuit_is_open(chat_id: int | None) -> bool:
    with _state_lock:
        until = float(_gemini_circuit_until.get(chat_id, 0.0))
        if time.monotonic() < until:
            return True
        _gemini_circuit_until.pop(chat_id, None)
        return False


def _open_circuit(chat_id: int | None) -> None:
    with _state_lock:
        _gemini_circuit_until[chat_id] = max(
            float(_gemini_circuit_until.get(chat_id, 0.0)),
            time.monotonic() + DND_GEMINI_CIRCUIT_SECONDS,
        )


def _close_circuit(chat_id: int | None) -> None:
    with _state_lock:
        _gemini_circuit_until.pop(chat_id, None)


def _extract_text(response: Any) -> str:
    try:
        text = getattr(response, "text", None)
    except Exception:
        text = None
    if text:
        return str(text).strip()
    for candidate in getattr(response, "candidates", None) or []:
        content = getattr(candidate, "content", None)
        for part in getattr(content, "parts", None) or []:
            part_text = getattr(part, "text", None)
            if part_text:
                return str(part_text).strip()
    return ""


def _status_code(error: Exception) -> int | None:
    value = getattr(error, "code", None) or getattr(error, "status_code", None)
    if value is None and getattr(error, "response", None) is not None:
        value = getattr(error.response, "status_code", None)
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _is_transient(error: Exception) -> bool:
    code = _status_code(error)
    text = str(error).casefold()
    return (
        code in {429, 500, 502, 503, 504}
        or "timeout" in text
        or "timed out" in text
        or "temporar" in text
        or "resourceexhausted" in text
        or "quotaexceeded" in text
        or "429" in text
        or "503" in text
    )


def _is_request_too_large(error: Exception) -> bool:
    """Return whether Groq rejected the fallback because the prompt is too large."""
    code = _status_code(error)
    text = str(error).casefold()
    return (
        code == 413
        or "request too large" in text
        or "prompt too large" in text
        or "context length" in text
    )


def _groq_retry_after_seconds(error: Exception) -> float | None:
    """Extract a short Groq Retry-After delay from a 429 response/message."""
    if _status_code(error) != 429:
        return None

    response = getattr(error, "response", None)
    headers = getattr(response, "headers", None) or {}
    raw_header = None
    try:
        raw_header = headers.get("retry-after")
    except AttributeError:
        raw_header = None
    if raw_header is not None:
        try:
            return max(0.0, float(raw_header))
        except (TypeError, ValueError):
            pass

    match = re.search(
        r"try again in\s+([0-9]+(?:\.[0-9]+)?)\s*(ms|s|sec(?:ond)?s?)\b",
        str(error),
        re.I,
    )
    if not match:
        return None
    value = float(match.group(1))
    unit = match.group(2).casefold()
    return value / 1000.0 if unit == "ms" else value


def _history_contents(session, prompt: str):
    """Build Gemini contents from system + a tiny recent window + current state.

    Durable conversation remains a full audit trail, but provider continuity is
    intentionally bounded on every request. Long-term truth is rebuilt into the
    current request by the Memory v2 context builder.
    """
    rows = []
    for item in getattr(session, "conversation", None) or []:
        if not isinstance(item, dict) or item.get("content") is None:
            continue
        source_role = item.get("role")
        role = "model" if source_role in {"assistant", "model"} else "user"
        rows.append((role, str(item["content"])))

    compact_protocol = getattr(session, "mode", None) in {"participants", "abstract"}
    current = _without_repeated_instructions(str(prompt)) if compact_protocol else str(prompt)
    total_chars = sum(len(text) for _role, text in rows) + len(current)

    def clip_middle(text: str, budget: int) -> str:
        value = str(text or "")
        budget = max(0, int(budget))
        if len(value) <= budget:
            return value
        if budget <= 1:
            return value[:budget]
        marker = "\n[...сокращено для Gemini...]\n"
        if budget <= len(marker) + 2:
            return value[:budget]
        payload = budget - len(marker)
        head = max(1, payload // 2)
        tail = payload - head
        return value[:head] + marker + value[-tail:]

    compact_rows = []
    prefix = rows[:2]
    if prefix:
        opening = prefix[1][1] if len(prefix) > 1 else ""
        system_budget = max(
            0,
            DND_GEMINI_SYSTEM_MAX_CHARS - min(len(opening), 256),
        )
        system = build_compact_system(session, current) if compact_protocol else clip_middle(prefix[0][1], system_budget)
        compact_rows.append((prefix[0][0], system))
        if len(prefix) > 1:
            compact_rows.append((prefix[1][0], opening[:256]))

    recent_source = rows[2:]
    recent_count = min(DND_GEMINI_RECENT_MESSAGES, len(recent_source))
    if recent_count % 2:
        recent_count -= 1
    recent = recent_source[-recent_count:] if recent_count else []
    if recent:
        budget = current_turn_budget()
        history_budget = min(DND_GEMINI_RECENT_HISTORY_MAX_CHARS,
                             budget.policy.history_chars if budget is not None else DND_GEMINI_RECENT_HISTORY_MAX_CHARS)
        per_message_budget = max(
            1,
            history_budget // len(recent),
        )
        compact_rows.extend(
            (role, clip_middle(text, per_message_budget))
            for role, text in recent
        )

    from AI.dnd_turn_contract import turn_contract

    contract = "" if compact_protocol else turn_contract(session)
    current_budget = DND_GEMINI_INPUT_MAX_CHARS - sum(len(text) for _role, text in compact_rows)
    if compact_protocol and len(current) > current_budget:
        raise DndAIBudgetExhausted("Текущие действия и память не помещаются в бюджет AI; заявка сохранена без обрезки.")
    if not compact_protocol:
        current = _bounded_current_request(current, DND_GEMINI_CURRENT_PROMPT_MAX_CHARS - len(contract) - 2)
    if contract:
        current = contract + "\n\n" + current
    contents = [
        {"role": role, "parts": [{"text": text}]}
        for role, text in compact_rows
        if text
    ]
    contents.append({"role": "user", "parts": [{"text": current}]})

    sent_chars = sum(
        len(part.get("text") or "")
        for item in contents
        for part in item.get("parts") or []
    )
    if len(recent_source) > len(recent) or total_chars > sent_chars:
        logging.info(
            "DnD Gemini history bounded chat_id=%s original_chars=%s sent_chars=%s "
            "original_messages=%s sent_messages=%s",
            getattr(session, "chat_id", None),
            total_chars,
            sent_chars,
            len(rows) + 1,
            len(contents),
        )
    return contents


def _without_repeated_instructions(prompt: str) -> str:
    # These exact static blocks are represented in the compact central protocol;
    # do not touch player text, state sections or code-produced consequences.
    from AI.dnd_style import DND_STYLE_INSTRUCTION
    from AI.dnd_player_agency import DND_PLAYER_AGENCY_RULES

    result = str(prompt).replace(DND_STYLE_INSTRUCTION, "").replace(DND_PLAYER_AGENCY_RULES, "")
    return result.strip()


def _service_prompt(session, prompt: str) -> str:
    """Task-specific input, not the campaign/system history for a five-line task."""
    from AI.dnd_current_turn_priority import CURRENT_REQUEST_MARKER

    current = _without_repeated_instructions(str(prompt))
    if current.startswith("СЛУЖЕБНАЯ ЗАДАЧА:"):
        return _bounded_head_tail(current, DND_SERVICE_INPUT_MAX_CHARS)
    if getattr(session, "_upupa_ephemeral_generation_depth", 0):
        current = current.split(CURRENT_REQUEST_MARKER, 1)[-1]
        for marker in ("\nПАРАМЕТРЫ ПРИКЛЮЧЕНИЯ:", "\n\nПАМЯТЬ DND", "\nРЕЖИССЁР СЦЕНЫ:"):
            current = current.split(marker, 1)[0]
        if "эпилог" in current.casefold():
            facts = "\n".join(str(row) for row in (getattr(session, "scene_log", None) or [])[-4:])
            current += "\nПОДТВЕРЖДЁННЫЕ ПОСЛЕДСТВИЯ:\n" + _bounded_head_tail(facts, 2_400)
        current = "СЛУЖЕБНАЯ ЗАДАЧА: исполни только заданный формат, без ACTION и новой сцены.\n" + current
    return _bounded_head_tail(current, DND_SERVICE_INPUT_MAX_CHARS)


@ai_feature("DnD", only_if_unset=True)
def _run_gemini_sync(
    session,
    prompt: str,
    *,
    attempts: int,
    http_timeout_ms: int,
    governor_timeout_seconds: float,
    queue_timeout_seconds: float,
    include_history: bool,
    lane: str,
    update_circuit: bool,
    optional_call: bool = False,
) -> str:
    chat_id = getattr(session, "chat_id", None)
    if update_circuit and _circuit_is_open(chat_id):
        raise DndGeminiCircuitOpen("DnD Gemini circuit is temporarily open")

    pairs = _attempt_pairs(chat_id, attempts)
    if not pairs:
        raise RuntimeError("DnD Gemini keys/models are not configured")

    contents = _history_contents(session, prompt) if include_history else _service_prompt(session, prompt)
    is_optional = optional_call or current_ai_purpose() in {"repair", "inventory_audit", "roll_repair"}
    max_tokens = 1800 if include_history else DND_SERVICE_MAX_TOKENS
    guard = current_call_guard() or DndCallGuard(governor_timeout_seconds * max(1, attempts))
    budget = current_turn_budget()
    if budget is not None and not is_optional:
        pairs = pairs[:budget.policy.primary_attempts]
    provider_text = contents if isinstance(contents, str) else "\n".join(
        str(part.get("text") or "") for item in contents for part in item.get("parts") or []
    )
    errors: list[Exception] = []
    saw_transient = False
    config = genai_types.GenerateContentConfig(
        temperature=0.8 if include_history else 0.1,
        max_output_tokens=max_tokens,
    )

    for api_key, model_name in pairs:
        reservation = None
        try:
            remaining = guard.remaining()
            reservation = _call_admission(provider_text, max_tokens, optional=is_optional)
            # Flash 2.5 counts thinking against max_output_tokens. A small text
            # budget otherwise gets exhausted before ACTION/ITEM can be emitted.
            config.thinking_config = (
                genai_types.ThinkingConfig(thinking_budget=0)
                if model_name.startswith("gemini-2.5-flash") else None
            )
            client = _get_client(api_key, min(http_timeout_ms, max(1, int(remaining * 1000))))
            with ai_execution_lane(lane):
                response = run_ai_provider_call(
                    "dnd.gemini.generate_content",
                    client.models.generate_content,
                    model=model_name,
                    contents=contents,
                    config=config,
                    timeout_seconds=min(governor_timeout_seconds, remaining),
                    queue_timeout_seconds=min(queue_timeout_seconds, remaining),
                    provider_deadline=guard.deadline,
                    provider_start_guard=lambda reservation=reservation, model_name=model_name: _start_provider_call(
                        guard, reservation, provider="gemini", model=model_name),
                )
            if reservation is not None:
                reservation.finish(_extract_token_usage(response)["total_tokens"])
            text = _extract_text(response)
            candidates = getattr(response, "candidates", None) or []
            if any(str(getattr(c, "finish_reason", "")).endswith("MAX_TOKENS") for c in candidates):
                raise RuntimeError("DnD Gemini returned truncated text")
            if not text:
                raise RuntimeError("DnD Gemini returned empty text")
            if update_circuit:
                _close_circuit(chat_id)
            logging.info(
                "DnD Gemini success chat_id=%s model=%s",
                chat_id,
                model_name,
            )
            return text
        except Exception as exc:
            if reservation is not None:
                reservation.finish()
            if isinstance(exc, DndAIBudgetExhausted):
                raise
            errors.append(exc)
            transient = _is_transient(exc)
            saw_transient = saw_transient or transient
            logging.warning(
                "DnD Gemini fast attempt failed chat_id=%s model=%s transient=%s error=%s",
                getattr(session, "chat_id", None),
                model_name,
                transient,
                exc,
            )

    if update_circuit and saw_transient:
        _open_circuit(chat_id)
        logging.warning(
            "DnD Gemini circuit opened chat_id=%s seconds=%s",
            chat_id,
            int(DND_GEMINI_CIRCUIT_SECONDS),
        )
    raise RuntimeError(f"DnD Gemini fast path failed: {errors[-1] if errors else 'unknown error'}")


def _bounded_head_tail(
    text: str,
    budget: int,
    *,
    head_ratio: float = 0.55,
    marker: str = "\n\n[...середина истории сокращена...]\n\n",
) -> str:
    """Keep both the start and end of an oversized continuity-critical block."""
    value = str(text or "")
    budget = max(0, int(budget))
    if not budget or not value:
        return ""
    if len(value) <= budget:
        return value
    if budget <= len(marker) + 2:
        return value[:budget]

    payload_budget = budget - len(marker)
    head_size = max(1, min(payload_budget - 1, int(payload_budget * head_ratio)))
    tail_size = payload_budget - head_size
    return value[:head_size] + marker + value[-tail_size:]


def _bounded_current_request(text: str, budget: int) -> str:
    """Keep actor/action at the start of the live block when clipping memory.

    A generic head/tail slice can remove the live request in the middle between
    a long memory snapshot and appended style/mechanical instructions.
    """
    from AI.dnd_current_turn_priority import CURRENT_REQUEST_MARKER

    if len(text) <= budget or CURRENT_REQUEST_MARKER not in text or budget < 512:
        return _bounded_head_tail(text, budget, head_ratio=0.62)
    memory, request = text.split(CURRENT_REQUEST_MARKER, 1)
    request = CURRENT_REQUEST_MARKER + request
    request_budget = min(len(request), int(budget * 0.75))
    memory_budget = budget - request_budget - 2
    return (_bounded_head_tail(memory, memory_budget, head_ratio=0.65) + "\n\n"
            + _bounded_head_tail(request, request_budget, head_ratio=0.75))


def _fallback_prompt(
    session,
    prompt: str,
    *,
    max_chars: int = DND_FALLBACK_PROMPT_MAX_CHARS,
    continuity_guard: str = DND_FALLBACK_CONTINUITY_GUARD,
) -> str:
    from AI.dnd_turn_contract import turn_contract

    if getattr(session, "mode", None) in {"participants", "abstract"}:
        current = _without_repeated_instructions(str(prompt or ""))
        system = build_compact_system(session, current)
        fixed = f"{continuity_guard}\n\nSYSTEM EXCERPT:\n{system}\n\nCURRENT REQUEST:\n{current}"
        if len(fixed) > max_chars:
            raise DndAIBudgetExhausted("Полные текущие действия и канон не помещаются в контекст резервной модели; заявка сохранена.")
        budget = current_turn_budget()
        history_limit = min(max_chars - len(fixed) - 20,
                            budget.policy.history_chars if budget else DND_GEMINI_RECENT_HISTORY_MAX_CHARS)
        history = []
        for row in reversed((getattr(session, "conversation", None) or [])[2:][-DND_FALLBACK_RECENT_MESSAGES:]):
            if not isinstance(row, dict):
                continue
            value = str(row.get("content") or "")
            if len(value) + 2 > history_limit:
                break
            history.insert(0, value)
            history_limit -= len(value) + 2
        if history:
            fixed += "\n\nRECENT HISTORY:\n" + "\n\n".join(history)
        return fixed

    contract = turn_contract(session)
    if contract:
        continuity_guard += "\n\n" + contract
    rows = []
    for item in getattr(session, "conversation", None) or []:
        if not isinstance(item, dict) or item.get("content") is None:
            continue
        role = "assistant" if item.get("role") in {"assistant", "model"} else "user"
        rows.append(f"{role}: {item['content']}")

    current = str(prompt or "")
    system = rows[0] if rows else ""
    opening = rows[1:2]
    recent_source = rows[2:]
    recent = recent_source[-DND_FALLBACK_RECENT_MESSAGES:]
    history_rows = [*opening]
    if len(recent_source) > len(recent):
        history_rows.append(
            "[...ранняя история опущена; долгосрочные факты бери из MEMORY V2 в CURRENT REQUEST...]"
        )
    history_rows.extend(recent)
    recent_history = "\n\n".join(history_rows)

    labels = (
        "SYSTEM EXCERPT:\n",
        "RECENT HISTORY:\n",
        "CURRENT REQUEST:\n",
    )
    full = "\n\n".join(
        (
            continuity_guard,
            labels[0] + system,
            labels[1] + recent_history,
            labels[2] + current,
        )
    )
    if len(full) <= max_chars:
        return full

    separator = "\n\n"
    fixed = (
        len(continuity_guard)
        + sum(len(label) for label in labels)
        + 3 * len(separator)
    )
    available = max(0, int(max_chars) - fixed)
    if available < 64:
        emergency = (
            continuity_guard
            + separator
            + labels[2]
            + current
        )
        return _bounded_head_tail(emergency, max_chars, head_ratio=0.35)

    # CURRENT REQUEST carries the Memory v2 authoritative state, so preserve it
    # ahead of old narrative history when the fallback prompt must be compressed.
    current_budget = int(available * 0.65)
    history_budget = int(available * 0.15)
    system_budget = available - current_budget - history_budget

    system_excerpt = _bounded_head_tail(system, system_budget, head_ratio=0.55)
    history_excerpt = recent_history[-history_budget:] if history_budget else ""
    current_excerpt = _bounded_current_request(current, current_budget)

    compact = separator.join(
        (
            continuity_guard,
            labels[0] + system_excerpt,
            labels[1] + history_excerpt,
            labels[2] + current_excerpt,
        )
    )
    return compact[:max_chars]


def build_bounded_text_prompt(
    session,
    prompt: str,
    *,
    max_chars: int = DND_FALLBACK_PROMPT_MAX_CHARS,
) -> str:
    """Public bounded text prompt for non-Gemini DnD provider paths."""
    return _fallback_prompt(
        session,
        prompt,
        max_chars=max_chars,
        continuity_guard=DND_DIRECT_CONTINUITY_GUARD,
    )


@ai_feature("DnD", only_if_unset=True)
def _run_groq_sync(
    session,
    prompt: str,
    *,
    max_prompt_chars: int = DND_FALLBACK_PROMPT_MAX_CHARS,
    max_tokens: int = DND_GROQ_FALLBACK_MAX_TOKENS,
) -> str:
    if not GROQ_API_KEY:
        raise RuntimeError("Groq is not configured")
    fallback_prompt = _fallback_prompt(session, prompt, max_chars=max_prompt_chars)
    text = _guarded_groq_text(fallback_prompt, max_tokens=max_tokens,
                             temperature=DND_GROQ_FALLBACK_TEMPERATURE,
                             timeout_seconds=DND_GROQ_HTTP_TIMEOUT_SECONDS, lane="interactive")
    text = str(text or "").strip()
    if not text or text == "Ключ Groq не настроен":
        raise RuntimeError("Groq returned empty text")
    return text


def _guarded_groq_text(prompt, *, max_tokens, temperature, timeout_seconds, lane, optional=False):
    guard = current_call_guard() or DndCallGuard(timeout_seconds + 2)
    remaining = guard.remaining()
    # Unwrap only here: this method is explicitly governed below, with the same
    # operation/usage attribution but a deadline and a dispatch cancellation gate.
    resource = groq_ai.unwrap() if callable(getattr(groq_ai, "unwrap", None)) else groq_ai
    model_name = getattr(resource, "text_model", None)
    reservation = _call_admission(prompt, max_tokens, optional=optional)

    def invoke():
        remaining_at_dispatch = guard.remaining()
        return resource.generate_text(prompt, max_tokens=max_tokens, temperature=temperature,
                                      max_retries=0,
                                      request_timeout_seconds=min(timeout_seconds, remaining_at_dispatch),
                                      reject_truncated=True)

    try:
        with ai_execution_lane(lane):
            result = run_ai_provider_call(
                "groq_ai.generate_text", invoke,
                timeout_seconds=min(timeout_seconds + 2, remaining),
                queue_timeout_seconds=min(2.0, remaining),
                provider_deadline=guard.deadline,
                provider_start_guard=lambda: _start_provider_call(guard, reservation, provider="groq", model=model_name),
                requested_model=model_name,
            )
        if reservation is not None:
            reservation.finish(_extract_token_usage(result)["total_tokens"])
        return result
    finally:
        if reservation is not None:
            reservation.finish()


async def _run_groq_fallback(session, prompt: str) -> str:
    try:
        return await _bounded_worker(
            _run_groq_sync,
            session,
            prompt,
            max_prompt_chars=DND_FALLBACK_PROMPT_MAX_CHARS,
            max_tokens=DND_GROQ_FALLBACK_MAX_TOKENS,
            timeout_seconds=DND_GROQ_FALLBACK_TIMEOUT_SECONDS,
        )
    except Exception as exc:
        if isinstance(exc, DndAIBudgetExhausted):
            raise
        if _is_request_too_large(exc) or "returned empty text" in str(exc) or "truncated text" in str(exc):
            logging.warning(
                "DnD Groq fallback request too large chat_id=%s; retrying compact prompt chars=%s",
                getattr(session, "chat_id", None),
                DND_FALLBACK_RETRY_PROMPT_MAX_CHARS,
            )
        else:
            retry_after = _groq_retry_after_seconds(exc)
            if (
                retry_after is None
                or retry_after > DND_GROQ_RATE_LIMIT_RETRY_CAP_SECONDS
            ):
                raise
            delay = retry_after + DND_GROQ_RATE_LIMIT_RETRY_PADDING_SECONDS
            budget = current_turn_budget()
            if budget is not None and delay >= budget.remaining_seconds():
                raise DndAIBudgetExhausted("Бюджет времени не допускает повтор после rate limit; заявка сохранена.") from exc
            logging.warning(
                "DnD Groq fallback rate limited chat_id=%s retry_after=%.3fs; "
                "retrying once with compact prompt",
                getattr(session, "chat_id", None),
                retry_after,
            )
            await asyncio.sleep(delay)

        return await _bounded_worker(
            _run_groq_sync,
            session,
            prompt,
            max_prompt_chars=DND_FALLBACK_RETRY_PROMPT_MAX_CHARS,
            max_tokens=DND_GROQ_FALLBACK_RETRY_MAX_TOKENS,
            timeout_seconds=DND_GROQ_FALLBACK_TIMEOUT_SECONDS,
        )


async def _generate_main_text(session, prompt: str) -> str:
    gemini_error: Exception | None = None
    try:
        text = await _bounded_worker(
            _run_gemini_sync,
            session,
            prompt,
            attempts=DND_GEMINI_ATTEMPTS,
            http_timeout_ms=DND_GEMINI_HTTP_TIMEOUT_MS,
            governor_timeout_seconds=DND_GEMINI_GOVERNOR_TIMEOUT_SECONDS,
            queue_timeout_seconds=DND_GEMINI_QUEUE_TIMEOUT_SECONDS,
            include_history=True,
            lane="interactive",
            update_circuit=True,
            timeout_seconds=DND_GEMINI_GOVERNOR_TIMEOUT_SECONDS * DND_GEMINI_ATTEMPTS,
        )
        session._dnd_last_generation_provider = "gemini"
        return text
    except Exception as exc:
        if isinstance(exc, DndAIBudgetExhausted):
            raise
        gemini_error = exc
        logging.warning(
            "DnD Gemini degraded chat_id=%s; switching to Groq: %s",
            getattr(session, "chat_id", None),
            exc,
        )

    try:
        with ai_call_purpose("fallback"):
            text = await _run_groq_fallback(session, prompt)
        session._dnd_last_generation_provider = "groq"
        logging.info(
            "DnD provider fallback success chat_id=%s provider=groq",
            getattr(session, "chat_id", None),
        )
        return text
    except Exception as exc:
        if isinstance(exc, DndAIBudgetExhausted):
            raise
        raise RuntimeError(
            f"DnD generation failed after Gemini/Groq fallback: gemini={gemini_error}; groq={exc}"
        ) from exc


@ai_feature("DnD", only_if_unset=True)
def _run_groq_auxiliary_sync(prompt: str) -> str:
    """Run one compact Groq-only auxiliary pass without conversation history."""
    if not GROQ_API_KEY:
        raise RuntimeError("Groq is not configured")
    compact = _bounded_head_tail(
        str(prompt or ""),
        DND_AUX_GROQ_MAX_PROMPT_CHARS,
        head_ratio=0.5,
    )
    text = _guarded_groq_text(compact, max_tokens=DND_AUX_GROQ_MAX_TOKENS,
                             temperature=DND_AUX_GROQ_TEMPERATURE,
                             timeout_seconds=DND_AUX_GROQ_TIMEOUT_SECONDS,
                             lane="background", optional=True)
    text = str(text or "").strip()
    if not text or text == "Ключ Groq не настроен":
        raise RuntimeError("Groq returned empty auxiliary text")
    return text


async def _generate_auxiliary_text(
    session,
    prompt: str,
    *,
    allow_groq_fallback: bool = False,
) -> str | None:
    """Run one short best-effort auxiliary pass without mutating conversation.

    Gemini remains the default. Callers that guard canonical mechanics may opt in
    to a compact Groq fallback so a temporary Gemini outage cannot silently skip
    the repair step.
    """
    if len(str(prompt)) > DND_SERVICE_INPUT_MAX_CHARS:
        logging.info("DnD auxiliary audit skipped: input exceeds intact service budget")
        return None
    chat_id = getattr(session, "chat_id", None)
    if not _circuit_is_open(chat_id):
        try:
            return await _bounded_worker(
                _run_gemini_sync,
                session,
                prompt,
                attempts=1,
                http_timeout_ms=DND_AUX_HTTP_TIMEOUT_MS,
                governor_timeout_seconds=DND_AUX_GOVERNOR_TIMEOUT_SECONDS,
                queue_timeout_seconds=DND_AUX_QUEUE_TIMEOUT_SECONDS,
                include_history=False,
                lane="background",
                update_circuit=False,
                optional_call=True,
                timeout_seconds=DND_AUX_GOVERNOR_TIMEOUT_SECONDS,
            )
        except Exception as exc:
            logging.info(
                "DnD auxiliary Gemini skipped chat_id=%s error=%s",
                chat_id,
                exc,
            )
            if isinstance(exc, DndAIBudgetExhausted) or not allow_groq_fallback:
                return None
    elif not allow_groq_fallback:
        return None
    else:
        logging.info(
            "DnD auxiliary Gemini circuit open chat_id=%s; using Groq fallback",
            chat_id,
        )

    try:
        return await _bounded_worker(
            _run_groq_auxiliary_sync, prompt,
            timeout_seconds=DND_AUX_GROQ_TIMEOUT_SECONDS,
        )
    except Exception as exc:
        logging.info(
            "DnD auxiliary Groq skipped chat_id=%s error=%s",
            chat_id,
            exc,
        )
        return None


async def generate_auxiliary_text(session, prompt: str, *, allow_groq_fallback=False, purpose=None):
    if purpose is None:
        text = str(prompt).casefold()
        purpose = "inventory_audit" if "инвентар" in text else "roll_repair" if "action:roll" in text else "repair"
    with ai_call_purpose(purpose):
        return await _generate_auxiliary_text(session, prompt, allow_groq_fallback=allow_groq_fallback)


def _service_purpose(prompt: str) -> str:
    value = str(prompt).casefold()
    for needle, purpose in (("эпилог", "epilogue"), ("распредели характеристики", "stats"),
                            ("профил", "profile"), ("сюжетов", "plot"), ("npc", "npc")):
        if needle in value:
            return purpose
    return "service"


async def _generate_service_text(session, prompt: str) -> str:
    compact = _service_prompt(session, prompt)
    with dnd_turn_budget(session, mode="service"), ai_call_purpose(_service_purpose(compact)):
        if getattr(session, "active_model", None) != "groq" and not _circuit_is_open(getattr(session, "chat_id", None)):
            try:
                return await _bounded_worker(
                    _run_gemini_sync, session, compact, attempts=1,
                    http_timeout_ms=DND_AUX_HTTP_TIMEOUT_MS,
                    governor_timeout_seconds=DND_AUX_GOVERNOR_TIMEOUT_SECONDS,
                    queue_timeout_seconds=DND_AUX_QUEUE_TIMEOUT_SECONDS,
                    include_history=False, lane="background", update_circuit=False,
                    timeout_seconds=DND_AUX_GOVERNOR_TIMEOUT_SECONDS,
                )
            except DndAIBudgetExhausted:
                raise
            except Exception:
                logging.info("DnD service primary failed purpose=%s; trying bounded fallback", current_ai_purpose())
        return str(await _bounded_worker(
            lambda: _guarded_groq_text(compact, max_tokens=DND_SERVICE_MAX_TOKENS,
                                      temperature=0.1, timeout_seconds=DND_AUX_GROQ_TIMEOUT_SECONDS,
                                      lane="background", optional=False),
            timeout_seconds=DND_AUX_GROQ_TIMEOUT_SECONDS,
        )).strip()


def configure_dnd_generation_resilience(dnd) -> None:
    """Install a bounded Gemini path plus one cross-provider fallback for DnD."""
    if getattr(dnd, "_upupa_dnd_generation_resilience_configured", False):
        return

    original_generate = dnd.generate_session_response

    async def resilient_generate(session, prompt: str) -> str:
        if getattr(session, "active_model", None) not in {"gemini", "groq"} or not hasattr(session, "conversation"):
            return await original_generate(session, prompt)

        if getattr(session, "_upupa_ephemeral_generation_depth", 0):
            result = await _generate_service_text(session, prompt)
        elif session.active_model == "groq":
            result = await _run_groq_fallback(session, prompt)
            session._dnd_last_generation_provider = "groq"
        else:
            result = await _generate_main_text(session, prompt)
        session.conversation.append({"role": "user", "content": str(prompt)})
        session.conversation.append({"role": "assistant", "content": result})
        if getattr(dnd, "dnd_sessions", {}).get(getattr(session, "chat_id", None)) is session:
            dnd.persist_dnd_sessions()
        return result

    dnd.generate_session_response = resilient_generate
    dnd._upupa_dnd_generation_resilience_base = resilient_generate
    dnd._upupa_dnd_generation_resilience_configured = True


__all__ = [
    "DndGeminiCircuitOpen",
    "build_bounded_text_prompt",
    "configure_dnd_generation_resilience",
    "generate_auxiliary_text",
    "build_compact_system",
]
