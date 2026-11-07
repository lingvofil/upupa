"""Тексты сообщений салона и раскладка кнопок — как данные, без aiogram.

Callback-данные: ``guru:<session>:<клиент>:<ход клиента>:<приём>``. Номер клиента
и хода защищают от старых кнопок и двойных нажатий: кнопка действует, только
пока партия стоит ровно в том состоянии, для которого её нарисовали.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from games.guru.engine import (
    IDLE_TIMEOUT_S,
    MOVES,
    VAGUE_TRUST_CAP,
    Client,
    Salon,
    ritual_chance,
    ritual_price,
    rub,
    salon_title,
    specific_chance,
)

CALLBACK_PREFIX = "guru:"
AGAIN_CALLBACK = "guru:again"

MOVE_LABELS: dict[str, str] = {
    "horoscope": "♈ Гороскоп",
    "rainbow": "🌈 И так, и эдак",
    "shotgun": "🎯 Пальнуть дробью",
    "leading": "❓ Наводящий вопрос",
    "specific": "🔮 Сказать конкретно",
    "bill": "💰 Взять за сеанс",
    "ritual": "🕯 Впарить ритуал",
}
KEYBOARD_LAYOUT: tuple[tuple[str, ...], ...] = (
    ("horoscope", "rainbow"),
    ("shotgun", "leading"),
    ("specific",),
    ("bill", "ritual"),
)

RESULT_LABELS: dict[str, str] = {
    "paid": "💰 Сеанс оплачен",
    "walked_out": "⏳ Время вышло, сеанс оплачен",
    "ritual_sold": "🕯 Ритуал продан",
    "ritual_failed": "🕯 Ритуал не впарился, клиент сбежал",
    "exposed": "💥 РАЗОБЛАЧЕНИЕ",
    "left_angry": "🚪 Клиент ушёл, хлопнув дверью",
    "mob": "🔥 Репутация на нуле",
}

ABANDON_LABELS: dict[str, str] = {
    "timeout": "💤 Мадам уснула над хрустальным шаром. Салон закрыт.",
    "quit": "🚪 Мадам повесила табличку «Закрыто». Салон закрыт.",
    "restart": "⚡ Во всём районе вырубили свет. Салон закрыт.",
}

_CALLBACK_RE = re.compile(r"^guru:([0-9a-f]{1,32}):(\d+):(\d+):([a-z_]+)$")


@dataclass(frozen=True)
class SalonCallback:
    action: str  # "move" | "again"
    session_id: str | None = None
    client_no: int | None = None
    turn: int | None = None
    move: str | None = None


def move_callback(salon: Salon, move: str) -> str:
    return f"{CALLBACK_PREFIX}{salon.session_id}:{salon.client_no}:{salon.client.turn}:{move}"


def parse_callback(data: str | None) -> SalonCallback | None:
    if data == AGAIN_CALLBACK:
        return SalonCallback("again")
    match = _CALLBACK_RE.match(data or "")
    if match is None or match.group(4) not in MOVES:
        return None
    session_id, client_no, turn, move = match.groups()
    return SalonCallback("move", session_id, int(client_no), int(turn), move)


def is_current(salon: Salon, callback: SalonCallback) -> bool:
    """Кнопка нарисована для текущего состояния партии."""
    return (
        not salon.finished
        and callback.action == "move"
        and callback.session_id == salon.session_id
        and callback.client_no == salon.client_no
        and callback.turn == salon.client.turn
    )


def client_keyboard(salon: Salon) -> list[list[tuple[str, str]]]:
    return [[(MOVE_LABELS[move], move_callback(salon, move)) for move in row] for row in KEYBOARD_LAYOUT]


def again_keyboard() -> list[list[tuple[str, str]]]:
    return [[("🔁 Ещё партию", AGAIN_CALLBACK)]]


def _percent(value: float) -> int:
    return int(round(value * 100))


def render_rules(salon: Salon) -> str:
    minutes = max(1, IDLE_TIMEOUT_S // 60)
    return "\n".join(
        (
            f"🔮 Мадам {salon.player_name} открывает салон. Сегодня {salon.clients_total} клиентов.",
            "",
            f"♈ Гороскоп — безопасно, доверие растёт, но только до {VAGUE_TRUST_CAP}",
            "🌈 И так, и эдак — немного доверия и шанс на зацепку",
            "🎯 Дробью, ❓ наводящий вопрос — добывают зацепки. Скептик всё записывает",
            "🔮 Конкретно — тратит зацепку. Попала: чаевые, репутация, сарафан. Мимо: позор",
            "💰 Взять за сеанс — платят по доверию",
            "🕯 Ритуал — дорого, но берут, только если уже верят",
            "",
            f"Кнопки жмёт только мадам. {minutes} мин без хода — салон закрывается.",
        )
    )


def _totals_line(salon: Salon) -> str:
    return f"Касса {rub(salon.money)} · Репутация {salon.reputation} · Сарафан {salon.buzz}"


def render_client(salon: Salon, client: Client, *, client_no: int, note: str | None = None) -> str:
    """Сообщение клиента: шапка, переписка, статус или итог приёма."""
    kind = client.kind
    lines = [
        f"🔮 Салон мадам {salon.player_name} · клиент {client_no + 1}/{salon.clients_total}",
        f"{kind.emoji} {kind.title} — {client.name}",
        client.intro,
        "",
        f"👁 Видно сразу: {client.facts[0].clue}",
    ]
    if client.lines:
        lines.append("")
        lines.extend(client.lines)
    lines.append("")

    if client.done:
        lines.append(RESULT_LABELS.get(client.result or "", client.result or ""))
        lines.append(f"С клиента: {rub(client.paid + client.tips)}")
    elif note is None:
        status = f"Доверие {client.trust} · Зацепки {client.fresh_clues} · Терпение {client.patience}"
        if kind.skeptic:
            status += f" · Подозрение {client.suspicion}/100"
        lines.append(status)
        lines.append(
            f"🔮 Попадание ~{_percent(specific_chance(client))}% · "
            f"🕯 Ритуал ~{_percent(ritual_chance(client))}% за {rub(ritual_price(salon, client))}"
        )
    if note:
        lines.append(note)
    lines.append(_totals_line(salon))
    return "\n".join(lines)


def render_summary(salon: Salon) -> str:
    if salon.finish_reason == "closed":
        header = f"🔥 Салон мадам {salon.player_name} сожгли: репутация на нуле."
    elif salon.finish_reason in ABANDON_LABELS:
        header = f"🏚 Салон мадам {salon.player_name} брошен."
    else:
        header = f"🏁 Салон мадам {salon.player_name} закрыт на сегодня."
    return "\n".join(
        (
            header,
            "",
            f"Касса: {rub(salon.money)}",
            f"Репутация: {salon.reputation}/100",
            f"Клиентов: {salon.served}/{salon.clients_total} · попаданий {salon.hits} · промахов {salon.misses}",
            f"Ритуалов продано: {salon.rituals_sold}",
            f"Разоблачений: {salon.exposures}",
            f"Звание: {salon_title(salon)}",
        )
    )


def render_abandoned_client(salon: Salon) -> str:
    """Текущий клиент на момент, когда партию бросили."""
    note = ABANDON_LABELS.get(salon.finish_reason or "", "Салон закрыт.")
    return render_client(salon, salon.client, client_no=salon.client_no, note=note)
