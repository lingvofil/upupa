"""Правила салона «Гуру» v0.2: единственное место, где меняется состояние партии.

Цикл клиента: приход (жалоба и видимый признак) → до двух инфо-приёмов
(ладонь, Таро, гороскоп, энергетика, вопрос; каждый — раз на клиента) →
конструктор «кто / что / когда» → объявление с уверенностью → разбор.

- Ладонь отвечает «да / нет» на сферу, а сфера однозначно задаёт вариант «что»:
  в конструкторе по одному событию на сферу.
- Таро намекает на «кто», гороскоп — на «когда», энергетика — на эмоцию,
  то есть сужает «что». Подсказка бывает ясной, мутной или ложной; ложная
  выглядит как ясная.
- Вопрос даёт убеждение клиента. Это «слова клиента»: выдать их за ясновидение,
  не подтвердив своим источником, — эхо, и цена эха зависит от архетипа.
- Уверенность — ставка (``SCORE``/``FEE``); критическое разоблачение стоит
  одного запаса легенды салона, на нуле салон разоблачён.

Случайность приходит как ``random.Random``, время — числом секунд, поэтому
партия воспроизводится по seed.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any

from games.guru import content as C
from games.guru.contract import Action, ActResult, ClientView, DisplayEvent, NoteView, View
from games.guru.model import (
    CLIENT_SOURCES,
    OWN_SOURCES,
    Client,
    ClientReport,
    HintResult,
    Knowledge,
    Participant,
    Session,
    SlotResult,
    Story,
)
from games.guru.stories import STORIES_BY_ID, stories_of

# Меняется при любом изменении баланса: по нему разделяют метрики в журнале.
RULES_VERSION = 2

ARCHETYPE_ORDER = ("skeptic", "jealous", "businessman")
CLIENTS_PER_SESSION = len(ARCHETYPE_ORDER)
LEGEND_START = 2
INFO_ACTIONS = 2
IDLE_TIMEOUT_S = 180

METHODS = ("palm", "tarot", "horoscope", "energy", "question")
HINT_QUALITY = {"tarot": (0.55, 0.30), "horoscope": (0.55, 0.30), "energy": (0.35, 0.45)}  # ясно, мутно
PALM_ERROR = 0.10
REACTION_ACCURACY = 0.90
SKEPTIC_START_SUSPICION = 20
SUSPICION_EXIT = 100
SKEPTIC_SEE_MISS_AT = 60
SOFT_ECHO_SUSPICION = 20
TRUST_DELTA = {"palm": 5, "tarot": 5, "energy": 5, "horoscope": 10, "question": -10}
SKEPTIC_HOROSCOPE_TRUST = -10

SCORE = {"hit": (2, 3, 5), "partial": (1, 1, -1), "miss": (0, -1, -3), "exposure": (-4, -4, -4)}
FEE = {"hit": (0.6, 1.0, 1.6), "partial": (0.5, 0.4, 0.2), "miss": (0.2, 0.1, 0.0), "exposure": (0, 0, 0)}
ECHO_DISCOUNT = 0.5
STREAK_BONUS = {2: 2, 3: 3}
MASTERY_BONUS = 2
CROOK_MONEY = 7000
CROOK_MAX_SCORE = 4

# Порядок важен: в журнал идёт первая сработавшая причина.
CRITICAL_REASONS = ("skeptic_echo", "sworn_miss", "skeptic_confident_miss", "sworn_echo", "trap", "suspicion")

SPHERE_ACCUSATIVE = {"love": "отношения", "work": "работу", "money": "деньги", "family": "семью"}
PROMPTS = {
    "palm": "Какую линию смотрим?",
    "ask": "О чём спросить?",
    "who": "Предсказание. Кто?",
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
    return f"{amount:,}".replace(",", " ") + " ₽"


# --- создание ---------------------------------------------------------------

def _what_options(story: Story, rng: random.Random) -> tuple[str, ...]:
    """По одному событию на сферу: правда, убеждение клиента, события джекпота и ловушки, остальное случайно."""
    belief = story.belief("what", story.what)
    forced = {C.WHAT[belief].sphere: belief} if belief != story.what else {}
    forced.update({
        C.WHAT[tag].sphere: tag
        for combo in (story.jackpot, story.trap)
        if combo is not None
        for slot, tag in combo.picks
        if slot == "what"
    })
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


def _observation_truthful(story: Story, slot: str, tags: tuple[str, ...], when: str) -> bool:
    truth = {"who": story.who, "what": story.what, "when": when,
             "sphere": story.sphere, "emotion": C.WHAT[story.what].emotion}[slot]
    return truth in tags


def new_client(story: Story, rng: random.Random, now: float) -> Client:
    kind = story.kind
    when = rng.choice(story.when_options)
    who_options = [story.who, *story.who_decoys]
    rng.shuffle(who_options)
    client = Client(
        story=story,
        name=rng.choice(story.names),
        sign=rng.choice(C.ZODIAC_SIGNS),
        truth={"who": story.who, "what": story.what, "when": when},
        options={"who": tuple(who_options), "what": _what_options(story, rng), "when": tuple(C.WHEN)},
        trust=kind.base_trust,
        suspicion=SKEPTIC_START_SUSPICION if kind.suspicion else 0,
        started_at=now,
        actions_left=INFO_ACTIONS,
    )
    for slot, tag in story.disclosed.items():
        client.knowledge.append(Knowledge(slot, (tag,), "disclosed", True, tag == client.truth[slot]))
    observation = story.observation
    tags = (when,) if observation.tags == ("@when",) else observation.tags
    client.knowledge.append(
        Knowledge(observation.slot, tags, "observation", len(tags) == 1,
                  _observation_truthful(story, observation.slot, tags, when))
    )
    return client


def new_session(
    *,
    session_id: str,
    chat_id: int,
    player_id: int,
    player_name: str,
    rng: random.Random,
    now: float,
    plan: tuple[str, ...] | None = None,
) -> Session:
    if plan is None:
        order = list(ARCHETYPE_ORDER)
        rng.shuffle(order)
        plan = tuple(rng.choice(stories_of(archetype)).id for archetype in order)
    if not plan or any(story_id not in STORIES_BY_ID for story_id in plan):
        raise ValueError(f"неизвестные истории: {plan}")
    return Session(
        session_id=session_id,
        chat_id=chat_id,
        participants=[Participant(player_id, player_name)],
        plan=tuple(plan),
        started_at=now,
        last_activity=now,
        client=new_client(STORIES_BY_ID[plan[0]], rng, now),
        legend=LEGEND_START,
        legend_start=LEGEND_START,
    )


# --- действия ---------------------------------------------------------------

def available_actions(session: Session) -> tuple[Action, ...]:
    if session.finished:
        return ()
    client = session.client
    phase = client.phase
    actions: list[Action] = []
    if phase == "research":
        if client.actions_left > 0:
            actions += [
                Action(f"m:{method}", C.METHOD_LABELS[method], "method")
                for method in METHODS
                if method not in client.used_methods
            ]
        actions.append(Action("go", "🔮 К предсказанию", "control"))
    elif phase == "palm":
        actions += [Action(f"p:{line.key}", line.label, "line") for line in C.PALM_LINES.values()]
        actions.append(Action("back", "← Назад", "control"))
    elif phase == "ask":
        actions += [Action(f"q:{slot}", f"{C.SLOT_TITLES[slot]}?", "question") for slot in C.SLOTS]
        actions.append(Action("back", "← Назад", "control"))
    elif phase in C.SLOTS:
        actions += [Action(f"s:{phase}:{tag}", _tag_label(phase, tag), "option") for tag in client.options[phase]]
        if phase != "who":
            actions.append(Action("reset", "↺ Заново", "control"))
    elif phase == "announce":
        actions += [Action(f"a:{level}", C.CONFIDENCE_LABELS[level], "confidence") for level in C.CONFIDENCE]
        actions.append(Action("reset", "↺ Заново", "control"))
    return tuple(actions)


@dataclass
class _Turn:
    events: list[DisplayEvent] = field(default_factory=list)
    metrics: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    finished_client: ClientView | None = None


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

    if head == "m":
        if rest == "palm":
            client.phase = "palm"
        elif rest == "question":
            client.phase = "ask"
        else:
            _info(session, client, rest, None, rng, turn, now)
    elif head == "p":
        _info(session, client, "palm", rest, rng, turn, now)
    elif head == "q":
        _info(session, client, "question", rest, rng, turn, now)
    elif head == "s":
        slot, _, tag = rest.partition(":")
        client.picks[slot] = tag
        following = C.SLOTS.index(slot) + 1
        client.phase = C.SLOTS[following] if following < len(C.SLOTS) else "announce"
    elif head == "a":
        _announce(session, client, rest, rng, turn, now)
    elif action_id == "go":
        client.phase = "who"
    elif action_id == "back":
        client.phase = "research"
    elif action_id == "reset":
        client.picks.clear()
        client.phase = "who"

    return ActResult(tuple(turn.events), tuple(turn.metrics), turn.finished_client, build_view(session))


def _line(client: Client, turn: _Turn, text: str) -> None:
    client.lines.append(text)
    turn.events.append(DisplayEvent("line", text))


def _quote(text: str) -> str:
    # По правилам точка уходит за кавычки; многоточие, «?» и «!» остаются внутри.
    if text.endswith(".") and not text.endswith(".."):
        text = text[:-1]
    return f"«{text}»"


def _say(client: Client, turn: _Turn, text: str) -> None:
    _line(client, turn, f"— Ты: {_quote(text)}")


def _slot_hint(client: Client, slot: str, method: str, rng: random.Random) -> tuple[Knowledge, str]:
    clear, vague = HINT_QUALITY[method]
    truth = client.truth[slot]
    others = [tag for tag in client.options[slot] if tag != truth]
    roll = rng.random()
    if roll < clear:
        return Knowledge(slot, (truth,), method, True, True), "clear"
    if roll < clear + vague:
        alt = rng.choice(others)
        tags = (truth, alt) if rng.random() < 0.5 else (alt, truth)
        return Knowledge(slot, tags, method, False, True), "vague"
    return Knowledge(slot, (rng.choice(others),), method, True, False), "false"


def _palm(client: Client, line_key: str, rng: random.Random, turn: _Turn) -> tuple[Knowledge, str]:
    line = C.PALM_LINES[line_key]
    correct = line.sphere == client.story.sphere
    shown = correct if rng.random() >= PALM_ERROR else not correct
    _say(client, turn, "Дай-ка руку…")
    _line(client, turn, f"🖐 {line.speaks if shown else line.silent}")
    return Knowledge("sphere", (line.sphere,), "palm", True, shown == correct, positive=shown), (
        "speaks" if shown else "silent"
    )


def _tarot(client: Client, rng: random.Random, turn: _Turn) -> tuple[Knowledge, str]:
    knowledge, quality = _slot_hint(client, "who", "tarot", rng)
    past, future = rng.sample(C.TAROT_DECOR, 2)
    _say(client, turn, "Сними колоду левой рукой. Левой, я сказала.")
    if knowledge.clear:
        card = C.WHO[knowledge.tags[0]]
        _line(client, turn, f"🃏 {past} · {card.card} · {future}. Карта настоящего — {card.card}: {card.meaning}.")
    else:
        first, second = (C.WHO[tag] for tag in knowledge.tags)
        _line(client, turn, f"🃏 {past} · {first.card} · {second.card}. Карты спорят: то ли {first.meaning}, "
                            f"то ли {second.meaning}.")
    return knowledge, quality


def _horoscope(client: Client, rng: random.Random, turn: _Turn) -> tuple[Knowledge, str]:
    _say(client, turn, "Кто ты по знаку?")
    _line(client, turn, f"{client.name}: {client.kind.sign_answer.format(sign=client.sign)}")
    knowledge, quality = _slot_hint(client, "when", "horoscope", rng)
    client.horoscope_used = True
    phrases = [C.WHEN[tag].phrase.rstrip(",") for tag in knowledge.tags]
    if knowledge.clear:
        _line(client, turn, f"♈ {client.sign}: звёзды говорят — главное случится {phrases[0]}.")
    else:
        _line(client, turn, f"♈ {client.sign}: звёзды мнутся — то ли {phrases[0]}, то ли {phrases[1]}.")
    return knowledge, quality


def _energy(client: Client, rng: random.Random, turn: _Turn) -> tuple[Knowledge, str]:
    clear, vague = HINT_QUALITY["energy"]
    truth = C.WHAT[client.truth["what"]].emotion
    others = [emotion for emotion in C.EMOTIONS if emotion != truth]
    roll = rng.random()
    if roll < clear:
        knowledge, quality = Knowledge("emotion", (truth,), "energy", True, True), "clear"
    elif roll < clear + vague:
        alt = rng.choice(others)
        tags = (truth, alt) if rng.random() < 0.5 else (alt, truth)
        knowledge, quality = Knowledge("emotion", tags, "energy", False, True), "vague"
    else:
        knowledge, quality = Knowledge("emotion", (rng.choice(others),), "energy", True, False), "false"
    _say(client, turn, "Помолчи. Я считываю энергетику…")
    if knowledge.clear:
        _line(client, turn, f"🌀 {C.ENERGY_CLEAR[knowledge.tags[0]]}")
    else:
        first, second = (C.EMOTIONS[tag] for tag in knowledge.tags)
        _line(client, turn, f"🌀 Аура мутная: то ли {first}, то ли {second}.")
    return knowledge, quality


def _answer_text(slot: str, tag: str) -> str:
    if slot == "who":
        return C.WHO[tag].phrase
    if slot == "what":
        return C.WHAT[tag].label.lower()
    return C.WHEN[tag].phrase.rstrip(",")


def _fill(template: str, value: str) -> str:
    if template.startswith("«{value}"):
        value = value[:1].upper() + value[1:]
    return template.format(value=value)


def _question(client: Client, slot: str, rng: random.Random, turn: _Turn) -> tuple[Knowledge, str]:
    kind = client.kind
    _say(client, turn, C.QUESTION_TEXTS[slot])
    value = client.story.belief(slot, client.truth[slot])
    _line(client, turn, f"{client.name}: {_fill(kind.answers[slot], _answer_text(slot, value))}")
    knowledge = Knowledge(slot, (value,), "question", True, value == client.truth[slot])
    if kind.blurts:
        known = {k.slot for k in client.knowledge if k.source in CLIENT_SOURCES} | {slot}
        rest = [other for other in C.SLOTS if other not in known]
        if rest:
            extra = rng.choice(rest)
            extra_value = client.story.belief(extra, client.truth[extra])
            _line(client, turn, f"…и заодно выбалтывает: {_fill(kind.answers[extra], _answer_text(extra, extra_value))}")
            client.knowledge.append(
                Knowledge(extra, (extra_value,), "question", True, extra_value == client.truth[extra])
            )
    return knowledge, "said"


def _react(client: Client, knowledge: Knowledge, rng: random.Random, turn: _Turn) -> None:
    kind = client.kind
    if kind.style == "poker":
        mood = "neutral"
    elif kind.style == "overreact":
        mood = "warm"
    else:
        accurate = rng.random() < REACTION_ACCURACY
        mood = "warm" if knowledge.truthful == accurate else "cold"
    _line(client, turn, rng.choice(kind.reactions[mood]).format(name=client.name))


def _info(session: Session, client: Client, method: str, sub: str | None, rng: random.Random,
          turn: _Turn, now: float) -> None:
    kind = client.kind
    client.actions_left -= 1
    client.used_methods.append(method)
    suspicion_before = client.suspicion
    if kind.suspicion:
        client.suspicion += kind.suspicion[method]
    trust_delta = SKEPTIC_HOROSCOPE_TRUST if method == "horoscope" and kind.suspicion else TRUST_DELTA[method]
    client.trust = int(_clamp(client.trust + trust_delta, 0, 100))

    if method == "palm":
        knowledge, quality = _palm(client, sub or "", rng, turn)
    elif method == "tarot":
        knowledge, quality = _tarot(client, rng, turn)
    elif method == "horoscope":
        knowledge, quality = _horoscope(client, rng, turn)
    elif method == "energy":
        knowledge, quality = _energy(client, rng, turn)
    else:
        knowledge, quality = _question(client, sub or "", rng, turn)
    client.knowledge.append(knowledge)
    if method != "question":
        _react(client, knowledge, rng, turn)

    crossed = [text for threshold, text in kind.suspicion_lines if suspicion_before < threshold <= client.suspicion]
    if crossed:
        _line(client, turn, crossed[-1].format(name=client.name))

    turn.metrics.append(("action", {
        "kind": "info",
        "client_no": session.client_no,
        "archetype": kind.key,
        "story": client.story.id,
        "method": method,
        "sub": sub,
        "quality": quality,
        "truthful": knowledge.truthful,
        "slot": knowledge.slot,
        "trust": client.trust,
        "suspicion": client.suspicion,
    }))

    if method in C.MAIN_METHODS:
        session.methods_used.add(method)
        if not session.mastery and set(C.MAIN_METHODS) <= session.methods_used:
            session.mastery = True
            session.score += MASTERY_BONUS
            _line(client, turn, f"{C.MASTERY_LINE} +{MASTERY_BONUS} очка.")
            turn.metrics.append(("mastery_bonus", {
                "client_no": session.client_no, "method": method, "points": MASTERY_BONUS,
            }))

    if kind.suspicion and client.suspicion >= SUSPICION_EXIT:
        _line(client, turn, C.SUSPICION_EXIT_LINE.format(name=client.name))
        _settle(session, client, rng, turn, now, confidence=None, reasons=("suspicion",),
                outcome="exposure", echo=(), slots=_slot_results(client, {}), jackpot=False, trap=False)
    else:
        client.phase = "who" if client.actions_left <= 0 else "research"


# --- предсказание -----------------------------------------------------------

def is_echo(client: Client, slot: str, tag: str) -> bool:
    """Тег взят из слов клиента и не подтверждён своим источником мадам."""
    said = any(k.source in CLIENT_SOURCES and k.slot == slot and tag in k.tags for k in client.knowledge)
    if not said:
        return False
    return not any(k.source in OWN_SOURCES and k.supports(slot, tag) for k in client.knowledge)


def prediction_body(picks: dict[str, str]) -> str:
    who = C.WHO[picks["who"]]
    what = C.WHAT[picks["what"]]
    verb = what.second if who.key == "self" else what.third
    return f"{C.WHEN[picks['when']].phrase} {who.phrase} {verb}"


def prediction_sentence(picks: dict[str, str], confidence: str) -> str:
    return f"{C.CONFIDENCE_PREFIX[confidence]} {prediction_body(picks)}."


def _slot_results(client: Client, matches: dict[str, bool], mercury: bool = False) -> tuple[SlotResult, ...]:
    return tuple(
        SlotResult(slot, client.picks.get(slot), client.truth[slot], matches.get(slot, False),
                   mercury and slot == "when")
        for slot in C.SLOTS
    )


def _announce(session: Session, client: Client, confidence: str, rng: random.Random, turn: _Turn,
              now: float) -> None:
    story, kind = client.story, client.kind
    picks = dict(client.picks)
    _say(client, turn, prediction_sentence(picks, confidence))

    matches = {slot: picks[slot] == client.truth[slot] for slot in C.SLOTS}
    mercury = False
    # Меркурий прощает только сроки: если мимо и «кто», и «что», оправдываться нечем.
    if not matches["when"] and (matches["who"] or matches["what"]) and client.horoscope_used and session.mercury_left:
        matches["when"] = True
        mercury = True
        session.mercury_left = False
        _line(client, turn, C.MERCURY_LINE)
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

    if echo:
        critical = outcome == "exposure" and reasons[0] in ("skeptic_echo", "sworn_echo")
        # Восторг ревнивой и торг бизнесмена уместны, только если что-то совпало.
        if critical or kind.echo == "expose" or outcome in ("hit", "partial"):
            _line(client, turn, kind.echo_lines["critical" if critical else "soft"].format(name=client.name))
        if kind.echo == "expose" and outcome != "exposure":
            client.suspicion += SOFT_ECHO_SUSPICION
    if outcome == "exposure" and reasons[0] == "trap" and story.trap is not None:
        _line(client, turn, f"💥 {story.trap.text}")
    elif outcome == "exposure":
        _line(client, turn, kind.outcomes["exposure"].format(name=client.name))
    elif jackpot and story.jackpot is not None:
        _line(client, turn, f"🎰 {story.jackpot.text}")
    else:
        _line(client, turn, kind.outcomes[outcome].format(name=client.name))
    if outcome == "partial" and confidence == "swear":
        _line(client, turn, C.SWORN_PARTIAL_LINE)

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


def _settle(session: Session, client: Client, rng: random.Random, turn: _Turn, now: float, *,
            confidence: str | None, reasons: tuple[str, ...], outcome: str, echo: tuple[str, ...],
            slots: tuple[SlotResult, ...], jackpot: bool, trap: bool) -> None:
    """Очки, гонорар, серия, легенда, развязка и переход к следующему клиенту."""
    story, kind = client.story, client.kind
    level = C.CONFIDENCE.index(confidence) if confidence else 1
    points = SCORE[outcome][level]
    fee = story.fee * (0.5 + _clamp(client.trust, 0, 100) / 100) * FEE[outcome][level]
    if kind.echo == "discount" and echo and outcome != "exposure":
        fee *= ECHO_DISCOUNT
    fee = _round_money(fee)

    streak_before, bonus = session.streak, 0
    if outcome == "hit":
        session.streak += 1
        bonus = STREAK_BONUS.get(session.streak, 0)
    elif outcome in ("miss", "exposure"):
        session.streak = 0
    session.max_streak = max(session.max_streak, session.streak)
    session.score += points + bonus
    session.money += fee

    _line(client, turn, "📜 " + story.reveal.format(when_lead=C.WHEN[client.truth["when"]].lead))
    if bonus:
        _line(client, turn, f"{C.STREAK_LINES[session.streak]} +{bonus} очка за серию.")
    if session.streak != streak_before:
        turn.metrics.append(("streak_updated", {
            "client_no": session.client_no, "before": streak_before, "after": session.streak, "bonus": bonus,
        }))

    legend_before = session.legend
    if outcome == "exposure":
        session.legend -= 1  # не больше одного запаса за клиента, сколько бы причин ни сработало
        if session.legend <= 0:
            _line(client, turn, C.LEGEND_GONE_LINE)
        else:
            _line(client, turn, C.LEGEND_LOST_LINE.format(left=session.legend))
            if session.legend == 1:
                _line(client, turn, C.LEGEND_LAST_LINE)
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
        reveal=story.reveal.format(when_lead=C.WHEN[client.truth["when"]].lead),
        slots=slots,
        hints=tuple(HintResult(k.source, k.slot, k.tags, k.clear, k.truthful, k.positive) for k in client.knowledge),
        echo_slots=echo,
        outcome=outcome,
        reasons=reasons,
        confidence=confidence,
        jackpot=jackpot,
        trap=trap,
        fee=fee,
        points=points + bonus,
        streak_before=streak_before,
        streak_after=session.streak,
        streak_bonus=bonus,
        legend_before=legend_before,
        legend_after=session.legend,
        info_actions=len(client.used_methods),
        duration_s=round(max(0.0, now - client.started_at), 1),
    )
    client.report = report
    client.phase = "done"
    session.reports.append(report)
    turn.finished_client = _client_view(session, client)
    turn.metrics.append(("client_reveal", report.log_data()))

    session.client_no += 1
    if session.legend <= 0:
        _finish(session, "exposed")
    elif session.client_no >= session.clients_total:
        _finish(session, "completed")
    else:
        session.client = new_client(STORIES_BY_ID[session.plan[session.client_no]], rng, now)
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


def is_idle(session: Session, now: float, timeout: float = IDLE_TIMEOUT_S) -> bool:
    return not session.finished and now - session.last_activity >= timeout


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


def summary(session: Session, now: float) -> dict[str, Any]:
    """Итог партии для журнала: всё, что потом не восстановить."""
    outcomes = [report.outcome for report in session.reports]
    return {
        "money": session.money,
        "score": session.score,
        "legend_left": session.legend,
        "legend_start": session.legend_start,
        "max_streak": session.max_streak,
        "hits": outcomes.count("hit"),
        "partials": outcomes.count("partial"),
        "misses": outcomes.count("miss"),
        "exposures": outcomes.count("exposure"),
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
        "rules": RULES_VERSION,
    }


def start_data(session: Session) -> dict[str, Any]:
    return {
        "stories": list(session.plan),
        "participants": len(session.participants),
        "legend": session.legend_start,
        "rules": RULES_VERSION,
    }


def abandon_summary(session: Session, reason: str, now: float) -> dict[str, Any]:
    idle_s = round(max(0.0, now - session.last_activity), 1)
    abandon(session, reason)
    data = summary(session, now)
    data.update(idle_s=idle_s, client_no=session.client_no, phase=session.client.phase)
    return data


# --- представление ----------------------------------------------------------

def _tag_label(slot: str, tag: str) -> str:
    return {"who": C.WHO, "what": C.WHAT, "when": C.WHEN}[slot][tag].label


def _note(knowledge: Knowledge) -> NoteView:
    icon, source = C.SOURCE_LABELS[knowledge.source]
    clarity = None if knowledge.source in CLIENT_SOURCES else ("ясно" if knowledge.clear else "мутно")
    slot = knowledge.slot
    if slot in C.SLOTS:
        text = f"{C.SLOT_TITLES[slot].lower()}: " + " или ".join(_tag_label(slot, tag) for tag in knowledge.tags)
    elif slot == "sphere" and knowledge.positive:
        text = "дело в: " + " или ".join(C.SPHERE_TITLES[tag] for tag in knowledge.tags)
    elif slot == "sphere":
        text = "не про " + SPHERE_ACCUSATIVE[knowledge.tags[0]]
    else:
        text = "эмоция: " + " или ".join(C.EMOTIONS[tag] for tag in knowledge.tags)
    return NoteView(icon, source, clarity, text)


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
        complaint=client.story.complaint,
        observation=client.observation_text,
        notebook=tuple(_note(k) for k in client.knowledge),
        dialog=tuple(client.lines),
        mood=_band(client.trust, C.MOOD_BANDS),
        suspicion=_band(client.suspicion, C.SUSPICION_BANDS) if kind.suspicion else None,
        actions_left=client.actions_left,
        phase=client.phase,
        picks=dict(client.picks),
        report=client.report,
    )


def _preview(client: Client) -> str | None:
    if client.phase not in (*C.SLOTS, "announce"):
        return None
    if all(slot in client.picks for slot in C.SLOTS):
        body = prediction_body(client.picks).replace(",", "")
        return "«" + body[:1].upper() + body[1:] + "»"
    return " · ".join(
        f"{C.SLOT_TITLES[slot]}: {_tag_label(slot, client.picks[slot]) if slot in client.picks else '…'}"
        for slot in C.SLOTS
    )


def build_view(session: Session) -> View:
    client = None if session.finished else session.client
    if client is None:
        prompt = ""
    elif client.phase == "research":
        prompt = (
            f"Выбери приём (осталось {client.actions_left}) или переходи к предсказанию."
            if client.actions_left > 0 else "Приёмы кончились — пора предсказывать."
        )
    else:
        prompt = PROMPTS.get(client.phase, "")
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
        sciences=tuple((method, method in session.methods_used) for method in C.MAIN_METHODS),
        client=_client_view(session, client) if client is not None else None,
        prompt=prompt,
        preview=_preview(client) if client is not None else None,
        actions=available_actions(session),
        finished=session.finished,
        finish_reason=session.finish_reason,
        title=title(session) if session.finished else None,
        achievements=achievements(session) if session.finished else (),
        outcomes=tuple(report.outcome for report in session.reports),
        max_streak=session.max_streak,
    )


def current_client_view(session: Session) -> ClientView:
    """Текущий клиент даже после конца партии — для экрана брошенной партии."""
    return _client_view(session, session.client)


# --- партии по чатам --------------------------------------------------------

class SessionRegistry:
    """Одна активная партия на чат. Живёт в памяти: рестарт бота бросает партии."""

    def __init__(self) -> None:
        self._by_chat: dict[int, Session] = {}

    def get(self, chat_id: int) -> Session | None:
        return self._by_chat.get(chat_id)

    def add(self, session: Session) -> None:
        if session.chat_id in self._by_chat:
            raise ValueError("в этом чате уже идёт партия")
        self._by_chat[session.chat_id] = session

    def remove(self, session: Session) -> bool:
        """Убрать именно эту партию; ``False``, если в чате уже другая."""
        if self._by_chat.get(session.chat_id) is not session:
            return False
        del self._by_chat[session.chat_id]
        return True

    def idle(self, now: float, timeout: float = IDLE_TIMEOUT_S) -> list[Session]:
        return [session for session in self._by_chat.values() if is_idle(session, now, timeout)]

    def active(self) -> list[Session]:
        return list(self._by_chat.values())

    def __len__(self) -> int:
        return len(self._by_chat)
