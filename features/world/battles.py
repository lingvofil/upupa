"""Battles — the way wars are actually fought in World of Upupa.

A battle lasts 24 hours.  Each side's score is its *mobilization*: the share of
the chat's members who wrote at least one message during the battle.  Ties are
broken by the number of messages; an exact tie is a draw.  Small and large
chats compete on equal terms because the score is per capita.

Declaring a war starts the first battle automatically; afterwards either side's
admin or ambassador may start a new one whenever no battle is running.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import logging

from core.history_store import get_history_repository
from core.paths import USER_MESSAGES_LOG_PATH
from features.world.delivery import deliver_text
from features.world.models import WorldState
from features.world.presentation import human_until
from features.world.service import WorldService, get_world_service
from infrastructure.persistence.sqlite_world_battles import WorldBattle


BATTLE_DURATION = timedelta(hours=24)


@dataclass(frozen=True)
class BattleSide:
    world_id: int
    messages: int
    participants: int
    population: int

    @property
    def mobilization(self) -> float:
        if self.population <= 0:
            return 0.0
        return round(min(100.0, 100.0 * self.participants / self.population), 1)


async def start_battle(
    service: WorldService,
    attacker: WorldState,
    defender: WorldState,
    *,
    now: datetime | None = None,
) -> tuple[str, WorldBattle | None]:
    if service.battles is None:
        return "unavailable", None
    if attacker.world_id == defender.world_id:
        return "self", None
    if not defender.enabled:
        return "target_disabled", None
    if await service.get_relation(attacker.world_id, defender.world_id) != "war":
        return "not_at_war", None
    status, battle = await asyncio.to_thread(
        service.battles.start_battle,
        attacker.world_id,
        defender.world_id,
        duration=BATTLE_DURATION,
        now=now,
    )
    if status == "started" and battle is not None:
        await service.record_event(
            "battle_started",
            actor_state=attacker.world_id,
            target_state=defender.world_id,
            payload={"battle_id": battle.battle_id, "ends_at": battle.ends_at.isoformat()},
            dedupe_key=f"battle_started:{battle.battle_id}",
        )
    return status, battle


def battle_announcement(battle: WorldBattle, attacker: WorldState, defender: WorldState) -> str:
    return (
        f"🥊 Битва началась: №{attacker.world_id} {attacker.title} против №{defender.world_id} {defender.title}.\n\n"
        "Сутки мобилизации: побеждает государство, где больше граждан напишет хоть что-нибудь. "
        "Счёт — доля активных граждан от населения, так что маленькие чаты не в проигрыше.\n"
        f"До конца: {human_until(battle.ends_at)}."
    )


async def _measure(bot, state: WorldState | None, start: datetime, end: datetime) -> BattleSide:
    if state is None:
        return BattleSide(world_id=0, messages=0, participants=0, population=0)
    history = get_history_repository(USER_MESSAGES_LOG_PATH)
    messages = participants = 0
    if history is not None:
        # The message log keeps naive local timestamps.
        local_start = start.astimezone().replace(tzinfo=None)
        local_end = end.astimezone().replace(tzinfo=None)
        try:
            count, people = await asyncio.gather(
                asyncio.to_thread(history.count, str(state.chat_id), start=local_start, end=local_end, nonempty=True),
                asyncio.to_thread(
                    history.participants, str(state.chat_id), start=local_start, end=local_end, nonempty=True
                ),
            )
            messages, participants = int(count), len(people)
        except Exception:
            logging.exception("World battle activity lookup failed state=%s", state.world_id)
    population = 0
    try:
        population = max(1, int(await bot.get_chat_member_count(state.chat_id)) - 1)
    except Exception:
        population = max(participants, 1)
    return BattleSide(
        world_id=state.world_id,
        messages=messages,
        participants=participants,
        population=max(population, participants),
    )


def decide_winner(side_a: BattleSide, side_b: BattleSide) -> int | None:
    key_a = (side_a.mobilization, side_a.messages)
    key_b = (side_b.mobilization, side_b.messages)
    if key_a == key_b:
        return None
    return side_a.world_id if key_a > key_b else side_b.world_id


def _side_line(state: WorldState | None, side: BattleSide) -> str:
    title = state.title if state else f"№{side.world_id}"
    return (
        f"№{side.world_id} {title}: мобилизация {side.mobilization}% "
        f"({side.participants} из {side.population}), сообщений {side.messages}"
    )


async def finish_battle(bot, service: WorldService, battle: WorldBattle, *, now: datetime | None = None) -> WorldBattle | None:
    current = now or datetime.now(timezone.utc)
    state_a, state_b = await asyncio.gather(
        service.get_state_by_world_id(battle.state_a),
        service.get_state_by_world_id(battle.state_b),
    )
    end = min(current, battle.ends_at)
    side_a, side_b = await asyncio.gather(
        _measure(bot, state_a, battle.started_at, end),
        _measure(bot, state_b, battle.started_at, end),
    )
    side_a = BattleSide(battle.state_a, side_a.messages, side_a.participants, side_a.population)
    side_b = BattleSide(battle.state_b, side_b.messages, side_b.participants, side_b.population)
    winner = decide_winner(side_a, side_b)
    finished = await asyncio.to_thread(
        service.battles.finish_battle,
        battle.battle_id,
        score_a=side_a.mobilization,
        score_b=side_b.mobilization,
        winner=winner,
        details={
            "messages_a": side_a.messages,
            "messages_b": side_b.messages,
            "participants_a": side_a.participants,
            "participants_b": side_b.participants,
            "population_a": side_a.population,
            "population_b": side_b.population,
        },
        now=current,
    )
    if finished is None:
        return None
    if winner is None:
        actor, target = battle.state_a, battle.state_b
        payload = {"battle_id": battle.battle_id, "draw": True}
    else:
        loser = battle.state_b if winner == battle.state_a else battle.state_a
        winner_side, loser_side = (side_a, side_b) if winner == battle.state_a else (side_b, side_a)
        actor, target = winner, loser
        payload = {
            "battle_id": battle.battle_id,
            "draw": False,
            "winner_score": winner_side.mobilization,
            "loser_score": loser_side.mobilization,
        }
    await service.record_event(
        "battle_finished",
        actor_state=actor,
        target_state=target,
        payload=payload,
        dedupe_key=f"battle_finished:{battle.battle_id}",
    )
    lines = ["🥊 Битва окончена", "", _side_line(state_a, side_a), _side_line(state_b, side_b), ""]
    for state in (state_a, state_b):
        if state is None:
            continue
        if winner is None:
            verdict = "🤝 Ничья. Обе армии одинаково пролежали на диване."
        elif winner == state.world_id:
            verdict = "🏆 Победа! +5 к авторитету на 30 дней."
        else:
            verdict = "💀 Поражение. −3 к авторитету на 30 дней. Можно реваншироваться новой битвой."
        await deliver_text(bot, service, state, "\n".join([*lines, verdict]))
    return finished


async def finish_due_battles(bot, service: WorldService | None = None) -> int:
    service = service or get_world_service()
    if service.battles is None:
        return 0
    due = await asyncio.to_thread(service.battles.list_due_battles, datetime.now(timezone.utc))
    finished = 0
    for battle in due:
        try:
            if await finish_battle(bot, service, battle) is not None:
                finished += 1
        except Exception:
            logging.exception("World battle finish failed battle=%s", battle.battle_id)
    return finished


async def active_battle_lines(service: WorldService, world_id: int) -> list[str]:
    if service.battles is None:
        return []
    battles = await asyncio.to_thread(service.battles.list_active_battles, world_id)
    lines = []
    for battle in battles:
        opponent = battle.opponent_of(world_id)
        lines.append(f"№{opponent}: ещё {human_until(battle.ends_at)}")
    return lines


async def battle_wins(service: WorldService, *, days: int = 30) -> dict[int, int]:
    if service.battles is None:
        return {}
    results = await asyncio.to_thread(service.battles.list_recent_results, days=days, limit=200)
    wins: dict[int, int] = {}
    for battle in results:
        if battle.winner is not None:
            wins[battle.winner] = wins.get(battle.winner, 0) + 1
    return wins
