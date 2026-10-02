"""One bounded AI allowance shared by every provider attempt of a DnD turn.

Token estimates are admission estimates, not provider billing or a tokenizer.
Unknown usage remains charged at its estimate; known usage replaces it.
"""
from __future__ import annotations

import contextvars
import logging
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from math import ceil


class DndAIBudgetExhausted(RuntimeError):
    """Keep the durable request pending until the player explicitly retries."""


@dataclass(frozen=True)
class DndAIPolicy:
    calls: int
    optional_calls: int
    primary_attempts: int
    estimated_tokens: int
    timeout_seconds: float
    history_chars: int


POLICIES = {
    "balanced": DndAIPolicy(4, 1, 2, 48_000, 90.0, 4_000),
    "economy": DndAIPolicy(2, 0, 1, 24_000, 60.0, 1_200),
    "service": DndAIPolicy(2, 0, 1, 6_000, 30.0, 0),
}
AI_CALL_PURPOSES = frozenset({
    "scene", "adjudication", "repair", "inventory_audit", "roll_repair",
    "profile", "stats", "plot", "npc", "epilogue", "service", "fallback",
})
_CURRENT_BUDGET = contextvars.ContextVar("dnd_turn_ai_budget", default=None)
_CURRENT_PURPOSE = contextvars.ContextVar("dnd_ai_call_purpose", default="scene")
_CURRENT_GUARD = contextvars.ContextVar("dnd_ai_call_guard", default=None)


def current_turn_budget():
    return _CURRENT_BUDGET.get()


def current_ai_purpose() -> str:
    return _CURRENT_PURPOSE.get()


@contextmanager
def ai_call_purpose(purpose: str):
    if purpose not in AI_CALL_PURPOSES:
        raise ValueError(f"Unknown DnD AI purpose: {purpose}")
    token = _CURRENT_PURPOSE.set(purpose)
    try:
        yield
    finally:
        _CURRENT_PURPOSE.reset(token)


@dataclass
class DndTurnBudget:
    mode: str = "balanced"
    turn_id: str = ""
    deadline: float = 0.0
    calls: int = 0
    optional_calls: int = 0
    estimated_tokens: int = 0
    known_tokens: int = 0
    unknown_usage_calls: int = 0
    expired: bool = False
    _reserved_calls: int = 0
    _reserved_optional: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def __post_init__(self):
        if self.mode not in POLICIES:
            self.mode = "balanced"
        if not self.deadline:
            self.deadline = time.monotonic() + self.policy.timeout_seconds

    @property
    def policy(self):
        return POLICIES[self.mode]

    def remaining_seconds(self) -> float:
        return max(0.0, self.deadline - time.monotonic())

    def check_alive(self):
        if self.expired or self.remaining_seconds() <= 0:
            raise DndAIBudgetExhausted("Лимит времени AI для этого хода исчерпан; заявка сохранена.")

    def reserve(self, prompt: str, output_tokens: int, *, optional: bool = False):
        estimate = ceil(len(str(prompt).encode("utf-8")) / 3) + max(0, int(output_tokens))
        with self._lock:
            self.check_alive()
            if self.calls + self._reserved_calls >= self.policy.calls:
                raise DndAIBudgetExhausted("Бюджет AI-запросов этого хода исчерпан; заявка сохранена.")
            if optional and self.optional_calls + self._reserved_optional >= self.policy.optional_calls:
                raise DndAIBudgetExhausted("Необязательная AI-проверка пропущена: бюджет хода исчерпан.")
            if self.estimated_tokens + estimate > self.policy.estimated_tokens:
                raise DndAIBudgetExhausted("Бюджет контекста AI этого хода исчерпан; заявка сохранена.")
            self._reserved_calls += 1
            self._reserved_optional += int(optional)
            self.estimated_tokens += estimate
        return BudgetReservation(self, estimate, optional)

    def snapshot(self) -> dict:
        with self._lock:
            return dict(mode=self.mode, calls=self.calls, optional_calls=self.optional_calls,
                        estimated_tokens=self.estimated_tokens, known_tokens=self.known_tokens,
                        unknown_usage_calls=self.unknown_usage_calls,
                        remaining_seconds=round(self.remaining_seconds(), 3))


class BudgetReservation:
    def __init__(self, budget, estimate, optional):
        self.budget, self.estimate, self.optional = budget, estimate, optional
        self.started = self.closed = False

    def start(self):
        with self.budget._lock:
            self.budget.check_alive()
            if self.closed:
                raise DndAIBudgetExhausted("AI-запрос уже отменён.")
            if self.started:
                return
            self.started = True
            self.budget._reserved_calls -= 1
            self.budget._reserved_optional -= int(self.optional)
            self.budget.calls += 1
            self.budget.optional_calls += int(self.optional)
            self.budget.unknown_usage_calls += 1

    def finish(self, total_tokens: int | None = None):
        with self.budget._lock:
            if self.closed:
                return
            self.closed = True
            if not self.started:
                self.budget._reserved_calls -= 1
                self.budget._reserved_optional -= int(self.optional)
                self.budget.estimated_tokens -= self.estimate
            elif total_tokens is not None:
                actual = max(0, int(total_tokens))
                self.budget.estimated_tokens += actual - self.estimate
                self.budget.known_tokens += actual
                self.budget.unknown_usage_calls -= 1


class DndCallGuard:
    """Cancellation survives coroutine cancellation and thread context copying."""
    def __init__(self, timeout_seconds: float):
        budget = current_turn_budget()
        self.deadline = min(time.monotonic() + timeout_seconds,
                            budget.deadline if budget is not None else float("inf"))
        self.cancelled = threading.Event()

    def remaining(self):
        remaining = self.deadline - time.monotonic()
        if self.cancelled.is_set() or remaining <= 0:
            raise DndAIBudgetExhausted("AI-запрос отменён или достиг лимита времени; заявка сохранена.")
        budget = current_turn_budget()
        if budget is not None:
            budget.check_alive()
        return remaining


@contextmanager
def ai_call_guard(guard):
    token = _CURRENT_GUARD.set(guard)
    try:
        yield
    finally:
        _CURRENT_GUARD.reset(token)


def current_call_guard():
    return _CURRENT_GUARD.get()


@contextmanager
def dnd_turn_budget(session=None, *, turn_id=None, mode=None, timeout_seconds=None):
    """Wrap generation AND parse/correction; nested scopes share the allowance."""
    existing = current_turn_budget()
    if existing is not None and not existing.expired:
        existing.check_alive()
        yield existing
        return
    if mode is None:
        from AI.dnd_settings import settings_for
        mode = settings_for(session).get("ai_mode", "balanced")
    budget = DndTurnBudget(mode=mode, turn_id=str(turn_id or ""))
    if timeout_seconds is not None:
        budget.deadline = min(budget.deadline, time.monotonic() + max(0.0, float(timeout_seconds)))
    token = _CURRENT_BUDGET.set(budget)
    try:
        yield budget
    finally:
        budget.expired = True
        logging.info("DnD AI turn budget turn_id=%s usage=%s", budget.turn_id, budget.snapshot())
        _CURRENT_BUDGET.reset(token)
