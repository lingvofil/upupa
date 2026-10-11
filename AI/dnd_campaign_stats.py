"""Per-hero campaign statistics and the deterministic awards of the final report.

XP is shared by the party, so nothing recorded who actually hit, rolled a
natural 20 or tanked the damage. The mechanics that already resolve those
events call ``record`` directly (no wrapper layer); the final report turns the
numbers into a few awards without any AI call.

``campaign_stats`` is presentation data: it is not one of the event journal's
canonical fields and never changes ``state_revision``.
"""
from __future__ import annotations

import copy
import logging
from collections import Counter

FIELD = "campaign_stats"
COUNTERS = ("damage", "hits", "misses", "kills", "stunts", "rolls", "successes", "nat20", "nat1", "damage_taken")

# (counter, emoji, title, description) — the hero with the unique top value wins.
AWARDS = (
    ("damage", "🗡", "Мясник", "больше всех урона: {value}"),
    ("kills", "💀", "Добивала", "последний удар по {value} врагам"),
    ("nat20", "🎲", "Любимчик кубов", "натуральных двадцаток: {value}"),
    ("nat1", "🤡", "Проклятый кубик", "натуральных единиц: {value}"),
    ("damage_taken", "🛡", "Живой щит", "принял на себя {value} урона"),
    ("items", "🎒", "Барахольщик", "унёс новых вещей: {value}"),
    ("fame", "🏷", "Народная молва", "новых слухов о себе: {value}"),
)
MAX_AWARDS = len(AWARDS)


def ensure(session) -> dict:
    stats = getattr(session, FIELD, None)
    if not isinstance(stats, dict):
        stats = {}
        setattr(session, FIELD, stats)
    return stats


def record(session, user_id, **deltas) -> None:
    """Add integer deltas to one hero's counters; never breaks the game flow."""
    if session is None or user_id is None:
        return
    try:
        row = ensure(session).setdefault(str(int(user_id)), {})
        for name, value in deltas.items():
            if name in COUNTERS and value:
                row[name] = int(row.get(name, 0)) + int(value)
    except Exception:
        logging.exception("DnD campaign stats record failed chat_id=%s", getattr(session, "chat_id", None))


def record_d20(session, user_id, natural, *, success=None, total=None, dc=None) -> None:
    if success is None and total is not None and dc is not None:
        try:
            success = int(total) >= int(dc)
        except (TypeError, ValueError):
            success = None
    try:
        natural = int(natural)
    except (TypeError, ValueError):
        return
    record(session, user_id, rolls=1, nat20=natural == 20, nat1=natural == 1, successes=bool(success))


def _gained_items(session, key) -> int:
    from AI.dnd_adventure import _items

    current = _items((getattr(session, "inventories", {}) or {}).get(key))
    initial = _items((getattr(session, "initial_inventories", {}) or {}).get(key))
    return sum((current - initial).values())


def _fame(session, key) -> int:
    rows = (getattr(session, "reputations", {}) or {}).get(key) or []
    start = (getattr(session, "campaign_stats_start", {}) or {}).get(key, 0)
    return max(0, len(rows) - int(start))


def totals(session) -> dict[str, Counter]:
    stats = getattr(session, FIELD, None) or {}
    result = {}
    for key, player in (getattr(session, "participants", {}) or {}).items():
        if not isinstance(player, dict):
            continue
        row = Counter({name: int(value) for name, value in (stats.get(str(key)) or {}).items() if name in COUNTERS})
        row["items"] = _gained_items(session, str(key))
        row["fame"] = _fame(session, str(key))
        result[str(key)] = row
    return result


def awards(session) -> list[str]:
    from AI.dnd_adventure import is_dead

    rows = totals(session)
    names = {key: (player.get("name") or key) for key, player in (getattr(session, "participants", {}) or {}).items()
             if isinstance(player, dict)}
    lines = []
    for counter, emoji, title, description in AWARDS:
        values = {key: row[counter] for key, row in rows.items() if row[counter] > 0}
        if not values:
            continue
        best = max(values.values())
        winners = [key for key, value in values.items() if value == best]
        if len(winners) != 1 or (len(rows) > 1 and best <= 0):
            continue
        lines.append(f"{emoji} «{title}» — {names[winners[0]]}: {description.format(value=best)}")
        if len(lines) >= MAX_AWARDS:
            break
    for key in rows:
        sheet = (getattr(session, "character_sheets", {}) or {}).get(key, {})
        if is_dead(sheet):
            lines.append(f"⚰️ «Пал смертью храбрых» — {names.get(key, key)}")
    return lines


def report_section(session) -> str:
    lines = awards(session)
    return ("🏆 Награды партии\n" + "\n".join(lines)) if lines else ""


def start_campaign(session) -> None:
    """Fresh counters for a new adventure; reputation is counted from here."""
    setattr(session, FIELD, {})
    session.campaign_stats_start = {str(key): len(rows or []) for key, rows in
                                    (getattr(session, "reputations", {}) or {}).items()}


def install_dnd_campaign_stats(*, state_policy) -> None:
    from AI.dnd_adventure import register_report_section

    state_policy.add_ensure_hook(ensure)
    state_policy.add_state_field(FIELD, lambda s: copy.deepcopy(getattr(s, FIELD, None) or {}))
    state_policy.add_state_field("campaign_stats_start", lambda s: copy.deepcopy(getattr(s, "campaign_stats_start", None) or {}))
    register_report_section(report_section)


__all__ = ["awards", "install_dnd_campaign_stats", "record", "record_d20", "report_section", "start_campaign", "totals"]
