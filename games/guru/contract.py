"""Контракт между ядром салона и интерфейсами.

Ядро отдаёт структурированное состояние (``View``), доступные действия
(``Action``) и события хода (``DisplayEvent``, метрики). Telegram-текст,
будущий Mini App или PC-клиент рисуют их по-своему и присылают обратно
только ``action_id``. Реплики — контент игры, поэтому приходят готовыми
строками; разбивка на сообщения, кнопки и оформление — дело адаптера.

``DisplayEvent.pause`` — драматическая пауза перед событием в секундах, ``spread`` —
сколько к ней можно случайно добавить, чтобы ритм не был механическим. Адаптер сам
решает, как её показать (Telegram — «печатает…», графический клиент — анимация).
``replace`` — событие заменяет предыдущее на экране («тасует колоду» превращается
в выпавшие карты). ``data["cards"]`` — рисунки расклада по порядку; адаптер показывает
их перед текстом события. ``data["palm"]`` — сферы, чьи линии на ладони с изъяном; адаптер
показывает рисунок ладони перед текстом.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from games.guru.model import ClientReport


@dataclass(frozen=True)
class Action:
    id: str  # короткий и стабильный: «m:tarot», «i:newbie», «s:who:colleague», «a:swear»
    label: str
    kind: str  # question | method | interpretation | option | confidence | verdict | intake | sign | control


@dataclass(frozen=True)
class DisplayEvent:
    kind: str  # arrive | tell | ask | answer | status | ritual | omen | voice | react | announce | line | reveal
    text: str
    pause: float = 0.0
    spread: float = 0.0
    replace: bool = False
    data: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ClientView:
    number: int  # с единицы
    archetype: str
    archetype_title: str
    emoji: str
    name: str
    mood: str
    satisfaction: str
    suspicion: str | None  # только у скептика
    questions_left: int
    readings_left: int
    phase: str
    picks: dict[str, str]
    report: ClientReport | None


@dataclass(frozen=True)
class View:
    session_id: str
    step: int
    player: str
    clients_total: int
    legend: int
    legend_start: int
    streak: int
    score: int
    money: int  # за партию; может быть отрицательным
    wallet: int  # касса игрока с учётом этой партии
    sciences: tuple[tuple[str, bool], ...]  # (метод, применён ли за партию)
    client: ClientView | None
    prompt: str
    preview: str | None  # собираемая фраза предсказания
    actions: tuple[Action, ...]
    finished: bool
    finish_reason: str | None
    title: str | None
    achievements: tuple[str, ...]
    outcomes: tuple[str, ...]  # исходы обслуженных клиентов по порядку
    max_streak: int
    salon: str | None = None  # строка о купленном; None, если лавку ещё не открывали

    def to_dict(self) -> dict[str, Any]:
        """Сериализуемое состояние — для будущего Mini App / PC-клиента."""
        return asdict(self)


@dataclass(frozen=True)
class LiveView:
    """Живой приём: что видно интерфейсу."""

    session_id: str
    step: int
    host: str
    client: str | None
    phase: str
    prompt: str
    actions: tuple[Action, ...]
    finished: bool
    finish_reason: str | None
    confidence: str | None
    outcome: str | None
    fee: int
    wallet: int  # касса мадам с учётом этого приёма
    intake_filled: int = 0  # сколько строк анкеты заполнено; сами ответы тайна до приговора
    preview: str | None = None  # собираемая фраза предсказания

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class LiveResult:
    events: tuple[DisplayEvent, ...]
    metrics: tuple[tuple[str, dict[str, Any]], ...]
    view: LiveView
    notice: str | None = None  # подтверждение, которое увидит только нажавший


@dataclass(frozen=True)
class ActResult:
    events: tuple[DisplayEvent, ...]
    metrics: tuple[tuple[str, dict[str, Any]], ...]  # (событие журнала, данные)
    finished_client: ClientView | None  # клиент, чей приём закончился этим ходом
    view: View
