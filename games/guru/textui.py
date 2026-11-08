"""Текстовый адаптер салона: события и ``View`` → короткие сообщения и кнопки (без aiogram).

Callback-данные: ``guru:<сессия>:<шаг>:<действие>``. Номер шага защищает от старых
кнопок и двойных нажатий. Отдельно: ``guru:again:yes|no:<сессия>`` (финал) и
``guru:rep:<клиент>:<сессия>`` (подробный разбор приёма), ``guru:livenew:<сессия>``
(после живого приёма: нажавший открывает следующий).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from games.guru import content as C
from games.guru.contract import DisplayEvent, LiveView, View
from games.guru.model import ClientReport
from games.guru.rules import rub, signed_rub

CALLBACK_PREFIX = "guru:"
MESSAGE_LIMIT = 4000  # у Telegram 4096, оставляем запас

_ACT_RE = re.compile(r"^guru:([0-9a-f]{1,32}):(\d+):([a-z]+(?::[a-z_0-9]+){0,2})$")
_AGAIN_RE = re.compile(r"^guru:again:(yes|no):([0-9a-f]{1,32})$")
_REPORT_RE = re.compile(r"^guru:rep:(\d+):([0-9a-f]{1,32})$")
_LIVE_NEW_RE = re.compile(r"^guru:livenew:([0-9a-f]{1,32})$")

ROW_WIDTH = {"question": 1, "method": 2, "interpretation": 1, "option": 2, "confidence": 1, "verdict": 2, "sign": 3, "intake": 2}

OUTCOME_TITLES = {
    "hit": "Попадание",
    "partial": "Частичное попадание",
    "miss": "Промах",
    "exposure": "Разоблачение",
}
OUTCOME_ICONS = {"hit": "🎯", "partial": "🌗", "miss": "💨", "exposure": "🚨"}
REASON_TEXTS = {
    "skeptic_echo": "скептик услышал свои же слова под видом ясновидения",
    "sworn_miss": "клятва не сбылась",
    "skeptic_confident_miss": "скептик поймал уверенный промах — он уже всё записывал",
    "sworn_echo": "клятва на словах самого клиента",
    "trap": "сюжетная ловушка",
    "suspicion": "скептик набрал достаточно подозрений",
}
QUALITY_TEXTS = {
    "sharp": "толкований было два, правда среди них",
    "plain": "правда была среди толкований",
    "off": "правды среди толкований не было",
}
ABANDON_NOTES = {
    "timeout": "💤 Мадам уснула над хрустальным шаром. Салон закрыт.",
    "quit": "🚪 Мадам повесила табличку «Закрыто». Салон закрыт.",
    "restart": "⚡ Во всём районе вырубили свет. Салон закрыт.",
    "send_failed": "📵 Связь с космосом потеряна. Салон закрыт.",
}
AGAIN_YES = "✅ Да, открываем салон"
AGAIN_NO = "🚪 С меня хватит, вызывайте налоговую"
REPORT_BUTTON = "📋 Разбор приёма"
LIVE_NEW_BUTTON = "🔮 Теперь я гадаю"
LIVE_WAIT_EXPIRED = "🕯 Никто не записался. Мадам гасит свечи."
LIVE_ABANDON_NOTES = {
    "timeout": "💤 Мадам и клиент разошлись, не попрощавшись. Денег никто не получил.",
    "quit": "🚪 Приём прерван. Денег никто не получил.",
    "restart": "⚡ Во всём районе вырубили свет. Приём прерван.",
    "send_failed": "📵 Связь с космосом потеряна. Приём прерван.",
}

Rows = list[list[tuple[str, str]]]


@dataclass(frozen=True)
class Callback:
    kind: str  # act | again | report | livenew
    session_id: str
    step: int | None = None
    action: str | None = None
    answer: str | None = None
    client_no: int | None = None


@dataclass(frozen=True)
class Bubble:
    """Одно сообщение: пауза перед ним, текст и заменяет ли оно предыдущее."""

    text: str
    pause: float = 0.0
    spread: float = 0.0
    replace: bool = False
    cards: tuple[str, ...] = ()  # рисунки, которые показать перед текстом


def encode(view: View | LiveView, action_id: str) -> str:
    return f"{CALLBACK_PREFIX}{view.session_id}:{view.step}:{action_id}"


def again_data(session_id: str, answer: str) -> str:
    return f"{CALLBACK_PREFIX}again:{answer}:{session_id}"


def report_data(session_id: str, client_no: int) -> str:
    return f"{CALLBACK_PREFIX}rep:{client_no}:{session_id}"


def live_new_data(session_id: str) -> str:
    return f"{CALLBACK_PREFIX}livenew:{session_id}"


def parse(data: str | None) -> Callback | None:
    data = data or ""
    match = _LIVE_NEW_RE.match(data)
    if match:
        return Callback("livenew", match.group(1))
    match = _AGAIN_RE.match(data)
    if match:
        return Callback("again", match.group(2), answer=match.group(1))
    match = _REPORT_RE.match(data)
    if match:
        return Callback("report", match.group(2), client_no=int(match.group(1)))
    match = _ACT_RE.match(data)
    if match:
        return Callback("act", match.group(1), int(match.group(2)), match.group(3))
    return None


# --- кнопки -----------------------------------------------------------------

def keyboard(view: View | LiveView) -> Rows:
    rows: Rows = []
    controls: list[tuple[str, str]] = []
    current_kind, row = None, []
    for action in view.actions:
        button = (action.label, encode(view, action.id))
        if action.kind == "control":
            controls.append(button)
            continue
        width = ROW_WIDTH.get(action.kind, 2)
        if action.kind != current_kind or len(row) >= width:
            if row:
                rows.append(row)
            row, current_kind = [], action.kind
        row.append(button)
    if row:
        rows.append(row)
    if controls:
        rows.append(controls)
    return rows


def again_keyboard(session_id: str) -> Rows:
    return [[(AGAIN_YES, again_data(session_id, "yes"))], [(AGAIN_NO, again_data(session_id, "no"))]]


def live_done_keyboard(view: LiveView) -> Rows:
    return [[(LIVE_NEW_BUTTON, live_new_data(view.session_id))]]


def client_done_keyboard(view: View, report: ClientReport) -> Rows:
    rows: Rows = [[(REPORT_BUTTON, report_data(view.session_id, report.client_no))]]
    return rows + keyboard(view)


# --- сообщения --------------------------------------------------------------

def _candles(view: View) -> str:
    return "🕯" * view.legend + "▫️" * max(0, view.legend_start - view.legend)


def _header(view: View) -> str:
    number = view.client.number if view.client else len(view.outcomes)
    return f"Клиент {number}/{view.clients_total} · Легенда {_candles(view)} · Серия 🔥{view.streak}"


def bubbles(events: tuple[DisplayEvent, ...], view: View | LiveView) -> list[Bubble]:
    """Сгруппировать события в сообщения: новое начинается там, где есть пауза."""
    result: list[Bubble] = []
    parts: list[str] = []
    pause, spread, replace = 0.0, 0.0, False
    cards: tuple[str, ...] = ()
    for event in events:
        if parts and (event.pause > 0 or event.replace):
            result.append(Bubble("\n".join(parts), pause, spread, replace, cards))
            parts = []
        if not parts:
            pause, spread, replace = event.pause, event.spread, event.replace
            cards = tuple(event.data.get("cards", ()))
        text = f"{_header(view)}\n\n{event.text}" if event.kind == "arrive" else event.text
        parts.append(text)
    if parts:
        result.append(Bubble("\n".join(parts), pause, spread, replace, cards))
    return result


def prompt_text(view: View | LiveView) -> str:
    return f"👉 {view.prompt}" if view.prompt else ""


def with_prompt(text: str, view: View) -> str:
    prompt = prompt_text(view)
    return f"{text}\n\n{prompt}" if text and prompt else (text or prompt)


def render_constructor(view: View) -> tuple[str, Rows]:
    """Сборка предсказания: одно сообщение, которое меняется по шагам."""
    client = view.client
    lines = ["🔮 Предсказание"]
    if client is not None:
        status = f"{client.name} {client.mood}."
        if client.suspicion:
            status = f"{client.name} {client.suspicion}."
        lines.append(status)
    if view.preview:
        lines.append(view.preview)
    lines += ["", prompt_text(view)]
    return "\n".join(lines), keyboard(view)


def render_verdict(view: View, report: ClientReport) -> str:
    """Итог под развязкой: что случилось и чего это стоило."""
    title = f"{OUTCOME_ICONS[report.outcome]} {OUTCOME_TITLES[report.outcome]}"
    lines = [f"{title} · очки {report.points:+d} · {signed_rub(report.fee)}"]
    if report.outcome == "exposure":
        lines.append("Это провал: клиент раскусил мадам, легенда салона стала меньше.")
    if report.fee < 0:
        lines.append(C.LOSS_LINES[report.outcome])
    if report.parting == "pleased":
        lines.append("🙂 Сам сеанс клиенту понравился, поэтому +1 очко" + (" и чаевые." if report.outcome == "exposure" else "."))
    elif report.parting == "sour":
        lines.append("🙁 Сеанс клиенту не понравился.")
    lines.append(f"Легенда {_candles(view)} · Серия 🔥{view.streak} · 💰 {rub(view.wallet)}")
    return "\n".join(lines)


def _tag_label(slot: str, tag: str) -> str:
    return {"who": C.WHO, "what": C.WHAT, "when": C.WHEN}[slot][tag].label


def _reading_label(slot: str, tag: str) -> str:
    return {"who": C.WHO, "what": C.WHAT, "sphere": C.SPHERES, "emotion": C.EMOTIONS}[slot][tag].button


def render_report(report: ClientReport, questions: dict[str, str] | None = None) -> str:
    """Подробный разбор: открывается по кнопке, чтобы не мешать игре."""
    lines = ["📋 Разбор приёма", ""]
    for slot in report.slots:
        label = C.SLOT_TITLES[slot.slot]
        picked = _tag_label(slot.slot, slot.picked) if slot.picked else "—"
        mark = "✅" if slot.matched else "❌"
        if slot.mercury:
            extra = " (Меркурий простил)"
        elif not slot.matched:
            extra = f" (было: {_tag_label(slot.slot, slot.truth)})"
        else:
            extra = ""
        lines.append(f"{label}: {picked} {mark}{extra}")
    if report.readings:
        lines.append("")
        for reading in report.readings:
            method = C.METHODS[reading.method].label
            verdict = "верно" if reading.correct else "мимо"
            lines.append(
                f"{method}: озвучено «{_reading_label(reading.slot, reading.voiced)}» — {verdict}; "
                f"{QUALITY_TEXTS[reading.quality]}."
            )
    told = [(slot, tag, truthful) for slot, tag, truthful in report.said if slot in C.SLOTS]
    if told:
        lines.append("")
        lines.append("Клиент сказал сам: " + ", ".join(
            f"{_tag_label(slot, tag)} ({'правда' if truthful else 'заблуждение'})" for slot, tag, truthful in told
        ))
    if report.echo_slots:
        lines.append("Эхо (его слова без гадания): " + ", ".join(C.SLOT_TITLES[s].lower() for s in report.echo_slots))
    lines.append("")
    if report.outcome == "exposure":
        lines.append(f"Почему разоблачение: {REASON_TEXTS.get(report.reasons[0], report.reasons[0])}.")
    else:
        lines.append(f"Совпало {report.matched} из 3.")
    if report.jackpot:
        lines.append("🎰 Сработал сюжетный джекпот.")
    lines.append(f"Серия {report.streak_before} → {report.streak_after} · Легенда {report.legend_before} → {report.legend_after}")
    return "\n".join(lines)[:MESSAGE_LIMIT]


def render_intro(view: View) -> str:
    return "\n".join((
        f"🔮 Мадам {view.player} открывает салон. Сегодня {view.clients_total} клиента.",
        "",
        "Слушай, уточняй, гадай, сама решай, что озвучить, и делай предсказание.",
        f"«Клянусь!» приносит больше всего, но несбывшаяся клятва стоит денег и легенды салона: {_candles(view)}.",
        f"💰 В кассе: {rub(view.wallet)}",
        "",
        "Кнопки жмёт только мадам. 15 минут без хода — салон закрывается.",
    ))


def render_abandoned(reason: str) -> str:
    return ABANDON_NOTES.get(reason, "Салон закрыт.")


def render_summary(view: View) -> tuple[str, Rows]:
    if view.finish_reason == "exposed":
        head = f"🚨 Салон мадам {view.player} разоблачён."
    elif view.finish_reason in ABANDON_NOTES:
        head = f"🏚 Салон мадам {view.player} брошен."
    else:
        head = f"🏁 Смена в салоне мадам {view.player} окончена."
    counts = {outcome: view.outcomes.count(outcome) for outcome in OUTCOME_TITLES}
    sciences = " ".join(f"{C.METHODS[m].label.split()[0]}{'✅' if used else '⬜'}" for m, used in view.sciences)
    lines = [
        head,
        "",
        f"Очки: {view.score} · За смену: {signed_rub(view.money)} · 💰 В кассе: {rub(view.wallet)}",
        f"Клиентов: {len(view.outcomes)}/{view.clients_total} · "
        + " ".join(f"{OUTCOME_ICONS[o]}{counts[o]}" for o in OUTCOME_TITLES),
        f"Легенда {_candles(view)} · Лучшая серия: {view.max_streak} · Науки: {sciences}",
    ]
    if view.title:
        lines.append(f"Звание: {view.title}")
    lines += [f"🏅 {achievement}" for achievement in view.achievements]
    lines += ["", "Хотите ещё одну смену?"]
    return "\n".join(lines), again_keyboard(view.session_id)


# --- живой приём ------------------------------------------------------------

def render_live_open(view: LiveView) -> str:
    return "\n".join((
        f"🔮 Мадам {view.host} принимает живых людей.",
        "",
        "Клиент кнопками заполняет анкету. Мадам расспрашивает в чате, гадает и толкует как умеет.",
        "В конце клиент решает, было ли это ясновидением. От этого зависят деньги мадам.",
        "",
        "Кто первым нажмёт кнопку, тот и клиент.",
    ))


def render_intake(view: LiveView) -> tuple[str, Rows | None]:
    """Анкета клиента: одно сообщение, которое заполняется по шагам."""
    lines = [f"📇 Анкета: {view.client}", *(f"{title}: {value}" for title, value in view.intake)]
    if view.phase != "intake":
        return "\n".join(lines), None
    return "\n".join((*lines, "", prompt_text(view))), keyboard(view)


def render_live_result(view: LiveView) -> str:
    outcome = view.outcome or "miss"
    lines = [f"{OUTCOME_ICONS[outcome]} {OUTCOME_TITLES[outcome]} · {signed_rub(view.fee)}"]
    if view.fee < 0:
        lines.append(C.LOSS_LINES[outcome])
    lines.append(f"💰 В кассе мадам {view.host}: {rub(view.wallet)}")
    return "\n".join(lines)


def render_live_abandoned(view: LiveView, reason: str) -> str:
    if view.phase == "waiting":
        return LIVE_WAIT_EXPIRED
    return LIVE_ABANDON_NOTES.get(reason, "Приём прерван.")


def render_wallets(top: list, own: object) -> str:
    """Таблица касс чата. ``top`` и ``own`` — записи с полями name, balance, earned, lost."""
    if not top:
        return "💰 В кассах пусто. Открыть салон: «гуру»."
    lines = ["💰 Кассы мадам", ""]
    lines += [f"{place}. {wallet.name} — {rub(wallet.balance)}" for place, wallet in enumerate(top, 1)]
    lines += ["", f"Твоя касса: {rub(own.balance)} · заработано {rub(own.earned)} · потеряно {rub(own.lost)}"]
    return "\n".join(lines)
