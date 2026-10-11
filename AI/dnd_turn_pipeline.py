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

Interceptors that may answer the turn themselves (combat, campaign end,
durable recovery, corrections) still wrap the parser around this pipeline.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable

Step = Callable[..., Awaitable]

# Historical chain position, outer → inner. Gaps leave room for new steps.
ORDER = {
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
}


@dataclass(frozen=True)
class TurnStep:
    name: str
    order: int
    run: Step


def _ensure_installed(dnd) -> dict[str, TurnStep]:
    steps = getattr(dnd, "_upupa_turn_steps", None)
    if isinstance(steps, dict):
        return steps
    steps = {}
    dnd._upupa_turn_steps = steps
    inner = dnd.parse_and_execute_turn

    async def run_pipeline(bot, chat_id, response):
        chain = sorted(steps.values(), key=lambda step: (-step.order, step.name))

        async def call(index, bot_, chat_id_, response_):
            if index >= len(chain):
                return await inner(bot_, chat_id_, response_)
            step = chain[index]

            async def call_next(next_bot, next_chat_id, next_response):
                return await call(index + 1, next_bot, next_chat_id, next_response)

            return await step.run(bot_, chat_id_, response_, call_next)

        return await call(0, bot, chat_id, response)

    run_pipeline._upupa_turn_pipeline = True
    dnd.parse_and_execute_turn = run_pipeline
    return steps


def register_turn_step(dnd, name: str, run: Step, *, order: int | None = None) -> TurnStep:
    """Add (or replace) one named step; installs the pipeline on first use.

    The pipeline wrapper sits where the first step was registered — in the
    production composition that is the historical position of the innermost
    guard (conditions), right outside the combat/campaign interceptors.
    """
    if order is None:
        order = ORDER.get(name)
    if order is None:
        raise ValueError(f"DnD turn step {name!r} needs an explicit order")
    steps = _ensure_installed(dnd)
    steps[name] = TurnStep(name=name, order=int(order), run=run)
    return steps[name]


def describe(dnd) -> list[str]:
    """Registered steps, outer → inner."""
    steps = getattr(dnd, "_upupa_turn_steps", None) or {}
    return [step.name for step in sorted(steps.values(), key=lambda step: (-step.order, step.name))]


__all__ = ["ORDER", "TurnStep", "describe", "register_turn_step"]
