"""Состояние салона «Гуру»: истории, клиент, партия. Без Telegram и без правил."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from games.guru.content import ARCHETYPES, WHAT, WHEN, Archetype

# Свои источники: то, что мадам «увидела». Слова клиента — эхо, если не подтверждены своим источником.
OWN_SOURCES = frozenset({"observation", "palm", "tarot", "horoscope", "energy"})
CLIENT_SOURCES = frozenset({"disclosed", "question"})


@dataclass(frozen=True)
class Combo:
    """Сюжетная комбинация конструктора: джекпот или ловушка."""

    picks: tuple[tuple[str, str], ...]
    text: str

    def matches(self, prediction: Mapping[str, str]) -> bool:
        return all(prediction.get(slot) == tag for slot, tag in self.picks)


@dataclass(frozen=True)
class Observation:
    """Видимый признак: настоящая подсказка по одному слоту (``who``, ``when``, ``sphere``, ``emotion``)."""

    slot: str
    tags: tuple[str, ...]  # один — ясно, два — мутно; ``("@when",)`` — подставить правду «когда»
    text: str  # может содержать {when_mark}


@dataclass(frozen=True)
class Story:
    id: str
    archetype: str
    names: tuple[str, ...]
    complaint: str
    disclosed: Mapping[str, str]  # что клиент сказал сам (его убеждение, может быть ложным)
    observation: Observation
    who: str
    what: str
    when_options: tuple[str, ...]  # правда «когда» выбирается на партию — против заучивания
    who_decoys: tuple[str, ...]
    reveal: str  # {when_lead} — начало развязки
    fee: int
    beliefs: Mapping[str, str] = field(default_factory=dict)  # ответы на вопросы, если клиент заблуждается
    jackpot: Combo | None = None
    trap: Combo | None = None

    @property
    def kind(self) -> Archetype:
        return ARCHETYPES[self.archetype]

    @property
    def sphere(self) -> str:
        return WHAT[self.what].sphere

    @property
    def twist(self) -> bool:
        """История с подвохом: клиент заблуждается насчёт «кто» или «что»."""
        return self.belief("who", self.who) != self.who or self.belief("what", self.what) != self.what

    def belief(self, slot: str, truth: str) -> str:
        """Что клиент ответит на вопрос по слоту: его убеждение или правда."""
        return self.beliefs.get(slot, self.disclosed.get(slot, truth))


@dataclass
class Knowledge:
    """Запись блокнота мадам."""

    slot: str  # who | what | when | sphere | emotion
    tags: tuple[str, ...]
    source: str
    clear: bool
    truthful: bool
    positive: bool = True  # для ладони: «линия молчит» — отрицательная запись

    def supports(self, slot: str, tag: str) -> bool:
        if not self.positive:
            return False
        if self.slot == slot:
            return tag in self.tags
        if slot == "what" and self.slot == "sphere":
            return WHAT[tag].sphere in self.tags
        if slot == "what" and self.slot == "emotion":
            return WHAT[tag].emotion in self.tags
        return False


@dataclass(frozen=True)
class SlotResult:
    slot: str
    picked: str | None
    truth: str
    matched: bool
    mercury: bool = False


@dataclass(frozen=True)
class HintResult:
    source: str
    slot: str
    tags: tuple[str, ...]
    clear: bool
    truthful: bool
    positive: bool = True


@dataclass(frozen=True)
class ClientReport:
    """Разбор приёма: что было на самом деле и почему такой исход."""

    client_no: int
    story: str
    archetype: str
    reveal: str
    slots: tuple[SlotResult, ...]
    hints: tuple[HintResult, ...]
    echo_slots: tuple[str, ...]
    outcome: str  # hit | partial | miss | exposure
    reasons: tuple[str, ...]
    confidence: str | None
    jackpot: bool
    trap: bool
    fee: int
    points: int
    streak_before: int
    streak_after: int
    streak_bonus: int
    legend_before: int
    legend_after: int
    info_actions: int
    duration_s: float

    @property
    def matched(self) -> int:
        return sum(1 for slot in self.slots if slot.matched)

    def log_data(self) -> dict[str, Any]:
        return {
            "client_no": self.client_no,
            "story": self.story,
            "archetype": self.archetype,
            "outcome": self.outcome,
            "reasons": list(self.reasons),
            "confidence": self.confidence,
            "slots": {s.slot: {"picked": s.picked, "truth": s.truth, "matched": s.matched} for s in self.slots},
            "mercury": any(s.mercury for s in self.slots),
            "hints": [
                {"source": h.source, "slot": h.slot, "clear": h.clear, "truthful": h.truthful} for h in self.hints
            ],
            "echo_slots": list(self.echo_slots),
            "jackpot": self.jackpot,
            "trap": self.trap,
            "fee": self.fee,
            "points": self.points,
            "streak_before": self.streak_before,
            "streak_after": self.streak_after,
            "legend_before": self.legend_before,
            "legend_after": self.legend_after,
            "info_actions": self.info_actions,
            "duration_s": self.duration_s,
        }


@dataclass
class Client:
    story: Story
    name: str
    sign: str
    truth: dict[str, str]
    options: dict[str, tuple[str, ...]]
    trust: int
    suspicion: int
    started_at: float
    actions_left: int = 2
    used_methods: list[str] = field(default_factory=list)
    knowledge: list[Knowledge] = field(default_factory=list)
    lines: list[str] = field(default_factory=list)
    phase: str = "research"  # research | palm | ask | who | what | when | announce | done
    picks: dict[str, str] = field(default_factory=dict)
    horoscope_used: bool = False
    report: ClientReport | None = None

    @property
    def kind(self) -> Archetype:
        return self.story.kind

    @property
    def observation_text(self) -> str:
        return self.story.observation.text.format(when_mark=WHEN[self.truth["when"]].mark)


@dataclass(frozen=True)
class Participant:
    user_id: int
    name: str


@dataclass
class Session:
    """Партия. Участников — список: сейчас всегда один, задел на кооператив."""

    session_id: str
    chat_id: int
    participants: list[Participant]
    plan: tuple[str, ...]
    started_at: float
    last_activity: float
    client: Client
    legend: int
    legend_start: int
    client_no: int = 0
    streak: int = 0
    max_streak: int = 0
    methods_used: set[str] = field(default_factory=set)
    mastery: bool = False
    mercury_left: bool = True
    score: int = 0
    money: int = 0
    step: int = 0
    reports: list[ClientReport] = field(default_factory=list)
    finished: bool = False
    finish_reason: str | None = None
    # Где живёт партия в мессенджере; нужно только transport-слою.
    message_id: int | None = None
    thread_id: int | None = None

    @property
    def host(self) -> Participant:
        return self.participants[0]

    @property
    def clients_total(self) -> int:
        return len(self.plan)

    def is_participant(self, user_id: int) -> bool:
        return any(p.user_id == user_id for p in self.participants)
