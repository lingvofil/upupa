"""Карта дня: короткий вход в салон без партии.

Один человек — одна карта в сутки. Карта зависит только от человека и дня, поэтому в любом
чате она одна и та же и её нельзя перетянуть. За карту в кассу падают чаевые, за дни подряд
они растут: это повод зайти завтра. Модуль не знает про Telegram и про то, где лежат кассы.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from games.guru import content as C
from games.guru import rules

TIP_BASE = 300
TIP_STEP = 100  # прибавка за каждый следующий день подряд
TIP_MAX = 900  # потолок ниже самой дешёвой вещи в лавке: карта не заменяет смену


@dataclass(frozen=True)
class DailyCard:
    card: C.TarotCard
    picks: dict[str, str]  # кто, что и когда: те же теги, что в предсказаниях мадам
    prediction: str
    advice: str


def draw(user_id: int, day: str) -> DailyCard:
    """Карта человека на день ``day`` (ISO-дата). Один и тот же ввод даёт одну и ту же карту."""
    rng = random.Random(f"guru-daily:{user_id}:{day}")
    card = rng.choice(list(C.TAROT_CARDS.values()))
    picks = {"who": card.who, "what": rng.choice(list(C.WHAT)), "when": rng.choice(list(C.WHEN))}
    return DailyCard(card, picks, rules.prediction_plain(picks), rng.choice(C.STAR_ADVICE))


def tip(streak: int) -> int:
    """Чаевые за карту: растут с каждым днём подряд и упираются в потолок."""
    return min(TIP_BASE + TIP_STEP * max(streak - 1, 0), TIP_MAX)
