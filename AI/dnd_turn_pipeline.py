"""One explicit, ordered pipeline for the DnD response guards.

``dnd.parse_and_execute_turn`` used to be reassigned by 24 modules; the order
of the resulting onion was whatever order the installers happened to run in,
and nothing showed it. Ten consecutive layers never take over the turn: they
transform the master's response, call the next layer and sometimes react
afterwards (poll agency, spotlight, target mentions, growth, item boosts, roll
ability labels, weakness luck, special-move offers, scene tactics,
conditions). They now register a step here instead of wrapping.

A step is ``async def step(bot, chat_id, response, call_next)``: everything
before ``await call_next(...)`` runs on the way in, everything after on the
way out — exactly the old wrapper semantics. Steps run by descending
``order`` (higher = outer), so the historical chain is pinned by numbers and
``describe(dnd)`` lists it; a test fixes that list.

The same mechanism runs the request decorators around the provider call
(``generate_session_response``): turn contract, pacing, growth, item uses,
inventory audit, weakness luck, player agency.

Interceptors that may answer the turn themselves (combat, campaign end,
durable recovery, corrections) still wrap these pipelines.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable

Step = Callable[..., Awaitable]

PARSE = "parse_and_execute_turn"
GENERATE = "generate_session_response"

# Historical chain position per hook, outer → inner. Gaps leave room for new steps.
ORDER = {
    PARSE: {
        "poll_agency": 100,
        "spotlight": 90,
        "target_mentions": 80,
        "growth": 70,
        "item_actions": 60,
        "roll_ability_display": 50,
        "weakness_luck": 40,
        "special_moves": 30,
        "scene_tactics": 20,
        "conditions": 10,
    },
    # Request decorators around the provider call: each one enriches the
    # prompt and/or post-processes the response, never answers by itself.
    GENERATE: {
        "turn_contract": 70,
        "pacing": 60,
        "growth": 50,
        "item_actions": 40,
        "inventory_reliability": 30,
        "weakness_luck": 20,
        "player_agency": 10,
    },
}


@dataclass(frozen=True)
class TurnStep:
    name: str
    order: int
    run: Step


def _registry_attr(hook: str) -> str:
    return "_upupa_turn_steps" if hook == PARSE else f"_upupa_steps_{hook}"


def _ensure_installed(dnd, hook: str) -> dict[str, TurnStep]:
    attr = _registry_attr(hook)
    steps = getattr(dnd, attr, None)
    if isinstance(steps, dict):
        return steps
    steps = {}
    setattr(dnd, attr, steps)
    inner = getattr(dnd, hook)

    async def run_pipeline(*args):
        chain = sorted(steps.values(), key=lambda step: (-step.order, step.name))

        async def call(index, *call_args):
            if index >= len(chain):
                return await inner(*call_args)

            async def call_next(*next_args):
                return await call(index + 1, *next_args)

            return await chain[index].run(*call_args, call_next)

        return await call(0, *args)

    run_pipeline._upupa_turn_pipeline = hook
    setattr(dnd, hook, run_pipeline)
    return steps


def register_step(dnd, hook: str, name: str, run: Step, *, order: int | None = None) -> TurnStep:
    """Add (or replace) one named step of ``hook``; installs its pipeline on first use.

    The pipeline wrapper sits where the first step was registered — in the
    production composition that is the historical position of the innermost
    step of the group (conditions / player_agency).
    """
    if order is None:
        order = ORDER.get(hook, {}).get(name)
    if order is None:
        raise ValueError(f"DnD {hook} step {name!r} needs an explicit order")
    steps = _ensure_installed(dnd, hook)
    steps[name] = TurnStep(name=name, order=int(order), run=run)
    return steps[name]


def register_turn_step(dnd, name: str, run: Step, *, order: int | None = None) -> TurnStep:
    """Response guard: ``async def step(bot, chat_id, response, call_next)``."""
    return register_step(dnd, PARSE, name, run, order=order)


def register_generation_step(dnd, name: str, run: Step, *, order: int | None = None) -> TurnStep:
    """Request decorator: ``async def step(session, prompt, call_next)``."""
    return register_step(dnd, GENERATE, name, run, order=order)


def describe(dnd, hook: str = PARSE) -> list[str]:
    """Registered steps of ``hook``, outer → inner."""
    steps = getattr(dnd, _registry_attr(hook), None) or {}
    return [step.name for step in sorted(steps.values(), key=lambda step: (-step.order, step.name))]


__all__ = ["GENERATE", "ORDER", "PARSE", "TurnStep", "describe", "register_generation_step", "register_step",
           "register_turn_step"]
