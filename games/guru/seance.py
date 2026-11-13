"""Сеанс: один клиент и долгий приём по схеме Guru-Web.

Скрытую правду здесь никто не угадывает. Мадам слушает, сама толкует знаки и решает, что и как
сказать, а клиент поступает по услышанному. Три гадания идут по порядку и отличаются действием:

* Таро — три карты на местах «видно / скрыто / откроется»; на карте рассматривают образы, и сказать
  можно только то, что прочитано в рассмотренном;
* ладонь — осмотр участков с лимитом и слово о руке: оно меняет то, как клиент распорядится услышанным;
* гороскоп — один прогноз из тенденций недели; эпилог проверяет, сбылся ли он.

Предсказание звучит осторожно или уверенно; в любом гадании можно уклониться от ответа. После слов
мадам клиент отвечает, а значимый поступок показан отдельной сценой. Замысел клиента можно поддержать
или удержать, своё последнее слово — уточнить, признать ошибкой или отозвать. Сделанного это не отменяет.

Клиентов много: у Людмилы правила авторские (``seance_ludmila``), остальные истории смены играются
по общему шаблону (``seance_casebook``). Автомат сеанса общий (``seance_engine``).

Без Telegram: ядро отдаёт события (``DisplayEvent``) и ``SeanceView``, как смена и живой приём.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any, Iterable

from games.guru import rules, salon, seance_casebook, seance_ludmila
from games.guru import seance_engine as E
from games.guru.contract import Action, DisplayEvent, SeanceResult, SeanceView
from games.guru.dialog import Dialog
from games.guru.model import Participant
from games.guru.stories import STORIES_BY_ID

MECHANIC = "web_v2"
LUDMILA = seance_ludmila.STORY_ID
STORY_IDS: tuple[str, ...] = tuple(STORIES_BY_ID)
FEE_RATE = {"content": 1.0, "mixed": 0.6, "displeased": 0.2}
RECOGNIZED_RATE = 1.5  # клиент признал, что мадам угадала
REFUND_RATE = -0.5  # вред причинён прямо словами мадам

RITUALS = {
    "tarot": "Мадам сдвигает колоду левой рукой и раскладывает три карты.",
    "palm": "Мадам надевает очки и берёт клиента за запястье.",
    "horoscope": "Мадам раскрывает звёздную карту и долго водит по ней карандашом.",
}
NEXT_LABELS = {"palm": "🖐 К ладони", "horoscope": "🌙 К гороскопу", None: "🚪 К напутствию"}
TONE_LABELS = {E.SOFT: "🕊 Осторожно: это знак", E.FIRM: "⚡ Уверенно: как есть"}
TONE_WORDS = {E.SOFT: "осторожно", E.FIRM: "уверенно"}
FIX_LABELS = {"clarify": "✏️ Уточнить", "admit": "🙇 Ошиблась", "retract": "🚫 Отозвать"}
METHOD_TITLES = {"tarot": "Таро", "palm": "Ладонь", "horoscope": "Гороскоп"}
VERDICT_ICONS = {"confirmed": "✅", "partly": "🌗", "failed": "❌", "uncertain": "❔", "empty": "▫️", "withdrawn": "↩️", "advice": "🖐"}
VERDICT_WORDS = {"confirmed": "сбылось", "partly": "сбылось отчасти", "failed": "не сбылось", "uncertain": "не проверить",
                 "empty": "пусто", "withdrawn": "отозвано", "advice": "совет"}
MARKS = ("А", "Б", "В", "Г", "Д", "Е")


def ending_key(story_id: str, ending: str) -> str:
    """Как развязка записана в кассе игрока: счётчик у каждой истории свой."""
    return f"{story_id}:{ending}"


def seen_endings(endings: Iterable[str], story_id: str) -> int:
    prefix = f"{story_id}:"
    return sum(1 for ending in endings if ending.startswith(prefix))


def pick_story(endings: Iterable[str], rng: random.Random) -> str:
    """Кого принять: первой приходит Людмила, дальше — тот, чьих развязок открыто меньше всего."""
    endings = list(endings)
    counts = {story_id: seen_endings(endings, story_id) for story_id in STORY_IDS}
    if not any(counts.values()):
        return LUDMILA
    fewest = min(counts.values())
    return rng.choice([story_id for story_id, count in counts.items() if count == fewest])


def make_story(story_id: str, rng: random.Random) -> E.Story:
    if story_id == LUDMILA:
        return seance_ludmila.STORY
    return seance_casebook.CasebookStory(STORIES_BY_ID[story_id], rng)


@dataclass
class Seance:
    """Партия-сеанс: одна мадам, один клиент."""

    session_id: str
    chat_id: int
    host: Participant
    started_at: float
    last_activity: float
    idle_limit: float
    wallet_start: int
    story: E.Story
    state: E.State
    items: tuple[str, ...] = ()
    gifts: tuple[str, ...] = ()
    phase: str = "questions"  # questions | tarot | tone | palm | word | horoscope | forecast | reaction | final | done
    card: int = 0  # какую карту расклада мадам сейчас читает
    planet: int | None = None
    forecast: str | None = None  # выбранный прогноз ждёт решения, как его сказать
    heard: list[str] = field(default_factory=list)  # что сказал клиент: мадам может перечитать
    shown: int = 0  # сколько реплик хроники уже показано
    outcome: E.Outcome | None = None
    ending: str | None = None
    fee: int = 0
    step: int = 0
    finished: bool = False
    finish_reason: str | None = None
    message_id: int | None = None
    thread_id: int | None = None
    dialog: Dialog = field(default_factory=Dialog)

    @property
    def content(self) -> E.StoryContent:
        return self.story.content

    @property
    def client(self) -> str:
        return self.content.client

    def is_participant(self, user_id: int) -> bool:
        return self.host.user_id == user_id


def new_session(*, session_id: str, chat_id: int, host_id: int, host_name: str, now: float, wallet: int = 0,
                items: tuple[str, ...] = (), gifts: tuple[str, ...] = (), story_id: str | None = None,
                rng: random.Random | None = None, endings: Iterable[str] = ()) -> Seance:
    rng = rng or random.Random()
    story = make_story(story_id or pick_story(endings, rng), rng)
    session = Seance(
        session_id=session_id, chat_id=chat_id, host=Participant(host_id, host_name), started_at=now, last_activity=now,
        idle_limit=rules.IDLE_TIMEOUT_S, wallet_start=wallet, story=story, state=E.new_state(story), items=tuple(items),
        gifts=tuple(gift.key for gift in salon.owned_gifts(gifts)),
    )
    session.heard.extend(f"— {line}" for line, _ in story.content.intro)
    return session


def _event(kind: str, text: str, pace: tuple[float, float] = rules.NO_PAUSE, **data: Any) -> DisplayEvent:
    return DisplayEvent(kind, text, pause=pace[0], spread=pace[1], data=data)


def arrival_events(session: Seance) -> tuple[DisplayEvent, ...]:
    """Клиент заходит и рассказывает. Торопиться некуда."""
    content = session.content
    events = [_event("line", content.arrival, scene=(content.archetype, session.items, session.gifts))]
    for line, direction in content.intro:
        events.append(_event("tell", f"— {line}", rules.PACE_TELL))
        if direction:
            events.append(_event("line", direction))
    return tuple(events)


# --- что можно нажать -------------------------------------------------------

def _questions_left(session: Seance) -> int:
    return session.content.question_limit - len(session.state.questions)


def available_actions(session: Seance) -> tuple[Action, ...]:
    if session.finished:
        return ()
    content, state, phase = session.content, session.state, session.phase
    hush = Action("hush", "🌫 Уклониться", "control")
    if phase == "questions":
        asks = [Action(f"q:{index}", question.label, "question") for index, question in enumerate(content.questions)
                if question.id not in state.questions] if _questions_left(session) > 0 else []
        return (*asks, Action("begin", "🃏 Разложить карты", "control"), Action("heard", "📜 Что прозвучало", "control"))
    if phase == "tarot":
        card = content.cards[session.card]
        studied = E.studied_on(state, card)
        actions = []
        for index, symbol in enumerate(card.symbols):
            if symbol in studied:
                actions += [Action(f"rd:{index}:{number}", reading.label, "choice") for number, reading in enumerate(symbol.readings)]
            elif len(studied) < content.study_limit:
                actions.append(Action(f"st:{index}", f"🔎 {symbol.title}", "look"))
        back = [Action("prev", "↩ Прошлая карта", "control")] if session.card else []
        return (*actions, *back, hush)
    if phase == "tone":
        return (*(Action(f"tone:{tone}", label, "choice") for tone, label in TONE_LABELS.items()),
                Action("redo", "↩ Передумать", "control"), hush)
    if phase == "palm":
        more = len(state.draft.inspected) < content.inspect_limit
        zones = [Action(f"zn:{index}", f"🔎 {zone.title}", "zone") for index, zone in enumerate(content.zones)
                 if zone.id not in state.draft.inspected] if more else []
        speak = [Action("words", "💬 Сказать о руке", "control")] if state.draft.inspected else []
        return (*zones, *speak, hush)
    if phase == "word":
        words = [Action(f"wd:{index}", word.label, "choice") for index, word in enumerate(content.words)
                 if word.zone in state.draft.inspected]
        back = [Action("back", "🔎 Осмотреть ещё", "control")] if len(state.draft.inspected) < content.inspect_limit else []
        return (*words, *back, hush)
    if phase == "horoscope":
        return (*(Action(f"pl:{index}", f"{tendency.planet} {tendency.name}", "sign") for index, tendency in enumerate(content.tendencies)),
                hush)
    if phase == "forecast":
        tendency = content.tendencies[session.planet]
        return (*(Action(f"fc:{index}", forecast.label, "choice") for index, forecast in enumerate(tendency.forecasts)),
                Action("back", "↩ Другая планета", "control"))
    if phase == "reaction":
        if state.queue:
            return (Action("scene", f"👀 {content.events[state.queue[0]].teaser}…", "choice"),)
        actions = []
        if E.can_decide(state):
            support, hold = content.intents[state.last.intent]
            actions += [Action("sup", f"👍 {support}", "choice"), Action("hold", f"✋ {hold}", "choice")]
        if E.can_correct(state):
            actions += [Action(f"fix:{kind}", label, "fix") for kind, label in FIX_LABELS.items()]
        return (*actions, Action("next", NEXT_LABELS[E.next_method(state)], "control"), Action("heard", "📜 Что прозвучало", "control"))
    if phase == "final":
        return tuple(Action(f"adv:{index}", label, "choice") for index, (_, label, _) in enumerate(content.advice))
    return ()


# --- хроника → события ------------------------------------------------------

def _beat_event(session: Seance, beat: E.Beat) -> DisplayEvent:
    if beat.kind == "client":
        session.heard.append(f"— {beat.text}")
        return _event("answer", f"— {beat.text}")
    if beat.kind == "you":
        return _event("voice", f"— Ты: {rules.quote(beat.text)}")
    if beat.kind == "finding":
        return _event("hint", f"🔎 {beat.who}. {beat.text}")
    if beat.kind == "message":
        return _event("hint", f"✉️ {beat.who}: {beat.text}")
    if beat.kind == "voice":
        return _event("hint", f"📞 {beat.who}: «{beat.text}»")
    return _event("line", beat.text)


def _fresh(session: Seance) -> list[DisplayEvent]:
    """Новые реплики хроники. Сцены поступков ждут, пока мадам на них посмотрит."""
    log = session.state.log
    beats, session.shown = log[session.shown:], len(log)
    return [_beat_event(session, beat) for beat in beats if beat.ev is None]


def _scene(session: Seance, event_id: str) -> list[DisplayEvent]:
    title = session.content.events[event_id].title
    return [_event("line", f"🎬 {title}", rules.PACE_REVEAL),
            *(_beat_event(session, beat) for beat in session.state.log if beat.ev == event_id)]


def _status(session: Seance) -> DisplayEvent:
    return _event("status", session.content.moods[session.state.mood])


def _open(session: Seance, events: list[DisplayEvent]) -> None:
    """Началось гадание: клиент что-то говорит, мадам берётся за реквизит."""
    method = session.state.scene
    session.phase, session.card, session.planet, session.forecast = method, 0, None, None
    events += _fresh(session)
    events.append(_event("ritual", RITUALS[method]))
    if method == "tarot":
        events.append(_event("omen", "", rules.PACE_RITUAL, cards=[card.art for card in session.content.cards]))
    elif method == "palm":
        events.append(_event("omen", "", rules.PACE_RITUAL, drawing=("zones", ())))


def _said(session: Seance, st: E.Statement, events: list[DisplayEvent], metrics: list) -> None:
    session.phase = "reaction"
    events += _fresh(session)
    events.append(_status(session))
    metrics.append(("action", {
        "kind": "say", "mode": "seance", "story": session.story.id, "method": st.method, "parts": list(st.parts), "tone": st.tone,
        "hollow": st.hollow, "events": list(st.events), "intent": st.intent, "misheard": st.misheard,
        "studied": st.data.get("studied", []), "inspected": st.data.get("inspected", []), "order": len(session.state.statements),
    }))


# --- развязка ---------------------------------------------------------------

def fee_of(base: int, outcome: E.Outcome) -> int:
    """Гонорар зависит от того, каким клиент ушёл, а не от угаданного ответа."""
    if outcome.refund:
        rate = REFUND_RATE
    elif outcome.client_state == "content" and outcome.recognized:
        rate = RECOGNIZED_RATE
    else:
        rate = FEE_RATE[outcome.client_state]
    return rules._round_money(base * rate)


def epilogue(outcome: E.Outcome) -> str:
    """Разбор: что сбылось, почему так и что было на самом деле."""
    blocks = []
    if outcome.predictions:
        lines = ["🔮 Что сбылось"]
        for prediction in outcome.predictions:
            tone = f" · {TONE_WORDS[prediction.tone]}" if prediction.tone else ""
            cause = " из-за слов мадам" if prediction.origin == "caused" else ""
            lines.append(f"{VERDICT_ICONS[prediction.verdict]} {METHOD_TITLES[prediction.method]}{tone} — «{prediction.summary}»: "
                         f"{VERDICT_WORDS[prediction.verdict]}{cause}. {prediction.note}")
        blocks.append("\n".join(lines))
    if outcome.chain:
        blocks.append("\n".join(["🧷 Почему так", *(f"• {label} {cause}" for label, cause in outcome.chain)]))
    if outcome.corrections:
        blocks.append("\n".join(["✏️ Исправления", *(f"• {line}" for line in outcome.corrections)]))
    blocks.append(f"📜 Как было на самом деле\n{outcome.truth}")
    return "\n\n".join(blocks)


def _finish(session: Seance, advice: int, now: float, events: list[DisplayEvent], metrics: list) -> None:
    story, state = session.story, session.state
    E.finish(story, state, session.content.advice[advice][0])
    events += _fresh(session)
    while state.queue:  # то, что клиент сделал уже без мадам
        events += _scene(session, E.shown(state))
    outcome = story.outcome(state)
    session.outcome, session.ending = outcome, outcome.ending
    session.fee = fee_of(session.content.fee, outcome)
    session.phase, session.finished, session.finish_reason = "done", True, "completed"
    events.append(_event("reveal", f"{outcome.mark} {outcome.title}\n{outcome.kicker}\n\n{outcome.text}", rules.PACE_REVEAL))
    events.append(_event("answer" if outcome.quote.startswith("«") else "line", outcome.quote))
    events.append(_event("line", outcome.afterword))
    metrics.append(("finish", {
        "mode": "seance", "mechanic": MECHANIC, "story": story.id, "ending": outcome.ending, "client_state": outcome.client_state,
        "recognized": outcome.recognized, "refund": outcome.refund, "fee": session.fee, "wallet": session.wallet_start + session.fee,
        "advice": state.advice, "questions": list(state.questions),
        "statements": [{"method": st.method, "parts": list(st.parts), "tone": st.tone, "hollow": st.hollow, "status": st.status,
                        "intent": st.intent_state} for st in state.statements],
        "verdicts": [prediction.verdict for prediction in outcome.predictions],
        "events": [{"id": event.id, "how": event.how, "later": event.later} for event in state.events],
        "moves": session.step, "duration_s": round(max(0.0, now - session.started_at), 1), "reason": "completed",
    }))


# --- ход --------------------------------------------------------------------

def act(session: Seance, actor_id: int, action_id: str, *, now: float) -> SeanceResult:
    """Применить действие мадам. Недопустимое — ``ValueError``, чужой — ``PermissionError``."""
    if session.finished:
        raise ValueError("сеанс уже закончен")
    if not session.is_participant(actor_id):
        raise PermissionError("действует не мадам")
    if action_id not in {action.id for action in available_actions(session)}:
        raise ValueError(f"действие недоступно: {action_id}")

    session.last_activity = now
    if action_id == "heard":
        # Перечитать — не ход: кнопки под текущим сообщением остаются рабочими.
        return SeanceResult((), (("action", {"kind": "heard", "mode": "seance", "lines": len(session.heard)}),), build_view(session), fresh=False)
    session.step += 1
    story, state, content = session.story, session.state, session.content
    events: list[DisplayEvent] = []
    metrics: list[tuple[str, dict[str, Any]]] = []
    head, _, rest = action_id.partition(":")

    if head == "q":
        question = content.questions[int(rest)]
        E.ask(story, state, question.id)
        events += _fresh(session)
        metrics.append(("action", {"kind": "question", "mode": "seance", "story": story.id, "question": question.id,
                                   "left": _questions_left(session)}))
    elif head == "begin":
        E.begin(story, state)
        _open(session, events)
    elif head == "st":
        E.study(story, state, content.cards[session.card].symbols[int(rest)].id)
    elif head == "rd":
        symbol, _, reading = rest.partition(":")
        E.read(story, state, content.cards[session.card].symbols[int(symbol)].readings[int(reading)].id)
        if session.card + 1 < len(content.cards):
            session.card += 1
        else:
            session.phase = "tone"
    elif head == "prev":
        session.card -= 1
    elif head == "redo":
        session.phase, session.card, session.forecast = state.scene, 0, None
    elif head == "tone":
        st = (E.say_tarot(story, state, rest) if state.scene == "tarot" else E.say_forecast(story, state, session.forecast, rest))
        _said(session, st, events, metrics)
    elif head == "hush":
        _said(session, E.say_nothing(story, state), events, metrics)
    elif head == "zn":
        E.inspect(story, state, content.zones[int(rest)].id)
        events += _fresh(session)
        events.append(_event("omen", "", rules.PACE_REACTION, drawing=("zones", tuple(state.draft.inspected))))
        if len(state.draft.inspected) >= content.inspect_limit:
            session.phase = "word"
    elif head == "words":
        session.phase = "word"
    elif head == "wd":
        _said(session, E.say_word(story, state, content.words[int(rest)].id), events, metrics)
    elif head == "pl":
        session.planet, session.phase = int(rest), "forecast"
    elif head == "fc":
        session.forecast, session.phase = content.tendencies[session.planet].forecasts[int(rest)].id, "tone"
    elif head == "back":
        session.phase = "palm" if session.phase == "word" else "horoscope"
    elif head == "scene":
        event_id = E.shown(state)
        events += _scene(session, event_id)
        events.append(_status(session))
        metrics.append(("action", {"kind": "scene", "mode": "seance", "story": story.id, "scene": event_id}))
    elif head in ("sup", "hold"):
        decision = "support" if head == "sup" else "hold"
        intent = state.last.intent
        E.decide(story, state, decision)
        events += _fresh(session)
        events.append(_status(session))
        metrics.append(("action", {"kind": "intent", "mode": "seance", "story": story.id, "intent": intent, "decision": decision}))
    elif head == "fix":
        E.correct(story, state, rest)
        events += _fresh(session)
        events.append(_status(session))
        metrics.append(("action", {"kind": "correct", "mode": "seance", "story": story.id, "correction": rest,
                                   "acted": bool(state.last.events)}))
    elif head == "next":
        E.proceed(story, state)
        if state.scene == "final":
            session.phase = "final"
            events += _fresh(session)
        else:
            _open(session, events)
    elif head == "adv":
        _finish(session, int(rest), now, events, metrics)
    return SeanceResult(tuple(events), tuple(metrics), build_view(session), fresh=bool(events))


# --- что на экране ----------------------------------------------------------

def _findings(session: Seance) -> list[str]:
    zones = {zone.id: zone for zone in session.content.zones}
    return [f"🔎 {zones[key].title}. {zones[key].finding}" for key in session.state.draft.inspected]


def _screen(session: Seance) -> str:
    """Текст текущего шага: он остаётся на экране, пока мадам выбирает."""
    content, state, phase = session.content, session.state, session.phase
    if phase == "tarot":
        card = content.cards[session.card]
        studied = E.studied_on(state, card)
        lines = [f"🃏 Карта {session.card + 1} из {len(content.cards)}. {card.place} — {card.title}", card.hint, ""]
        lines += [f"🔎 {symbol.title}. {symbol.look}" if symbol in studied else f"· {symbol.title}" for symbol in card.symbols]
        chosen = []
        for done in content.cards:
            found = E.find_reading(session.story, state.draft.readings.get(done.id, ""))
            if found is not None:
                chosen.append(f"✔ {done.place}: {found[1].title} — {found[2].label}")
        return "\n".join(lines + ([""] + chosen if chosen else []))
    if phase == "tone" and state.scene == "tarot":
        return "\n\n".join(("🃏 Вот что складывается:", *(f"{place}\n{text}" for place, text in E.tarot_parts(session.story, state))))
    if phase == "tone":
        return f"🌙 {E.forecast_text(session.story, session.forecast, None)}"
    if phase == "palm":
        head = (f"🖐 {session.client}: рука лежит на столе ладонью вверх. Долго держать её не дадут: "
                f"осмотреть получится участков — {content.inspect_limit} из {len(content.zones)}.")
        return "\n".join((head, *_findings(session)))
    if phase == "word":
        words = [f"{MARKS[index]}. {word.label}\n{rules.quote(word.text)}" for index, word in enumerate(E.available_words(session.story, state))]
        return "\n\n".join(("\n".join(_findings(session)), *words))
    if phase == "horoscope":
        return "\n\n".join((f"{content.sign}. Тенденции недели",
                            *(f"{tendency.planet} {tendency.name} — {tendency.theme}\n{tendency.text}" for tendency in content.tendencies)))
    if phase == "forecast":
        tendency = content.tendencies[session.planet]
        return "\n\n".join((f"{tendency.planet} {tendency.name} — {tendency.theme}\n{tendency.text}",
                            *(f"{MARKS[index]}. «{forecast.phrase}»." for index, forecast in enumerate(tendency.forecasts))))
    if phase == "final":
        said = [f"{METHOD_TITLES[st.method]}: «{st.summary}»" + (f" — {TONE_WORDS[st.tone]}" if st.tone else "")
                for st in state.statements]
        advice = [f"{MARKS[index]}. {label}: {rules.quote(text)}" for index, (_, label, text) in enumerate(content.advice)]
        return "\n".join(("📜 Сказано за сеанс", *(said or ["Ничего определённого."]), "", *advice))
    return ""


def _prompt(session: Seance) -> str:
    state, phase = session.state, session.phase
    if phase == "questions":
        left = _questions_left(session)
        return f"Что уточнить? Осталось вопросов: {left}" if left else "Вопросы заданы. Пора раскладывать карты."
    if phase == "tarot":
        card = session.content.cards[session.card]
        studied = len(E.studied_on(state, card))
        if not studied:
            return "Какой образ рассмотреть?"
        return "Как прочесть этот образ?" if studied >= session.content.study_limit else "Рассмотри ещё образ или выбери прочтение."
    if phase == "tone":
        return "Как это сказать?"
    if phase == "palm":
        return "Какой участок осмотреть?" if not state.draft.inspected else "Что осмотреть ещё?"
    if phase == "word":
        return "Что сказать о руке?"
    if phase == "horoscope":
        return "Какую планету читать?"
    if phase == "forecast":
        return "Что предсказать?"
    if phase == "reaction":
        return "Что там происходит?" if state.queue else "Что дальше?"
    if phase == "final":
        return "Что сказать на прощание?"
    return ""


def heard_text(session: Seance) -> str:
    """Всё, что сказал клиент, по порядку. Без выводов: думать мадам придётся самой."""
    return "\n\n".join((f"📜 {session.client}: что прозвучало", *session.heard))


def build_view(session: Seance) -> SeanceView:
    outcome = session.outcome
    return SeanceView(
        session_id=session.session_id,
        step=session.step,
        host=session.host.name,
        client=session.client,
        phase=session.phase,
        prompt=_prompt(session),
        screen=_screen(session),
        actions=available_actions(session),
        finished=session.finished,
        finish_reason=session.finish_reason,
        fee=session.fee,
        wallet=session.wallet_start + session.fee,
        ending=session.ending,
        ending_title=outcome.title if outcome else None,
        voiced=sum(1 for st in session.state.statements if not st.hollow),
        story=session.story.id,
        endings_total=len(session.story.endings),
        client_state=outcome.client_state if outcome else "",
        client_text=outcome.client_text if outcome else "",
        epilogue=epilogue(outcome) if outcome else "",
    )


def start_data(session: Seance) -> dict[str, Any]:
    return {"mode": "seance", "mechanic": MECHANIC, "story": session.story.id, "client": session.client, "items": list(session.items)}


def abandon_summary(session: Seance, reason: str, now: float) -> dict[str, Any]:
    idle_s = round(max(0.0, now - session.last_activity), 1)
    if not session.finished:
        session.finished, session.finish_reason = True, reason
    state = session.state
    return {
        "mode": "seance", "mechanic": MECHANIC, "story": session.story.id, "reason": reason, "phase": session.phase, "idle_s": idle_s,
        "statements": [f"{st.method}:{'hollow' if st.hollow else st.tone or 'word'}" for st in state.statements],
        "questions": list(state.questions), "events": [event.id for event in state.events], "moves": session.step,
        "duration_s": round(max(0.0, now - session.started_at), 1),
    }
