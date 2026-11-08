"""Живой приём: один игрок гадает другому.

Скрытой правды здесь нет. Клиент — человек из чата: рассказывает своё словами, мадам
расспрашивает и толкует выпавшее тоже словами, а ядро даёт реквизит (расклады, ритуал,
паузы), принимает ставку уверенности и считает деньги. Было ли это ясновидением, решает
сам клиент. Сообщения людей ядро не читает.

Гонорар мадам платит касса салона, а не клиент: иначе клиенту всегда выгодно кричать
«Шарлатанка!», и оценка перестанет что-либо значить.
"""

from __future__ import annotations

import random
from typing import Any

from games.guru import content as C
from games.guru import rules
from games.guru.contract import Action, DisplayEvent, LiveResult, LiveView
from games.guru.model import LiveSession, Participant

LIVE_FEE = 1500
LIVE_READINGS = 3
WAIT_TIMEOUT_S = 180  # открытый приём не должен надолго занимать чат
PALM_LINES = 3

HOST_ACTIONS = ("m", "go", "a")


def new_session(*, session_id: str, chat_id: int, host_id: int, host_name: str, now: float, wallet: int = 0) -> LiveSession:
    return LiveSession(
        session_id=session_id,
        chat_id=chat_id,
        host=Participant(host_id, host_name),
        started_at=now,
        last_activity=now,
        idle_limit=WAIT_TIMEOUT_S,
        wallet_start=wallet,
        readings_left=LIVE_READINGS,
    )


def available_actions(session: LiveSession) -> tuple[Action, ...]:
    if session.finished:
        return ()
    if session.phase == "waiting":
        return (Action("join", "🙋 Записаться на приём", "control"),)
    if session.phase == "divine":
        actions: list[Action] = []
        if session.readings_left > 0:
            actions += [Action(f"m:{m.key}", m.label, "method") for m in C.METHODS.values()
                        if m.key not in session.used_methods]
        actions.append(Action("go", "🔮 К предсказанию", "control"))
        return tuple(actions)
    if session.phase == "sign":
        signs = [Action(f"z:{key}", f"{C.ZODIAC[sign][0]} {sign}", "sign") for key, sign in C.ZODIAC_IDS.items()]
        return (*signs, Action("stars", C.LIVE_SIGN_SKIP_LABEL, "control"))
    if session.phase == "announce":
        return tuple(Action(f"a:{level}", C.CONFIDENCE_LABELS[level], "confidence") for level in C.CONFIDENCE)
    if session.phase == "verdict":
        return tuple(Action(f"v:{key}", label, "verdict") for key, label in C.LIVE_VERDICTS.items())
    return ()


def refusal(session: LiveSession, actor_id: int, action_id: str) -> str | None:
    """Почему этому человеку нельзя жать эту кнопку; ``None``, если можно."""
    head = action_id.partition(":")[0]
    if head == "join":
        return "Самой себе гадать нельзя: дождись клиента." if actor_id == session.host.user_id else None
    if not session.is_participant(actor_id):
        return f"Это салон мадам {session.host.name}. Смотри и учись."
    if head == "stars" and actor_id != session.host.user_id:
        return "Это запасной выход для мадам. А ты жми свой знак."
    if head == "z" and actor_id == session.host.user_id:
        return "Знак называет клиент. Мадам делает вид, что и так его знает."
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
    session.phase = "divine"
    session.idle_limit = rules.IDLE_TIMEOUT_S
    events.append(_event("line", rng.choice(C.LIVE_SIT_LINES).format(client=actor_name)))
    events.append(_event("tell", C.LIVE_TELL_LINE.format(client=actor_name), rules.PACE_REACTION))
    metrics.append(("action", {"kind": "join", "mode": "live", "wait_s": round(max(0.0, now - session.started_at), 1)}))


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
    events.append(_event("ritual", rng.choice(method.rituals)))
    if method.key == "tarot":
        cards = rules.deal_spread((), rng)
        events.append(_event("omen", rules.spread_text(cards), rules.PACE_RITUAL,
                             data={"cards": [card.art for card in cards]}))
    else:
        if method.key == "palm":
            lines = rng.sample([tag.omen for tag in C.SPHERES.values()], PALM_LINES)
            text = C.LIVE_PALM_LINE.format(omens=", ".join(lines).capitalize())
        else:
            text = f"🌀 {rng.choice(C.AURA_OMENS)}"
        events.append(_event("omen", text, rules.PACE_RITUAL, replace=True))
    metrics.append(("action", {"kind": "reading", "mode": "live", "method": method.key}))


def _horoscope(session: LiveSession, sign: str, by_stars: bool, rng: random.Random, events: list[DisplayEvent],
               metrics: list[tuple[str, dict[str, Any]]]) -> None:
    """Прогноз для знака. Выбор звёзд объявляем вслух: клиент его не называл."""
    session.sign = sign
    session.phase = "divine"
    symbol, trend = C.ZODIAC[sign]
    text = f"{symbol} {sign}. {trend}\nСовет звёзд: {rng.choice(C.STAR_ADVICE)}"
    if by_stars:
        text = f"{C.LIVE_SIGN_LINE.format(client=session.client.name, sign=sign)}\n\n{text}"
    events.append(_event("ritual", rng.choice(C.METHODS["horoscope"].rituals)))
    events.append(_event("omen", text, rules.PACE_RITUAL, replace=True))
    metrics.append(("action", {"kind": "reading", "mode": "live", "method": "horoscope",
                               "sign_by": "stars" if by_stars else "client"}))


def _verdict(session: LiveSession, picked: str, now: float, events: list[DisplayEvent],
             metrics: list[tuple[str, dict[str, Any]]]) -> None:
    level = C.CONFIDENCE.index(session.confidence)
    outcome = "exposure" if picked == "miss" and session.confidence == "swear" else picked
    session.outcome = outcome
    session.fee = rules.flat_fee(LIVE_FEE, outcome, level)
    session.phase = "done"
    client = session.client.name
    events.append(_event("react", C.LIVE_OUTCOME_LINES[picked].format(client=client), rules.PACE_REVEAL))
    if outcome != picked:
        events.append(_event("line", C.LIVE_SWORN_MISS_LINE))
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
    head, _, rest = action_id.partition(":")

    if head == "join":
        _join(session, actor_id, actor_name, rng, now, events, metrics)
    elif head == "m":
        _read(session, rest, rng, events, metrics)
    elif head == "z":
        _horoscope(session, C.ZODIAC_IDS[rest], False, rng, events, metrics)
    elif head == "stars":
        _horoscope(session, rng.choice(C.ZODIAC_SIGNS), True, rng, events, metrics)
    elif head == "go":
        session.phase = "announce"
        events.append(_event("line", C.LIVE_ANNOUNCE_LINE, rules.PACE_REACTION))
    elif head == "a":
        session.confidence = rest
        session.phase = "verdict"
        events.append(_event("announce", C.LIVE_CONFIDENCE_LINE.format(label=C.CONFIDENCE_LABELS[rest]), rules.PACE_REACTION))
        metrics.append(("action", {"kind": "confidence", "mode": "live", "confidence": rest}))
    elif head == "v":
        _verdict(session, rest, now, events, metrics)

    return LiveResult(tuple(events), tuple(metrics), build_view(session))


def _prompt(session: LiveSession) -> str:
    if session.phase == "divine":
        if session.used_methods:
            if session.readings_left > 0:
                return f"Мадам, толкуй вслух, прямо в чат. Что дальше? Осталось раскладов: {session.readings_left}"
            return "Мадам, толкуй вслух, прямо в чат. Раскладов больше нет, пора к предсказанию."
        return f"Мадам, спрашивай словами, сколько нужно. Чем гадать? Раскладов: {session.readings_left}"
    if session.phase == "sign" and session.client is not None:
        return f"{session.client.name}, какой у тебя знак?"
    if session.phase == "announce":
        return "Мадам, скажи предсказание прямо в чат и выбери, как объявить."
    if session.phase == "verdict" and session.client is not None:
        return f"{session.client.name}, твой приговор?"
    return ""


def build_view(session: LiveSession) -> LiveView:
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
