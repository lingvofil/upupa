"""Живой приём: один игрок гадает другому.

Клиент — человек из чата: тайно заполняет анкету кнопками (беда, кто замешан, что на душе),
мадам её не видит и гадает вслепую. Ядро показывает расклады и предлагает толкования, среди
которых ответ клиента бывает не всегда. Мадам выбирает, что озвучить, и собирает предсказание
кнопками, а бот произносит всё это вслух. ИИ не нужен. Было ли это ясновидением, решает сам
клиент; после приговора анкета открывается. Сообщения людей ядро не читает.

Гонорар мадам платит касса салона, а не клиент: иначе клиенту всегда выгодно кричать
«Шарлатанка!», и оценка перестанет что-либо значить.
"""

from __future__ import annotations

import random
from typing import Any

from games.guru import content as C
from games.guru import rules, salon
from games.guru.contract import Action, Certificate, DisplayEvent, LiveResult, LiveView
from games.guru.model import LiveSession, Participant, Reading

LIVE_FEE = 1500
LIVE_READINGS = 3
LIVE_OPTIONS = 3  # сколько толкований предлагает расклад
LIVE_TRUTH_CHANCE = 0.75  # как часто ответ клиента из анкеты попадает в толкования
LIVE_TRUTH_MAX = 0.95  # никакой реквизит не делает мадам всевидящей
WAIT_TIMEOUT_S = 180  # открытый приём не должен надолго занимать чат

HOST_ACTIONS = ("m", "go", "i", "s", "a")
SLOT_TAGS = {"who": C.WHO, "what": C.WHAT, "when": C.WHEN}
READING_TAGS = {"who": C.WHO, "what": C.WHAT, "sphere": C.SPHERES, "emotion": C.EMOTIONS}


def new_session(*, session_id: str, chat_id: int, host_id: int, host_name: str, now: float, wallet: int = 0,
                items: tuple[str, ...] = (), gifts: tuple[str, ...] = ()) -> LiveSession:
    perks = salon.perks_of(items)
    return LiveSession(
        session_id=session_id,
        chat_id=chat_id,
        host=Participant(host_id, host_name),
        started_at=now,
        last_activity=now,
        idle_limit=WAIT_TIMEOUT_S,
        wallet_start=wallet,
        readings_left=min(LIVE_READINGS + perks.readings, len(C.METHODS)),
        items=tuple(items),
        gifts=tuple(gift.key for gift in salon.owned_gifts(gifts)),
        perks=perks,
    )


def _intake_slot(session: LiveSession) -> C.IntakeSlot | None:
    """Следующая незаполненная строка анкеты."""
    return next((slot for slot in C.LIVE_INTAKE if slot.key not in session.intake), None)


def available_actions(session: LiveSession) -> tuple[Action, ...]:
    if session.finished:
        return ()
    phase = session.phase
    if phase == "waiting":
        return (Action("join", "🙋 Записаться на приём", "control"),)
    if phase == "intake":
        options = [Action(f"q:{key}", value.capitalize(), "intake") for key, value in _intake_slot(session).options.items()]
        return (*options, Action("skip", C.LIVE_INTAKE_SKIP_LABEL, "control"))
    if phase == "divine":
        actions: list[Action] = []
        if session.readings_left > 0:
            actions += [Action(f"m:{m.key}", m.label, "method") for m in C.METHODS.values()
                        if m.key not in session.used_methods]
        actions.append(Action("go", "🔮 К предсказанию", "control"))
        return tuple(actions)
    if phase == "sign":
        signs = [Action(f"z:{key}", f"{C.ZODIAC[sign][0]} {sign}", "sign") for key, sign in C.ZODIAC_IDS.items()]
        return (*signs, Action("stars", C.LIVE_SIGN_SKIP_LABEL, "control"))
    if phase == "hand":
        hands = [Action(f"h:{key}", label, "hand") for key, (label, _) in C.LIVE_HANDS.items()]
        return (*hands, Action("anyhand", C.LIVE_HAND_SKIP_LABEL, "control"))
    if phase == "interpret":
        slot = session.pending.slot
        return tuple(Action(f"i:{tag}", rules.interpretation(slot, tag).button, "interpretation")
                     for tag in session.pending.offered)
    if phase in C.SLOTS:
        return tuple(Action(f"s:{phase}:{key}", tag.label, "option") for key, tag in SLOT_TAGS[phase].items())
    if phase == "announce":
        return tuple(Action(f"a:{level}", C.CONFIDENCE_LABELS[level], "confidence") for level in C.CONFIDENCE)
    if phase == "verdict":
        return tuple(Action(f"v:{key}", label, "verdict") for key, label in C.LIVE_VERDICTS.items())
    return ()


def refusal(session: LiveSession, actor_id: int, action_id: str) -> str | None:
    """Почему этому человеку нельзя жать эту кнопку; ``None``, если можно."""
    head = action_id.partition(":")[0]
    if head == "join":
        return "Самой себе гадать нельзя: дождись клиента." if actor_id == session.host.user_id else None
    if not session.is_participant(actor_id):
        return C.SPECTATOR_LINE.format(name=session.host.name)
    if head in ("stars", "skip", "anyhand") and actor_id != session.host.user_id:
        return "Это запасной выход для мадам. А ты жми свой вариант."
    if head == "h" and actor_id == session.host.user_id:
        return "Руку показывает клиент. Мадам пока протирает лупу."
    if head == "z" and actor_id == session.host.user_id:
        return "Знак называет клиент. Мадам делает вид, что и так его знает."
    if head == "q" and actor_id == session.host.user_id:
        return "Анкету заполняет клиент. Мадам подглядывать нельзя."
    if head in HOST_ACTIONS and actor_id != session.host.user_id:
        return "Реквизит в руках у мадам. Твоё слово будет в конце."
    if head == "v" and actor_id == session.host.user_id:
        return "Приговор выносит клиент. Мадам остаётся надеяться."
    return None


def _event(kind: str, text: str, pace: tuple[float, float] = rules.NO_PAUSE, *, replace: bool = False,
           data: dict[str, Any] | None = None) -> DisplayEvent:
    return DisplayEvent(kind, text, pause=pace[0], spread=pace[1], replace=replace, data=data or {})


def _join(session: LiveSession, actor_id: int, actor_name: str, rng: random.Random, now: float,
          events: list[DisplayEvent], metrics: list[tuple[str, dict[str, Any]]]) -> None:
    session.client = Participant(actor_id, actor_name)
    session.joined_at = now
    session.phase = "intake"
    session.idle_limit = rules.IDLE_TIMEOUT_S
    events.append(_event("line", rng.choice(C.LIVE_SIT_LINES).format(client=actor_name),
                         data={"scene": (salon.GUEST, session.items, session.gifts)}))
    events.append(_event("line", C.LIVE_INTAKE_OPEN_LINE, rules.PACE_REACTION))
    metrics.append(("action", {"kind": "join", "mode": "live", "wait_s": round(max(0.0, now - session.started_at), 1)}))


def _open_seance(session: LiveSession, events: list[DisplayEvent]) -> None:
    session.phase = "divine"
    events.append(_event("hint", C.LIVE_TELL_LINE.format(client=session.client.name), rules.PACE_REACTION))


def _answer_intake(session: LiveSession, key: str, events: list[DisplayEvent],
                   metrics: list[tuple[str, dict[str, Any]]]) -> str:
    """Записать ответ. Возвращает подтверждение, которое увидит только клиент."""
    slot = _intake_slot(session)
    session.intake[slot.key] = key
    metrics.append(("action", {"kind": "intake", "mode": "live", "slot": slot.key, "value": key}))
    if _intake_slot(session) is None:
        _open_seance(session, events)
    return C.LIVE_INTAKE_NOTICE.format(value=slot.options[key])


def _skip_intake(session: LiveSession, events: list[DisplayEvent], metrics: list[tuple[str, dict[str, Any]]]) -> None:
    events.append(_event("line", C.LIVE_INTAKE_SKIP_LINE))
    metrics.append(("action", {"kind": "intake_skip", "mode": "live", "answered": len(session.intake)}))
    _open_seance(session, events)


def _truth(session: LiveSession, slot: str, rng: random.Random) -> str | None:
    """Ответ клиента на то, о чём говорит расклад. «Что случится» в анкете нет: берём событие из его беды."""
    if slot != "what":
        return session.intake.get(slot)
    sphere = session.intake.get("sphere")
    if sphere is None:
        return None
    same_sphere = [tag for tag in C.WHAT.values() if tag.sphere == sphere]
    same_mood = [tag for tag in same_sphere if tag.emotion == session.intake.get("emotion")]
    return rng.choice(same_mood or same_sphere).key


def _deal(session: LiveSession, method: C.Method, rng: random.Random) -> Reading:
    tags = list(READING_TAGS[method.slot])
    truth = _truth(session, method.slot, rng)
    if truth is None:
        return Reading(method.key, method.slot, tuple(rng.sample(tags, LIVE_OPTIONS)), "blind")
    others = [tag for tag in tags if tag != truth]
    if rng.random() >= min(LIVE_TRUTH_CHANCE + session.perks.quality.get(method.key, 0.0), LIVE_TRUTH_MAX):
        return Reading(method.key, method.slot, tuple(rng.sample(others, LIVE_OPTIONS)), "off")
    offered = [truth, *rng.sample(others, LIVE_OPTIONS - 1)]
    rng.shuffle(offered)
    return Reading(method.key, method.slot, tuple(offered), "plain")


def _log_reading(reading: Reading, metrics: list[tuple[str, dict[str, Any]]], **extra: Any) -> None:
    metrics.append(("action", {
        "kind": "reading", "mode": "live", "method": reading.method, "slot": reading.slot,
        "quality": reading.quality, "offered": list(reading.offered), **extra,
    }))


def _read(session: LiveSession, method_key: str, rng: random.Random, events: list[DisplayEvent],
          metrics: list[tuple[str, dict[str, Any]]]) -> None:
    method = C.METHODS[method_key]
    session.readings_left -= 1
    session.used_methods.append(method.key)
    if method.key == "horoscope":
        # Гороскоп продолжится, когда клиент назовёт знак (или мадам устанет ждать).
        session.phase = "sign"
        events.append(_event("line", C.LIVE_SIGN_ASK_LINE))
        return
    if method.key == "palm":
        # Ладонь продолжится, когда клиент выберет руку, похожую на свою (или мадам устанет ждать).
        session.phase = "hand"
        events.append(_event("line", C.LIVE_HAND_ASK_LINE))
        return
    reading = session.pending = _deal(session, method, rng)
    session.phase = "interpret"
    events.append(_event("ritual", rng.choice(method.rituals)))
    if method.key == "tarot":
        cards = rules.deal_spread(reading.offered, rng)
        events.append(_event("omen", rules.spread_text(cards), rules.PACE_RITUAL,
                             data={"cards": [card.art for card in cards]}))
    else:
        events.append(_event("omen", rules.aura_text(reading.offered, rng), rules.PACE_RITUAL,
                             data={"drawing": ("aura", reading.offered)}))
    _log_reading(reading, metrics)


def _palm(session: LiveSession, hand: str | None, rng: random.Random, events: list[DisplayEvent],
          metrics: list[tuple[str, dict[str, Any]]]) -> None:
    """Ладонь того типа, который клиент выбрал как свой. ``None`` — клиент промолчал: рука обычная."""
    method = C.METHODS["palm"]
    session.hand = hand or ""
    reading = session.pending = _deal(session, method, rng)
    session.phase = "interpret"
    omens = ", ".join(C.SPHERES[tag].omen for tag in reading.offered)
    text = C.LIVE_PALM_LINE.format(omens=omens.capitalize())
    drawing: tuple = ("palm", reading.offered)
    if hand:
        text = f"{C.LIVE_HANDS[hand][1]}\n{text}"
        drawing = ("palm", reading.offered, hand)
    events.append(_event("ritual", rng.choice(method.rituals)))
    events.append(_event("omen", text, rules.PACE_RITUAL, data={"drawing": drawing}))
    _log_reading(reading, metrics, hand=hand, hand_by="client" if hand else "host")


def _horoscope(session: LiveSession, sign: str, by_stars: bool, rng: random.Random, events: list[DisplayEvent],
               metrics: list[tuple[str, dict[str, Any]]]) -> None:
    """Прогноз для знака. Выбор звёзд объявляем вслух: клиент его не называл."""
    method = C.METHODS["horoscope"]
    session.sign = sign
    reading = session.pending = _deal(session, method, rng)
    session.phase = "interpret"
    symbol, trend = C.ZODIAC[sign]
    forecasts = "\n".join(f"· {C.WHAT[tag].forecast}" for tag in reading.offered)
    text = f"{symbol} {sign}. {trend}\n{forecasts}\nСовет звёзд: {rng.choice(C.STAR_ADVICE)}"
    if by_stars:
        text = f"{C.LIVE_SIGN_LINE.format(client=session.client.name, sign=sign)}\n\n{text}"
    events.append(_event("ritual", rng.choice(method.rituals)))
    events.append(_event("omen", text, rules.PACE_RITUAL, replace=True))
    _log_reading(reading, metrics, sign_by="stars" if by_stars else "client")


def _interpret(session: LiveSession, tag: str, events: list[DisplayEvent], metrics: list[tuple[str, dict[str, Any]]]) -> None:
    reading = session.pending
    session.pending = None
    session.phase = "divine"
    voice = rules.quote(rules.interpretation(reading.slot, tag).voice)
    events.append(_event("voice", C.LIVE_VOICE_LINE.format(text=voice), rules.PACE_REACTION))
    metrics.append(("action", {"kind": "interpretation", "mode": "live", "method": reading.method,
                               "slot": reading.slot, "voiced": tag}))


def _announce(session: LiveSession, level: str, events: list[DisplayEvent], metrics: list[tuple[str, dict[str, Any]]]) -> None:
    session.confidence = level
    session.phase = "verdict"
    sentence = rules.quote(rules.prediction_sentence(session.picks, level))
    events.append(_event("announce", C.LIVE_CONFIDENCE_LINE.format(sentence=sentence), rules.PACE_REACTION))
    metrics.append(("action", {"kind": "confidence", "mode": "live", "confidence": level}))


def _reveal(session: LiveSession) -> str | None:
    """Анкета открывается только после приговора. Пропущенная анкета открывать нечего."""
    rows = [f"{slot.title}: {slot.options[session.intake[slot.key]]}" for slot in C.LIVE_INTAKE if slot.key in session.intake]
    return "\n".join((C.LIVE_REVEAL_LINE, *rows)) if rows else None


def _verdict(session: LiveSession, picked: str, now: float, events: list[DisplayEvent],
             metrics: list[tuple[str, dict[str, Any]]]) -> None:
    level = C.CONFIDENCE.index(session.confidence)
    outcome = "exposure" if picked == "miss" and session.confidence == "swear" else picked
    session.outcome = outcome
    session.fee = rules.flat_fee(LIVE_FEE, outcome, level, session.perks.fee)
    session.phase = "done"
    client = session.client.name
    events.append(_event("react", C.LIVE_OUTCOME_LINES[picked].format(client=client), rules.PACE_REVEAL))
    if outcome != picked:
        events.append(_event("line", C.LIVE_SWORN_MISS_LINE))
    reveal = _reveal(session)
    if reveal is not None:
        events.append(_event("reveal", reveal, rules.PACE_REVEAL))
    session.finished = True
    session.finish_reason = "completed"
    metrics.append(("finish", {
        "mode": "live",
        "verdict": picked,
        "outcome": outcome,
        "confidence": session.confidence,
        "fee": session.fee,
        "wallet": session.wallet_start + session.fee,
        "methods_used": list(session.used_methods),
        "intake": dict(session.intake),
        "picks": dict(session.picks),
        "client": session.client.user_id,
        "duration_s": round(max(0.0, now - session.started_at), 1),
        "reason": "completed",
        "rules": rules.RULES_VERSION,
    }))


def act(session: LiveSession, actor_id: int, actor_name: str, action_id: str, rng: random.Random, *,
        now: float) -> LiveResult:
    """Применить действие. Недопустимое — ``ValueError``, чужая кнопка — ``PermissionError``."""
    if session.finished:
        raise ValueError("приём уже закончен")
    if action_id not in {action.id for action in available_actions(session)}:
        raise ValueError(f"действие недоступно: {action_id}")
    if refusal(session, actor_id, action_id) is not None:
        raise PermissionError("эта кнопка не для этого человека")

    session.step += 1
    session.last_activity = now
    events: list[DisplayEvent] = []
    metrics: list[tuple[str, dict[str, Any]]] = []
    notice: str | None = None
    head, _, rest = action_id.partition(":")

    if head == "join":
        _join(session, actor_id, actor_name, rng, now, events, metrics)
    elif head == "q":
        notice = _answer_intake(session, rest, events, metrics)
    elif head == "skip":
        _skip_intake(session, events, metrics)
    elif head == "m":
        _read(session, rest, rng, events, metrics)
    elif head == "z":
        _horoscope(session, C.ZODIAC_IDS[rest], False, rng, events, metrics)
    elif head == "h":
        _palm(session, rest, rng, events, metrics)
    elif head == "anyhand":
        _palm(session, None, rng, events, metrics)
    elif head == "stars":
        _horoscope(session, rng.choice(C.ZODIAC_SIGNS), True, rng, events, metrics)
    elif head == "i":
        _interpret(session, rest, events, metrics)
    elif head == "go":
        session.phase = "who"
        events.append(_event("line", C.LIVE_ANNOUNCE_LINE, rules.PACE_REACTION))
    elif head == "s":
        slot, _, tag = rest.partition(":")
        session.picks[slot] = tag
        following = C.SLOTS.index(slot) + 1
        session.phase = C.SLOTS[following] if following < len(C.SLOTS) else "announce"
    elif head == "a":
        _announce(session, rest, events, metrics)
    elif head == "v":
        _verdict(session, rest, now, events, metrics)

    return LiveResult(tuple(events), tuple(metrics), build_view(session), notice)


def _prompt(session: LiveSession) -> str:
    phase = session.phase
    if phase == "intake":
        return f"{session.client.name}, {_intake_slot(session).question}"
    if phase == "divine":
        if not session.used_methods:
            return f"Мадам, чем гадать? Раскладов: {session.readings_left}"
        if session.readings_left > 0:
            return f"Мадам, что дальше? Осталось раскладов: {session.readings_left}"
        return "Мадам, раскладов больше нет, пора к предсказанию."
    if phase == "sign" and session.client is not None:
        return f"{session.client.name}, какой у тебя знак?"
    if phase == "hand" and session.client is not None:
        return f"{session.client.name}, какая у тебя рука? Выбери самую похожую."
    if phase == "verdict" and session.client is not None:
        return f"{session.client.name}, твой приговор?"
    if phase in rules.PROMPTS:
        return f"Мадам, {rules.PROMPTS[phase].lower()}"
    return ""


def build_view(session: LiveSession) -> LiveView:
    """Ответы анкеты сюда не попадают: их не должен увидеть ни один интерфейс до приговора."""
    return LiveView(
        session_id=session.session_id,
        step=session.step,
        host=session.host.name,
        client=session.client.name if session.client else None,
        phase=session.phase,
        prompt=_prompt(session),
        actions=available_actions(session),
        finished=session.finished,
        finish_reason=session.finish_reason,
        confidence=session.confidence,
        outcome=session.outcome,
        fee=session.fee,
        wallet=session.wallet_start + session.fee,
        intake_filled=len(session.intake),
        preview=rules.preview_text(session.picks) if session.phase in (*C.SLOTS, "announce") else None,
    )


def certificate(session: LiveSession) -> Certificate:
    """Справка о законченном приёме. До приговора её не бывает."""
    if session.client is None or session.outcome is None or session.confidence is None:
        raise ValueError("справку выдают после приговора")
    return Certificate(
        number=rules.paper_number(session.session_id),
        host=session.host.name,
        client=session.client.name,
        prediction=rules.prediction_plain(session.picks),
        confidence=session.confidence,
        outcome=session.outcome,
        fee=session.fee,
    )


def start_data() -> dict[str, Any]:
    return {"mode": "live", "rules": rules.RULES_VERSION}


def abandon_summary(session: LiveSession, reason: str, now: float) -> dict[str, Any]:
    idle_s = round(max(0.0, now - session.last_activity), 1)
    if not session.finished:
        session.finished = True
        session.finish_reason = reason
    return {
        "mode": "live",
        "reason": reason,
        "phase": session.phase,
        "idle_s": idle_s,
        "client_joined": session.client is not None,
        "methods_used": list(session.used_methods),
        "duration_s": round(max(0.0, now - session.started_at), 1),
        "rules": rules.RULES_VERSION,
    }
