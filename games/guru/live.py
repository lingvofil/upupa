"""Живой сеанс: мадам толкует символы, клиент отвечает и выносит приговор.

Тайная анкета открывается только в финале и не управляет раскладами. Реплики людей
остаются в чате; ядро не читает их и не сочиняет реакций за клиента. Без Telegram и ИИ.
"""
from __future__ import annotations

import random
from typing import Any

from games.guru import content as C
from games.guru import live_readings as R
from games.guru import rules, salon
from games.guru.contract import Action, Certificate, DisplayEvent, LiveResult, LiveView
from games.guru.model import LiveSession, Participant

LIVE_FEE = 1500
LIVE_READINGS = 3
WAIT_TIMEOUT_S = 180
MECHANIC = "seance_v1"
METHODS = ("tarot", "palm", "horoscope")
FALLBACKS = ("stars", "skip", "anyhand", "noanswer", "nodate", "noreaction")
CLIENT_ACTIONS = ("q", "h", "z", "d", "said", "feedback", "v")


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
        readings_left=LIVE_READINGS + perks.readings,
        items=tuple(items),
        gifts=tuple(gift.key for gift in salon.owned_gifts(gifts)),
        perks=perks,
    )


def _intake_slot(session: LiveSession) -> C.IntakeSlot | None:
    """Следующая незаполненная строка анкеты."""
    return next((slot for slot in C.LIVE_INTAKE if slot.key not in session.intake), None)


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
        "voiced": list(session.voiced), "drafts": list(session.drafts), "reactions": list(session.reactions),
        "questions": list(session.asked), "horizon": session.horizon,
        "client": session.client.user_id,
        "duration_s": round(max(0.0, now - session.started_at), 1),
        "reason": "completed",
        "rules": rules.RULES_VERSION, "mechanic": MECHANIC,
    }))


def certificate(session: LiveSession) -> Certificate:
    """Справка о законченном приёме. До приговора её не бывает."""
    if session.client is None or session.outcome is None or session.confidence is None:
        raise ValueError("справку выдают после приговора")
    return Certificate(
        number=rules.paper_number(session.session_id),
        host=session.host.name,
        client=session.client.name,
        prediction=R.FAREWELLS[session.picks["farewell"]][1],
        confidence=session.confidence,
        outcome=session.outcome,
        fee=session.fee,
    )


def start_data() -> dict[str, Any]:
    return {"mode": "live", "rules": rules.RULES_VERSION, "mechanic": MECHANIC}


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
        "rules": rules.RULES_VERSION, "mechanic": MECHANIC,
    }


def available_actions(session: LiveSession) -> tuple[Action, ...]:
    if session.finished:
        return ()
    phase = session.phase
    if phase == "waiting":
        return (Action("join", "🙋 Записаться на приём", "control"),)
    if phase == "intake":
        return (*(Action(f"q:{key}", value.capitalize(), "intake") for key, value in _intake_slot(session).options.items()),
                Action("skip", C.LIVE_INTAKE_SKIP_LABEL, "control"))
    if phase == "divine":
        questions_left = min(2 + session.perks.questions, len(R.QUESTIONS)) - len(session.asked)
        actions = [Action("ask", f"❓ Уточнить ({questions_left})", "hub")] if questions_left > 0 else []
        repeats = len(session.used_methods) - len(set(session.used_methods))
        if session.readings_left > 0:
            actions += [Action(f"m:{key}", C.METHODS[key].label + (" ещё раз" if key in session.used_methods else ""), "method")
                        for key in METHODS if key not in session.used_methods or
                        (repeats < session.perks.readings and session.used_methods.count(key) < 2)]
        return (*actions, Action("go", "🚪 К напутствию", "control"))
    if phase == "ask":
        return (*(Action(f"question:{key}", label, "question") for key, label in R.QUESTIONS.items() if key not in session.asked),
                Action("back", "↩ Назад", "control"))
    if phase == "answer":
        return (Action("said", "💬 Ответил в чат", "choice"), Action("noanswer", "Продолжить без ответа", "control"))
    if phase == "sign":
        return (*(Action(f"z:{key}", f"{C.ZODIAC[sign][0]} {sign}", "sign") for key, sign in C.ZODIAC_IDS.items()),
                Action("stars", C.LIVE_SIGN_SKIP_LABEL, "control"))
    if phase == "horizon":
        return (*(Action(f"d:{key}", label, "choice") for key, label in R.HORIZONS.items()),
                Action("nodate", "🌠 Продолжить без срока", "control"))
    if phase == "hand":
        return (*(Action(f"h:{key}", label, "hand") for key, (label, _) in C.LIVE_HANDS.items()),
                Action("anyhand", C.LIVE_HAND_SKIP_LABEL, "control"))
    if phase == "tarot":
        return tuple(Action(f"pair:{key}", label, "choice") for key, (label, _) in R.PAIRS.items())
    if phase == "palm":
        return tuple(Action(f"area:{key}", title, "area") for key, (title, _) in R.AREAS.items() if key not in session.areas)
    if phase == "pick":
        actions = [Action(f"r:{code}", R.READINGS[code].label, "choice") for code in session.options]
        if session.method == "tarot":
            actions.append(Action("back", "↩ Другая пара карт", "control"))
        return tuple(actions)
    if phase == "mode":
        return (*(Action(f"say:{key}", label, "choice") for key, label in R.MODES.items()), Action("back", "↩ Назад", "control"))
    if phase == "reaction":
        return (*(Action(f"feedback:{key}", label, "choice") for key, label in R.REACTIONS.items()),
                Action("noreaction", "🌠 Продолжить без реакции", "control"))
    if phase == "farewell":
        return tuple(Action(f"f:{key}", label, "choice") for key, (label, _) in R.FAREWELLS.items())
    if phase == "announce":
        return tuple(Action(f"a:{level}", C.CONFIDENCE_LABELS[level], "confidence") for level in C.CONFIDENCE)
    if phase == "verdict":
        return tuple(Action(f"v:{key}", label, "verdict") for key, label in C.LIVE_VERDICTS.items())
    return ()


def refusal(session: LiveSession, actor_id: int, action_id: str) -> str | None:
    head = action_id.partition(":")[0]
    if head == "join":
        return "Самой себе гадать нельзя: дождись клиента." if actor_id == session.host.user_id else None
    if not session.is_participant(actor_id):
        return C.SPECTATOR_LINE.format(name=session.host.name)
    host = actor_id == session.host.user_id
    if head in FALLBACKS and not host:
        return "Это запасной выход для мадам. А ты жми свой вариант."
    if head in CLIENT_ACTIONS and host:
        return {"q": "Анкету заполняет клиент. Мадам подглядывать нельзя.",
                "h": "Руку показывает клиент. Мадам пока протирает лупу.",
                "z": "Знак называет клиент. Мадам делает вид, что и так его знает.",
                "v": "Приговор выносит клиент. Мадам остаётся надеяться."}.get(head, "Здесь отвечает клиент, дождись его слова.")
    if head not in CLIENT_ACTIONS and not host:
        return "Реквизит в руках у мадам. Твоё слово будет после трактовки."
    return None


def _metric(kind: str, **data: Any) -> tuple[str, dict[str, Any]]:
    return "action", {"kind": kind, "mode": "live", **data}


def _study(session: LiveSession, events: list[DisplayEvent]) -> None:
    session.phase = "palm"
    text = C.LIVE_HANDS[session.hand][1] if session.hand else "Рука как рука."
    events.append(_event("omen", f"🖐 {text} Осмотри две области из трёх.\n"
                         "Приметы нарисованы для игры; клиент выбирает только похожую форму руки.",
                         data={"drawing": ("study", (), session.hand) if session.hand else ("study", ())}))


def _conflict(session: LiveSession, code: str) -> tuple[str, str] | None:
    for first, second in R.CONFLICTS:
        for mine, theirs in ((first, second), (second, first)):
            if code in mine:
                previous = next((item for item in reversed(session.voiced) if item[0] in theirs), None)
                if previous:
                    return previous
    return None


def _voice(session: LiveSession, mode: str, events: list[DisplayEvent], metrics: list) -> None:
    code, session.pending = session.pending, None
    if mode == "draft":
        session.drafts.append(code)
        session.phase = "divine"
        events.append(_event("line", "Мадам делает пометку в тетради и вслух ничего не говорит."))
        metrics.append(_metric("draft", code=code, method=session.method))
        return
    previous = _conflict(session, code)
    lead = "Я бы рассмотрела такую версию" if mode == "idea" else "Я утверждаю"
    events.append(_event("voice", f"— Мадам: «{lead}: {R.READINGS[code].voice}»."))
    if previous:
        events.append(_event("line", f"Раньше прозвучало: «{R.READINGS[previous[0]].voice}». "
                             "Теперь звучит другая трактовка. Клиент решит, как к этому отнестись."))
    session.voiced.append((code, mode))
    session.phase = "reaction"
    metrics.append(_metric("voice", code=code, method=session.method, **{"as": mode}, conflict=previous is not None,
                           order=len(session.voiced)))


def _announce(session: LiveSession, level: str, events: list[DisplayEvent], metrics: list) -> None:
    # Уверенное заявление нельзя отменить осторожным финалом. Черновики не считаются.
    floor = 1 if any(mode == "claim" for _, mode in session.voiced) else 0
    session.confidence = C.CONFIDENCE[max(C.CONFIDENCE.index(level), floor)]
    session.phase = "verdict"
    farewell = R.FAREWELLS[session.picks['farewell']][1]
    sentence = f"{C.CONFIDENCE_PREFIX[session.confidence]} {farewell[:1].lower()}{farewell[1:]}"
    events.append(_event("announce", f"🔮 Мадам объявляет: {rules.quote(sentence)}"))
    if session.confidence != level:
        events.append(_event("line", "В приёме уже прозвучало утверждение. Его ответственность сохраняется: "
                             "клиент оценивает весь сеанс, уверенность не ниже «Я отчётливо вижу»."))
    metrics.append(_metric("confidence", confidence=session.confidence, requested=level))


def act(session: LiveSession, actor_id: int, actor_name: str, action_id: str, rng: random.Random, *, now: float) -> LiveResult:
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
    notice = None
    head, _, rest = action_id.partition(":")
    if head == "join":
        _join(session, actor_id, actor_name, rng, now, events, metrics)
    elif head == "q":
        notice = _answer_intake(session, rest, events, metrics)
    elif head == "skip":
        _skip_intake(session, events, metrics)
    elif head == "ask":
        session.phase = "ask"
    elif head == "question":
        session.asked.append(rest)
        session.question, session.phase = rest, "answer"
        events.append(_event("ask", f"— Мадам: {R.QUESTIONS[rest]}"))
        metrics.append(_metric("question", question=rest,
                               left=min(2 + session.perks.questions, len(R.QUESTIONS)) - len(session.asked)))
    elif head in ("said", "noanswer"):
        session.phase = "divine"
        events.append(_event("line", "Клиент отметил, что ответил в чате." if head == "said" else "Мадам продолжает без ответа."))
        metrics.append(_metric("answer", question=session.question, answered=head == "said"))
    elif head == "m":
        if rest in session.used_methods:
            events.append(_event("line", "Мадам берётся за реквизит ещё раз. Новая трактовка не стирает уже произнесённых слов."))
        session.method = rest
        session.pending, session.options = None, ()
        session.areas.clear()
        session.used_methods.append(rest)
        session.readings_left -= 1
        metrics.append(_metric("reading", method=rest, quality="symbols"))
        if rest == "tarot":
            session.phase = "tarot"
            events += [_event("ritual", "Мадам раскладывает колоду: внутри, между, способ донести."),
                       _event("omen", "", rules.PACE_RITUAL, data={"cards": list(R.CARDS)})]
        elif rest == "palm":
            session.phase = "hand"
            events.append(_event("line", C.LIVE_HAND_ASK_LINE))
        else:
            session.phase = "sign"
            events.append(_event("line", C.LIVE_SIGN_ASK_LINE))
    elif head in ("h", "anyhand"):
        session.hand = rest if head == "h" else ""
        _study(session, events)
        metrics.append(_metric("hand", hand=session.hand, hand_by="client" if head == "h" else "host"))
    elif head in ("z", "stars"):
        session.sign = C.ZODIAC_IDS[rest] if head == "z" else rng.choice(C.ZODIAC_SIGNS)
        session.phase = "horizon"
        symbol, trend = C.ZODIAC[session.sign]
        events.append(_event("omen", f"{symbol} {session.sign}. {trend}"))
        if head == "stars":
            events.append(_event("line", C.LIVE_SIGN_LINE.format(client=session.client.name, sign=session.sign)))
        metrics.append(_metric("sign", method="horoscope", sign_by="client" if head == "z" else "stars"))
    elif head in ("d", "nodate"):
        session.horizon = R.HORIZONS[rest] if head == "d" else "Когда клиент сам решит вернуться к вопросу"
        session.options, session.phase = ("g1", "g2", "g3"), "pick"
        events.append(_event("line", f"Срок: {session.horizon}. Это известный ориентир, а не предсказанная дата."))
        metrics.append(_metric("horizon", value=rest if head == "d" else "none"))
    elif head == "pair":
        session.options, session.phase = R.PAIRS[rest][1], "pick"
    elif head == "area":
        session.areas.append(rest)
        if len(session.areas) == 2:
            session.options, session.phase = R.PALM_PAIRS[frozenset(session.areas)], "pick"
        events.append(_event("omen", f"🔎 {R.AREAS[rest][1]}",
                             data={"drawing": ("study", tuple(session.areas), session.hand) if session.hand else ("study", tuple(session.areas))}))
    elif head == "r":
        session.pending, session.phase = rest, "mode"
    elif head == "say":
        _voice(session, rest, events, metrics)
    elif head in ("feedback", "noreaction"):
        code = session.voiced[-1][0]
        if head == "feedback":
            session.reactions.append((code, rest))
            events.append(_event("answer", f"— {session.client.name}: «{R.REACTIONS[rest]}»."))
        else:
            events.append(_event("line", "Мадам продолжает без реакции; согласие клиента не записано."))
        metrics.append(_metric("reaction", code=code, reaction=rest if head == "feedback" else "none"))
        session.phase = "divine"
    elif head == "go":
        session.phase = "farewell"
        events.append(_event("line", C.LIVE_ANNOUNCE_LINE))
    elif head == "f":
        session.picks["farewell"], session.phase = rest, "announce"
    elif head == "a":
        _announce(session, rest, events, metrics)
    elif head == "v":
        _verdict(session, rest, now, events, metrics)
    elif head == "back":
        if session.phase == "mode":
            session.pending, session.phase = None, "pick"
        elif session.phase == "pick":
            session.options, session.phase = (), "tarot"
        else:
            session.phase = "divine"
    return LiveResult(tuple(events), tuple(metrics), build_view(session), notice)


def _screen(session: LiveSession) -> str:
    phase = session.phase
    if phase == "tarot":
        return R.TAROT_TEXT
    if phase == "palm":
        return "🖐 Выбери две области из трёх.\n" + "\n".join(R.AREAS[key][1] for key in session.areas)
    if phase == "pick":
        head = f"♈ Срок: {session.horizon}" if session.method == "horoscope" else (
            "\n".join(R.AREAS[key][1] for key in session.areas) if session.method == "palm" else
            next(label for label, codes in R.PAIRS.values() if codes == session.options))
        return "\n\n".join((head, *(f"{R.READINGS[code].link}\n«{R.READINGS[code].voice}»" for code in session.options)))
    if phase == "mode":
        return (f"«{R.READINGS[session.pending].voice}»\n\nВерсия предлагает подумать; утверждение звучит как знание "
                "и не позволит снизить итоговую уверенность до «Мне кажется». Клиент услышит содержание в обоих случаях. Черновик не произносится.")
    if phase in ("farewell", "announce", "verdict"):
        lines = ["📜 Что прозвучало:"]
        lines += [f"{'Версия' if mode == 'idea' else 'Утверждение'}: {R.READINGS[code].voice}" for code, mode in session.voiced]
        if not session.voiced:
            lines.append("Трактовок вслух не было.")
        lines += [f"Ответ клиента: {R.REACTIONS[reaction]} — «{R.READINGS[code].voice}»" for code, reaction in session.reactions]
        if "farewell" in session.picks:
            lines.append("Напутствие: " + R.FAREWELLS[session.picks["farewell"]][1])
        if any(mode == "claim" for _, mode in session.voiced):
            lines.append("❗ Утверждение уже прозвучало: итоговая уверенность не ниже «Я отчётливо вижу».")
        return "\n\n".join(lines)
    return ""


def _prompt(session: LiveSession) -> str:
    if session.phase == "intake":
        return f"{session.client.name}, {_intake_slot(session).question}"
    name = session.client.name if session.client else "Клиент"
    return {
        "divine": "Мадам, что дальше? Можно уточнить, погадать или перейти к напутствию.",
        "ask": "Что уточнить? Клиент отвечает своими словами в чате.",
        "answer": f"{name}, ответь в чате и нажми «Ответил в чат». Бот не читает твой ответ.",
        "tarot": "Мадам, какие две карты связать с рассказом?",
        "palm": "Мадам, какую область осмотреть?",
        "pick": "Мадам, что озвучить? Выбери свою трактовку.",
        "mode": "Мадам, как это сказать?",
        "sign": f"{name}, какой у тебя знак?",
        "hand": f"{name}, какая у тебя рука? Выбери самую похожую.",
        "horizon": f"{name}, к какому сроку или событию нужен ответ? Событие опиши в чате.",
        "reaction": f"{name}, как тебе эта трактовка? Можешь также ответить мадам в чате.",
        "farewell": "Мадам, что сказать на прощание?",
        "announce": "Мадам, как объявить напутствие?",
        "verdict": f"{name}, твой приговор всему сеансу, включая все произнесённые версии и утверждения?",
    }.get(session.phase, "")


def build_view(session: LiveSession) -> LiveView:
    return LiveView(
        session_id=session.session_id, step=session.step, host=session.host.name,
        client=session.client.name if session.client else None, phase=session.phase, prompt=_prompt(session),
        actions=available_actions(session), finished=session.finished, finish_reason=session.finish_reason,
        confidence=session.confidence, outcome=session.outcome, fee=session.fee, wallet=session.wallet_start + session.fee,
        intake_filled=len(session.intake), screen=_screen(session),
        preview=R.FAREWELLS[session.picks['farewell']][1] if 'farewell' in session.picks else None,
    )
