"""Правила салона «Гуру»: состояние партии и чистые функции ходов.

Партия — ``CLIENTS_PER_SALON`` клиентов, по одному каждого типа в случайном
порядке. С каждым клиентом игрок делает приёмы, пока не возьмёт деньги, не
продаст ритуал или клиент не уйдёт сам.

Главное решение — конкретность:

- гороскоп и «радуга» (эффект Барнума) безопасны, но поднимают доверие
  только до ``VAGUE_TRUST_CAP`` и денег почти не приносят. Гороскоп на партию
  один: каждый клиент получает тот же прогноз, какой бы знак ни назвал;
- зацепки добываются «дробью» и наводящими вопросами, но скептик это видит;
- конкретное предсказание тратит свежую зацепку: попадание даёт чаевые,
  репутацию и сарафан, промах бьёт по доверию и репутации;
- ритуал — дорогая продажа, шанс зависит от доверия.

Скептик копит подозрение; на ``EXPOSURE_AT`` он разоблачает салон.
Сарафан и репутация поднимают стартовое доверие следующих клиентов.

Модуль не знает про Telegram и время: случайность приходит как ``random.Random``,
время — как число секунд (``time.monotonic()`` в проде).
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any

from games.guru.content import (
    CLIENT_KINDS,
    CLOSED_TITLE,
    EXPOSED_TITLE,
    HOROSCOPE_AGAIN,
    HOROSCOPE_ASK,
    HOROSCOPE_READ,
    HOROSCOPES,
    KINDS_BY_KEY,
    MOB_LINE,
    MONEY_TITLES,
    PLAYER_LINES,
    REACTIONS,
    SIGN_ANSWERS,
    ZODIAC_SIGNS,
    ClientKind,
    Decoy,
    Fact,
)

# Меняется при любом изменении баланса: по нему разделяют метрики в журнале.
RULES_VERSION = 1

CLIENTS_PER_SALON = len(CLIENT_KINDS)
START_REPUTATION = 50
IDLE_TIMEOUT_S = 180

TECHNIQUES = ("horoscope", "rainbow", "shotgun", "leading", "specific")
CLOSERS = ("bill", "ritual")
MOVES = TECHNIQUES + CLOSERS
VAGUE_MOVES = ("horoscope", "rainbow")

VAGUE_TRUST_CAP = 70
EXPOSURE_AT = 100
SUSPICIOUS_AT = 60
SKEPTIC_START_SUSPICION = 20
MAX_BUZZ_BONUS = 10

# Подозрение скептика за приём.
SUSPICION = {"horoscope": 20, "rainbow": 8, "shotgun": 25, "leading": 35, "miss": 45}


@dataclass
class Client:
    kind: ClientKind
    name: str
    intro: str
    facts: tuple[Fact, ...]
    decoys: tuple[Decoy, ...]
    trust: int
    suspicion: int
    patience: int
    sign: str = ""
    revealed: list[int] = field(default_factory=list)
    used: set[int] = field(default_factory=set)
    turn: int = 0
    vague_uses: int = 0
    horoscopes: int = 0
    tips: int = 0
    paid: int = 0
    hits: int = 0
    misses: int = 0
    result: str | None = None
    lines: list[str] = field(default_factory=list)

    @property
    def done(self) -> bool:
        return self.result is not None

    @property
    def fresh_clues(self) -> int:
        """Зацепки, ещё не потраченные на конкретное предсказание."""
        return sum(1 for index in self.revealed if index not in self.used)


@dataclass
class Salon:
    session_id: str
    chat_id: int
    player_id: int
    player_name: str
    plan: tuple[str, ...]
    started_at: float
    last_activity: float
    client: Client
    horoscope: str = ""
    client_no: int = 0
    money: int = 0
    reputation: int = START_REPUTATION
    buzz: int = 0
    exposures: int = 0
    served: int = 0
    hits: int = 0
    misses: int = 0
    rituals_sold: int = 0
    rituals_failed: int = 0
    moves: int = 0
    finished: bool = False
    finish_reason: str | None = None
    # Где живёт партия в мессенджере; нужно только transport-слою.
    message_id: int | None = None
    thread_id: int | None = None

    @property
    def clients_total(self) -> int:
        return len(self.plan)


@dataclass(frozen=True)
class MoveResult:
    move: str
    client_kind: str
    client_no: int
    turn: int
    hit: bool | None
    clue: bool
    trust: int
    suspicion: int
    money_delta: int
    reputation_delta: int
    buzz_delta: int
    client_result: str | None
    finished_client: Client | None
    salon_over: bool

    def log_data(self) -> dict[str, Any]:
        return {
            "move": self.move,
            "client": self.client_kind,
            "client_no": self.client_no,
            "turn": self.turn,
            "hit": self.hit,
            "clue": self.clue,
            "trust": self.trust,
            "suspicion": self.suspicion,
            "money_delta": self.money_delta,
            "rep_delta": self.reputation_delta,
            "buzz_delta": self.buzz_delta,
            "result": self.client_result,
        }


# --- числа ------------------------------------------------------------------

def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _round_money(value: float) -> int:
    return int(round(value / 50.0)) * 50


def buzz_multiplier(salon: Salon) -> float:
    return 1 + 0.15 * min(salon.buzz, MAX_BUZZ_BONUS)


def specific_chance(client: Client) -> float:
    """Шанс попасть конкретным предсказанием: свежие зацепки и доверие."""
    chance = 0.10 + 0.22 * client.fresh_clues + (client.trust - 50) / 200
    return _clamp(chance, 0.05, 0.90)


def ritual_chance(client: Client) -> float:
    return _clamp((client.trust - 50) / 45, 0.05, 0.95)


def session_fee(salon: Salon, client: Client) -> int:
    return _round_money(client.kind.budget * client.trust / 100 * buzz_multiplier(salon))


def ritual_price(salon: Salon, client: Client) -> int:
    return _round_money(client.kind.budget * 2 * buzz_multiplier(salon))


def _starting_trust(kind: ClientKind, reputation: int, buzz: int) -> int:
    trust = kind.base_trust + (reputation - START_REPUTATION) // 5 + 3 * min(buzz, MAX_BUZZ_BONUS)
    return int(_clamp(trust, 5, 90))


# --- создание партии --------------------------------------------------------

def new_client(kind: ClientKind, rng: random.Random, *, reputation: int, buzz: int) -> Client:
    facts = tuple(rng.sample(kind.facts, len(kind.facts)))
    decoys = tuple(rng.sample(kind.decoys, len(kind.decoys)))
    return Client(
        kind=kind,
        name=rng.choice(kind.names),
        intro=rng.choice(kind.intros),
        facts=facts,
        decoys=decoys,
        trust=_starting_trust(kind, reputation, buzz),
        suspicion=SKEPTIC_START_SUSPICION if kind.skeptic else 0,
        patience=kind.patience,
        sign=rng.choice(ZODIAC_SIGNS),
        # Первую зацепку игрок видит сам: холодное чтение начинается с наблюдения.
        revealed=[0],
    )


def new_salon(
    *,
    session_id: str,
    chat_id: int,
    player_id: int,
    player_name: str,
    rng: random.Random,
    now: float,
    clients: int = CLIENTS_PER_SALON,
) -> Salon:
    if not 1 <= clients <= len(CLIENT_KINDS):
        raise ValueError(f"клиентов должно быть от 1 до {len(CLIENT_KINDS)}")
    plan = tuple(rng.sample([kind.key for kind in CLIENT_KINDS], clients))
    first = new_client(KINDS_BY_KEY[plan[0]], rng, reputation=START_REPUTATION, buzz=0)
    return Salon(
        session_id=session_id,
        chat_id=chat_id,
        player_id=player_id,
        player_name=player_name,
        plan=plan,
        started_at=now,
        last_activity=now,
        client=first,
        horoscope=rng.choice(HOROSCOPES),
    )


# --- реплики ----------------------------------------------------------------

def _reaction(rng: random.Random, outcome: str, client: Client, **values: Any) -> str:
    pool = REACTIONS[outcome]
    lines = pool.get(client.kind.key) or pool["*"]
    return rng.choice(lines).format(name=client.name, **values)


def rub(amount: int) -> str:
    """Сумма с разрядами: 12 500 ₽."""
    return f"{amount:,}".replace(",", " ") + " ₽"


def _quote(text: str) -> str:
    # По правилам точка уходит за кавычки; многоточие, «?» и «!» остаются внутри.
    if text.endswith(".") and not text.endswith(".."):
        text = text[:-1]
    return f"«{text}»"


def _say(client: Client, text: str) -> None:
    client.lines.append(f"— Ты: {_quote(text)}")


def _narrate(client: Client, text: str) -> None:
    client.lines.append(text)


def _read_horoscope(salon: Salon, client: Client, rng: random.Random) -> None:
    """Спросить знак (только в первый раз) и зачитать прогноз партии."""
    if client.horoscopes == 0:
        _say(client, HOROSCOPE_ASK)
        answers = SIGN_ANSWERS.get(client.kind.key) or SIGN_ANSWERS["*"]
        _narrate(client, rng.choice(answers).format(name=client.name, sign=client.sign))
        _say(client, HOROSCOPE_READ.format(sign=client.sign, text=salon.horoscope))
    else:
        _say(client, HOROSCOPE_AGAIN.format(sign=client.sign, text=salon.horoscope))
    client.horoscopes += 1


# --- ходы -------------------------------------------------------------------

def _raise_trust(client: Client, amount: int, *, cap: int = 100) -> int:
    before = client.trust
    if amount > 0:
        client.trust = max(before, min(cap, before + amount))
    else:
        client.trust = int(_clamp(before + amount, 0, 100))
    return client.trust - before


def _reveal_clue(client: Client) -> Fact | None:
    for index in range(len(client.facts)):
        if index not in client.revealed:
            client.revealed.append(index)
            return client.facts[index]
    return None


def _change_reputation(salon: Salon, amount: int) -> None:
    salon.reputation = int(_clamp(salon.reputation + amount, 0, 100))


def _change_buzz(salon: Salon, amount: int) -> None:
    salon.buzz = max(0, salon.buzz + amount)


def _vague(salon: Salon, client: Client, move: str, rng: random.Random) -> tuple[int, bool]:
    kind = client.kind
    if kind.hates_vague:
        delta = _raise_trust(client, -8 if move == "horoscope" else -5)
    else:
        base = 12 if move == "horoscope" else 5
        gain = round(base * kind.gullibility * 0.5 ** client.vague_uses)
        delta = _raise_trust(client, gain, cap=VAGUE_TRUST_CAP)
    client.vague_uses += 1

    clue = None
    if move == "rainbow" and rng.random() < min(0.9, 0.5 * kind.gullibility):
        clue = _reveal_clue(client)

    if clue is not None:
        _narrate(client, _reaction(rng, "clue", client))
        _narrate(client, f"🧩 Зацепка: {clue.clue}")
    elif move == "horoscope" and client.horoscopes > 1:
        _narrate(client, _reaction(rng, "horo_again", client))
    else:
        _narrate(client, _reaction(rng, "warm" if delta > 0 else "cold", client))
    return delta, clue is not None


def _fish(salon: Salon, client: Client, move: str, rng: random.Random) -> tuple[int, bool]:
    kind = client.kind
    delta = 0
    if move == "leading":
        clue = _reveal_clue(client)
        # Наводящий вопрос всегда работает, но гадалка, которая спрашивает, — так себе гадалка.
        delta = _raise_trust(client, -8)
    else:
        clue = _reveal_clue(client) if rng.random() < min(0.95, 0.7 * kind.gullibility) else None
        if clue is None:
            delta = _raise_trust(client, -5)

    if clue is not None:
        _narrate(client, _reaction(rng, "clue", client))
        _narrate(client, f"🧩 Зацепка: {clue.clue}")
    else:
        _narrate(client, _reaction(rng, "dry", client))
    return delta, clue is not None


def _pick_hit_fact(client: Client, rng: random.Random) -> int:
    fresh = [index for index in client.revealed if index not in client.used]
    if fresh:
        return fresh[0]
    hidden = [index for index in range(len(client.facts)) if index not in client.used]
    if hidden:
        return hidden[0]
    return rng.randrange(len(client.facts))


def _specific(salon: Salon, client: Client, rng: random.Random) -> tuple[int, bool]:
    kind = client.kind
    hit = rng.random() < specific_chance(client)
    if hit:
        index = _pick_hit_fact(client, rng)
        if index not in client.revealed:
            client.revealed.append(index)
        client.used.add(index)
        _say(client, client.facts[index].hit)
        _narrate(client, _reaction(rng, "hit", client))
        delta = _raise_trust(client, 30)
        tip = _round_money(kind.budget * 0.5 * buzz_multiplier(salon))
        client.tips += tip
        salon.money += tip
        client.hits += 1
        salon.hits += 1
        if kind.skeptic:
            _change_buzz(salon, 2)
            _change_reputation(salon, 12)
            client.suspicion = max(0, client.suspicion - 50)
        else:
            _change_buzz(salon, kind.amplify)
            _change_reputation(salon, 4 * kind.amplify)
        _narrate(client, f"💸 +{rub(tip)} сверху")
    else:
        decoy = client.decoys[client.misses % len(client.decoys)]
        _say(client, decoy.guess)
        _narrate(client, f"{client.name}: {_quote(decoy.retort)}")
        delta = _raise_trust(client, -25)
        client.misses += 1
        salon.misses += 1
        _change_reputation(salon, -6 * kind.amplify)
        if kind.skeptic:
            client.suspicion += SUSPICION["miss"]
    return delta, hit


def _expose(salon: Salon, client: Client, rng: random.Random) -> None:
    _narrate(client, _reaction(rng, "exposed", client))
    salon.exposures += 1
    _change_reputation(salon, -20)
    _change_buzz(salon, -2)
    client.result = "exposed"


def _pay(salon: Salon, client: Client, amount: int, outcome: str, rng: random.Random, **values: Any) -> None:
    client.paid = amount
    salon.money += amount
    if amount > 0:
        _narrate(client, _reaction(rng, outcome, client, amount=rub(amount), **values))
    else:
        _narrate(client, _reaction(rng, "unpaid", client))


def _bill(salon: Salon, client: Client, rng: random.Random, *, result: str) -> None:
    kind = client.kind
    if result == "walked_out":
        _narrate(client, _reaction(rng, "walked_out", client))
    _pay(salon, client, session_fee(salon, client), "paid", rng)
    if client.trust >= 70:
        _change_buzz(salon, kind.amplify)
    elif client.trust < 25:
        _change_reputation(salon, -4 * kind.amplify)
    client.result = result


def _ritual(salon: Salon, client: Client, rng: random.Random) -> bool:
    kind = client.kind
    price = ritual_price(salon, client)
    _say(client, rng.choice(PLAYER_LINES["ritual"]).format(price=rub(price)))
    if rng.random() < ritual_chance(client):
        _pay(salon, client, session_fee(salon, client) + price, "ritual_ok", rng)
        salon.rituals_sold += 1
        client.result = "ritual_sold"
        return True
    salon.rituals_failed += 1
    if kind.skeptic:
        _expose(salon, client, rng)
    else:
        _narrate(client, _reaction(rng, "ritual_fail", client, price=rub(price)))
        _change_reputation(salon, -10 * kind.amplify)
        client.result = "ritual_failed"
    return False


def _effects_line(trust_delta: int, suspicion_delta: int, rep_delta: int, buzz_delta: int) -> str | None:
    parts = []
    if trust_delta:
        parts.append(f"доверие {trust_delta:+d}")
    if suspicion_delta:
        parts.append(f"подозрение {suspicion_delta:+d}")
    if rep_delta:
        parts.append(f"репутация {rep_delta:+d}")
    if buzz_delta:
        parts.append(f"сарафан {buzz_delta:+d}")
    return "↳ " + " · ".join(parts) if parts else None


def apply_move(salon: Salon, move: str, rng: random.Random, *, now: float) -> MoveResult:
    """Сыграть приём или закрыть клиента. Мутирует ``salon``."""
    if salon.finished:
        raise ValueError("партия уже закончена")
    if move not in MOVES:
        raise ValueError(f"неизвестный ход: {move}")

    client = salon.client
    kind = client.kind
    money_before, rep_before, buzz_before = salon.money, salon.reputation, salon.buzz
    suspicion_before = client.suspicion
    client_no = salon.client_no
    turn = client.turn
    hit: bool | None = None
    clue = False
    trust_delta = 0

    if move in TECHNIQUES:
        if move == "horoscope":
            _read_horoscope(salon, client, rng)
        elif move != "specific":
            _say(client, rng.choice(PLAYER_LINES[move]))
        if move in VAGUE_MOVES:
            trust_delta, clue = _vague(salon, client, move, rng)
        elif move == "specific":
            trust_delta, hit = _specific(salon, client, rng)
        else:
            trust_delta, clue = _fish(salon, client, move, rng)
        if kind.skeptic and move in SUSPICION:
            client.suspicion += SUSPICION[move]
        client.patience -= 1
        client.turn += 1

        if kind.skeptic and client.suspicion >= EXPOSURE_AT:
            _expose(salon, client, rng)
        elif client.trust <= 0:
            _narrate(client, _reaction(rng, "left_angry", client))
            _change_reputation(salon, -5 * kind.amplify)
            client.result = "left_angry"
        elif kind.skeptic and client.suspicion >= SUSPICIOUS_AT and client.suspicion > suspicion_before:
            _narrate(client, _reaction(rng, "suspicious", client))
    elif move == "bill":
        _say(client, rng.choice(PLAYER_LINES["bill"]))
        _bill(salon, client, rng, result="paid")
    else:
        _ritual(salon, client, rng)

    effects = _effects_line(
        trust_delta,
        client.suspicion - suspicion_before if kind.skeptic and client.result != "exposed" else 0,
        salon.reputation - rep_before,
        salon.buzz - buzz_before,
    )
    if effects:
        _narrate(client, effects)

    if not client.done and salon.reputation <= 0:
        # Репутация на нуле — салон закрывается прямо посреди приёма.
        _narrate(client, MOB_LINE)
        client.result = "mob"
    # Терпение кончилось, а клиент ещё сидит: платит по доверию и уходит.
    elif not client.done and client.patience <= 0:
        _bill(salon, client, rng, result="walked_out")

    salon.moves += 1
    salon.last_activity = now
    finished_client = None
    if client.done:
        finished_client = client
        _advance(salon, rng)

    return MoveResult(
        move=move,
        client_kind=kind.key,
        client_no=client_no,
        turn=turn,
        hit=hit,
        clue=clue,
        trust=client.trust,
        suspicion=client.suspicion,
        money_delta=salon.money - money_before,
        reputation_delta=salon.reputation - rep_before,
        buzz_delta=salon.buzz - buzz_before,
        client_result=client.result,
        finished_client=finished_client,
        salon_over=salon.finished,
    )


def _advance(salon: Salon, rng: random.Random) -> None:
    salon.served += 1
    if salon.reputation <= 0:
        _finish(salon, "closed")
        return
    if salon.client_no + 1 >= salon.clients_total:
        _finish(salon, "completed")
        return
    salon.client_no += 1
    kind = KINDS_BY_KEY[salon.plan[salon.client_no]]
    salon.client = new_client(kind, rng, reputation=salon.reputation, buzz=salon.buzz)


def _finish(salon: Salon, reason: str) -> None:
    salon.finished = True
    salon.finish_reason = reason


def abandon_salon(salon: Salon, reason: str) -> None:
    """Партия брошена (таймаут, «салон стоп», рестарт бота)."""
    if not salon.finished:
        _finish(salon, reason)


def is_idle(salon: Salon, now: float, timeout: float = IDLE_TIMEOUT_S) -> bool:
    return not salon.finished and now - salon.last_activity >= timeout


# --- итог -------------------------------------------------------------------

def salon_title(salon: Salon) -> str:
    if salon.finish_reason == "closed":
        return CLOSED_TITLE
    if salon.exposures >= 2:
        return EXPOSED_TITLE
    title = MONEY_TITLES[0][1]
    for threshold, name in MONEY_TITLES:
        if salon.money >= threshold:
            title = name
    return title


def salon_summary(salon: Salon, now: float) -> dict[str, Any]:
    """Итог партии для журнала: всё, что потом не восстановить."""
    return {
        "money": salon.money,
        "reputation": salon.reputation,
        "exposures": salon.exposures,
        "clients": salon.served,
        "clients_total": salon.clients_total,
        "hits": salon.hits,
        "misses": salon.misses,
        "rituals_sold": salon.rituals_sold,
        "rituals_failed": salon.rituals_failed,
        "buzz": salon.buzz,
        "moves": salon.moves,
        "duration_s": round(max(0.0, now - salon.started_at), 1),
        "reason": salon.finish_reason,
        "title": salon_title(salon),
        "rules": RULES_VERSION,
    }


# --- партии по чатам --------------------------------------------------------

class SalonRegistry:
    """Одна активная партия на чат. Живёт в памяти: рестарт бота бросает партии."""

    def __init__(self) -> None:
        self._by_chat: dict[int, Salon] = {}

    def get(self, chat_id: int) -> Salon | None:
        return self._by_chat.get(chat_id)

    def add(self, salon: Salon) -> None:
        if salon.chat_id in self._by_chat:
            raise ValueError("в этом чате уже идёт партия")
        self._by_chat[salon.chat_id] = salon

    def remove(self, salon: Salon) -> bool:
        """Убрать именно эту партию; ``False``, если в чате уже другая."""
        if self._by_chat.get(salon.chat_id) is not salon:
            return False
        del self._by_chat[salon.chat_id]
        return True

    def idle(self, now: float, timeout: float = IDLE_TIMEOUT_S) -> list[Salon]:
        return [salon for salon in self._by_chat.values() if is_idle(salon, now, timeout)]

    def active(self) -> list[Salon]:
        return list(self._by_chat.values())

    def __len__(self) -> int:
        return len(self._by_chat)
