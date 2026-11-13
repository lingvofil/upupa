"""Текстовый адаптер салона: события и ``View`` → короткие сообщения и кнопки (без aiogram).

Callback-данные: ``guru:<сессия>:<шаг>:<действие>``. Номер шага защищает от старых
кнопок и двойных нажатий. Отдельно: ``guru:again:yes|no:<сессия>`` (финал) и
``guru:rep:<клиент>:<сессия>`` (подробный разбор приёма), ``guru:livenew:<сессия>``
(после живого приёма: нажавший открывает следующий), ``guru:buy:<вещь>:<владелец>`` (лавка),
``guru:menu:<пункт>`` (меню салона: кнопка работает для любого, кто её нажал),
``guru:nav:<раздел>`` (разделы меню: открываются в том же сообщении, а не новым).

Сообщения партии (``bubbles``) размечены для Telegram как HTML: слова клиента стоят цитатой,
то, что происходит в салоне, — курсивом. Весь чужой текст в них экранирован (``escape``).
Остальные тексты модуля — обычные строки без разметки.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass

from games.guru import content as C
from games.guru import salon
from games.guru.contract import DisplayEvent, LiveView, SeanceView, View
from games.guru.daily import DailyCard
from games.guru.model import ClientReport
from games.guru.rules import rub, signed_rub

CALLBACK_PREFIX = "guru:"
MESSAGE_LIMIT = 4000  # у Telegram 4096, оставляем запас

_ACT_RE = re.compile(r"^guru:([0-9a-f]{1,32}):(\d+):([a-z]+(?::[a-z_0-9]+){0,2})$")
_AGAIN_RE = re.compile(r"^guru:again:(yes|no):([0-9a-f]{1,32})$")
_REPORT_RE = re.compile(r"^guru:rep:(\d+):([0-9a-f]{1,32})$")
_LIVE_NEW_RE = re.compile(r"^guru:livenew:([0-9a-f]{1,32})$")
_BUY_RE = re.compile(r"^guru:buy:([a-z_0-9]+):(\d+)$")
_MENU_RE = re.compile(r"^guru:menu:([a-z]+)$")
_NAV_RE = re.compile(r"^guru:nav:([a-z]+)$")
_DIALOG_RE = re.compile(r"^guru:ui:([0-9a-f]{1,32}):(\d+):(screen|log|heard):(\d+)$")

ROW_WIDTH = {"question": 1, "method": 2, "interpretation": 1, "option": 2, "confidence": 1, "verdict": 2, "sign": 3, "intake": 2,
             "hub": 2, "choice": 1, "area": 3, "hand": 1, "look": 1, "zone": 2, "fix": 3}

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
MENU_SHIFT = "🔮 Открыть смену"
MENU_LIVE = "🤝 Живой приём"
MENU_CARD = "🃏 Карта дня"
MENU_CARD_OWN = "🃏 Вытянуть свою карту"
MENU_SHOP = "🏪 Лавка"
MENU_WALLET = "💰 Кассы"
MENU_SALON = "🏠 Мой салон"
MENU_SALONS = "👥 Салоны чата"
MENU_SHELF = "🎁 Полка"
NAV_MENU = "⬅️ Меню"
MENU_SEANCE = "🕯 Сеанс"
MENU_SEANCE_AGAIN = "🕯 Следующий клиент"
MENU_ACTIONS = frozenset({"shift", "live", "card", "shop", "wallet", "salon", "salons", "shelf", "seance"})
# Разделы меню: справки салона листаются в одном сообщении. Порядок — порядок кнопок под разделом.
# «Мой салон» в их число не входит: это рисунок с подписью, он всегда приходит отдельным сообщением.
NAV_LABELS = {"shop": MENU_SHOP, "wallet": MENU_WALLET, "shelf": MENU_SHELF, "salons": MENU_SALONS, "menu": NAV_MENU}
NAV_SECTIONS = frozenset(NAV_LABELS)
NAV_LEGACY = frozenset({"salon"})  # кнопки из старых сообщений: по ним тоже придёт рисунок
SHELF_HINT = "Подарок оставляет клиент, которому сбылось: по одному от каждого типа."
SEANCE_ABANDON_NOTES = {
    "timeout": "💤 Мадам задумалась так надолго, что клиент ушёл сам. Денег никто не получил.",
    "quit": "🚪 Сеанс прерван. Клиент уходит, так и не узнав, что было в картах.",
    "restart": "⚡ Во всём районе вырубили свет. Сеанс прерван.",
    "send_failed": "📵 Связь с космосом потеряна. Сеанс прерван.",
}
CLIENT_STATE_ICONS = {"content": "🙂", "mixed": "😐", "displeased": "🙁"}
SPEECH_KINDS = frozenset({"tell", "answer"})  # слова клиента: цитатой
ROOM_KINDS = frozenset({"arrive", "line", "status", "ritual", "react"})  # что происходит в салоне: курсивом
LIVE_NEEDS_GROUP = "Живому приёму нужен второй человек. Открой его в группе."
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
    kind: str  # act | again | report | livenew | buy | menu | nav | dialog
    session_id: str = ""
    step: int | None = None
    action: str | None = None
    answer: str | None = None
    client_no: int | None = None
    item: str | None = None
    owner: int | None = None
    page: int = 0


@dataclass(frozen=True)
class Bubble:
    """Одно сообщение: пауза перед ним, текст и заменяет ли оно предыдущее."""

    text: str
    pause: float = 0.0
    spread: float = 0.0
    replace: bool = False
    cards: tuple[str, ...] = ()  # рисунки, которые показать перед текстом
    drawing: tuple[str, tuple[str, ...]] | None = None  # рисунок перед текстом: (ладонь или аура, ключи)
    scene: tuple[str, tuple[str, ...], tuple[str, ...]] | None = None  # салон с текстом в подписи: (клиент, вещи, подарки)


def encode(view: View | LiveView | SeanceView, action_id: str) -> str:
    return f"{CALLBACK_PREFIX}{view.session_id}:{view.step}:{action_id}"


def again_data(session_id: str, answer: str) -> str:
    return f"{CALLBACK_PREFIX}again:{answer}:{session_id}"


def report_data(session_id: str, client_no: int) -> str:
    return f"{CALLBACK_PREFIX}rep:{client_no}:{session_id}"


def live_new_data(session_id: str) -> str:
    return f"{CALLBACK_PREFIX}livenew:{session_id}"


def buy_data(item: str, owner: int) -> str:
    return f"{CALLBACK_PREFIX}buy:{item}:{owner}"


def menu_data(action: str) -> str:
    return f"{CALLBACK_PREFIX}menu:{action}"


def nav_data(section: str) -> str:
    return f"{CALLBACK_PREFIX}nav:{section}"


def parse(data: str | None) -> Callback | None:
    data = data or ""
    match = _DIALOG_RE.match(data)
    if match:
        return Callback("dialog", match.group(1), int(match.group(2)), match.group(3), page=int(match.group(4)))
    match = _MENU_RE.match(data)
    if match:
        return Callback("menu", action=match.group(1)) if match.group(1) in MENU_ACTIONS else None
    match = _NAV_RE.match(data)
    if match:
        return Callback("nav", action=match.group(1)) if match.group(1) in NAV_SECTIONS | NAV_LEGACY else None
    match = _BUY_RE.match(data)
    if match:
        return Callback("buy", item=match.group(1), owner=int(match.group(2)))
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

def keyboard(view: View | LiveView | SeanceView) -> Rows:
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


def dialog_data(session_id: str, step: int, mode: str, page: int = 0) -> str:
    return f"guru:ui:{session_id}:{step}:{mode}:{page}"


def dialog_keyboard(session_id: str, step: int, rows: Rows, *, mode: str = "screen", page: int = 0,
                    total: int = 1, history: bool = True) -> Rows:
    result = [list(row) for row in rows]
    navigation = []
    if page > 0:
        navigation.append(("◀", dialog_data(session_id, step, mode, page - 1)))
    if page + 1 < total:
        navigation.append(("▶", dialog_data(session_id, step, mode, page + 1)))
    if navigation:
        result.append(navigation)
    if mode in ("log", "heard"):
        result.append([("↩ К приёму", dialog_data(session_id, step, "screen"))])
    elif history:
        result.append([("📜 Диалог", dialog_data(session_id, step, "log"))])
    if not any(data == menu_data("salon") for row in result for _, data in row):
        result.append([(MENU_SALON, menu_data("salon"))])
    return result


def _live_door(group: bool) -> list[tuple[str, str]]:
    """Живой приём предлагаем только там, где есть второй человек."""
    return [(MENU_LIVE, menu_data("live"))] if group else []


def again_keyboard(session_id: str, group: bool = True) -> Rows:
    return [
        [(AGAIN_YES, again_data(session_id, "yes"))],
        [*_live_door(group), (MENU_CARD, menu_data("card"))],
        [(AGAIN_NO, again_data(session_id, "no"))],
        [(MENU_SALON, menu_data("salon"))],
    ]


def live_done_keyboard(view: LiveView) -> Rows:
    return [[(LIVE_NEW_BUTTON, live_new_data(view.session_id))], [(MENU_CARD, menu_data("card")), (MENU_SALON, menu_data("salon"))]]


def _nav_button(section: str) -> tuple[str, str]:
    return NAV_LABELS[section], nav_data(section)


def menu_keyboard(group: bool = True) -> Rows:
    """Меню — главная страница: партии начинаются новым сообщением, справки открываются в этом же."""
    return [
        [(MENU_SHIFT, menu_data("shift")), *_live_door(group)],
        [(MENU_SEANCE, menu_data("seance")), (MENU_CARD, menu_data("card"))],
        [_nav_button("shop"), _nav_button("wallet"), (MENU_SALON, menu_data("salon"))],
        [_nav_button("shelf"), _nav_button("salons")],
    ]


def section_keyboard(section: str, rows: Rows | None = None) -> Rows:
    """Кнопки под разделом меню: свои (``rows``), потом остальные разделы и возврат в меню."""
    doors = [_nav_button(other) for other in NAV_LABELS if other not in (section, "menu")]
    result = [list(row) for row in rows or []]
    # Салон — рисунок: в текстовое сообщение его не превратить, он придёт отдельно.
    return [*result, doors, [(MENU_SALON, menu_data("salon")), _nav_button("menu")]]


def salon_keyboard() -> Rows:
    """Под рисунком салона: кнопки работают для любого, кто нажал, и открывают его меню на нужном разделе."""
    return [[(MENU_SHOP, menu_data("shop")), (MENU_SHELF, menu_data("shelf")), (MENU_SALONS, menu_data("salons"))],
            [(MENU_SALON, menu_data("salon"))]]


def daily_keyboard() -> Rows:
    return [[(MENU_CARD_OWN, menu_data("card"))], [(MENU_SHIFT, menu_data("shift")), (MENU_SALON, menu_data("salon"))]]


def client_done_keyboard(view: View, report: ClientReport) -> Rows:
    rows: Rows = [[(REPORT_BUTTON, report_data(view.session_id, report.client_no))]]
    return rows + keyboard(view)


# --- сообщения --------------------------------------------------------------

def _candles(view: View) -> str:
    return "🕯" * view.legend + "▫️" * max(0, view.legend_start - view.legend)


def _header(view: View) -> str:
    number = view.client.number if view.client else len(view.outcomes)
    return f"<b>Клиент {number} из {view.clients_total}</b> · Легенда {_candles(view)} · Серия 🔥{view.streak}"


def escape(text: str) -> str:
    """Обезвредить текст для HTML-сообщения: имена из мессенджера бывают любыми."""
    return html.escape(text, quote=False)


def _styled(event: DisplayEvent, view: View | LiveView) -> str:
    """Реплика в разметке: речь клиента — цитатой, происходящее в салоне — курсивом."""
    text = escape(event.text)
    if event.kind in SPEECH_KINDS:
        return f"<blockquote>{text}</blockquote>"
    if event.kind in ROOM_KINDS:
        text = f"<i>{text}</i>"
    if event.kind == "arrive":
        return f"{_header(view)}\n\n{text}"
    return text


def bubbles(events: tuple[DisplayEvent, ...], view: View | LiveView) -> list[Bubble]:
    """Сгруппировать события в сообщения с HTML-разметкой: новое начинается там, где есть пауза."""
    result: list[Bubble] = []
    parts: list[str] = []
    pause, spread, replace = 0.0, 0.0, False
    cards: tuple[str, ...] = ()
    drawing: tuple[str, tuple[str, ...]] | None = None
    scene: tuple[str, tuple[str, ...], tuple[str, ...]] | None = None
    for event in events:
        if parts and (event.pause > 0 or event.replace):
            result.append(Bubble("\n".join(parts), pause, spread, replace, cards, drawing, scene))
            parts = []
        if not parts:
            pause, spread, replace = event.pause, event.spread, event.replace
            cards = tuple(event.data.get("cards", ()))
            drawing = event.data.get("drawing")
            scene = event.data.get("scene")
        parts.append(_styled(event, view))
    if parts:
        result.append(Bubble("\n".join(parts), pause, spread, replace, cards, drawing, scene))
    return result


def prompt_text(view: View | LiveView | SeanceView) -> str:
    prompt = f"👉 {view.prompt}" if view.prompt else ""
    if isinstance(view, LiveView) and view.screen:
        return f"{view.screen}\n\n{prompt}" if prompt else view.screen
    return prompt


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
    if report.outcome == "exposure" and report.legend_after < report.legend_before:
        lines.append("Это провал: клиент раскусил мадам, легенда салона стала меньше.")
    elif report.outcome == "exposure":
        lines.append("Клиент раскусил мадам, но шума не вышло: легенда салона цела.")
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
    lines = [
        f"🔮 Мадам {view.player} открывает салон. Сегодня {view.clients_total} клиента.",
        "",
        "Слушай, уточняй, гадай, сама решай, что озвучить, и делай предсказание.",
        f"«Клянусь!» приносит больше всего, но несбывшаяся клятва стоит денег и легенды салона: {_candles(view)}.",
        f"💰 В кассе: {rub(view.wallet)}" + (f" · {view.salon}" if view.salon else ""),
    ]
    if view.novice:
        lines.append(C.NOVICE_INTRO_LINE)
    lines += ["", "Кнопки жмёт только мадам. 15 минут без хода — салон закрывается."]
    return "\n".join(lines)


def render_abandoned(reason: str) -> str:
    return ABANDON_NOTES.get(reason, "Салон закрыт.")


def render_summary(view: View, group: bool = True) -> tuple[str, Rows]:
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
    return "\n".join(lines), again_keyboard(view.session_id, group)


# --- меню и карта дня -------------------------------------------------------

def render_menu(name: str, own: object, group: bool = True) -> str:
    """Вход в салон: что здесь делают и с чего начать. ``own`` — касса с полями balance и items."""
    lines = [
        "🔮 Салон «Гуру»",
        "",
        "Здесь гадают за деньги: слушаешь клиента, раскладываешь карты, решаешь, что озвучить, и делаешь предсказание. "
        "Сбылось — платят. Поклялась и промахнулась — платишь сама.",
        "",
        "🔮 Смена — три клиента со своими тайнами, играешь одна.",
    ]
    if group:
        lines.append("🤝 Живой приём — гадаешь человеку из чата, приговор выносит он.")
    else:
        lines.append("🤝 Живой приём — в группе: там гадают настоящим людям.")
    lines += ["🕯 Сеанс — один клиент и долгий приём: карты, ладонь, звёзды. Что скажешь, то он и сделает.",
              "🃏 Карта дня — одна в сутки, с чаевыми в кассу.", ""]
    lines.append(f"💰 Касса мадам {name}: {rub(own.balance)}" + (f" · {salon.describe(own.items)}" if own.items else ""))
    return "\n".join(lines)


def render_daily(name: str, daily: DailyCard, tip: int, streak: int, next_tip: int, balance: int) -> str:
    """Подпись под картой дня. Чаевые видны всем: это и есть повод вытянуть свою."""
    lines = [
        f"🃏 Карта дня для {name}: «{daily.card.name}»",
        f"Образы: {daily.card.symbols}.",
        "",
        f"🔮 {daily.prediction}",
        f"✨ {daily.advice}",
        "",
        f"🪙 Вселенная оставила на чай {rub(tip)}. В кассе: {rub(balance)}",
    ]
    if next_tip > tip:
        lines.append(f"🔥 Дней подряд: {streak}. Завтра чаевые будут {rub(next_tip)}.")
    else:
        lines.append(f"🔥 Дней подряд: {streak}.")
    return "\n".join(lines)


def render_daily_repeat(name: str, daily: DailyCard) -> str:
    return f"🃏 {name}, твоя карта дня уже вытянута: «{daily.card.name}». {daily.prediction} Новая будет завтра."


def render_daily_toast(daily: DailyCard) -> str:
    """Повтор по кнопке: всплывающая строка, а не сообщение в чат. У мессенджера на неё 200 знаков."""
    return f"Твоя карта дня уже вытянута: «{daily.card.name}». Новая будет завтра."


# --- сеанс ------------------------------------------------------------------

def render_seance_intro(view: SeanceView) -> str:
    return "\n".join((
        f"🕯 Мадам {view.host} принимает одного клиента. Торопиться некуда.",
        "",
        "Правильного ответа здесь нет. Слушай, рассматривай знаки и решай, что сказать и как: осторожно или уверенно. "
        "Клиент поступит по услышанному, а эпилог покажет, что из сказанного сбылось. Уклониться можно в любом гадании, "
        "но за туманом сюда не ходят.",
        "",
        "Кнопки жмёт только мадам. 15 минут без хода — сеанс прерывается.",
    ))


def render_seance(view: SeanceView) -> tuple[str, Rows]:
    """Текущий экран сеанса: то, что лежит на столе, вопрос к мадам и кнопки."""
    prompt = prompt_text(view)
    return (f"{view.screen}\n\n{prompt}" if view.screen else prompt), keyboard(view)


def render_seance_result(view: SeanceView, seen: int) -> str:
    """Итог сеанса: развязка, деньги, счётчик развязок этой истории и разбор, что сбылось и почему."""
    icon = CLIENT_STATE_ICONS.get(view.client_state, "🕯")
    lines = [
        f"🕯 Развязка: «{view.ending_title}» · {signed_rub(view.fee)}",
        f"{icon} {view.client_text}",
        f"💰 В кассе мадам {view.host}: {rub(view.wallet)}",
        f"Развязок этой истории открыто: {seen} из {view.endings_total}. Скажешь иначе — выйдет иначе.",
    ]
    if view.epilogue:
        lines += ["", view.epilogue]
    return "\n".join(lines)


def seance_done_keyboard() -> Rows:
    return [[(MENU_SEANCE_AGAIN, menu_data("seance"))], [(MENU_SHIFT, menu_data("shift")), (MENU_CARD, menu_data("card"))],
            [(MENU_SALON, menu_data("salon"))]]


def render_seance_abandoned(reason: str) -> str:
    return SEANCE_ABANDON_NOTES.get(reason, "Сеанс прерван.")


# --- живой приём ------------------------------------------------------------

def render_live_open(view: LiveView) -> str:
    return "\n".join((
        f"🔮 Мадам {view.host} принимает живых людей.",
        "",
        "Клиент тайно заполняет анкету и рассказывает в чате. Мадам связывает карты, осматривает ладонь или выбирает путь к сроку.",
        "Трактовку можно сказать версией, утверждением или оставить при себе. Клиент отвечает и оценивает весь сеанс. От приговора зависят деньги мадам.",
        "",
        "Кто первым нажмёт кнопку, тот и клиент.",
    ))


def render_intake(view: LiveView) -> tuple[str, Rows | None]:
    """Анкета клиента: одно сообщение со счётчиком. Ответов в нём нет, они тайна до приговора."""
    head = f"📇 Анкета: {view.client}"
    if view.phase != "intake":
        return f"{head}\n{C.LIVE_INTAKE_CLOSED_LINE}", None
    return f"{head}\nЗаполнено: {view.intake_filled} из {len(C.LIVE_INTAKE)}\n\n{prompt_text(view)}", keyboard(view)


def render_live_constructor(view: LiveView) -> tuple[str, Rows]:
    """Напутствие и всё произнесённое за сеанс, без тайной анкеты."""
    return f"🔮 Напутствие\n\n{prompt_text(view)}", keyboard(view)


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
    lines += [f"{place}. {wallet.name} — {rub(wallet.balance)}" + (f" · {salon.icons(wallet.items)}" if wallet.items else "")
              for place, wallet in enumerate(top, 1)]
    lines += ["", f"Твоя касса: {rub(own.balance)} · заработано {rub(own.earned)} · потеряно {rub(own.lost)}"]
    lines += [f"🏠 {salon.rank(len(own.items))} · потрачено в лавке {rub(own.spent)}", "Лавка: «гуру лавка». Кто что купил: «гуру салон»."]
    return "\n".join(lines)


def _gifts_of(player: object) -> tuple:
    return salon.owned_gifts(getattr(player, "gifts", ()))


def render_salons(players: list, own: object) -> str:
    """Что стоит в салонах этого чата и что приносит. ``players`` и ``own`` — записи с полями name, items и gifts."""
    equipped = [player for player in players if player.items]
    if not equipped:
        return "🏠 Пока никто ничего не купил. Лавка: «гуру лавка»."
    lines = ["🏠 Салоны мадам"]
    for player in equipped:
        gifts = _gifts_of(player)
        shelf = f" · 🎁 {len(gifts)}" if gifts else ""
        lines += ["", f"{player.name} · {salon.rank(len(player.items))} · {salon.icons(player.items)}{shelf}", " · ".join(salon.effects(player.items))]
    if own.items:
        lines += ["", "Твой салон:"]
        lines += [f"{item.emoji} {item.name} — {item.note}" for item in salon.owned_items(own.items)]
    lines += ["", "Заглянуть в салон: «гуру салон». В чужой — той же командой в ответ на сообщение мадам."]
    return "\n".join(lines)[:MESSAGE_LIMIT]


def render_salon_card(name: str, own: object, visitor: bool = False) -> str:
    """Подпись под рисунком салона: звание, что куплено, что это даёт и какие подарки стоят на полке."""
    items, gifts = salon.owned_items(own.items), _gifts_of(own)
    lines = [f"🏠 Салон мадам {name}", f"{salon.rank(len(items))} · куплено {len(items)} из {len(salon.ITEMS)}"]
    if items:
        lines += ["", salon.icons(own.items), "Что даёт: " + " · ".join(salon.effects(own.items)) + "."]
    elif visitor:
        lines += ["", "Пока тут только стол, стул и плафон от люстры."]
    else:
        lines += ["", "Пока тут только стол, стул и плафон от люстры. Обставить: «гуру лавка»."]
    if gifts:
        lines += ["", f"🎁 Подарки клиентов, {len(gifts)} из {len(salon.GIFTS)}: " + ", ".join(f"{gift.emoji} {gift.name}" for gift in gifts) + "."]
    else:
        lines += ["", "🎁 Полка для подарков пуста: их оставляют клиенты, которым сбылось."]
    return "\n".join(lines)


def render_shelf(name: str, own: object) -> str:
    """Полка подарков вблизи: что стоит, от кого и с какой историей. Пустое место называет только того, кто ещё не дарил."""
    slots = salon.shelf(getattr(own, "gifts", ()))
    count = sum(owned for _, owned in slots)
    blocks = [f"🎁 Полка мадам {name}: {count} из {len(slots)}"]
    waiting: list[str] = []  # пустые места подряд идут одним абзацем
    for gift, owned in slots:
        if not owned:
            waiting.append(f"▫️ от {gift.giver}: ещё не подарили")
            continue
        if waiting:
            blocks.append("\n".join(waiting))
            waiting = []
        blocks.append(f"{gift.emoji} {gift.name[0].upper()}{gift.name[1:]} · от {gift.giver}\n{gift.story}")
    if waiting:
        blocks.append("\n".join(waiting))
    blocks.append(salon.GIFTS_COMPLETE_LINE if count == len(slots) else SHELF_HINT)
    return "\n\n".join(blocks)[:MESSAGE_LIMIT]


# --- лавка ------------------------------------------------------------------

def render_shop(name: str, balance: int, owned: list[str]) -> str:
    """Витрина: что есть, сколько стоит и что даёт."""
    lines = [f"🏪 Лавка салона мадам {name}", f"🏠 {salon.rank(len(owned))} · 💰 В кассе: {rub(balance)}"]
    for group, title in salon.GROUP_TITLES.items():
        lines += ["", title]
        for item in salon.ITEMS.values():
            if item.group == group:
                state = "✅" if item.key in owned else rub(item.price)
                lines.append(f"{item.emoji} {item.name} · {state} · {item.note}")
    lines += ["", "Покупка остаётся навсегда и работает со следующей партии.",
              "В живом приёме работают надбавка к гонорару, вопрос от тетрадки и повтор гадания от шара. "
              "Точность инструментов относится к одиночной смене; тайную анкету они не раскрывают."]
    return "\n".join(lines)[:MESSAGE_LIMIT]


def render_purchase(item: salon.Item, name: str, balance: int) -> str:
    """Реплика в чат: покупку должны видеть все, а не только нажавший."""
    return f"{salon.PURCHASE_LINES[item.key].format(name=name)}\n{item.note.capitalize()}. 💰 В кассе: {rub(balance)}"


def shop_keyboard(owned: list[str], owner: int) -> Rows:
    """Товары, которых ещё нет, и под ними остальные разделы меню."""
    buttons = [(f"{item.emoji} {item.short}", buy_data(item.key, owner)) for item in salon.ITEMS.values() if item.key not in owned]
    return section_keyboard("shop", [buttons[index:index + 2] for index in range(0, len(buttons), 2)])
