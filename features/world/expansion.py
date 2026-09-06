"""Measured state characteristics, sanctions and international court for World Upupa."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta
import re

from AI.summarize import _generate_with_active_model
from core.history_store import get_history_repository
from core.paths import USER_MESSAGES_LOG_PATH, WORLD_DB_PATH
from features.social_graph.analysis import aggregate_edges
from features.social_graph.service import get_graph_data, is_social_graph_enabled
from features.world.identity import ensure_state_identity
from features.world.news import format_event_fact
from features.world.presentation import format_world_profile
from features.world.service import calculate_authority, get_world_service
from infrastructure.persistence.sqlite_world_expansion import (
    SQLiteWorldExpansionRepository,
    WorldCourtCase,
    WorldSanction,
)


SANCTION_AUTHORITY_PENALTY = 3
MAX_SANCTION_AUTHORITY_PENALTY = 18
LEGACY_TEMPLATE_THREATS = {
    "понедельник",
    "внезапный рабочий созвон",
    "исчезновение последнего админа",
    "голосовые по семь минут",
    "фраза «есть минутка?»",
    "слишком серьёзный разговор",
    "массовый уход читать, но не отвечать",
    "неожиданная трезвость населения",
    "спор, начавшийся со слова «вообще-то»",
    "человек, который решил всё организовать",
    "дефицит мемов стратегического назначения",
    "сообщение «доброе утро» в 06:12",
}
_repo = SQLiteWorldExpansionRepository(WORLD_DB_PATH)
_schema_ready = False
_schema_lock = asyncio.Lock()


@dataclass(frozen=True)
class StateCharacteristics:
    messages_7d: int
    active_citizens_7d: int
    social_edges_30d: int
    cohesion: int | None
    sanctions_outgoing: int
    sanctions_incoming: int
    base_authority: int
    effective_authority: int


async def _ensure_schema() -> None:
    global _schema_ready
    if _schema_ready:
        return
    async with _schema_lock:
        if not _schema_ready:
            await asyncio.to_thread(_repo.init_schema)
            _schema_ready = True


def _weighted_cohesion(edges) -> int | None:
    total_weight = sum(edge.total_weight for edge in edges)
    if total_weight <= 0:
        return None
    reciprocity = sum(edge.reciprocity * edge.total_weight for edge in edges) / total_weight
    return max(0, min(100, round(reciprocity * 100)))


async def _activity_metrics(chat_id: int) -> tuple[int, int]:
    history = get_history_repository(USER_MESSAGES_LOG_PATH)
    if history is None:
        return 0, 0
    since = datetime.now() - timedelta(days=7)
    try:
        messages, participants = await asyncio.gather(
            asyncio.to_thread(history.count, str(chat_id), start=since, nonempty=True),
            asyncio.to_thread(history.participants, str(chat_id), start=since, nonempty=True),
        )
        return int(messages), len(participants)
    except Exception:
        return 0, 0


async def get_state_characteristics(profile) -> StateCharacteristics:
    await _ensure_schema()
    messages_7d, citizens_7d = await _activity_metrics(profile.state.chat_id)

    edges = ()
    if is_social_graph_enabled(profile.state.chat_id):
        try:
            graph = await get_graph_data(profile.state.chat_id, period_days=30)
            edges = aggregate_edges(graph.interactions)
        except Exception:
            edges = ()

    sanctions = await asyncio.to_thread(_repo.list_active_sanctions, profile.state.world_id)
    incoming = sum(1 for item in sanctions if item.target_state == profile.state.world_id)
    outgoing = sum(1 for item in sanctions if item.source_state == profile.state.world_id)
    base_authority = calculate_authority(profile)
    penalty = min(MAX_SANCTION_AUTHORITY_PENALTY, incoming * SANCTION_AUTHORITY_PENALTY)
    return StateCharacteristics(
        messages_7d=messages_7d,
        active_citizens_7d=citizens_7d,
        social_edges_30d=len(edges),
        cohesion=_weighted_cohesion(edges),
        sanctions_outgoing=outgoing,
        sanctions_incoming=incoming,
        base_authority=base_authority,
        effective_authority=max(0, base_authority - penalty),
    )


def _characteristics_block(metrics: StateCharacteristics) -> str:
    cohesion = f"{metrics.cohesion}/100" if metrics.cohesion is not None else "нет данных"
    authority = str(metrics.effective_authority)
    if metrics.effective_authority != metrics.base_authority:
        authority += f" (базовый {metrics.base_authority}, санкции −{metrics.base_authority - metrics.effective_authority})"
    return "\n".join([
        "",
        "Характеристики государства:",
        f"📨 Активность за 7 дней: {metrics.messages_7d} сообщений",
        f"🧑‍🤝‍🧑 Активных граждан за 7 дней: {metrics.active_citizens_7d}",
        f"🕸 Социальных связей за 30 дней: {metrics.social_edges_30d}",
        f"🤜🤛 Взаимность связей: {cohesion}",
        f"🌐 Эффективный международный авторитет: {authority}",
        f"🚫 Санкции: введено {metrics.sanctions_outgoing}, получено {metrics.sanctions_incoming}",
    ])


async def _visible_identity(service, profile):
    """Never expose the old deterministic identity templates in the state card."""
    identity = await ensure_state_identity(service, profile.state)
    if identity is None:
        return None
    threat = str(identity.details.main_threat or "").casefold().strip()
    if threat in LEGACY_TEMPLATE_THREATS:
        identity = await ensure_state_identity(service, profile.state, force=True)
        if identity is None:
            return None
        threat = str(identity.details.main_threat or "").casefold().strip()
        if threat in LEGACY_TEMPLATE_THREATS:
            return None
    return identity


async def build_expanded_state_card(world_id: int, bot=None) -> str | None:
    service = get_world_service()
    profile = await service.get_profile_by_world_id(world_id)
    if profile is None:
        return None
    identity = await _visible_identity(service, profile)
    details = identity.details if identity is not None else None
    population = None
    if bot is not None:
        try:
            population = await bot.get_chat_member_count(profile.state.chat_id)
        except Exception:
            population = None
    metrics = await get_state_characteristics(profile)
    base = format_world_profile(
        profile,
        population,
        details=details,
        authority=metrics.effective_authority,
        identity_rationale=(identity.rationale if identity is not None else None),
    )
    return base + _characteristics_block(metrics)


async def list_state_sanctions(world_id: int) -> tuple[WorldSanction, ...]:
    await _ensure_schema()
    return tuple(await asyncio.to_thread(_repo.list_active_sanctions, world_id))


async def impose_sanctions(source_state: int, target_state: int, reason: str) -> tuple[str, WorldSanction | None]:
    await _ensure_schema()
    if source_state == target_state:
        return "self", None
    service = get_world_service()
    target = await service.get_state_by_world_id(target_state)
    if target is None or not target.enabled:
        return "unknown_target", None
    created, sanction = await asyncio.to_thread(_repo.impose_sanction, source_state, target_state, reason)
    if not created:
        return "exists", None
    if sanction is not None:
        await service._record_event(
            "sanctions_imposed",
            actor_state=source_state,
            target_state=target_state,
            payload={"reason": sanction.reason},
        )
    return "created", sanction


async def lift_sanctions(source_state: int, target_state: int) -> str:
    await _ensure_schema()
    lifted = await asyncio.to_thread(_repo.lift_sanction, source_state, target_state)
    if not lifted:
        return "missing"
    service = get_world_service()
    await service._record_event("sanctions_lifted", actor_state=source_state, target_state=target_state)
    return "lifted"


def format_sanctions(sanctions: tuple[WorldSanction, ...], world_id: int, state_names: dict[int, str]) -> str:
    incoming = [item for item in sanctions if item.target_state == world_id]
    outgoing = [item for item in sanctions if item.source_state == world_id]
    lines = ["🚫 Санкционный режим"]
    if outgoing:
        lines.append("\nВведены нами:")
        for item in outgoing:
            reason = f" — {item.reason}" if item.reason else ""
            lines.append(f"• №{item.target_state} {state_names.get(item.target_state, '')}{reason}".rstrip())
    else:
        lines.append("\nВведены нами: —")
    if incoming:
        lines.append("\nВведены против нас:")
        for item in incoming:
            reason = f" — {item.reason}" if item.reason else ""
            lines.append(f"• №{item.source_state} {state_names.get(item.source_state, '')}{reason}".rstrip())
    else:
        lines.append("\nВведены против нас: —")
    lines.append(f"\nКаждый входящий активный пакет снижает отображаемый международный авторитет на {SANCTION_AUTHORITY_PENALTY}, максимум на {MAX_SANCTION_AUTHORITY_PENALTY}.")
    return "\n".join(lines)


def _pair_evidence(events, states: dict[int, object], state_a: int, state_b: int) -> str:
    pair = {state_a, state_b}
    selected = [
        event for event in events
        if event.actor_state in pair and (event.target_state is None or event.target_state in pair)
    ][:20]
    if not selected:
        return "В мировой хронике за последние 60 дней прямых событий между сторонами не найдено."
    return "\n".join(
        f"{event.created_at.strftime('%d.%m.%Y')}: {format_event_fact(event, states)}"
        for event in reversed(selected)
    )


async def international_court(plaintiff_state: int, defendant_state: int, claim: str, chat_id: str) -> tuple[str, WorldCourtCase | None]:
    await _ensure_schema()
    service = get_world_service()
    plaintiff, defendant = await asyncio.gather(
        service.get_state_by_world_id(plaintiff_state),
        service.get_state_by_world_id(defendant_state),
    )
    if plaintiff is None or defendant is None or not defendant.enabled:
        return "unknown_target", None
    if plaintiff_state == defendant_state:
        return "self", None
    clean_claim = re.sub(r"\s+", " ", claim or "").strip()[:1000]
    if not clean_claim:
        return "empty", None

    states_list, events = await asyncio.gather(
        service.list_all_states(),
        service.list_events(limit=80, days=60),
    )
    state_map = {state.world_id: state for state in states_list}
    evidence = _pair_evidence(events, state_map, plaintiff_state, defendant_state)
    prompt = f"""Ты — Международный суд Мира Упупы. Рассматривается сатирический спор двух Telegram-государств.

Истец: государство №{plaintiff.world_id} «{plaintiff.title}».
Ответчик: государство №{defendant.world_id} «{defendant.title}».
Иск: {clean_claim}

Ниже только фактическая международная хроника. Не придумывай событий, договоров или доказательств, которых в ней нет.
Если доказательств недостаточно, это должно повлиять на решение. Суд не вводит санкции автоматически и не начинает войну.

Хроника:
{evidence}

Вынеси решение в формате обычного текста:
Позиция суда: ...
Установлено: ...
Решение: иск удовлетворить / удовлетворить частично / отказать.
Рекомендация: одна короткая дипломатическая мера без реального вреда.

Не более 180 слов, можно с сарказмом.
"""
    verdict = (await _generate_with_active_model(prompt, chat_id) or "").strip()
    if not verdict:
        return "empty_response", None
    case = await asyncio.to_thread(
        _repo.record_court_case,
        plaintiff_state,
        defendant_state,
        clean_claim,
        verdict,
    )
    await service._record_event(
        "international_court_verdict",
        actor_state=plaintiff_state,
        target_state=defendant_state,
        payload={"case_id": case.case_id, "claim": clean_claim, "verdict": verdict[:700]},
    )
    return "decided", case


async def list_international_cases(limit: int = 8) -> tuple[WorldCourtCase, ...]:
    await _ensure_schema()
    return tuple(await asyncio.to_thread(_repo.list_court_cases, limit=limit))


def format_international_cases(cases: tuple[WorldCourtCase, ...], state_names: dict[int, str]) -> str:
    if not cases:
        return "⚖️ Международный суд пока никого не осудил. Подозрительное затишье."
    lines = ["⚖️ Практика Международного суда", ""]
    for case in cases:
        plaintiff = state_names.get(case.plaintiff_state, f"№{case.plaintiff_state}")
        defendant = state_names.get(case.defendant_state, f"№{case.defendant_state}")
        compact = re.sub(r"\s+", " ", case.verdict).strip()
        if len(compact) > 220:
            compact = compact[:219].rstrip() + "…"
        lines.append(f"Дело №{case.case_id} · {case.created_at.strftime('%d.%m.%Y')}")
        lines.append(f"{plaintiff} против {defendant}: {case.claim}")
        lines.append(compact)
        lines.append("")
    return "\n".join(lines).rstrip()
