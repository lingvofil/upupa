"""Контракт между ядром салона и интерфейсами.

Ядро отдаёт структурированное состояние (``View``), доступные действия
(``Action``) и события хода (``DisplayEvent``, метрики). Telegram-текст,
будущий Mini App или PC-клиент рисуют их по-своему и присылают обратно
только ``action_id``. Реплики клиентов — контент игры, поэтому приходят
готовыми строками; раскладка, кнопки и оформление — дело адаптера.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from games.guru.model import ClientReport


@dataclass(frozen=True)
class Action:
    id: str  # короткий и стабильный: «m:palm», «s:who:colleague», «a:swear»
    label: str
    kind: str  # method | line | question | option | confidence | control


@dataclass(frozen=True)
class DisplayEvent:
    kind: str  # line | report
    text: str
    data: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class NoteView:
    icon: str
    source: str
    clarity: str | None  # «ясно» | «мутно» | None для слов клиента
    text: str


@dataclass(frozen=True)
class ClientView:
    number: int  # с единицы
    archetype: str
    archetype_title: str
    emoji: str
    name: str
    complaint: str
    observation: str
    notebook: tuple[NoteView, ...]
    dialog: tuple[str, ...]
    mood: str
    suspicion: str | None  # только у скептика
    actions_left: int
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
    money: int
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

    def to_dict(self) -> dict[str, Any]:
        """Сериализуемое состояние — для будущего Mini App / PC-клиента."""
        return asdict(self)


@dataclass(frozen=True)
class ActResult:
    events: tuple[DisplayEvent, ...]
    metrics: tuple[tuple[str, dict[str, Any]], ...]  # (событие журнала, данные)
    finished_client: ClientView | None  # клиент, чей приём закончился этим ходом
    view: View
