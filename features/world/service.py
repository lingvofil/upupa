"""Application service for states and diplomatic relations."""

from __future__ import annotations

import asyncio
from collections import defaultdict
import logging
from typing import Protocol

from features.world.authority import (
    AuthorityBreakdown,
    AuthorityInputs,
    build_breakdown,
    load_authority_inputs,
)
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
    def get_relation(self, state_a: int, state_b: int) -> str | None: ...
    def get_pending_request_between(self, state_a: int, state_b: int) -> DiplomaticRequest | None: ...
    def create_request(
        self,
        source_state: int,
        target_state: int,
        kind: str = "alliance",
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
        path = getattr(repository, "path", None)
        if ledger is None and path is not None:
            ledger = WorldLedger(path)
        self.ledger = ledger
        self.expansion = None
        self.battles = None
        self.blocs = None
        if self.ledger is not None:
            self.ledger.init_schema()
            self._init_extensions(self.ledger.path)
            self._bootstrap_ledger()

    def _init_extensions(self, path) -> None:
        """Every World table lives in one file; create them all at startup."""
        from infrastructure.persistence.sqlite_world_battles import SQLiteWorldBattleRepository
        from infrastructure.persistence.sqlite_world_blocs import SQLiteWorldBlocRepository
        from infrastructure.persistence.sqlite_world_expansion import SQLiteWorldExpansionRepository

        self.expansion = SQLiteWorldExpansionRepository(path)
        self.battles = SQLiteWorldBattleRepository(path)
        self.blocs = SQLiteWorldBlocRepository(path)
        for repository in (self.expansion, self.battles, self.blocs):
            repository.init_schema()

    def _bootstrap_ledger(self) -> None:
        """One pass at startup instead of re-checking ledger rows on every read."""
        try:
            for state in self.repository.list_enabled_states(None):
                self.ledger.ensure_details(state)
                self.ledger.ensure_foundation_event(state)
        except Exception:
            logging.exception("World ledger bootstrap failed")

    async def _ensure_ledger_state(self, state: WorldState | None) -> None:
        if state is None or self.ledger is None:
            return
        try:
            await asyncio.to_thread(self.ledger.ensure_details, state)
            await asyncio.to_thread(self.ledger.ensure_foundation_event, state)
        except Exception:
            logging.exception("World ledger bootstrap failed state=%s", state.world_id)

    async def record_event(self, event_type: str, **kwargs) -> bool:
        if self.ledger is None:
            return False
        try:
            return await asyncio.to_thread(self.ledger.record_event, event_type, **kwargs)
        except Exception:
            logging.exception("World ledger event failed type=%s", event_type)
            return False

    # Historical name used by extension modules.
    _record_event = record_event

    # ------------------------------------------------------------ states

    async def enable_state(self, chat_id: int, title: str) -> WorldState:
        previous = await asyncio.to_thread(self.repository.get_state_by_chat_id, chat_id)
        state = await asyncio.to_thread(self.repository.enable_state, chat_id, title)
        await self._ensure_ledger_state(state)
        if previous is not None and not previous.enabled:
            await self.record_event(
                "state_reenabled",
                actor_state=state.world_id,
                payload={"title": state.title},
            )
        return state

    async def disable_state(self, chat_id: int, *, reason: str | None = None) -> WorldState | None:
        previous = await asyncio.to_thread(self.repository.get_state_by_chat_id, chat_id)
        state = await asyncio.to_thread(self.repository.disable_state, chat_id)
        if state is not None and previous is not None and previous.enabled:
            payload = {"title": state.title}
            if reason:
                payload["reason"] = reason
            await self.record_event("state_disabled", actor_state=state.world_id, payload=payload)
            if self.blocs is not None:
                try:
                    await asyncio.to_thread(self.blocs.remove_state_everywhere, state.world_id)
                except Exception:
                    logging.exception("World bloc cleanup failed state=%s", state.world_id)
        return state

    async def migrate_chat(self, old_chat_id: int, new_chat_id: int) -> WorldState | None:
        migrate = getattr(self.repository, "migrate_chat", None)
        if migrate is None:
            return None
        state = await asyncio.to_thread(migrate, old_chat_id, new_chat_id)
        if state is not None:
            logging.info(
                "World state %s migrated chat %s -> %s", state.world_id, old_chat_id, new_chat_id
            )
        return state

    async def get_state(
        self,
        chat_id: int,
        current_title: str | None = None,
    ) -> WorldState | None:
        state = await asyncio.to_thread(self.repository.get_state_by_chat_id, chat_id)
        if state is not None and current_title and state.title != current_title:
            await asyncio.to_thread(self.repository.update_title, chat_id, current_title)
            state = await asyncio.to_thread(self.repository.get_state_by_chat_id, chat_id)
        return state

    async def get_state_by_world_id(self, world_id: int) -> WorldState | None:
        return await asyncio.to_thread(self.repository.get_state_by_world_id, world_id)

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
        return tuple(states)

    async def list_all_states(self) -> tuple[WorldState, ...]:
        states = await asyncio.to_thread(self.repository.list_enabled_states, None)
        return tuple(states)

    async def _build_profile(self, state: WorldState) -> WorldProfile:
        enabled_states, active_allies, all_allies, active_wars, all_wars = await asyncio.gather(
            asyncio.to_thread(self.repository.list_enabled_states, state.world_id),
            asyncio.to_thread(self.repository.list_relation_states, state.world_id, "allied", active_only=True),
            asyncio.to_thread(self.repository.list_relation_states, state.world_id, "allied", active_only=False),
            asyncio.to_thread(self.repository.list_relation_states, state.world_id, "war", active_only=True),
            asyncio.to_thread(self.repository.list_relation_states, state.world_id, "war", active_only=False),
        )
        non_neutral_ids = {item.world_id for item in (*active_allies, *active_wars)}
        neutral = tuple(
            candidate for candidate in enabled_states if candidate.world_id not in non_neutral_ids
        )
        return WorldProfile(
            state=state,
            allies=tuple(active_allies),
            wars=tuple(active_wars),
            neutral=neutral,
            inactive_allies=tuple(item for item in all_allies if not item.enabled),
            inactive_wars=tuple(item for item in all_wars if not item.enabled),
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

    async def get_relation(self, state_a: int, state_b: int) -> str | None:
        return await asyncio.to_thread(self.repository.get_relation, state_a, state_b)

    # ------------------------------------------------------------ details

    async def get_details(self, world_id: int) -> WorldDetails | None:
        if self.ledger is None:
            return None
        details = await asyncio.to_thread(self.ledger.get_details, world_id)
        if details is not None:
            return details
        state = await self.get_state_by_world_id(world_id)
        if state is None:
            return None
        return await asyncio.to_thread(self.ledger.ensure_details, state)

    async def list_details(self) -> dict[int, WorldDetails]:
        if self.ledger is None:
            return {}
        return await asyncio.to_thread(self.ledger.list_details)

    async def set_ambassador(
        self,
        world_id: int,
        user_id: int | None,
        name: str | None,
    ) -> WorldDetails | None:
        if await self.get_details(world_id) is None:
            return None
        details = await asyncio.to_thread(self.ledger.set_ambassador, world_id, user_id, name)
        await self.record_event(
            "ambassador_set" if user_id is not None else "ambassador_removed",
            actor_state=world_id,
            payload={"name": name or ""},
        )
        return details

    async def set_setting(self, world_id: int, key: str, value: bool) -> WorldDetails | None:
        if await self.get_details(world_id) is None:
            return None
        return await asyncio.to_thread(self.ledger.set_setting, world_id, key, value)

    async def is_ambassador(self, chat_id: int, user_id: int) -> bool:
        state = await self.get_state(chat_id)
        if state is None or not state.enabled or self.ledger is None:
            return False
        details = await asyncio.to_thread(self.ledger.get_details, state.world_id)
        return bool(details and details.ambassador_user_id == int(user_id))

    # ---------------------------------------------------------- alliances

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
        allied = await asyncio.to_thread(self.repository.has_alliance, source.world_id, target.world_id)
        if not allied:
            return "not_allied", source, target, None
        clean = " ".join(name.split()).strip()[:80]
        if not clean:
            return "empty", source, target, None
        if self.ledger is None:
            return "unavailable", source, target, None
        await asyncio.to_thread(self.ledger.set_alliance_name, source.world_id, target.world_id, clean)
        await self.record_event(
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

    async def get_alliance_names(self, world_id: int) -> dict[int, str]:
        if self.ledger is None:
            return {}
        return await asyncio.to_thread(self.ledger.get_alliance_names, world_id)

    async def list_relations(self, *, active_only: bool = True) -> tuple[WorldRelation, ...]:
        if self.ledger is None:
            return ()
        return tuple(await asyncio.to_thread(self.ledger.list_relations, active_only=active_only))

    async def list_events(
        self,
        *,
        limit: int = 30,
        days: int | None = None,
        world_id: int | None = None,
        event_types: set[str] | None = None,
        actor_only: bool = False,
    ) -> tuple[WorldEvent, ...]:
        if self.ledger is None:
            return ()
        events = await asyncio.to_thread(
            self.ledger.list_events,
            limit=limit,
            days=days,
            world_id=world_id,
            event_types=event_types,
            actor_only=actor_only,
        )
        return tuple(events)

    async def latest_event(
        self,
        event_types: set[str],
        *,
        actor_state: int | None = None,
        target_state: int | None = None,
    ) -> WorldEvent | None:
        if self.ledger is None:
            return None
        return await asyncio.to_thread(
            self.ledger.latest_event,
            event_types,
            actor_state=actor_state,
            target_state=target_state,
        )

    # ------------------------------------------------------- diplomacy core

    async def _propose(
        self,
        source_chat_id: int,
        source_title: str,
        target_world_id: int,
        kind: str,
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
            kind,
        )
        if creation_status == "created" and request is not None:
            await self.record_event(
                "alliance_proposed" if kind == "alliance" else "peace_proposed",
                actor_state=source.world_id,
                target_state=target.world_id,
                dedupe_key=f"{kind}_proposed:{request.request_id}",
            )
        return ProposalResult(creation_status, source=source, target=target, request=request)

    async def propose_alliance(self, source_chat_id: int, source_title: str, target_world_id: int) -> ProposalResult:
        return await self._propose(source_chat_id, source_title, target_world_id, "alliance")

    async def propose_peace(self, source_chat_id: int, source_title: str, target_world_id: int) -> ProposalResult:
        return await self._propose(source_chat_id, source_title, target_world_id, "peace")

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
            kind = request.kind
            if status == "accepted" and kind == "alliance":
                await self.record_event(
                    "alliance_formed",
                    actor_state=source.world_id,
                    target_state=target.world_id,
                    dedupe_key=f"alliance_formed:{request.request_id}",
                )
            elif status == "accepted" and kind == "peace":
                await self._stop_battles(source.world_id, target.world_id, "peace")
                await self.record_event(
                    "war_ended",
                    actor_state=source.world_id,
                    target_state=target.world_id,
                    payload={"by": "peace"},
                    dedupe_key=f"peace_concluded:{request.request_id}",
                )
            elif status == "rejected":
                await self.record_event(
                    "alliance_rejected" if kind == "alliance" else "peace_rejected",
                    actor_state=target.world_id,
                    target_state=source.world_id,
                    dedupe_key=f"{kind}_rejected:{request.request_id}",
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
        removed = await asyncio.to_thread(self.repository.break_alliance, source.world_id, target.world_id)
        if removed:
            if self.ledger is not None:
                await asyncio.to_thread(self.ledger.clear_alliance_name, source.world_id, target.world_id)
            await self.record_event(
                "alliance_broken",
                actor_state=source.world_id,
                target_state=target.world_id,
            )
        return BreakAllianceResult("broken" if removed else "not_allied", source=source, target=target)

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
                await asyncio.to_thread(self.ledger.clear_alliance_name, source.world_id, target.world_id)
            await self.record_event(
                "war_declared",
                actor_state=source.world_id,
                target_state=target.world_id,
                payload={"previous_relation": previous_relation or "neutral"},
            )
        return WarResult(status, source=source, target=target, previous_relation=previous_relation)

    async def _stop_battles(self, state_a: int, state_b: int, reason: str) -> None:
        if self.battles is None:
            return
        try:
            await asyncio.to_thread(self.battles.cancel_active_battles, state_a, state_b, reason=reason)
        except Exception:
            logging.exception("World battle cancel failed %s-%s", state_a, state_b)

    async def end_war(
        self,
        source_chat_id: int,
        source_title: str,
        target_world_id: int,
    ) -> WarResult:
        """Low-level peace without consent; UI uses mutual peace or capitulation."""
        source = await self.get_state(source_chat_id, source_title)
        if source is None or not source.enabled:
            return WarResult("source_disabled", source=source)
        target = await self.get_state_by_world_id(target_world_id)
        if target is None:
            return WarResult("unknown_target", source=source)
        if source.world_id == target.world_id:
            return WarResult("self", source=source, target=target)
        ended = await asyncio.to_thread(self.repository.end_war, source.world_id, target.world_id)
        if ended:
            await self._stop_battles(source.world_id, target.world_id, "peace")
            await self.record_event("war_ended", actor_state=source.world_id, target_state=target.world_id)
        return WarResult(
            "ended" if ended else "not_at_war",
            source=source,
            target=target,
            previous_relation="war" if ended else None,
        )

    async def capitulate(
        self,
        source_chat_id: int,
        source_title: str,
        target_world_id: int,
    ) -> WarResult:
        """Unilateral exit from a war at the price of authority."""
        source = await self.get_state(source_chat_id, source_title)
        if source is None or not source.enabled:
            return WarResult("source_disabled", source=source)
        target = await self.get_state_by_world_id(target_world_id)
        if target is None:
            return WarResult("unknown_target", source=source)
        if source.world_id == target.world_id:
            return WarResult("self", source=source, target=target)
        ended = await asyncio.to_thread(self.repository.end_war, source.world_id, target.world_id)
        if not ended:
            return WarResult("not_at_war", source=source, target=target)
        await self._stop_battles(source.world_id, target.world_id, "capitulation")
        await self.record_event(
            "war_capitulated",
            actor_state=source.world_id,
            target_state=target.world_id,
        )
        return WarResult("capitulated", source=source, target=target, previous_relation="war")

    # ----------------------------------------------------------- authority

    async def get_authority_map(self) -> dict[int, AuthorityBreakdown]:
        states, relations = await asyncio.gather(self.list_all_states(), self.list_relations(active_only=True))
        if self.ledger is not None:
            inputs = await asyncio.to_thread(load_authority_inputs, self.ledger.path)
        else:
            inputs = AuthorityInputs()
        allies: dict[int, int] = defaultdict(int)
        wars: dict[int, int] = defaultdict(int)
        since: dict[int, list] = defaultdict(list)
        for relation in relations:
            if relation.relation == "allied":
                for side in (relation.state_a, relation.state_b):
                    allies[side] += 1
                    since[side].append(relation.since)
            elif relation.relation == "war":
                wars[relation.state_a] += 1
                wars[relation.state_b] += 1
        return {
            state.world_id: build_breakdown(
                state.world_id,
                allies=allies[state.world_id],
                wars=wars[state.world_id],
                alliance_since=since[state.world_id],
                inputs=inputs,
            )
            for state in states
        }

    async def get_authority(self, world_id: int) -> AuthorityBreakdown:
        authority = (await self.get_authority_map()).get(int(world_id))
        return authority or AuthorityBreakdown(base=50)


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
