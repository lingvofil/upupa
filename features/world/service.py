"""Application service for states and diplomatic relations."""

from __future__ import annotations

import asyncio
import logging
from typing import Protocol

from features.world.ledger import WorldDetails, WorldEvent, WorldLedger, WorldRelation
from features.world.models import (
    BreakAllianceResult,
    DiplomaticRequest,
    ProposalResult,
    ResolutionResult,
    WarResult,
    WorldProfile,
    WorldState,
)


class WorldRepository(Protocol):
    def enable_state(self, chat_id: int, title: str) -> WorldState: ...
    def disable_state(self, chat_id: int) -> WorldState | None: ...
    def update_title(self, chat_id: int, title: str) -> None: ...
    def get_state_by_chat_id(self, chat_id: int) -> WorldState | None: ...
    def get_state_by_world_id(self, world_id: int) -> WorldState | None: ...
    def list_enabled_states(self, exclude_world_id: int | None = None) -> list[WorldState]: ...
    def list_relation_states(
        self,
        world_id: int,
        relation: str,
        *,
        active_only: bool,
    ) -> list[WorldState]: ...
    def has_alliance(self, state_a: int, state_b: int) -> bool: ...
    def get_pending_request_between(self, state_a: int, state_b: int) -> DiplomaticRequest | None: ...
    def create_request(
        self,
        source_state: int,
        target_state: int,
    ) -> tuple[str, DiplomaticRequest | None]: ...
    def cancel_request(self, request_id: int) -> bool: ...
    def resolve_request(
        self,
        request_id: int,
        target_chat_id: int,
        decision: str,
    ) -> tuple[str, DiplomaticRequest | None, WorldState | None, WorldState | None]: ...
    def break_alliance(self, state_a: int, state_b: int) -> bool: ...
    def declare_war(self, state_a: int, state_b: int) -> tuple[str, str | None]: ...
    def end_war(self, state_a: int, state_b: int) -> bool: ...


class WorldService:
    def __init__(self, repository: WorldRepository, ledger: WorldLedger | None = None) -> None:
        self.repository = repository
        if ledger is None:
            path = getattr(repository, "path", None)
            ledger = WorldLedger(path) if path is not None else None
        self.ledger = ledger
        if self.ledger is not None:
            self.ledger.init_schema()

    async def _ensure_ledger_state(self, state: WorldState | None) -> None:
        if state is None or self.ledger is None:
            return
        try:
            await asyncio.gather(
                asyncio.to_thread(self.ledger.ensure_details, state),
                asyncio.to_thread(self.ledger.ensure_foundation_event, state),
            )
        except Exception:
            logging.exception("World ledger bootstrap failed state=%s", state.world_id)

    async def _record_event(self, event_type: str, **kwargs) -> None:
        if self.ledger is None:
            return
        try:
            await asyncio.to_thread(self.ledger.record_event, event_type, **kwargs)
        except Exception:
            logging.exception("World ledger event failed type=%s", event_type)

    async def enable_state(self, chat_id: int, title: str) -> WorldState:
        previous = await asyncio.to_thread(self.repository.get_state_by_chat_id, chat_id)
        state = await asyncio.to_thread(self.repository.enable_state, chat_id, title)
        await self._ensure_ledger_state(state)
        if previous is not None and not previous.enabled:
            await self._record_event(
                "state_reenabled",
                actor_state=state.world_id,
                payload={"title": state.title},
            )
        return state

    async def disable_state(self, chat_id: int) -> WorldState | None:
        previous = await asyncio.to_thread(self.repository.get_state_by_chat_id, chat_id)
        state = await asyncio.to_thread(self.repository.disable_state, chat_id)
        if state is not None and previous is not None and previous.enabled:
            await self._record_event(
                "state_disabled",
                actor_state=state.world_id,
                payload={"title": state.title},
            )
        return state

    async def get_state(
        self,
        chat_id: int,
        current_title: str | None = None,
    ) -> WorldState | None:
        if current_title:
            await asyncio.to_thread(self.repository.update_title, chat_id, current_title)
        state = await asyncio.to_thread(self.repository.get_state_by_chat_id, chat_id)
        await self._ensure_ledger_state(state)
        return state

    async def get_state_by_world_id(self, world_id: int) -> WorldState | None:
        state = await asyncio.to_thread(self.repository.get_state_by_world_id, world_id)
        await self._ensure_ledger_state(state)
        return state

    async def is_enabled(self, chat_id: int) -> bool:
        state = await self.get_state(chat_id)
        return bool(state and state.enabled)

    async def list_states(
        self,
        chat_id: int,
        current_title: str | None = None,
    ) -> tuple[WorldState, ...] | None:
        state = await self.get_state(chat_id, current_title)
        if state is None or not state.enabled:
            return None
        states = await asyncio.to_thread(self.repository.list_enabled_states, state.world_id)
        await asyncio.gather(*(self._ensure_ledger_state(item) for item in states))
        return tuple(states)

    async def list_all_states(self) -> tuple[WorldState, ...]:
        states = await asyncio.to_thread(self.repository.list_enabled_states, None)
        await asyncio.gather(*(self._ensure_ledger_state(item) for item in states))
        return tuple(states)

    async def _build_profile(self, state: WorldState) -> WorldProfile:
        enabled_states, active_allies, all_allies, active_wars, all_wars = await asyncio.gather(
            asyncio.to_thread(self.repository.list_enabled_states, state.world_id),
            asyncio.to_thread(
                self.repository.list_relation_states,
                state.world_id,
                "allied",
                active_only=True,
            ),
            asyncio.to_thread(
                self.repository.list_relation_states,
                state.world_id,
                "allied",
                active_only=False,
            ),
            asyncio.to_thread(
                self.repository.list_relation_states,
                state.world_id,
                "war",
                active_only=True,
            ),
            asyncio.to_thread(
                self.repository.list_relation_states,
                state.world_id,
                "war",
                active_only=False,
            ),
        )
        non_neutral_ids = {item.world_id for item in (*active_allies, *active_wars)}
        neutral = tuple(
            candidate for candidate in enabled_states if candidate.world_id not in non_neutral_ids
        )
        inactive_allies = tuple(item for item in all_allies if not item.enabled)
        inactive_wars = tuple(item for item in all_wars if not item.enabled)
        return WorldProfile(
            state=state,
            allies=tuple(active_allies),
            wars=tuple(active_wars),
            neutral=neutral,
            inactive_allies=inactive_allies,
            inactive_wars=inactive_wars,
        )

    async def get_profile(
        self,
        chat_id: int,
        current_title: str | None = None,
    ) -> WorldProfile | None:
        state = await self.get_state(chat_id, current_title)
        if state is None or not state.enabled:
            return None
        return await self._build_profile(state)

    async def get_profile_by_world_id(self, world_id: int) -> WorldProfile | None:
        state = await self.get_state_by_world_id(world_id)
        if state is None or not state.enabled:
            return None
        return await self._build_profile(state)

    async def get_details(self, world_id: int) -> WorldDetails | None:
        state = await self.get_state_by_world_id(world_id)
        if state is None or self.ledger is None:
            return None
        return await asyncio.to_thread(self.ledger.ensure_details, state)

    async def set_ambassador(
        self,
        world_id: int,
        user_id: int | None,
        name: str | None,
    ) -> WorldDetails | None:
        state = await self.get_state_by_world_id(world_id)
        if state is None or self.ledger is None:
            return None
        await asyncio.to_thread(self.ledger.ensure_details, state)
        details = await asyncio.to_thread(self.ledger.set_ambassador, world_id, user_id, name)
        await self._record_event(
            "ambassador_set" if user_id is not None else "ambassador_removed",
            actor_state=world_id,
            payload={"name": name or ""},
        )
        return details

    async def is_ambassador(self, chat_id: int, user_id: int) -> bool:
        state = await self.get_state(chat_id)
        if state is None or not state.enabled:
            return False
        details = await self.get_details(state.world_id)
        return bool(details and details.ambassador_user_id == int(user_id))

    async def name_alliance(
        self,
        source_chat_id: int,
        source_title: str,
        target_world_id: int,
        name: str,
    ) -> tuple[str, WorldState | None, WorldState | None, str | None]:
        source = await self.get_state(source_chat_id, source_title)
        if source is None or not source.enabled:
            return "source_disabled", source, None, None
        target = await self.get_state_by_world_id(target_world_id)
        if target is None:
            return "unknown_target", source, None, None
        if source.world_id == target.world_id:
            return "self", source, target, None
        allied = await asyncio.to_thread(
            self.repository.has_alliance,
            source.world_id,
            target.world_id,
        )
        if not allied:
            return "not_allied", source, target, None
        clean = " ".join(name.split()).strip()[:80]
        if not clean:
            return "empty", source, target, None
        if self.ledger is None:
            return "unavailable", source, target, None
        await asyncio.to_thread(
            self.ledger.set_alliance_name,
            source.world_id,
            target.world_id,
            clean,
        )
        await self._record_event(
            "alliance_named",
            actor_state=source.world_id,
            target_state=target.world_id,
            payload={"name": clean},
        )
        return "named", source, target, clean

    async def get_alliance_name(self, state_a: int, state_b: int) -> str | None:
        if self.ledger is None:
            return None
        return await asyncio.to_thread(self.ledger.get_alliance_name, state_a, state_b)

    async def list_relations(self, *, active_only: bool = True) -> tuple[WorldRelation, ...]:
        if self.ledger is None:
            return ()
        relations = await asyncio.to_thread(self.ledger.list_relations, active_only=active_only)
        return tuple(relations)

    async def list_events(
        self,
        *,
        limit: int = 30,
        days: int | None = None,
        world_id: int | None = None,
        event_types: set[str] | None = None,
    ) -> tuple[WorldEvent, ...]:
        if self.ledger is None:
            return ()
        events = await asyncio.to_thread(
            self.ledger.list_events,
            limit=limit,
            days=days,
            world_id=world_id,
            event_types=event_types,
        )
        return tuple(events)

    async def propose_alliance(
        self,
        source_chat_id: int,
        source_title: str,
        target_world_id: int,
    ) -> ProposalResult:
        source = await self.get_state(source_chat_id, source_title)
        if source is None or not source.enabled:
            return ProposalResult("source_disabled", source=source)

        target = await self.get_state_by_world_id(target_world_id)
        if target is None:
            return ProposalResult("unknown_target", source=source)
        if not target.enabled:
            return ProposalResult("target_disabled", source=source, target=target)
        if source.world_id == target.world_id:
            return ProposalResult("self", source=source, target=target)

        creation_status, request = await asyncio.to_thread(
            self.repository.create_request,
            source.world_id,
            target.world_id,
        )
        if creation_status == "created" and request is not None:
            await self._record_event(
                "alliance_proposed",
                actor_state=source.world_id,
                target_state=target.world_id,
                dedupe_key=f"alliance_proposed:{request.request_id}",
            )
        return ProposalResult(
            creation_status,
            source=source,
            target=target,
            request=request,
        )

    async def cancel_request(self, request_id: int) -> bool:
        return await asyncio.to_thread(self.repository.cancel_request, request_id)

    async def resolve_request(
        self,
        request_id: int,
        target_chat_id: int,
        decision: str,
    ) -> ResolutionResult:
        status, request, source, target = await asyncio.to_thread(
            self.repository.resolve_request,
            request_id,
            target_chat_id,
            decision,
        )
        if request is not None and source is not None and target is not None:
            if status == "accepted":
                await self._record_event(
                    "alliance_formed",
                    actor_state=source.world_id,
                    target_state=target.world_id,
                    dedupe_key=f"alliance_formed:{request.request_id}",
                )
            elif status == "rejected":
                await self._record_event(
                    "alliance_rejected",
                    actor_state=target.world_id,
                    target_state=source.world_id,
                    dedupe_key=f"alliance_rejected:{request.request_id}",
                )
        return ResolutionResult(status, request, source, target)

    async def break_alliance(
        self,
        source_chat_id: int,
        source_title: str,
        target_world_id: int,
    ) -> BreakAllianceResult:
        source = await self.get_state(source_chat_id, source_title)
        if source is None or not source.enabled:
            return BreakAllianceResult("source_disabled", source=source)

        target = await self.get_state_by_world_id(target_world_id)
        if target is None:
            return BreakAllianceResult("unknown_target", source=source)
        if source.world_id == target.world_id:
            return BreakAllianceResult("self", source=source, target=target)

        removed = await asyncio.to_thread(
            self.repository.break_alliance,
            source.world_id,
            target.world_id,
        )
        if removed:
            if self.ledger is not None:
                await asyncio.to_thread(
                    self.ledger.clear_alliance_name,
                    source.world_id,
                    target.world_id,
                )
            await self._record_event(
                "alliance_broken",
                actor_state=source.world_id,
                target_state=target.world_id,
            )
        return BreakAllianceResult(
            "broken" if removed else "not_allied",
            source=source,
            target=target,
        )

    async def declare_war(
        self,
        source_chat_id: int,
        source_title: str,
        target_world_id: int,
    ) -> WarResult:
        source = await self.get_state(source_chat_id, source_title)
        if source is None or not source.enabled:
            return WarResult("source_disabled", source=source)

        target = await self.get_state_by_world_id(target_world_id)
        if target is None:
            return WarResult("unknown_target", source=source)
        if source.world_id == target.world_id:
            return WarResult("self", source=source, target=target)
        if not target.enabled:
            return WarResult("target_disabled", source=source, target=target)

        status, previous_relation = await asyncio.to_thread(
            self.repository.declare_war,
            source.world_id,
            target.world_id,
        )
        if status == "declared":
            if previous_relation == "allied" and self.ledger is not None:
                await asyncio.to_thread(
                    self.ledger.clear_alliance_name,
                    source.world_id,
                    target.world_id,
                )
            await self._record_event(
                "war_declared",
                actor_state=source.world_id,
                target_state=target.world_id,
                payload={"previous_relation": previous_relation or "neutral"},
            )
        return WarResult(
            status,
            source=source,
            target=target,
            previous_relation=previous_relation,
        )

    async def end_war(
        self,
        source_chat_id: int,
        source_title: str,
        target_world_id: int,
    ) -> WarResult:
        source = await self.get_state(source_chat_id, source_title)
        if source is None or not source.enabled:
            return WarResult("source_disabled", source=source)

        target = await self.get_state_by_world_id(target_world_id)
        if target is None:
            return WarResult("unknown_target", source=source)
        if source.world_id == target.world_id:
            return WarResult("self", source=source, target=target)

        ended = await asyncio.to_thread(
            self.repository.end_war,
            source.world_id,
            target.world_id,
        )
        if ended:
            await self._record_event(
                "war_ended",
                actor_state=source.world_id,
                target_state=target.world_id,
            )
        return WarResult(
            "ended" if ended else "not_at_war",
            source=source,
            target=target,
            previous_relation="war" if ended else None,
        )


_world_service: WorldService | None = None


def configure_world_service(service: WorldService) -> None:
    global _world_service
    _world_service = service


def get_world_service() -> WorldService:
    if _world_service is None:
        raise RuntimeError("WorldService is not configured")
    return _world_service


async def is_world_enabled(chat_id: int | str) -> bool:
    """Settings-safe status lookup; unconfigured test/import state means opt-out."""
    if _world_service is None:
        return False
    return await _world_service.is_enabled(int(chat_id))


def calculate_authority(profile: WorldProfile) -> int:
    """V1 authority: diplomatic reach without introducing a separate reputation economy."""
    return max(0, min(100, 50 + 8 * len(profile.allies) - 5 * len(profile.wars)))


def _ids(states: tuple[WorldState, ...]) -> str:
    return ", ".join(f"№{state.world_id}" for state in states) or "—"


def format_world_profile(
    profile: WorldProfile,
    population: int | None = None,
    *,
    details: WorldDetails | None = None,
    authority: int | None = None,
    most_active: str | None = None,
    alliance_names: dict[int, str] | None = None,
) -> str:
    population_text = str(population) if population is not None else "неизвестно"
    lines = [
        f"🏳 Государство №{profile.state.world_id}",
        profile.state.title,
        f"👥 Долбоебов: {population_text}",
        f"🏛 Основано: {profile.state.created_at.strftime('%d.%m.%Y')}",
    ]
    if details is not None:
        lines.extend(
            [
                f"🏛 Государственный строй: {details.government_form}",
                f"🌦 Климат: {details.climate}",
                f"☠️ Главная угроза: {details.main_threat}",
            ]
        )
        if details.ambassador_name:
            lines.append(f"🎩 Посол: {details.ambassador_name}")
    if most_active:
        lines.append(f"🗣 Самый активный долбоеб за 7 дней: {most_active}")
    if authority is not None:
        lines.append(f"🌐 Международный авторитет: {authority}/100")

    lines.extend(["", "Дипломатические отношения:"])
    if profile.allies:
        names = alliance_names or {}
        allies = []
        for state in profile.allies:
            suffix = f" «{names[state.world_id]}»" if state.world_id in names else ""
            allies.append(f"№{state.world_id}{suffix}")
        lines.append(f"🤝 Союзники: {', '.join(allies)}")
    else:
        lines.append("🤝 Союзники: —")
    lines.extend(
        [
            f"⚔️ Война: {_ids(profile.wars)}",
            f"😐 Нейтральные: {_ids(profile.neutral)}",
        ]
    )
    if profile.inactive_allies:
        lines.append(f"⏸ Союзы вне мира: {_ids(profile.inactive_allies)}")
    if profile.inactive_wars:
        lines.append(f"⏸ Войны вне мира: {_ids(profile.inactive_wars)}")
    return "\n".join(lines)


def format_states(states: tuple[WorldState, ...]) -> str:
    if not states:
        return "🌍 Других активных государств пока нет."
    lines = ["🌍 Государства Мира Упупы:"]
    lines.extend(f"№{state.world_id} — {state.title}" for state in states)
    return "\n".join(lines)


def format_diplomacy(
    profile: WorldProfile,
    alliance_names: dict[int, str] | None = None,
) -> str:
    lines = [f"🌍 Дипломатия государства №{profile.state.world_id}"]
    names = alliance_names or {}
    if profile.allies:
        lines.append("\n🤝 Союзники:")
        for state in profile.allies:
            suffix = f" — союз «{names[state.world_id]}»" if state.world_id in names else ""
            lines.append(f"№{state.world_id} — {state.title}{suffix}")
    else:
        lines.append("\n🤝 Союзников пока нет.")

    if profile.wars:
        lines.append("\n⚔️ Война:")
        lines.extend(f"№{state.world_id} — {state.title}" for state in profile.wars)
    else:
        lines.append("\n⚔️ Ни с кем не воюем.")

    if profile.neutral:
        lines.append("\n😐 Нейтральные:")
        lines.extend(f"№{state.world_id} — {state.title}" for state in profile.neutral)
    if profile.inactive_allies:
        lines.append("\n⏸ Сохранённые союзы с выключенными государствами:")
        lines.extend(f"№{state.world_id} — {state.title}" for state in profile.inactive_allies)
    if profile.inactive_wars:
        lines.append("\n⏸ Сохранённые войны с выключенными государствами:")
        lines.extend(f"№{state.world_id} — {state.title}" for state in profile.inactive_wars)
    return "\n".join(lines)
