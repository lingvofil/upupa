"""Replay and role-specific presentation for a private Mini App reception."""
from __future__ import annotations

import random

from games.guru import content, live, live_readings

CLIENT_FIELDS = {
    "intake": "Анкету видит только клиент. Она откроется после приговора.",
    "divine": "Мадам выбирает, что спросить или какую практику открыть.",
    "ask": "Мадам выбирает вопрос.",
    "answer": "Ответьте мадам своими словами.",
    "tarot": "Мадам связывает образы на картах.",
    "palm": "Мадам рассматривает ладонь.",
    "pick": "Мадам обдумывает трактовку.",
    "mode": "Мадам решает, что озвучить.",
    "reaction": "Как вам эта трактовка?",
    "farewell": "Мадам готовит напутствие.",
    "announce": "Мадам готовит напутствие.",
}
QUESTIONS = {
    "ask": "О чём спросить клиента?",
    "answer": "Клиент пишет ответ.",
    "horizon": "К какому сроку нужен ответ?",
    "intake": "Клиент заполняет анкету.",
}
CARD_TITLES = {card.art: card.name for card in content.TAROT_CARDS.values()}


def reply_text(value):
    if not isinstance(value, str) or len(value) > 1000:
        raise ValueError("Ответ должен содержать до 1000 символов.")
    return value.strip()


def needs_intro(session, moves):
    return session.phase == "divine" and not any(move[0] in ("introduce", "skip_intro") for move in moves)


def apply_move(session, rng, moves, owner, action, text, *, client):
    host_turn = owner == session.host.user_id
    intro = needs_intro(session, moves)
    if action in ("introduce", "skip_intro"):
        if not intro or (action == "skip_intro") != host_turn:
            raise ValueError("Сейчас это действие недоступно.")
        return [f"— {session.client.name}: {text}"] if text else ["Мадам начинает приём."]
    if intro:
        raise ValueError("Дождитесь рассказа клиента или продолжите без него.")
    if action == "said" and not text:
        raise ValueError("Напишите ответ на вопрос мадам.")
    if live.refusal(session, owner, action) is not None:
        raise PermissionError("Этот ход делает другой игрок.")
    name = session.host.name if host_turn else client
    result = live.act(session, owner, name, action, rng, now=session.started_at + len(moves) + 1)
    if action == "said":
        return [f"— {session.client.name}: {text}"]
    return [event.text for event in result.events
            if event.text and not event.text.endswith("Бот отворачивается.")]


def replay(token, host_id, guest_id, saved):
    rng = random.Random(saved["seed"])
    session = live.new_session(session_id=token, chat_id=0, host_id=host_id, host_name=saved["host"],
                               now=saved["started"], wallet=saved["wallet"], items=tuple(saved["items"]))
    lines = []
    moves = []
    for entry in saved["moves"]:
        action, as_host, text = entry
        owner = host_id if as_host else guest_id
        lines.extend(apply_move(session, rng, moves, owner, action, text, client=saved.get("guest")))
        moves.append(entry)
    return session, rng, lines


def view(token, owner, host, guest, saved, status, session, lines):
    role = "host" if owner == host else "client" if owner == guest else "invite"
    result = {"id": token, "role": role, "host": saved["host"], "status": status, "rev": len(saved["moves"])}
    if role == "invite":
        return result

    host_turn = role == "host"
    phase = session.phase
    result.update(client=saved.get("guest"), phase=phase, lines=lines, actions=[])
    if host_turn and session.drafts:
        result["drafts"] = [live_readings.READINGS[code].voice for code in session.drafts]
    if not host_turn and status == "active" and session.intake:
        result["intake"] = [f"{slot.title} · {slot.options[session.intake[slot.key]]}"
                            for slot in content.LIVE_INTAKE if slot.key in session.intake]
    if status == "done":
        result["outcome"] = content.LIVE_VERDICTS[saved["moves"][-1][0].split(":", 1)[1]]
        if host_turn:
            result["fee"] = session.fee
        return result
    if status != "active" or phase == "waiting":
        return result

    if needs_intro(session, saved["moves"]):
        result["prompt"] = "Клиент может рассказать, с чем пришёл." if host_turn else "С чем вы пришли к мадам?"
        if host_turn:
            result["actions"] = [{"id": "skip_intro", "label": "Начать без рассказа", "kind": "control"}]
        else:
            result["input"] = "introduce"
        return result

    projected = live.build_view(session)
    result["prompt"] = (QUESTIONS.get(phase, projected.prompt) if host_turn else
                        CLIENT_FIELDS.get(phase, QUESTIONS["horizon"] if phase == "horizon" else projected.prompt))
    if phase == "answer" and not host_turn:
        result["input"] = "said"
    result["actions"] = [
        {"id": action.id, "label": action.label, "kind": action.kind}
        for action in projected.actions
        if action.id != "said" and live.refusal(session, owner, action.id) is None
    ]
    if host_turn:
        result["screen"] = projected.screen
        if phase in ("tarot", "pick", "mode") and session.method == "tarot":
            result["cards"] = [{"art": art, "title": CARD_TITLES[art]} for art in live_readings.CARDS]
        if phase in ("palm", "pick", "mode") and session.method == "palm":
            result["palm"] = session.hand or "standard"
    return result
