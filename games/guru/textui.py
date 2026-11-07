"""Текстовый адаптер салона: ``View`` → текст и раскладка кнопок (чистый Python, без aiogram).

Callback-данные: ``guru:<сессия>:<шаг>:<действие>``. Номер шага защищает от
старых кнопок и двойных нажатий: кнопка действует, только пока партия стоит
ровно на том шаге, для которого её нарисовали. Финальные кнопки:
``guru:again:yes|no:<сессия>``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from games.guru import content as C
from games.guru.contract import ClientView, View
from games.guru.model import CLIENT_SOURCES, ClientReport
from games.guru.rules import rub

CALLBACK_PREFIX = "guru:"
MESSAGE_LIMIT = 4000  # у Telegram 4096, оставляем запас

_ACT_RE = re.compile(r"^guru:([0-9a-f]{1,32}):(\d+):([a-z]+(?::[a-z_]+){0,2})$")
_AGAIN_RE = re.compile(r"^guru:again:(yes|no):([0-9a-f]{1,32})$")

ROW_WIDTH = {"method": 2, "line": 2, "question": 3, "option": 2, "confidence": 1, "control": 3}

OUTCOME_TITLES = {
    "hit": "попадание",
    "partial": "частичное попадание",
    "miss": "промах",
    "exposure": "разоблачение",
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
ABANDON_NOTES = {
    "timeout": "💤 Мадам уснула над хрустальным шаром. Салон закрыт.",
    "quit": "🚪 Мадам повесила табличку «Закрыто». Салон закрыт.",
    "restart": "⚡ Во всём районе вырубили свет. Салон закрыт.",
    "send_failed": "📵 Связь с космосом потеряна. Салон закрыт.",
}
AGAIN_YES = "✅ Да, открываем салон"
AGAIN_NO = "🚪 С меня хватит, вызывайте налоговую"

Rows = list[list[tuple[str, str]]]


@dataclass(frozen=True)
class Callback:
    kind: str  # act | again
    session_id: str
    step: int | None = None
    action: str | None = None
    answer: str | None = None


def encode(view: View, action_id: str) -> str:
    return f"{CALLBACK_PREFIX}{view.session_id}:{view.step}:{action_id}"


def again_data(session_id: str, answer: str) -> str:
    return f"{CALLBACK_PREFIX}again:{answer}:{session_id}"


def parse(data: str | None) -> Callback | None:
    data = data or ""
    match = _AGAIN_RE.match(data)
    if match:
        return Callback("again", match.group(2), answer=match.group(1))
    match = _ACT_RE.match(data)
    if match:
        return Callback("act", match.group(1), int(match.group(2)), match.group(3))
    return None


# --- кнопки -----------------------------------------------------------------

def keyboard(view: View) -> Rows:
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


# --- текст ------------------------------------------------------------------

def _candles(view: View) -> str:
    return "🕯" * view.legend + "▫️" * max(0, view.legend_start - view.legend)


def _header(view: View, number: int) -> list[str]:
    return [
        f"🔮 Салон мадам {view.player} · клиент {number}/{view.clients_total}",
        f"Легенда {_candles(view)} · Серия 🔥{view.streak} · Очки {view.score} · Касса {rub(view.money)}",
    ]


def _card(client: ClientView) -> list[str]:
    lines = [
        f"{client.emoji} {client.archetype_title} — {client.name}",
        f"💬 {client.complaint}",
        f"👀 {client.observation}",
        "",
        "📒 Блокнот:",
    ]
    for note in client.notebook:
        clarity = f" ({note.clarity})" if note.clarity else ""
        lines.append(f"{note.icon} {note.source}{clarity} — {note.text}")
    return lines


def _status(client: ClientView) -> str:
    status = f"{client.name} {client.mood}."
    if client.suspicion:
        status += f" Скептик {client.suspicion}."
    return status


def _sciences(view: View) -> str:
    marks = " ".join(
        f"{C.METHOD_LABELS[method].split()[0]}{'✅' if used else '⬜'}" for method, used in view.sciences
    )
    return f"Науки за смену: {marks}"


def _hint_verdict(source: str, clear: bool, truthful: bool) -> str:
    if not truthful:
        return "ложь" if source not in CLIENT_SOURCES else "заблуждение"
    return "правда" if clear else "мутно, но правда внутри"


def _report(report: ClientReport) -> list[str]:
    slots = []
    for slot in report.slots:
        label = C.SLOT_TITLES[slot.slot]
        picked = _tag_label(slot.slot, slot.picked) if slot.picked else "—"
        mark = "✅" if slot.matched else "❌"
        extra = " (Меркурий)" if slot.mercury else ""
        if not slot.matched:
            extra = f" (было: {_tag_label(slot.slot, slot.truth)})"
        slots.append(f"{label}: {picked} {mark}{extra}")
    own = [h for h in report.hints if h.source not in CLIENT_SOURCES]
    words = [h for h in report.hints if h.source in CLIENT_SOURCES]
    lines = ["", "━━ Разбор ━━", " · ".join(slots)]
    if own:
        lines.append("Подсказки: " + " · ".join(
            f"{C.SOURCE_LABELS[h.source][0]} {C.SOURCE_LABELS[h.source][1]} — {_hint_verdict(h.source, h.clear, h.truthful)}"
            for h in own
        ))
    if words:
        lines.append("Со слов клиента: " + ", ".join(
            f"{C.SLOT_TITLES[h.slot].lower() if h.slot in C.SLOTS else h.slot} — "
            f"{_hint_verdict(h.source, h.clear, h.truthful)}"
            for h in words
        ))
    if report.echo_slots:
        lines.append("Эхо (выдано за ясновидение без подтверждения): "
                     + ", ".join(C.SLOT_TITLES[s].lower() for s in report.echo_slots))
    verdict = f"{OUTCOME_ICONS[report.outcome]} {report.matched} из 3 — {OUTCOME_TITLES[report.outcome]}"
    if report.outcome == "exposure":
        verdict = f"{OUTCOME_ICONS['exposure']} Разоблачение: {REASON_TEXTS.get(report.reasons[0], report.reasons[0])}"
    if report.jackpot:
        verdict += " · 🎰 джекпот"
    if report.confidence:
        verdict += f" · объявлено «{C.CONFIDENCE_LABELS[report.confidence].split(' ', 1)[1]}»"
    lines.append(verdict)
    streak = f"серия {report.streak_before} → {report.streak_after}"
    legend = f"легенда {report.legend_before} → {report.legend_after}"
    lines.append(f"{rub(report.fee)} · очки {report.points:+d} · {streak} · {legend}")
    return lines


def _tag_label(slot: str, tag: str) -> str:
    return {"who": C.WHO, "what": C.WHAT, "when": C.WHEN}[slot][tag].label


def _fit(head: list[str], dialog: list[str], tail: list[str]) -> str:
    """Собрать сообщение; если не влезает в лимит, срезать начало переписки."""
    dialog = list(dialog)
    while True:
        parts = head + ([""] + dialog if dialog else []) + tail
        text = "\n".join(parts)
        if len(text) <= MESSAGE_LIMIT or not dialog:
            return text[:MESSAGE_LIMIT]
        dialog = dialog[1:]
        if dialog and dialog[0] != "…":
            dialog[0] = "…"


def render_intro(view: View) -> str:
    return "\n".join((
        f"🔮 Мадам {view.player} открывает салон. Сегодня {view.clients_total} клиента.",
        "",
        "У каждого клиента до двух приёмов: 🖐 ладонь подскажет сферу (а значит, что случится), "
        "🃏 Таро — кто, ♈ гороскоп — когда, 🌀 энергетика — настроение, ❓ вопрос — что клиент думает сам.",
        "Потом собери предсказание: кто, что, когда — и объяви.",
        "",
        "🤔 «Мне кажется» — безопасно. 🔥 «Клянусь!» — больше всего очков, но несбывшаяся клятва "
        "или свои же слова клиента, выданные за ясновидение, — это разоблачение.",
        f"Легенда салона: {_candles(view)}. Кончится — салон закроют.",
        "",
        "Кнопки жмёт только мадам. 3 минуты без хода — салон закрывается.",
    ))


def render_view(view: View) -> tuple[str, Rows]:
    """Активный приём: карточка, блокнот, переписка, подсказка к действию и кнопки."""
    client = view.client
    if client is None:
        return render_summary(view)
    tail = ["", _status(client)]
    if client.phase == "research":
        tail.append(_sciences(view))
    if view.preview:
        tail.append(f"🔮 {view.preview}")
    tail.append(f"👉 {view.prompt}")
    text = _fit(_header(view, client.number) + [""] + _card(client), list(client.dialog), tail)
    return text, keyboard(view)


def render_finished(view: View, client: ClientView) -> str:
    """Законченный приём: переписка, развязка и разбор, без кнопок."""
    tail = _report(client.report) if client.report else []
    return _fit(_header(view, client.number) + [""] + _card(client), list(client.dialog), tail)


def render_abandoned(view: View, client: ClientView, reason: str) -> str:
    tail = ["", ABANDON_NOTES.get(reason, "Салон закрыт.")]
    return _fit(_header(view, client.number) + [""] + _card(client), list(client.dialog), tail)


def render_summary(view: View) -> tuple[str, Rows]:
    if view.finish_reason == "exposed":
        head = f"🚨 Салон мадам {view.player} разоблачён."
    elif view.finish_reason in ABANDON_NOTES:
        head = f"🏚 Салон мадам {view.player} брошен."
    else:
        head = f"🏁 Смена в салоне мадам {view.player} окончена."
    counts = {outcome: view.outcomes.count(outcome) for outcome in OUTCOME_TITLES}
    lines = [
        head,
        "",
        f"Очки: {view.score}",
        f"Касса: {rub(view.money)}",
        f"Легенда: {_candles(view)}",
        f"Клиентов: {len(view.outcomes)}/{view.clients_total} · "
        + " · ".join(f"{OUTCOME_ICONS[o]} {counts[o]}" for o in OUTCOME_TITLES),
        f"Лучшая серия: {view.max_streak}",
        _sciences(view),
    ]
    if view.title:
        lines.append(f"Звание: {view.title}")
    for achievement in view.achievements:
        lines.append(f"🏅 {achievement}")
    lines += ["", "Хотите ещё одну смену?"]
    return "\n".join(lines), again_keyboard(view.session_id)
