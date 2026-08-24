"""Application service for states and diplomatic relations."""

from __future__ import annotations

import asyncio
from typing import Protocol

from features.world.models import (
    BreakAllianceResult,
    DiplomaticRequest,
    ProposalResult,
    ResolutionResult,
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
    def list_allied_states(self, world_id: int, *, active_only: bool) -> list[WorldState]: ...
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


class WorldService:
    def __init__(self, repository: WorldRepository) -> None:
        self.repository = repository

    async def enable_state(self, chat_id: int, title: str) -> WorldState:
        return await asyncio.to_thread(self.repository.enable_state, chat_id, title)

    async def disable_state(self, chat_id: int) -> WorldState | None:
        return await asyncio.to_thread(self.repository.disable_state, chat_id)

    async def get_state(
        self,
        chat_id: int,
        current_title: str | None = None,
    ) -> WorldState | None:
        if current_title:
            await asyncio.to_thread(self.repository.update_title, chat_id, current_title)
        return await asyncio.to_thread(self.repository.get_state_by_chat_id, chat_id)

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
        states = await asyncio.to_thread(
            self.repository.list_enabled_states,
            state.world_id,
        )
        return tuple(states)

    async def get_profile(
        self,
        chat_id: int,
        current_title: str | None = None,
    ) -> WorldProfile | None:
        state = await self.get_state(chat_id, current_title)
        if state is None or not state.enabled:
            return None

        enabled_states, active_allies, all_allies = await asyncio.gather(
            asyncio.to_thread(self.repository.list_enabled_states, state.world_id),
            asyncio.to_thread(
                self.repository.list_allied_states,
                state.world_id,
                active_only=True,
            ),
            asyncio.to_thread(
                self.repository.list_allied_states,
                state.world_id,
                active_only=False,
            ),
        )
        active_ally_ids = {ally.world_id for ally in active_allies}
        neutral = tuple(
            candidate
            for candidate in enabled_states
            if candidate.world_id not in active_ally_ids
        )
        inactive_allies = tuple(ally for ally in all_allies if not ally.enabled)
        return WorldProfile(
            state=state,
            allies=tuple(active_allies),
            neutral=neutral,
            inactive_allies=inactive_allies,
        )

    async def propose_alliance(
        self,
        source_chat_id: int,
        source_title: str,
        target_world_id: int,
    ) -> ProposalResult:
        source = await self.get_state(source_chat_id, source_title)
        if source is None or not source.enabled:
            return ProposalResult("source_disabled", source=source)

        target = await asyncio.to_thread(
            self.repository.get_state_by_world_id,
            target_world_id,
        )
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

        target = await asyncio.to_thread(
            self.repository.get_state_by_world_id,
            target_world_id,
        )
        if target is None:
            return BreakAllianceResult("unknown_target", source=source)
        if source.world_id == target.world_id:
            return BreakAllianceResult("self", source=source, target=target)

        removed = await asyncio.to_thread(
            self.repository.break_alliance,
            source.world_id,
            target.world_id,
        )
        return BreakAllianceResult(
            "broken" if removed else "not_allied",
            source=source,
            target=target,
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


def format_world_profile(profile: WorldProfile) -> str:
    allies = ", ".join(f"№{state.world_id}" for state in profile.allies) or "—"
    neutral = ", ".join(f"№{state.world_id}" for state in profile.neutral) or "—"
    lines = [
        f"🏳 Государство №{profile.state.world_id}",
        profile.state.title,
        f"Создано: {profile.state.created_at.strftime('%d.%m.%Y')}",
        "",
        "Дипломатические отношения:",
        f"🤝 Союзники: {allies}",
        f"😐 Нейтральные: {neutral}",
    ]
    if profile.inactive_allies:
        inactive = ", ".join(f"№{state.world_id}" for state in profile.inactive_allies)
        lines.append(f"⏸ Союзы вне мира: {inactive}")
    return "\n".join(lines)


def format_states(states: tuple[WorldState, ...]) -> str:
    if not states:
        return "🌍 Других активных государств пока нет."
    lines = ["🌍 Государства Мира Упупы:"]
    lines.extend(f"№{state.world_id} — {state.title}" for state in states)
    return "\n".join(lines)


def format_diplomacy(profile: WorldProfile) -> str:
    lines = [f"🌍 Дипломатия государства №{profile.state.world_id}"]
    if profile.allies:
        lines.append("\n🤝 Союзники:")
        lines.extend(f"№{state.world_id} — {state.title}" for state in profile.allies)
    else:
        lines.append("\n🤝 Союзников пока нет.")

    if profile.neutral:
        lines.append("\n😐 Нейтральные:")
        lines.extend(f"№{state.world_id} — {state.title}" for state in profile.neutral)
    if profile.inactive_allies:
        lines.append("\n⏸ Сохранённые союзы с выключенными государствами:")
        lines.extend(f"№{state.world_id} — {state.title}" for state in profile.inactive_allies)
    return "\n".join(lines)
