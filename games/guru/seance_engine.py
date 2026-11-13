"""Универсальный сеанс гадания: автомат без единого факта о конкретном клиенте.

Перенос движка из Guru-Web v0.2. Движок знает три механики и порядок ходов:

* Таро — расклад с местами: на каждой карте можно рассмотреть ограниченное число образов,
  у рассмотренного образа есть прочтения, по одному на карту складывается предсказание;
* ладонь — осмотр с лимитом участков и слово о руке по осмотренному;
* гороскоп — выбор одного прогноза из тенденций недели.

В любом гадании можно уклониться от ответа. Что значит каждое прочтение и как на него ответит
клиент, решает ``Story``: ``on_statement``, ``on_intent``, ``on_correction``, ``open_line``,
``final_line``, ``on_finish``, ``outcome``. Состояние клиента (``world``) движку непрозрачно.

Скрытых очков нет: последствия считаются по событиям со стабильными идентификаторами
(``GameEvent``) и по названным состояниям в ``world``. Без Telegram, часов и случайности.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol

METHODS = ("tarot", "palm", "horoscope")
SOFT, FIRM = "soft", "firm"
TONES = (SOFT, FIRM)
CORRECTIONS = ("clarify", "admit", "retract")
ALIVE = ("stands", "clarified")  # утверждение, которое гадалка не забрала назад


# --- данные истории ---------------------------------------------------------

@dataclass(frozen=True)
class ReadingData:
    id: str
    label: str  # подпись на кнопке
    phrase: str  # что произносит мадам, с маленькой буквы и без точки
    meta: Mapping[str, Any] = field(default_factory=dict)  # разметка, которую читают только правила истории


@dataclass(frozen=True)
class SymbolData:
    id: str
    title: str
    look: str  # что видно на карте: открывается, когда образ рассмотрен
    readings: tuple[ReadingData, ...]


@dataclass(frozen=True)
class CardData:
    id: str
    art: str  # имя рисунка в колоде
    title: str
    place: str  # место в раскладе: «То, что видно»
    hint: str  # о чём это место
    lead: str  # как место звучит во фразе: «на месте скрытого»
    symbols: tuple[SymbolData, ...]


@dataclass(frozen=True)
class ZoneData:
    id: str
    title: str
    finding: str  # что мадам видит на участке
    remark: str = ""  # что клиент говорит, пока его руку рассматривают


@dataclass(frozen=True)
class WordData:
    id: str
    zone: str
    label: str
    text: str


@dataclass(frozen=True)
class ForecastData:
    id: str
    label: str
    phrase: str


@dataclass(frozen=True)
class TendencyData:
    id: str
    planet: str
    name: str
    theme: str
    text: str
    forecasts: tuple[ForecastData, ...]


@dataclass(frozen=True)
class QuestionData:
    id: str
    label: str
    answer: str


@dataclass(frozen=True)
class EventData:
    title: str  # заголовок сцены
    teaser: str  # что видно до сцены: «Она уже печатает»
    label: str  # строка в разборе «почему так»


@dataclass(frozen=True)
class StoryContent:
    client: str
    archetype: str  # кто нарисован за столом
    arrival: str
    intro: tuple[tuple[str, str], ...]  # (реплика, ремарка)
    questions: tuple[QuestionData, ...]
    question_limit: int
    cards: tuple[CardData, ...]
    study_limit: int
    tarot_lead: Mapping[str, str]
    tarot_evasive: str
    zones: tuple[ZoneData, ...]
    words: tuple[WordData, ...]
    inspect_limit: int
    palm_evasive: str
    sign: str  # «♎ Весы»
    tendencies: tuple[TendencyData, ...]
    horoscope_lead: Mapping[str, str]
    horoscope_evasive: str
    corrections: Mapping[str, tuple[str, str]]  # вид → (кнопка, слова мадам)
    advice: tuple[tuple[str, str, str], ...]  # (id, кнопка, слова мадам)
    events: Mapping[str, EventData]
    intents: Mapping[str, tuple[str, str]]  # замысел → (поддержать, удержать)
    moods: Mapping[str, str]
    endings: Mapping[str, str]  # развязка → название
    fee: int = 2000


# --- состояние партии -------------------------------------------------------

@dataclass
class Beat:
    """Одна реплика или ремарка в хронологии сеанса."""

    kind: str  # client | you | narration | message | voice | finding
    who: str
    text: str
    ev: str | None = None  # событие, сцене которого принадлежит реплика


@dataclass
class Statement:
    id: str
    method: str
    parts: list[str]  # стабильные идентификаторы содержания: прочтения, слово о руке или прогноз
    tone: str | None
    hollow: bool  # уклончивый ответ без содержания
    text: str
    summary: str
    status: str = "stands"  # stands | clarified | admitted | retracted
    understood: str = ""
    misheard: bool = False  # клиент услышал не то, что было сказано
    events: list[str] = field(default_factory=list)  # события, причиной которых стало это утверждение
    intent: str | None = None
    intent_state: str | None = None  # open | supported | held | dropped | fired
    data: dict[str, Any] = field(default_factory=dict)  # смысловая разметка, которую ведёт история

    @property
    def alive(self) -> bool:
        return self.status in ALIVE


@dataclass
class GameEvent:
    id: str  # по нему, а не по тексту, считаются последствия
    how: str
    statement_id: str | None
    later: bool  # произошло после сеанса, без гадалки


@dataclass
class Draft:
    studied: list[str] = field(default_factory=list)
    readings: dict[str, str] = field(default_factory=dict)  # карта → прочтение
    inspected: list[str] = field(default_factory=list)


@dataclass
class State:
    world: Any
    mood: str
    scene: str = "questions"  # questions | tarot | palm | horoscope | reaction | final | ending
    questions: list[str] = field(default_factory=list)
    draft: Draft = field(default_factory=Draft)
    statements: list[Statement] = field(default_factory=list)
    done: dict[str, str] = field(default_factory=dict)  # метод → утверждение
    events: list[GameEvent] = field(default_factory=list)
    queue: list[str] = field(default_factory=list)  # события, сцену которых мадам ещё не видела
    advice: str | None = None
    ending: str | None = None
    log: list[Beat] = field(default_factory=list)

    @property
    def last(self) -> Statement | None:
        return self.statements[-1] if self.statements else None


@dataclass(frozen=True)
class Prediction:
    summary: str
    method: str
    tone: str | None
    status: str
    verdict: str  # confirmed | partly | failed | uncertain | empty | withdrawn | advice
    origin: str | None  # planned — задумано до гадания; caused — случилось из-за слов гадалки
    note: str


@dataclass(frozen=True)
class Outcome:
    ending: str
    title: str
    kicker: str
    mark: str
    text: str
    quote: str
    afterword: str
    client_state: str  # content | mixed | displeased
    client_text: str
    recognized: bool  # клиент признал, что гадалка угадала
    predictions: tuple[Prediction, ...]
    chain: tuple[tuple[str, str], ...]  # (что случилось, почему)
    corrections: tuple[str, ...]
    truth: str
    refund: bool = False  # вред причинён прямо словами мадам: гонорар возвращают


class Story(Protocol):
    """Причинные правила конкретной истории. Движок не знает, что в них написано."""

    id: str
    content: StoryContent
    initial_mood: str
    endings: tuple[str, ...]  # развязки, до которых в этой истории можно дойти

    def create_world(self) -> Any: ...
    def open_line(self, s: State, method: str) -> str: ...
    def on_statement(self, s: State, st: Statement) -> None: ...
    def on_correction(self, s: State, st: Statement, kind: str) -> None: ...
    def on_intent(self, s: State, st: Statement, decision: str) -> None: ...
    def final_line(self, s: State) -> str: ...
    def on_finish(self, s: State) -> str: ...
    def outcome(self, s: State) -> Outcome: ...


# --- справочники ------------------------------------------------------------

def new_state(story: Story) -> State:
    return State(world=story.create_world(), mood=story.initial_mood)


def card_of(story: Story, card_id: str) -> CardData:
    return next(card for card in story.content.cards if card.id == card_id)


def find_symbol(story: Story, symbol_id: str) -> tuple[CardData, SymbolData] | None:
    for card in story.content.cards:
        for symbol in card.symbols:
            if symbol.id == symbol_id:
                return card, symbol
    return None


def find_reading(story: Story, reading_id: str) -> tuple[CardData, SymbolData, ReadingData] | None:
    for card in story.content.cards:
        for symbol in card.symbols:
            for reading in symbol.readings:
                if reading.id == reading_id:
                    return card, symbol, reading
    return None


def find_forecast(story: Story, forecast_id: str) -> tuple[TendencyData, ForecastData] | None:
    for tendency in story.content.tendencies:
        for forecast in tendency.forecasts:
            if forecast.id == forecast_id:
                return tendency, forecast
    return None


def find_word(story: Story, word_id: str) -> WordData | None:
    return next((word for word in story.content.words if word.id == word_id), None)


def studied_on(s: State, card: CardData) -> list[SymbolData]:
    return [symbol for symbol in card.symbols if symbol.id in s.draft.studied]


def available_words(story: Story, s: State) -> list[WordData]:
    """Слова о руке доступны только по тем участкам, которые мадам осмотрела."""
    return [word for word in story.content.words if word.zone in s.draft.inspected]


def has_event(s: State, event_id: str) -> bool:
    return any(event.id == event_id for event in s.events)


def event_of(s: State, event_id: str) -> GameEvent | None:
    return next((event for event in s.events if event.id == event_id), None)


def statement_of(s: State, statement_id: str | None) -> Statement | None:
    return next((st for st in s.statements if st.id == statement_id), None)


def next_method(s: State) -> str | None:
    """Какое гадание идёт следующим; ``None`` — гадания кончились, впереди напутствие."""
    return next((method for method in METHODS if method not in s.done), None)


# --- помощники для правил истории -------------------------------------------

def push(s: State, kind: str, who: str, text: str, ev: str | None = None) -> None:
    s.log.append(Beat(kind, who, text, ev))


def record_event(s: State, event_id: str, how: str, st: Statement | None, later: bool = False) -> None:
    """Записать причинное событие и поставить его сцену в очередь показа."""
    s.events.append(GameEvent(event_id, how, st.id if st else None, later))
    if st is not None:
        st.events.append(event_id)
    s.queue.append(event_id)


# --- ходы -------------------------------------------------------------------

def _need(condition: bool, why: str) -> None:
    if not condition:
        raise ValueError(why)


def ask(story: Story, s: State, question_id: str) -> None:
    content = story.content
    question = next((item for item in content.questions if item.id == question_id), None)
    _need(s.scene == "questions" and question is not None, "вопрос недоступен")
    _need(len(s.questions) < content.question_limit and question_id not in s.questions, "вопрос недоступен")
    s.questions.append(question_id)
    push(s, "you", "Ты", question.label)
    push(s, "client", content.client, question.answer)


def _start(story: Story, s: State, method: str) -> None:
    s.scene = method
    s.draft = Draft()
    push(s, "client", story.content.client, story.open_line(s, method))


def begin(story: Story, s: State) -> None:
    _need(s.scene == "questions", "гадания уже идут")
    _start(story, s, METHODS[0])


def study(story: Story, s: State, symbol_id: str) -> None:
    found = find_symbol(story, symbol_id)
    _need(s.scene == "tarot" and found is not None and symbol_id not in s.draft.studied, "образ недоступен")
    _need(len(studied_on(s, found[0])) < story.content.study_limit, "на этой карте больше не рассмотреть")
    s.draft.studied.append(symbol_id)


def read(story: Story, s: State, reading_id: str) -> None:
    found = find_reading(story, reading_id)
    _need(s.scene == "tarot" and found is not None and found[1].id in s.draft.studied, "прочтение недоступно")
    s.draft.readings[found[0].id] = reading_id


def inspect(story: Story, s: State, zone_id: str) -> None:
    content = story.content
    zone = next((item for item in content.zones if item.id == zone_id), None)
    _need(s.scene == "palm" and zone is not None and zone_id not in s.draft.inspected, "участок недоступен")
    _need(len(s.draft.inspected) < content.inspect_limit, "руку больше не дадут")
    s.draft.inspected.append(zone_id)
    push(s, "finding", zone.title, zone.finding)
    if zone.remark:
        push(s, "client", content.client, zone.remark)


def tarot_parts(story: Story, s: State) -> list[tuple[str, str]]:
    """Предсказание по местам расклада: игрок видит, откуда взялось каждое слово."""
    parts = []
    for card in story.content.cards:
        found = find_reading(story, s.draft.readings.get(card.id, ""))
        if found is not None:
            parts.append((card.place, f"{found[1].title} — {card.lead}: {found[2].phrase}."))
    return parts


def tarot_ready(story: Story, s: State) -> bool:
    return all(card.id in s.draft.readings for card in story.content.cards)


def forecast_text(story: Story, forecast_id: str, tone: str | None) -> str:
    tendency, forecast = find_forecast(story, forecast_id)
    lead = f"{story.content.horoscope_lead[tone]} " if tone else ""
    return f"{tendency.name} ведёт вашу неделю. {lead}{forecast.phrase}."


def _state(story: Story, s: State, method: str, parts: list[str], tone: str | None, hollow: bool, text: str,
           summary: str) -> Statement:
    statement = Statement(
        id=f"s{len(s.statements) + 1}", method=method, parts=parts, tone=tone, hollow=hollow, text=text, summary=summary,
        data={"studied": list(s.draft.studied), "inspected": list(s.draft.inspected)},
    )
    s.statements.append(statement)
    s.done[method] = statement.id
    push(s, "you", "Ты", text)
    story.on_statement(s, statement)
    s.scene = "reaction"
    s.draft = Draft()
    return statement


def say_tarot(story: Story, s: State, tone: str) -> Statement:
    content = story.content
    _need(s.scene == "tarot" and tone in TONES and tarot_ready(story, s), "расклад ещё не прочитан")
    parts = [s.draft.readings[card.id] for card in content.cards]
    body = " ".join(text for _, text in tarot_parts(story, s))
    summary = " · ".join(find_reading(story, part)[2].label for part in parts)
    return _state(story, s, "tarot", parts, tone, False, f"{content.tarot_lead[tone]} {body}", summary)


def say_word(story: Story, s: State, word_id: str) -> Statement:
    word = find_word(story, word_id)
    _need(s.scene == "palm" and word is not None and word.zone in s.draft.inspected, "этого участка мадам не видела")
    return _state(story, s, "palm", [word.id], None, False, word.text, word.label)


def say_forecast(story: Story, s: State, forecast_id: str, tone: str) -> Statement:
    found = find_forecast(story, forecast_id)
    _need(s.scene == "horoscope" and found is not None and tone in TONES, "прогноз недоступен")
    return _state(story, s, "horoscope", [forecast_id], tone, False, forecast_text(story, forecast_id, tone), found[1].label)


def say_nothing(story: Story, s: State) -> Statement:
    """Уклончивый ответ: знаки перед мадам, а сказано ничего."""
    _need(s.scene in METHODS, "уклоняться не от чего")
    content = story.content
    text = {"tarot": content.tarot_evasive, "palm": content.palm_evasive, "horoscope": content.horoscope_evasive}[s.scene]
    return _state(story, s, s.scene, ["evasive"], None, True, text, "Уклончивый ответ")


def can_decide(s: State) -> bool:
    """Замысел клиента ждёт слова мадам: поддержать или удержать."""
    return s.scene == "reaction" and not s.queue and s.last is not None and s.last.intent_state == "open"


def can_correct(s: State) -> bool:
    """Исправить можно только последнее утверждение и только один раз."""
    return s.scene == "reaction" and not s.queue and s.last is not None and not s.last.hollow and s.last.status == "stands"


def decide(story: Story, s: State, decision: str) -> None:
    _need(can_decide(s) and decision in ("support", "hold"), "решать нечего")
    story.on_intent(s, s.last, decision)


def correct(story: Story, s: State, kind: str) -> None:
    _need(can_correct(s) and kind in CORRECTIONS, "исправлять нечего")
    push(s, "you", "Ты", story.content.corrections[kind][1])
    story.on_correction(s, s.last, kind)


def shown(s: State) -> str:
    """Мадам увидела сцену очередного события."""
    _need(bool(s.queue), "сцен в очереди нет")
    return s.queue.pop(0)


def proceed(story: Story, s: State) -> None:
    """Из реакции — к следующему гаданию или, после последнего, к напутствию."""
    _need(s.scene == "reaction" and not s.queue, "сначала досмотри, что она делает")
    method = next_method(s)
    if method is None:
        s.scene = "final"
        push(s, "client", story.content.client, story.final_line(s))
    else:
        _start(story, s, method)


def finish(story: Story, s: State, advice_id: str) -> None:
    label = next((text for key, _, text in story.content.advice if key == advice_id), None)
    _need(s.scene == "final" and label is not None, "напутствие недоступно")
    s.advice = advice_id
    push(s, "you", "Ты", label)
    s.ending = story.on_finish(s)
    s.scene = "ending"
