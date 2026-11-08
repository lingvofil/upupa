"""Состояние салона «Гуру»: истории, клиент, партия. Без Telegram и без правил."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from games.guru.content import ARCHETYPES, WHAT, WHEN, Archetype

# Слова клиента. Выданные за ясновидение без своего подтверждения — это эхо.
CLIENT_SOURCES = frozenset({"disclosed", "question"})

WHEN_TRUTH = "@when"  # подставляется правда «когда» этой партии

Pairs = tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class Combo:
    """Сюжетная комбинация конструктора: джекпот или ловушка."""

    picks: Pairs
    text: str

    def matches(self, prediction: Mapping[str, str]) -> bool:
        return all(prediction.get(slot) == tag for slot, tag in self.picks)


@dataclass(frozen=True)
class Question:
    """Уточнение: проверка версии (``check``) или неожиданная деталь (``open``).

    Ответ полезен, но не обязан быть однозначным. ``said`` — что клиент сказал
    прямо (для правила эха). ``points`` и ``doubts`` игрок не видит: это разметка
    того, что внимательный слушатель может понять из ответа, для симуляции баланса
    и журнала.
    """

    id: str
    label: str
    answer: str
    kind: str
    said: Mapping[str, str] = field(default_factory=dict)
    points: Pairs = ()
    doubts: Pairs = ()


@dataclass(frozen=True)
class Story:
    id: str
    archetype: str
    names: tuple[str, ...]
    arrival: str
    told: tuple[str, ...]  # рассказ клиента по репликам
    said: Mapping[str, str]  # что клиент утверждает сам (его убеждение, может быть ложным)
    points: Pairs  # на что рассказ намекает внимательному слушателю
    questions: tuple[Question, ...]
    who: str
    what: str
    when_options: tuple[str, ...]  # правда «когда» выбирается на партию — против заучивания
    who_decoys: tuple[str, ...]
    reveal: str  # {when_lead} — начало развязки
    fee: int
    theme: str = "other"  # relationships | other — для баланса контента и журнала
    jackpot: Combo | None = None
    trap: Combo | None = None

    @property
    def kind(self) -> Archetype:
        return ARCHETYPES[self.archetype]

    @property
    def sphere(self) -> str:
        return WHAT[self.what].sphere

    @property
    def emotion(self) -> str:
        return WHAT[self.what].emotion

    @property
    def twist(self) -> bool:
        """История с подвохом: клиент заблуждается насчёт «кто» или «что»."""
        return self.said.get("who", self.who) != self.who or self.said.get("what", self.what) != self.what

    def question(self, question_id: str) -> Question:
        return next(q for q in self.questions if q.id == question_id)


@dataclass
class Knowledge:
    """Скрытая запись: что прозвучало на приёме. Игроку не показывается."""

    slot: str  # who | what | when | sphere | emotion
    tag: str
    source: str  # disclosed | question | palm | tarot | horoscope | energy
    truthful: bool

    def supports(self, slot: str, tag: str) -> bool:
        if self.slot == slot:
            return self.tag == tag
        if slot == "what" and self.slot == "sphere":
            return WHAT[tag].sphere == self.tag
        if slot == "what" and self.slot == "emotion":
            return WHAT[tag].emotion == self.tag
        return False


@dataclass(frozen=True)
class Reading:
    """Открытое гадание: что выпало и какие толкования предложены."""

    method: str
    slot: str
    offered: tuple[str, ...]
    quality: str  # sharp (два толкования) | plain (три) | off (правды среди них нет)


@dataclass(frozen=True)
class ReadingResult:
    method: str
    slot: str
    offered: tuple[str, ...]
    voiced: str
    truth: str
    quality: str
    reaction: str  # warm | cold

    @property
    def correct(self) -> bool:
        return self.voiced == self.truth


@dataclass(frozen=True)
class SlotResult:
    slot: str
    picked: str | None
    truth: str
    matched: bool
    mercury: bool = False


@dataclass(frozen=True)
class ClientReport:
    """Разбор приёма: что было на самом деле и почему такой исход."""

    client_no: int
    story: str
    archetype: str
    reveal: str
    slots: tuple[SlotResult, ...]
    readings: tuple[ReadingResult, ...]
    questions: tuple[str, ...]
    said: tuple[tuple[str, str, bool], ...]  # (слот, тег, правда ли) — что клиент сказал сам
    echo_slots: tuple[str, ...]
    outcome: str  # hit | partial | miss | exposure
    reasons: tuple[str, ...]
    confidence: str | None
    jackpot: bool
    trap: bool
    fee: int
    points: int
    satisfaction: int  # понравился ли сам сеанс, 0–100: отдельно от доверия и исхода
    parting: str  # pleased | mixed | sour — как клиент ушёл
    streak_before: int
    streak_after: int
    streak_bonus: int
    legend_before: int
    legend_after: int
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
            "readings": [
                {"method": r.method, "voiced": r.voiced, "correct": r.correct, "quality": r.quality,
                 "reaction": r.reaction}
                for r in self.readings
            ],
            "questions": list(self.questions),
            "echo_slots": list(self.echo_slots),
            "jackpot": self.jackpot,
            "trap": self.trap,
            "fee": self.fee,
            "points": self.points,
            "satisfaction": self.satisfaction,
            "parting": self.parting,
            "streak_before": self.streak_before,
            "streak_after": self.streak_after,
            "legend_before": self.legend_before,
            "legend_after": self.legend_after,
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
    satisfaction: int
    started_at: float
    questions_left: int
    readings_left: int
    asked: list[str] = field(default_factory=list)
    used_methods: list[str] = field(default_factory=list)
    knowledge: list[Knowledge] = field(default_factory=list)
    readings: list[ReadingResult] = field(default_factory=list)
    phase: str = "clarify"  # clarify | divine | interpret | who | what | when | announce | done
    pending: Reading | None = None
    picks: dict[str, str] = field(default_factory=dict)
    horoscope_used: bool = False
    report: ClientReport | None = None

    @property
    def kind(self) -> Archetype:
        return self.story.kind

    def render(self, text: str) -> str:
        return text.format(name=self.name, when_mark=WHEN[self.truth["when"]].mark)

    def resolve(self, tag: str) -> str:
        return self.truth["when"] if tag == WHEN_TRUTH else tag


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
    money: int = 0  # заработано за партию; может уйти в минус
    wallet_start: int = 0  # сколько было в кассе игрока до партии
    idle_limit: float = 900.0
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


@dataclass
class LiveSession:
    """Живой приём: один игрок гадает другому. Скрытой правды нет, исход решает клиент."""

    session_id: str
    chat_id: int
    host: Participant
    started_at: float
    last_activity: float
    idle_limit: float
    wallet_start: int
    readings_left: int
    client: Participant | None = None
    joined_at: float | None = None
    sign: str = ""
    phase: str = "waiting"  # waiting | divine | sign | announce | verdict | done
    used_methods: list[str] = field(default_factory=list)
    confidence: str | None = None
    outcome: str | None = None
    fee: int = 0
    step: int = 0
    finished: bool = False
    finish_reason: str | None = None
    message_id: int | None = None
    thread_id: int | None = None

    def is_participant(self, user_id: int) -> bool:
        return self.host.user_id == user_id or (self.client is not None and self.client.user_id == user_id)
