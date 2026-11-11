"""Правила салона «Гуру»: единственное место, где меняется состояние партии.

Приём клиента повторяет работу гадалки:

1. Клиент приходит и рассказывает свою версию. Выводы игрок делает сам.
2. До двух уточнений (``clarify``): проверить версию клиента или узнать новое.
3. До двух гаданий (``divine``): метод показывает расклад и толкования, игрок сам
   выбирает, что озвучить (``interpret``). Попал или нет, видно только по реакции,
   а реакция зависит от характера клиента. Настроение клиента меняется вслух.
4. Конструктор «кто / что / когда» и объявление с уверенностью (``announce``).
5. Развязка и итог (``done``); следующий клиент приходит по кнопке.

Таро намекает на «кто», гороскоп — на то, что ожидается, ладонь — на сферу, энергетика —
на эмоцию; срок приходится выяснять у самого клиента. Эхо — слова клиента, выданные за
ясновидение без своего гадания: цена зависит от архетипа. Уверенность — ставка;
критическое разоблачение стоит запаса легенды салона.

У клиента три разные шкалы: доверие к гадалке, удовлетворённость сеансом и склонность
верить в мистику. Скептик может не поверить ни слову и уйти довольным: сам процесс
гадания чего-то стоит, даже когда предсказание провалилось.

Случайность приходит как ``random.Random``, время — числом секунд, поэтому партия
воспроизводится по seed.
"""

from __future__ import annotations

import random
import zlib
from dataclasses import dataclass, field
from typing import Any

from games.guru import content as C
from games.guru.contract import Action, ActResult, ClientView, Diploma, DisplayEvent, View
from games.guru import salon
from games.guru.model import (
    CLIENT_SOURCES,
    Client,
    ClientReport,
    Knowledge,
    Participant,
    Reading,
    ReadingResult,
    Session,
    SlotResult,
    Story,
)
from games.guru.stories import STORIES_BY_ID, stories_of

# Меняется при любом изменении баланса: по нему разделяют метрики в журнале.
RULES_VERSION = 9

ARCHETYPE_ORDER = (
    "skeptic", "jealous", "businessman", "romantic", "granny", "blogger", "conspiracy", "student", "tycoon", "desperate",
)
CLIENTS_PER_SESSION = 3
SKEPTIC_CHANCE = 0.25  # доля смен, в которых появляется скептик
EVENT_CHANCE = 0.35  # как часто на приёме что-то случается
LEGEND_START = 2
NOVICE_SHIFTS = 2  # столько первых смен игрок считается новичком
NOVICE_LUCK = 1  # сколько разоблачений за смену сходит новичку с рук
CLARIFICATIONS = 2
READINGS = 2
IDLE_TIMEOUT_S = 900

# Драматические паузы: (минимум секунд, сколько можно случайно добавить).
NO_PAUSE = (0.0, 0.0)
PACE_TELL = (3.0, 2.0)  # эмоциональный рассказ
PACE_ANSWER = (3.0, 1.0)  # обычная реплика клиента
PACE_RITUAL = (4.0, 1.0)  # тасование, разглядывание ладони
PACE_REACTION = (1.0, 1.0)  # реакция на выбор игрока
PACE_REVEAL = (4.0, 2.0)  # важное открытие

DRAWINGS = {"palm": "palm", "energy": "aura"}  # метод → рисунок, который показывает адаптер

# Качество расклада: (два толкования с правдой, три с правдой); остаток — правды среди них нет.
READING_QUALITY = {
    "palm": (0.25, 0.65),
    "tarot": (0.20, 0.65),
    "horoscope": (0.20, 0.65),
    "energy": (0.15, 0.60),
}
TRUST_WARM = 10
TRUST_COLD = -8
TRUST_OVERREACT = 6
HOROSCOPE_TRUST = 4
SKEPTIC_HOROSCOPE_TRUST = -5
SUSPICION_WARM = -5
SUSPICION_COLD = 10
SKEPTIC_START_SUSPICION = 20
SUSPICION_EXIT = 100
SKEPTIC_SEE_MISS_AT = 60
SOFT_ECHO_SUSPICION = 20

SATISFACTION_START = 40
SATISFACTION_QUESTION = 3
SATISFACTION_WARM = 12
SATISFACTION_COLD = 3  # сам ритуал чего-то стоит, даже если мимо
SATISFACTION_OUTCOME = {"hit": 25, "partial": 10, "miss": 0, "exposure": -5}
SATISFACTION_JACKPOT = 15
PLEASED_AT = 65
SOUR_BELOW = 35
PLEASED_POINTS = 1
EXPOSURE_TIP = 0.2  # разоблачили, но сеанс понравился: чаевые

SCORE = {"hit": (2, 3, 5), "partial": (1, 1, -1), "miss": (0, -1, -3), "exposure": (-4, -4, -4)}
# Доля гонорара: (кажется, вижу, клянусь). Минус — мадам возвращает деньги, настроение клиента тут не важно.
FEE = {
    "hit": (0.6, 1.0, 1.6),
    "partial": (0.5, 0.4, -0.2),
    "miss": (0.2, -0.3, -0.6),
    "exposure": (-0.4, -0.6, -1.0),
}
ECHO_DISCOUNT = 0.5
STREAK_BONUS = {2: 2, 3: 3}
MASTERY_BONUS = 2
CROOK_MONEY = 7000
CROOK_MAX_SCORE = 4

# Порядок важен: в журнал идёт первая сработавшая причина.
CRITICAL_REASONS = ("skeptic_echo", "sworn_miss", "skeptic_confident_miss", "sworn_echo", "trap", "suspicion")

PROMPTS = {
    "interpret": "Что озвучить?",
    "who": "Кто?",
    "what": "Что произойдёт?",
    "when": "Когда?",
    "announce": "Как объявить?",
}


# --- числа ------------------------------------------------------------------

def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _round_money(value: float) -> int:
    return int(round(value / 50.0)) * 50


def rub(amount: int) -> str:
    return f"{amount:,}".replace(",", " ").replace("-", "−") + " ₽"


def signed_rub(amount: int) -> str:
    return f"+{rub(amount)}" if amount > 0 else rub(amount)


def flat_fee(base: int, outcome: str, level: int, bonus: float = 0.0) -> int:
    """Деньги за исход без поправки на настроение клиента. Надбавка не увеличивает возврат."""
    rate = FEE[outcome][level]
    return _round_money(base * rate * (1 + bonus if rate > 0 else 1))


# --- создание ---------------------------------------------------------------

_NO_EVENT = C.ClientEvent("none", "")


def _what_options(story: Story, rng: random.Random) -> tuple[str, ...]:
    """По одному событию на сферу: правда, версии клиента, события джекпота и ловушки, остальное случайно."""
    forced: dict[str, str] = {}
    claims = [story.said.get("what")] + [q.said.get("what") for q in story.questions]
    for tag in claims:
        if tag and tag != story.what:
            forced[C.WHAT[tag].sphere] = tag
    for combo in (story.jackpot, story.trap):
        if combo is not None:
            for slot, tag in combo.picks:
                if slot == "what":
                    forced[C.WHAT[tag].sphere] = tag
    options = []
    for sphere in C.SPHERES:
        if sphere == story.sphere:
            options.append(story.what)
        elif sphere in forced:
            options.append(forced[sphere])
        else:
            options.append(rng.choice([tag.key for tag in C.WHAT.values() if tag.sphere == sphere]))
    rng.shuffle(options)
    return tuple(options)


def new_client(story: Story, rng: random.Random, now: float, *, perks: salon.Perks = salon.NO_PERKS,
               event_chance: float = 0.0) -> Client:
    kind = story.kind
    event = rng.choice(C.CLIENT_EVENTS) if event_chance and rng.random() < event_chance else None
    ritual = rng.choice(perks.rituals) if perks.rituals else None
    shift = event or _NO_EVENT
    when = rng.choice(story.when_options)
    who_options = [story.who, *story.who_decoys]
    rng.shuffle(who_options)
    client = Client(
        story=story,
        name=rng.choice(story.names),
        sign=rng.choice(C.ZODIAC_SIGNS),
        truth={"who": story.who, "what": story.what, "when": when},
        options={"who": tuple(who_options), "what": _what_options(story, rng), "when": tuple(C.WHEN)},
        trust=int(_clamp(kind.base_trust + perks.trust + shift.trust, 0, 100)),
        suspicion=SKEPTIC_START_SUSPICION + shift.suspicion if kind.suspicion else 0,
        satisfaction=int(_clamp(SATISFACTION_START + perks.satisfaction + shift.satisfaction, 0, 100)),
        started_at=now,
        questions_left=CLARIFICATIONS + perks.questions + shift.questions,
        readings_left=READINGS + perks.readings + shift.readings,
        event=event,
        ritual=ritual,
        perks=perks,
    )
    for slot, tag in story.said.items():
        resolved = client.resolve(tag)
        client.knowledge.append(Knowledge(slot, resolved, "disclosed", resolved == client.truth[slot]))
    return client


def shift_archetypes(rng: random.Random, clients: int = CLIENTS_PER_SESSION, skeptic_factor: float = 1.0) -> list[str]:
    """Типы клиентов на смену: разные, скептик — с вероятностью ``SKEPTIC_CHANCE`` (лавка может её снизить)."""
    others = [archetype for archetype in ARCHETYPE_ORDER if archetype != "skeptic"]
    if rng.random() >= SKEPTIC_CHANCE * skeptic_factor:
        return rng.sample(others, clients)
    order = ["skeptic", *rng.sample(others, clients - 1)]
    rng.shuffle(order)
    return order


def new_session(
    *,
    session_id: str,
    chat_id: int,
    player_id: int,
    player_name: str,
    rng: random.Random,
    now: float,
    plan: tuple[str, ...] | None = None,
    wallet: int = 0,
    items: tuple[str, ...] = (),
    novice: bool = False,
) -> Session:
    perks = salon.perks_of(items)
    # Заданный план — воспроизводимый сценарий, случайные события ему не нужны.
    event_chance = EVENT_CHANCE if plan is None else 0.0
    if plan is None:
        # Скептик разоблачает за одну ошибку: новичку он достаётся, когда правила уже понятны.
        archetypes = shift_archetypes(rng, CLIENTS_PER_SESSION + perks.clients, 0.0 if novice else perks.skeptic)
        plan = tuple(rng.choice(stories_of(archetype)).id for archetype in archetypes)
    if not plan or any(story_id not in STORIES_BY_ID for story_id in plan):
        raise ValueError(f"неизвестные истории: {plan}")
    return Session(
        session_id=session_id,
        chat_id=chat_id,
        participants=[Participant(player_id, player_name)],
        plan=tuple(plan),
        started_at=now,
        last_activity=now,
        client=new_client(STORIES_BY_ID[plan[0]], rng, now, perks=perks, event_chance=event_chance),
        legend=LEGEND_START,
        legend_start=LEGEND_START,
        wallet_start=wallet,
        idle_limit=IDLE_TIMEOUT_S,
        items=tuple(items),
        perks=perks,
        event_chance=event_chance,
        forgive_left=perks.forgive,
        novice=novice,
        luck_left=NOVICE_LUCK if novice else 0,
    )


def arrival_events(session: Session) -> tuple[DisplayEvent, ...]:
    """Клиент заходит и рассказывает свою версию."""
    client = session.client
    events = [DisplayEvent("arrive", client.render(client.story.arrival))]
    events += [DisplayEvent("line", client.render(line)) for line in (client.ritual, client.event.line if client.event else None) if line]
    events += [
        DisplayEvent("tell", client.render(part), pause=PACE_TELL[0], spread=PACE_TELL[1])
        for part in client.story.told
    ]
    return tuple(events)


# --- действия ---------------------------------------------------------------

def _tag_label(slot: str, tag: str) -> str:
    return {"who": C.WHO, "what": C.WHAT, "when": C.WHEN}[slot][tag].label


def interpretation(slot: str, tag: str) -> Any:
    return {"who": C.WHO, "what": C.WHAT, "sphere": C.SPHERES, "emotion": C.EMOTIONS}[slot][tag]


def available_actions(session: Session) -> tuple[Action, ...]:
    if session.finished:
        return ()
    client = session.client
    phase = client.phase
    actions: list[Action] = []
    if phase == "clarify":
        actions += [Action(f"c:{q.id}", q.label, "question") for q in client.story.questions if q.id not in client.asked]
        actions.append(Action("c:stop", "Хватит вопросов", "control"))
    elif phase == "divine":
        actions += [Action(f"m:{m.key}", m.label, "method") for m in C.METHODS.values() if m.key not in client.used_methods]
        actions.append(Action("go", "🔮 К предсказанию", "control"))
    elif phase == "interpret" and client.pending is not None:
        slot = client.pending.slot
        actions += [Action(f"i:{tag}", interpretation(slot, tag).button, "interpretation") for tag in client.pending.offered]
    elif phase in C.SLOTS:
        actions += [Action(f"s:{phase}:{tag}", _tag_label(phase, tag), "option") for tag in client.options[phase]]
        if phase != "who":
            actions.append(Action("reset", "↺ Заново", "control"))
    elif phase == "announce":
        actions += [Action(f"a:{level}", C.CONFIDENCE_LABELS[level], "confidence") for level in C.CONFIDENCE]
        actions.append(Action("reset", "↺ Заново", "control"))
    elif phase == "done":
        actions.append(Action("next", "🚪 Следующий клиент", "control"))
    return tuple(actions)


@dataclass
class _Turn:
    events: list[DisplayEvent] = field(default_factory=list)
    metrics: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    finished_client: ClientView | None = None

    def show(self, kind: str, text: str, pace: tuple[float, float] = NO_PAUSE, *, replace: bool = False,
             data: dict[str, Any] | None = None) -> None:
        self.events.append(DisplayEvent(kind, text, pause=pace[0], spread=pace[1], replace=replace, data=data or {}))


def act(session: Session, actor_id: int, action_id: str, rng: random.Random, *, now: float) -> ActResult:
    """Применить действие участника. Недопустимое действие — ``ValueError``, чужой — ``PermissionError``."""
    if session.finished:
        raise ValueError("партия уже закончена")
    if not session.is_participant(actor_id):
        raise PermissionError("действует не участник партии")
    if action_id not in {action.id for action in available_actions(session)}:
        raise ValueError(f"действие недоступно: {action_id}")

    session.step += 1
    session.last_activity = now
    turn = _Turn()
    client = session.client
    head, _, rest = action_id.partition(":")

    if head == "c":
        if rest == "stop":
            client.phase = "divine"
        else:
            _clarify(session, client, rest, rng, turn, now)
    elif head == "m":
        _open_reading(client, rest, rng, turn)
    elif head == "i":
        _interpret(session, client, rest, rng, turn, now)
    elif head == "s":
        slot, _, tag = rest.partition(":")
        client.picks[slot] = tag
        following = C.SLOTS.index(slot) + 1
        client.phase = C.SLOTS[following] if following < len(C.SLOTS) else "announce"
    elif head == "a":
        _announce(session, client, rest, rng, turn, now)
    elif action_id == "go":
        client.phase = "who"
    elif action_id == "reset":
        client.picks.clear()
        client.phase = "who"
    elif action_id == "next":
        session.client_no += 1
        session.client = new_client(STORIES_BY_ID[session.plan[session.client_no]], rng, now,
                                    perks=session.perks, event_chance=session.event_chance)
        turn.events.extend(arrival_events(session))

    return ActResult(tuple(turn.events), tuple(turn.metrics), turn.finished_client, build_view(session))


def quote(text: str) -> str:
    # По правилам точка уходит за кавычки; многоточие, «?» и «!» остаются внутри.
    if text.endswith(".") and not text.endswith(".."):
        text = text[:-1]
    return f"«{text}»"


def _crossed_suspicion(client: Client, before: int) -> str | None:
    crossed = [text for threshold, text in client.kind.suspicion_lines if before < threshold <= client.suspicion]
    return crossed[-1].format(name=client.name) if crossed else None


def _set_trust(client: Client, delta: int) -> None:
    client.trust = int(_clamp(client.trust + delta, 0, 100))


def _add_satisfaction(client: Client, delta: int) -> None:
    client.satisfaction = int(_clamp(client.satisfaction + delta, 0, 100))


def _parting(client: Client) -> str:
    if client.satisfaction >= PLEASED_AT:
        return "pleased"
    return "sour" if client.satisfaction < SOUR_BELOW else "mixed"


def _check_exit(session: Session, client: Client, rng: random.Random, turn: _Turn, now: float) -> bool:
    """Скептик набрал достаточно и уходит посреди сеанса."""
    if not client.kind.suspicion or client.suspicion < SUSPICION_EXIT:
        return False
    turn.show("line", C.SUSPICION_EXIT_LINE.format(name=client.name), PACE_ANSWER)
    _settle(session, client, rng, turn, now, confidence=None, reasons=("suspicion",), outcome="exposure",
            echo=(), slots=_slot_results(client, {}), jackpot=False, trap=False)
    return True


def _clarify(session: Session, client: Client, question_id: str, rng: random.Random, turn: _Turn, now: float) -> None:
    kind = client.kind
    question = client.story.question(question_id)
    client.questions_left -= 1
    client.asked.append(question.id)
    before = client.suspicion
    soft = client.perks.soft_questions
    client.suspicion += kind.question_suspicion // 2 if soft else kind.question_suspicion
    _set_trust(client, max(kind.question_trust, 0) if soft else kind.question_trust)
    _add_satisfaction(client, SATISFACTION_QUESTION)
    for slot, tag in question.said.items():
        resolved = client.resolve(tag)
        client.knowledge.append(Knowledge(slot, resolved, "question", resolved == client.truth[slot]))

    turn.show("ask", f"— Ты: {quote(question.label)}", PACE_ANSWER)
    turn.show("answer", client.render(question.answer))
    turn.show("status", _crossed_suspicion(client, before) or rng.choice(kind.statuses["asked"]).format(name=client.name))
    turn.metrics.append(("action", {
        "kind": "question",
        "client_no": session.client_no,
        "archetype": kind.key,
        "story": client.story.id,
        "question": question.id,
        "question_kind": question.kind,
        "trust": client.trust,
        "suspicion": client.suspicion,
    }))
    if not _check_exit(session, client, rng, turn, now) and client.questions_left <= 0:
        client.phase = "divine"


def _reading_truth(client: Client, slot: str) -> str:
    if slot == "sphere":
        return client.story.sphere
    if slot == "emotion":
        return client.story.emotion
    return client.truth[slot]


def _universe(client: Client, slot: str) -> tuple[str, ...]:
    if slot in ("who", "what"):
        return client.options[slot]
    return tuple(C.SPHERES) if slot == "sphere" else tuple(C.EMOTIONS)


def _deal(client: Client, method: C.Method, rng: random.Random) -> Reading:
    truth = _reading_truth(client, method.slot)
    others = [tag for tag in _universe(client, method.slot) if tag != truth]
    sharp, plain = READING_QUALITY[method.key]
    # Хороший инструмент сначала убирает расклады без правды, потом делает их точнее.
    sharp = min(1.0, sharp + client.perks.quality.get(method.key, 0.0))
    plain = min(plain, 1.0 - sharp)
    roll = rng.random()
    if roll < sharp:
        offered, quality = [truth, rng.choice(others)], "sharp"
    elif roll < sharp + plain:
        offered, quality = [truth, *rng.sample(others, 2)], "plain"
    else:
        offered, quality = rng.sample(others, 3), "off"
    rng.shuffle(offered)
    return Reading(method.key, method.slot, tuple(offered), quality)


def deal_spread(offered: tuple[str, ...], rng: random.Random) -> tuple[C.TarotCard, ...]:
    """Расклад: случайная карта на каждое толкование и добор из колоды, по положениям как ляжет."""
    cards = [rng.choice(C.cards_of(who)) for who in offered]
    taken = {card.name for card in cards}
    spare = [card for card in C.TAROT_CARDS.values() if card.name not in taken]
    cards += rng.sample(spare, len(C.TAROT_POSITIONS) - len(cards))
    rng.shuffle(cards)
    return tuple(cards)


def spread_text(cards: tuple[C.TarotCard, ...]) -> str:
    lines = ["🃏 Расклад:"]
    for position, card in zip(C.TAROT_POSITIONS, cards):
        lines += ["", f"{position} — {card.name}", f"Образы: {card.symbols}."]
    return "\n".join(lines)


def aura_text(offered: tuple[str, ...], rng: random.Random) -> str:
    colors = ", ".join(C.EMOTIONS[tag].omen for tag in offered)
    return f"🌀 {rng.choice(C.AURA_OMENS)} {colors.capitalize()}."


def _deadline_hint(client: Client, rng: random.Random) -> str:
    """Альманах из лавки называет срок. Иногда промахивается, как и положено альманаху."""
    if not client.perks.deadline:
        return ""
    truth = client.truth["when"]
    tag = truth if rng.random() < client.perks.deadline else rng.choice([key for key in C.WHEN if key != truth])
    client.knowledge.append(Knowledge("when", tag, "horoscope", tag == truth))
    return f"🌙 Альманах подсказывает срок: {C.WHEN[tag].label.lower()}.\n"


def _omen(client: Client, reading: Reading, rng: random.Random) -> str:
    if reading.method == "palm":
        return "🖐 Ладонь горячая. " + ", ".join(C.SPHERES[tag].omen for tag in reading.offered).capitalize() + "."
    if reading.method == "horoscope":
        symbol, trend = C.ZODIAC[client.sign]
        forecasts = "\n".join(f"· {C.WHAT[tag].forecast}" for tag in reading.offered)
        hint = _deadline_hint(client, rng)
        return (f"{client.name}: {client.kind.sign_answer.format(sign=client.sign)}\n\n"
                f"{symbol} {client.sign}. {trend}\n{forecasts}\n{hint}"
                f"Совет звёзд: {rng.choice(C.STAR_ADVICE)}")
    return aura_text(reading.offered, rng)


def _open_reading(client: Client, method_key: str, rng: random.Random, turn: _Turn) -> None:
    method = C.METHODS[method_key]
    client.readings_left -= 1
    client.used_methods.append(method.key)
    client.pending = _deal(client, method, rng)
    client.phase = "interpret"
    turn.show("ritual", rng.choice(method.rituals))
    if method.key == "tarot":
        cards = deal_spread(client.pending.offered, rng)
        turn.show("omen", spread_text(cards), PACE_RITUAL, data={"cards": [card.art for card in cards]})
    elif method.key in DRAWINGS:
        turn.show("omen", _omen(client, client.pending, rng), PACE_RITUAL,
                  data={"drawing": (DRAWINGS[method.key], client.pending.offered)})
    else:
        turn.show("omen", _omen(client, client.pending, rng), PACE_RITUAL, replace=True)


def _reaction(client: Client, correct: bool, rng: random.Random) -> str:
    if client.kind.style == "overreact":
        return "warm"
    accurate = rng.random() < client.kind.accuracy + client.perks.accuracy
    return "warm" if correct == accurate else "cold"


def _interpret(session: Session, client: Client, tag: str, rng: random.Random, turn: _Turn, now: float) -> None:
    kind = client.kind
    reading = client.pending
    assert reading is not None
    client.pending = None
    truth = _reading_truth(client, reading.slot)
    correct = tag == truth
    reaction = _reaction(client, correct, rng)
    before = client.suspicion

    if kind.style == "overreact":
        trust_delta = TRUST_OVERREACT
    elif reaction == "warm":
        # Чем сильнее человек верит в мистику, тем легче завоевать его доверие.
        trust_delta = round(TRUST_WARM * (0.5 + kind.belief / 100))
    else:
        trust_delta = TRUST_COLD
    _add_satisfaction(client, SATISFACTION_WARM if reaction == "warm" else SATISFACTION_COLD)
    if kind.suspicion:
        client.suspicion += kind.suspicion[reading.method] + (SUSPICION_WARM if reaction == "warm" else SUSPICION_COLD)
    if reading.method == "horoscope":
        client.horoscope_used = True
        trust_delta += SKEPTIC_HOROSCOPE_TRUST if kind.suspicion else HOROSCOPE_TRUST
    _set_trust(client, trust_delta)

    client.knowledge.append(Knowledge(reading.slot, tag, reading.method, correct))
    client.readings.append(ReadingResult(reading.method, reading.slot, reading.offered, tag, truth, reading.quality, reaction))

    turn.show("voice", f"— Ты: {quote(interpretation(reading.slot, tag).voice)}", PACE_REACTION)
    turn.show("react", rng.choice(kind.reactions[reaction]).format(name=client.name))
    status_key = "up" if reaction == "warm" else "down"
    turn.show("status", _crossed_suspicion(client, before) or rng.choice(kind.statuses[status_key]).format(name=client.name))
    turn.metrics.append(("action", {
        "kind": "reading",
        "client_no": session.client_no,
        "archetype": kind.key,
        "story": client.story.id,
        "method": reading.method,
        "slot": reading.slot,
        "quality": reading.quality,
        "offered": list(reading.offered),
        "voiced": tag,
        "correct": correct,
        "reaction": reaction,
        "trust": client.trust,
        "suspicion": client.suspicion,
    }))

    if reading.method in C.MAIN_METHODS:
        session.methods_used.add(reading.method)
        if not session.mastery and set(C.MAIN_METHODS) <= session.methods_used:
            session.mastery = True
            session.score += MASTERY_BONUS
            turn.show("line", f"{C.MASTERY_LINE} +{MASTERY_BONUS} очка.")
            turn.metrics.append(("mastery_bonus", {
                "client_no": session.client_no, "method": reading.method, "points": MASTERY_BONUS,
            }))

    if not _check_exit(session, client, rng, turn, now):
        client.phase = "who" if client.readings_left <= 0 else "divine"


# --- предсказание -----------------------------------------------------------

def is_echo(client: Client, slot: str, tag: str) -> bool:
    """Тег взят из слов клиента и не подтверждён своим гаданием."""
    said = any(k.source in CLIENT_SOURCES and k.slot == slot and k.tag == tag for k in client.knowledge)
    if not said:
        return False
    return not any(k.source not in CLIENT_SOURCES and k.supports(slot, tag) for k in client.knowledge)


def prediction_body(picks: dict[str, str]) -> str:
    who = C.WHO[picks["who"]]
    what = C.WHAT[picks["what"]]
    verb = what.second if who.key == "self" else what.third
    return f"{C.WHEN[picks['when']].phrase} {who.phrase} {verb}"


def prediction_sentence(picks: dict[str, str], confidence: str) -> str:
    return f"{C.CONFIDENCE_PREFIX[confidence]} {prediction_body(picks)}."


def prediction_plain(picks: dict[str, str]) -> str:
    """Предсказание отдельной фразой, без вводных слов мадам: для карты дня и справки."""
    body = prediction_body(picks)
    return body[:1].upper() + body[1:] + "."


def _slot_results(client: Client, matches: dict[str, bool], mercury: bool = False) -> tuple[SlotResult, ...]:
    return tuple(
        SlotResult(slot, client.picks.get(slot), client.truth[slot], matches.get(slot, False),
                   mercury and slot == "when")
        for slot in C.SLOTS
    )


def _announce(session: Session, client: Client, confidence: str, rng: random.Random, turn: _Turn, now: float) -> None:
    story, kind = client.story, client.kind
    picks = dict(client.picks)
    turn.show("announce", f"— Ты: {quote(prediction_sentence(picks, confidence))}", PACE_REACTION)

    matches = {slot: picks[slot] == client.truth[slot] for slot in C.SLOTS}
    mercury = False
    # Меркурий прощает только сроки: если мимо и «кто», и «что», оправдываться нечем.
    if not matches["when"] and (matches["who"] or matches["what"]) and client.horoscope_used and session.mercury_left:
        matches["when"] = True
        mercury = True
        session.mercury_left = False
    count = sum(matches.values())
    outcome = "hit" if count == 3 else "partial" if count == 2 else "miss"
    jackpot = bool(story.jackpot and story.jackpot.matches(picks))
    if jackpot:
        outcome = "hit"
    echo = tuple(slot for slot in C.SLOTS if is_echo(client, slot, picks[slot]))
    trap = bool(story.trap and story.trap.matches(picks) and confidence != "maybe")

    reasons: list[str] = []
    if kind.echo == "expose" and echo and confidence != "maybe":
        reasons.append("skeptic_echo")
    if outcome == "miss" and confidence == "swear":
        reasons.append("sworn_miss")
    if kind.suspicion and outcome == "miss" and confidence == "see" and client.suspicion >= SKEPTIC_SEE_MISS_AT:
        reasons.append("skeptic_confident_miss")
    if kind.echo == "discount" and confidence == "swear" and len(echo) >= 2:
        reasons.append("sworn_echo")
    if trap:
        reasons.append("trap")
    if kind.suspicion and client.suspicion >= SUSPICION_EXIT:
        reasons.append("suspicion")
    if reasons:
        outcome = "exposure"

    pause = PACE_ANSWER
    if mercury:
        turn.show("line", C.MERCURY_LINE, pause)
        pause = NO_PAUSE
    if echo:
        critical = outcome == "exposure" and reasons[0] in ("skeptic_echo", "sworn_echo")
        # Восторг ревнивой и торг бизнесмена уместны, только если что-то совпало.
        if critical or kind.echo == "expose" or outcome in ("hit", "partial"):
            turn.show("react", kind.echo_lines["critical" if critical else "soft"].format(name=client.name), pause)
            pause = NO_PAUSE
        if kind.echo == "expose" and outcome != "exposure":
            client.suspicion += SOFT_ECHO_SUSPICION
    if outcome == "exposure" and reasons[0] == "trap" and story.trap is not None:
        turn.show("react", f"💥 {story.trap.text}", pause)
    elif outcome == "exposure":
        turn.show("react", kind.outcomes["exposure"].format(name=client.name), pause)
    elif jackpot and story.jackpot is not None:
        turn.show("react", f"🎰 {story.jackpot.text}", pause)
    else:
        turn.show("react", kind.outcomes[outcome].format(name=client.name), pause)
    if outcome == "partial" and confidence == "swear":
        turn.show("line", C.SWORN_PARTIAL_LINE)

    turn.metrics.append(("action", {
        "kind": "prediction",
        "client_no": session.client_no,
        "archetype": kind.key,
        "story": story.id,
        "picks": picks,
        "matches": matches,
        "confidence": confidence,
        "echo_slots": list(echo),
        "mercury": mercury,
        "jackpot": jackpot,
        "trap": trap,
        "outcome": outcome,
    }))
    _settle(session, client, rng, turn, now, confidence=confidence, reasons=tuple(reasons), outcome=outcome,
            echo=echo, slots=_slot_results(client, matches, mercury), jackpot=jackpot, trap=trap)


def _payment(client: Client, outcome: str, level: int, echo: tuple[str, ...], parting: str) -> int:
    """Гонорар клиента или, со знаком минус, сколько мадам возвращает."""
    story, kind = client.story, client.kind
    if outcome == "exposure" and parting == "pleased":
        return _round_money(story.fee * EXPOSURE_TIP)
    rate = FEE[outcome][level]
    if rate <= 0:
        return flat_fee(story.fee, outcome, level)
    fee = story.fee * (0.5 + (client.trust + client.satisfaction) / 200) * rate
    if kind.echo == "discount" and echo:
        fee *= ECHO_DISCOUNT
    event_fee = client.event.fee if client.event else 1.0
    return _round_money(fee * event_fee * (1 + client.perks.fee))


def _settle(session: Session, client: Client, rng: random.Random, turn: _Turn, now: float, *,
            confidence: str | None, reasons: tuple[str, ...], outcome: str, echo: tuple[str, ...],
            slots: tuple[SlotResult, ...], jackpot: bool, trap: bool) -> None:
    """Очки, гонорар, серия, легенда и развязка. Следующий клиент приходит по кнопке."""
    story, kind = client.story, client.kind
    level = C.CONFIDENCE.index(confidence) if confidence else 1
    _add_satisfaction(client, SATISFACTION_OUTCOME[outcome] + (SATISFACTION_JACKPOT if jackpot else 0))
    parting = _parting(client)
    points = SCORE[outcome][level] + (PLEASED_POINTS if parting == "pleased" else 0)
    fee = _payment(client, outcome, level, echo, parting)
    forgiven = outcome == "exposure" and session.forgive_left > 0
    lucky = outcome == "exposure" and not forgiven and session.luck_left > 0
    if forgiven:
        session.forgive_left -= 1
        fee = max(fee, 0)
    elif lucky:
        session.luck_left -= 1
        fee = max(fee, 0)

    streak_before, bonus = session.streak, 0
    if outcome == "hit":
        session.streak += 1
        bonus = STREAK_BONUS.get(session.streak, 0)
    elif outcome in ("miss", "exposure"):
        session.streak = 0
    session.max_streak = max(session.max_streak, session.streak)
    session.score += points + bonus
    session.money += fee

    reveal = story.reveal.format(when_lead=C.WHEN[client.truth["when"]].lead)
    turn.show("reveal", f"📜 {reveal}", PACE_REVEAL)
    turn.show("line", kind.farewells[parting].format(name=client.name))
    if bonus:
        turn.show("line", f"{C.STREAK_LINES[session.streak]} +{bonus} очка за серию.")
    if session.streak != streak_before:
        turn.metrics.append(("streak_updated", {
            "client_no": session.client_no, "before": streak_before, "after": session.streak, "bonus": bonus,
        }))

    legend_before = session.legend
    if forgiven:
        turn.show("line", C.CAT_FORGIVE_LINE)
    elif lucky:
        turn.show("line", C.NOVICE_LUCK_LINE)
        turn.metrics.append(("novice_luck", {
            "client_no": session.client_no, "archetype": kind.key, "story": story.id, "reason": reasons[0],
        }))
    elif outcome == "exposure":
        session.legend -= 1  # не больше одного запаса за клиента, сколько бы причин ни сработало
        if session.legend <= 0:
            turn.show("line", C.LEGEND_GONE_LINE)
        else:
            turn.show("line", C.LEGEND_LOST_LINE.format(left=session.legend))
            if session.legend == 1:
                turn.show("line", C.LEGEND_LAST_LINE)
        turn.metrics.append(("legend_lost", {
            "client_no": session.client_no,
            "archetype": kind.key,
            "story": story.id,
            "reason": reasons[0],
            "reasons": list(reasons),
            "before": legend_before,
            "after": session.legend,
        }))

    report = ClientReport(
        client_no=session.client_no,
        story=story.id,
        archetype=kind.key,
        reveal=reveal,
        slots=slots,
        readings=tuple(client.readings),
        questions=tuple(client.asked),
        said=tuple((k.slot, k.tag, k.truthful) for k in client.knowledge if k.source in CLIENT_SOURCES),
        echo_slots=echo,
        outcome=outcome,
        reasons=reasons,
        confidence=confidence,
        jackpot=jackpot,
        trap=trap,
        fee=fee,
        points=points + bonus,
        satisfaction=client.satisfaction,
        parting=parting,
        streak_before=streak_before,
        streak_after=session.streak,
        streak_bonus=bonus,
        legend_before=legend_before,
        legend_after=session.legend,
        duration_s=round(max(0.0, now - client.started_at), 1),
        event=client.event.key if client.event else None,
    )
    client.report = report
    client.phase = "done"
    client.pending = None
    session.reports.append(report)
    turn.finished_client = _client_view(session, client)
    turn.metrics.append(("client_reveal", report.log_data()))

    if session.legend <= 0:
        _finish(session, "exposed")
    elif session.client_no + 1 >= session.clients_total:
        _finish(session, "completed")
    if session.finished:
        turn.metrics.append(("finish", summary(session, now)))


# --- конец партии -----------------------------------------------------------

def _finish(session: Session, reason: str) -> None:
    session.finished = True
    session.finish_reason = reason


def abandon(session: Session, reason: str) -> None:
    """Партия брошена (таймаут, «стоп», рестарт бота, ошибка Telegram)."""
    if not session.finished:
        _finish(session, reason)


def is_idle(session: Any, now: float, timeout: float | None = None) -> bool:
    limit = session.idle_limit if timeout is None else timeout
    return not session.finished and now - session.last_activity >= limit


def title(session: Session) -> str:
    if session.finish_reason == "exposed":
        return C.EXPOSED_TITLE
    if session.money >= CROOK_MONEY and session.score <= CROOK_MAX_SCORE:
        return C.CROOK_TITLE
    for threshold, name in C.SCORE_TITLES:
        if session.score >= threshold:
            return name
    return C.LOW_SCORE_TITLE


def achievements(session: Session) -> tuple[str, ...]:
    earned = []
    if session.mastery:
        earned.append(C.MASTERY_ACHIEVEMENT)
    if session.max_streak >= 3:
        earned.append(C.STREAK_ACHIEVEMENT)
    return tuple(earned)


def paper_number(session_id: str) -> int:
    """Номер справки или диплома: четыре цифры, для одной партии всегда одни и те же."""
    return 1000 + zlib.crc32(session_id.encode()) % 9000


def diploma(session: Session) -> Diploma:
    outcomes = [report.outcome for report in session.reports]
    return Diploma(
        number=paper_number(session.session_id),
        player=session.host.name,
        title=title(session),
        score=session.score,
        money=session.money,
        hits=outcomes.count("hit"),
        clients=len(outcomes),
        achievements=achievements(session),
        exposed=session.finish_reason == "exposed",
    )


def summary(session: Session, now: float) -> dict[str, Any]:
    """Итог партии для журнала: всё, что потом не восстановить."""
    outcomes = [report.outcome for report in session.reports]
    return {
        "money": session.money,
        "wallet": session.wallet_start + session.money,
        "score": session.score,
        "legend_left": session.legend,
        "legend_start": session.legend_start,
        "max_streak": session.max_streak,
        "hits": outcomes.count("hit"),
        "partials": outcomes.count("partial"),
        "misses": outcomes.count("miss"),
        "exposures": outcomes.count("exposure"),
        "pleased": sum(report.parting == "pleased" for report in session.reports),
        "methods_used": sorted(session.methods_used),
        "mastery": session.mastery,
        "clients": len(session.reports),
        "clients_total": session.clients_total,
        "moves": session.step,
        "duration_s": round(max(0.0, now - session.started_at), 1),
        "reason": session.finish_reason,
        "title": title(session),
        "achievements": list(achievements(session)),
        "stories": list(session.plan),
        "items": list(session.items),
        "rules": RULES_VERSION,
    }


def start_data(session: Session) -> dict[str, Any]:
    return {
        "stories": list(session.plan),
        "items": list(session.items),
        "participants": len(session.participants),
        "legend": session.legend_start,
        "novice": session.novice,
        "rules": RULES_VERSION,
    }


def abandon_summary(session: Session, reason: str, now: float) -> dict[str, Any]:
    idle_s = round(max(0.0, now - session.last_activity), 1)
    abandon(session, reason)
    data = summary(session, now)
    data.update(idle_s=idle_s, client_no=session.client_no, phase=session.client.phase)
    return data


# --- представление ----------------------------------------------------------

def _band(value: int, bands: tuple[tuple[int, str], ...]) -> str:
    for threshold, label in bands:
        if value >= threshold:
            return label
    return bands[-1][1]


def _client_view(session: Session, client: Client) -> ClientView:
    kind = client.kind
    return ClientView(
        number=session.client_no + 1,
        archetype=kind.key,
        archetype_title=kind.title,
        emoji=kind.emoji,
        name=client.name,
        mood=_band(client.trust, C.MOOD_BANDS),
        satisfaction=_band(client.satisfaction, C.SATISFACTION_BANDS),
        suspicion=_band(client.suspicion, C.SUSPICION_BANDS) if kind.suspicion else None,
        questions_left=client.questions_left,
        readings_left=client.readings_left,
        phase=client.phase,
        picks=dict(client.picks),
        report=client.report,
    )


def preview_text(picks: dict[str, str]) -> str:
    """Собираемая фраза предсказания: по слотам, а когда всё выбрано — целиком."""
    if all(slot in picks for slot in C.SLOTS):
        body = prediction_body(picks)
        return "«" + body[:1].upper() + body[1:] + "»"
    return " · ".join(
        f"{C.SLOT_TITLES[slot]}: {_tag_label(slot, picks[slot]) if slot in picks else '…'}"
        for slot in C.SLOTS
    )


def _preview(client: Client) -> str | None:
    return preview_text(client.picks) if client.phase in (*C.SLOTS, "announce") else None


def _prompt(client: Client) -> str:
    if client.phase == "clarify":
        return f"Что уточнить? Осталось вопросов: {client.questions_left}"
    if client.phase == "divine":
        return f"Чем гадать? Осталось раскладов: {client.readings_left}"
    return PROMPTS.get(client.phase, "")


def build_view(session: Session) -> View:
    client = None if session.finished else session.client
    return View(
        session_id=session.session_id,
        step=session.step,
        player=session.host.name,
        clients_total=session.clients_total,
        legend=session.legend,
        legend_start=session.legend_start,
        streak=session.streak,
        score=session.score,
        money=session.money,
        wallet=session.wallet_start + session.money,
        sciences=tuple((method, method in session.methods_used) for method in C.MAIN_METHODS),
        client=_client_view(session, client) if client is not None else None,
        prompt=_prompt(client) if client is not None else "",
        preview=_preview(client) if client is not None else None,
        actions=available_actions(session),
        finished=session.finished,
        finish_reason=session.finish_reason,
        title=title(session) if session.finished else None,
        achievements=achievements(session) if session.finished else (),
        outcomes=tuple(report.outcome for report in session.reports),
        max_streak=session.max_streak,
        salon=salon.describe(session.items),
        novice=session.novice,
    )


# --- партии по чатам --------------------------------------------------------

class SessionRegistry:
    """Одна активная партия на чат (одиночная или живой приём). Живёт в памяти: рестарт бота её бросает."""

    def __init__(self) -> None:
        self._by_chat: dict[int, Any] = {}

    def get(self, chat_id: int) -> Any | None:
        return self._by_chat.get(chat_id)

    def add(self, session: Any) -> None:
        if session.chat_id in self._by_chat:
            raise ValueError("в этом чате уже идёт партия")
        self._by_chat[session.chat_id] = session

    def remove(self, session: Any) -> bool:
        """Убрать именно эту партию; ``False``, если в чате уже другая."""
        if self._by_chat.get(session.chat_id) is not session:
            return False
        del self._by_chat[session.chat_id]
        return True

    def idle(self, now: float, timeout: float | None = None) -> list[Any]:
        return [session for session in self._by_chat.values() if is_idle(session, now, timeout)]

    def active(self) -> list[Any]:
        return list(self._by_chat.values())

    def __len__(self) -> int:
        return len(self._by_chat)
