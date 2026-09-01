"""Chronicle formatting and factual World of Upupa news generation."""

from __future__ import annotations

from AI.dialog.settings import build_prompt_with_current_chat_prompt
from AI.summarize import _generate_with_active_model
from features.world.ledger import WorldEvent, WorldRelation
from features.world.models import WorldState
from features.world.service import WorldService, get_world_service


SIGNIFICANT_EVENT_TYPES = {
    "state_founded",
    "state_reenabled",
    "state_disabled",
    "alliance_formed",
    "alliance_broken",
    "alliance_named",
    "war_declared",
    "war_ended",
    "ambassador_set",
    "ambassador_removed",
    "state_visit_invited",
    "state_visit_accepted",
    "state_visit_rejected",
    "state_visit_showcase",
    "state_visit_finished",
    "state_insult",
}


def _label(world_id: int | None, states: dict[int, WorldState]) -> str:
    if world_id is None:
        return "неизвестное государство"
    state = states.get(world_id)
    return f"№{world_id} — {state.title}" if state else f"№{world_id}"


def format_event_fact(event: WorldEvent, states: dict[int, WorldState]) -> str:
    actor = _label(event.actor_state, states)
    target = _label(event.target_state, states)
    payload = event.payload

    if event.event_type == "state_founded":
        return f"{actor} основано и появилось на карте Мира Упупы."
    if event.event_type == "state_reenabled":
        return f"{actor} вернулось в Мир Упупы."
    if event.event_type == "state_disabled":
        return f"{actor} временно вышло из Мира Упупы."
    if event.event_type == "alliance_formed":
        return f"{actor} и {target} заключили союз."
    if event.event_type == "alliance_broken":
        return f"{actor} разорвало союз с {target}."
    if event.event_type == "alliance_named":
        name = str(payload.get("name") or "безымянный союз")
        return f"Союз между {actor} и {target} получил название «{name}»."
    if event.event_type == "war_declared":
        return f"{actor} объявило войну государству {target}."
    if event.event_type == "war_ended":
        return f"{actor} прекратило войну с {target}; отношения снова нейтральные."
    if event.event_type == "ambassador_set":
        name = str(payload.get("name") or "неизвестный дипломат")
        return f"{actor} назначило послом гражданина {name}."
    if event.event_type == "ambassador_removed":
        return f"{actor} оставило должность посла вакантной."
    if event.event_type == "alliance_proposed":
        return f"{actor} предложило союз государству {target}."
    if event.event_type == "alliance_rejected":
        return f"{actor} отклонило предложение союза от {target}."
    if event.event_type == "state_visit_invited":
        return f"{actor} пригласило государство {target} с государственным визитом."
    if event.event_type == "state_visit_accepted":
        return f"{actor} приняло приглашение на государственный визит от {target}."
    if event.event_type == "state_visit_rejected":
        return f"{actor} отказалось от государственного визита в {target}."
    if event.event_type == "state_visit_showcase":
        name = str(payload.get("user_name") or "неизвестный экскурсовод")
        text = str(payload.get("text") or "что-то чрезвычайно государственное")
        return f"Во время визита в {actor} гражданин {name} показал гостям из {target}: {text}"
    if event.event_type == "state_visit_finished":
        if str(payload.get("reason") or "") == "timeout":
            return f"Государственный визит {target} в {actor} завершился по истечении 24 часов."
        return f"Государственный визит {target} в {actor} завершён досрочно."
    if event.event_type == "state_insult":
        text = str(payload.get("text") or "дипломатически оскорбительное заявление")
        return f"{actor} официально оскорбило государство {target}: {text}"
    return f"{actor}: событие {event.event_type}."


def _current_relations_facts(
    relations: tuple[WorldRelation, ...],
    states: dict[int, WorldState],
) -> list[str]:
    facts: list[str] = []
    for relation in relations:
        left = _label(relation.state_a, states)
        right = _label(relation.state_b, states)
        if relation.relation == "allied":
            suffix = f" под названием «{relation.alliance_name}»" if relation.alliance_name else ""
            facts.append(f"Сейчас {left} и {right} состоят в союзе{suffix}.")
        elif relation.relation == "war":
            facts.append(f"Сейчас {left} и {right} находятся в состоянии войны.")
    return facts


async def build_world_fact_feed(
    service: WorldService | None = None,
    *,
    days: int = 7,
    event_limit: int = 20,
) -> str:
    service = service or get_world_service()
    states = await service.list_all_states()
    if not states:
        return "Активных государств в Мире Упупы пока нет."
    state_map = {state.world_id: state for state in states}
    events = await service.list_events(
        limit=event_limit,
        days=days,
        event_types=SIGNIFICANT_EVENT_TYPES,
    )
    relations = await service.list_relations(active_only=True)

    lines = [f"Активных государств: {len(states)}."]
    for event in reversed(events):
        lines.append(
            f"{event.created_at.strftime('%d.%m %H:%M')} — {format_event_fact(event, state_map)}"
        )
    if not events:
        lines.append("За последние дни значимых дипломатических событий не зафиксировано.")
    current = _current_relations_facts(relations, state_map)
    if current:
        lines.append("Текущая расстановка сил:")
        lines.extend(current)
    else:
        lines.append("Все активные государства сейчас нейтральны друг к другу.")
    return "\n".join(lines)


async def generate_world_news(chat_id: str, service: WorldService | None = None) -> str:
    facts = await build_world_fact_feed(service)
    task = f"""Ты выпускаешь короткие «Мировые новости» Мира Упупы — сети Telegram-чатов, где каждый чат является государством.

Ниже дан единственный источник фактов. Нельзя придумывать события, причины, договоры, мнения государств или результаты, которых здесь нет.
Сделай живую и ироничную сводку на русском, примерно 100–180 слов. Можно сарказм и обсценную лексику в духе текущего промпта чата, но факты не искажай.
Не используй Markdown, заголовки и списки. Начни сразу с новостей и закончи одной короткой фразой о текущей международной обстановке.

ФАКТЫ МИРА:
{facts}
"""
    prompt = build_prompt_with_current_chat_prompt(
        chat_id,
        task,
        task_name="мировые новости Мира Упупы",
    )
    try:
        text = await _generate_with_active_model(prompt, chat_id, is_summarization=True)
    except Exception:
        text = ""
    cleaned = " ".join((text or "").split()).strip()
    if cleaned:
        return cleaned
    return facts


async def build_world_radio_context(service: WorldService | None = None) -> str:
    """Return factual context only; Radio Upupa will style it in its existing AI pass."""
    facts = await build_world_fact_feed(service, days=7, event_limit=12)
    return facts[:5000]
