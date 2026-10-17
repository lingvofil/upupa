"""Text presentation for World of Upupa screens (cards, diplomacy, lists, rating)."""

from __future__ import annotations

from datetime import datetime, timezone

from features.world.authority import AuthorityBreakdown
from features.world.ledger import WorldDetails
from features.world.models import WorldProfile, WorldState


TELEGRAM_TEXT_LIMIT = 4096


def _ids(states: tuple[WorldState, ...]) -> str:
    return ", ".join(f"№{state.world_id}" for state in states) or "—"


def short_title(title: str, limit: int = 26) -> str:
    compact = " ".join((title or "").split())
    return compact if len(compact) <= limit else compact[: limit - 1].rstrip() + "…"


def state_label(state: WorldState | None, world_id: int | None = None) -> str:
    if state is None:
        return f"№{world_id}" if world_id is not None else "неизвестное государство"
    return f"№{state.world_id} — {state.title}"


def authority_total(authority: AuthorityBreakdown | int | None) -> int | None:
    if authority is None:
        return None
    if isinstance(authority, AuthorityBreakdown):
        return authority.total
    return int(authority)


def format_authority_line(authority: AuthorityBreakdown | int | None, *, detailed: bool = False) -> list[str]:
    total = authority_total(authority)
    if total is None:
        return []
    lines = [f"🌐 Международный авторитет: {total}"]
    if detailed and isinstance(authority, AuthorityBreakdown):
        parts = authority.explain()
        if parts:
            lines.append(f"   база {authority.base} · " + " · ".join(parts))
    return lines


def format_world_profile(
    profile: WorldProfile,
    population: int | None = None,
    *,
    details: WorldDetails | None = None,
    authority: AuthorityBreakdown | int | None = None,
    detailed_authority: bool = False,
    most_active: str | None = None,
    alliance_names: dict[int, str] | None = None,
    identity_rationale: str | None = None,
    bloc_line: str | None = None,
    symbol_line: str | None = None,
    tourism_line: str | None = None,
) -> str:
    population_text = str(population) if population is not None else "неизвестно"
    lines = [
        f"🏳 Государство №{profile.state.world_id}",
        profile.state.title,
        f"👥 Долбоебов: {population_text}",
        f"🏛 Основано: {profile.state.created_at.strftime('%d.%m.%Y')}",
    ]
    if bloc_line:
        lines.append(bloc_line)
    if details is not None:
        lines.extend(
            [
                f"🏛 Государственный строй: {details.government_form}",
                f"🌦 Климат: {details.climate}",
                f"☠️ Главная угроза: {details.main_threat}",
            ]
        )
        if identity_rationale:
            lines.append(f"🔎 Основание: {identity_rationale}")
        if details.ambassador_name:
            lines.append(f"🎩 Посол: {details.ambassador_name}")
    if symbol_line:
        lines.append(symbol_line)
    if most_active:
        lines.append(f"🗣 Самый активный долбоеб за 7 дней: {most_active}")
    lines.extend(format_authority_line(authority, detailed=detailed_authority))
    if tourism_line:
        lines.append(tourism_line)

    lines.extend(["", "Дипломатические отношения:"])
    if profile.allies:
        names = alliance_names or {}
        allies = []
        for state in profile.allies:
            suffix = f" «{names[state.world_id]}»" if state.world_id in names else ""
            allies.append(f"№{state.world_id}{suffix}")
        lines.append(f"🤝 Союзники: {', '.join(allies)}")
    else:
        lines.append("🤝 Союзники: —")
    lines.extend(
        [
            f"⚔️ Война: {_ids(profile.wars)}",
            f"😐 Нейтральные: {_ids(profile.neutral)}",
        ]
    )
    if profile.inactive_allies:
        lines.append(f"⏸ Союзы вне мира: {_ids(profile.inactive_allies)}")
    if profile.inactive_wars:
        lines.append(f"⏸ Войны вне мира: {_ids(profile.inactive_wars)}")
    return "\n".join(lines)


def format_diplomacy(
    profile: WorldProfile,
    alliance_names: dict[int, str] | None = None,
    *,
    battle_lines: list[str] | None = None,
    sanctions_text: str | None = None,
) -> str:
    lines = [f"🌍 Дипломатия государства №{profile.state.world_id}"]
    names = alliance_names or {}
    if profile.allies:
        lines.append("\n🤝 Союзники:")
        for state in profile.allies:
            suffix = f" — союз «{names[state.world_id]}»" if state.world_id in names else ""
            lines.append(f"№{state.world_id} — {state.title}{suffix}")
    else:
        lines.append("\n🤝 Союзников пока нет.")

    if profile.wars:
        lines.append("\n⚔️ Война:")
        lines.extend(f"№{state.world_id} — {state.title}" for state in profile.wars)
    else:
        lines.append("\n⚔️ Ни с кем не воюем.")
    if battle_lines:
        lines.append("\n🥊 Идущие битвы:")
        lines.extend(battle_lines)

    if profile.neutral:
        lines.append("\n😐 Нейтральные:")
        lines.extend(f"№{state.world_id} — {state.title}" for state in profile.neutral)
    if profile.inactive_allies:
        lines.append("\n⏸ Сохранённые союзы с выключенными государствами:")
        lines.extend(f"№{state.world_id} — {state.title}" for state in profile.inactive_allies)
    if profile.inactive_wars:
        lines.append("\n⏸ Сохранённые войны с выключенными государствами:")
        lines.extend(f"№{state.world_id} — {state.title}" for state in profile.inactive_wars)
    if sanctions_text:
        lines.append("")
        lines.append(sanctions_text)
    return "\n".join(lines)


def format_hub_text(state: WorldState, authority: AuthorityBreakdown | int | None) -> str:
    total = authority_total(authority)
    return (
        "🌍 Мир Упупы\n\n"
        f"Вы — государство №{state.world_id} «{state.title}».\n"
        f"Международный авторитет: {total if total is not None else 50}.\n\n"
        "Куда полезем?"
    )


def format_rating(
    states: tuple[WorldState, ...],
    authority: dict[int, AuthorityBreakdown],
    *,
    tourism: dict[int, tuple[float, int]] | None = None,
    battle_wins: dict[int, int] | None = None,
    current_world_id: int | None = None,
    limit: int = 15,
) -> str:
    if not states:
        return "🏆 Рейтинг пуст: государств ещё нет."
    ranked = sorted(
        states,
        key=lambda state: (-(authority.get(state.world_id).total if authority.get(state.world_id) else 0), state.world_id),
    )
    lines = ["🏆 Рейтинг государств Мира Упупы", ""]
    medals = {0: "🥇", 1: "🥈", 2: "🥉"}
    for index, state in enumerate(ranked[:limit]):
        breakdown = authority.get(state.world_id)
        total = breakdown.total if breakdown else 0
        prefix = medals.get(index, f"{index + 1}.")
        marker = " ← вы" if state.world_id == current_world_id else ""
        extra = []
        if tourism and state.world_id in tourism:
            avg, votes = tourism[state.world_id]
            extra.append(f"🧳 ★{avg:.1f}")
        if battle_wins and battle_wins.get(state.world_id):
            extra.append(f"🥊 {battle_wins[state.world_id]} побед")
        suffix = f" · {' · '.join(extra)}" if extra else ""
        lines.append(f"{prefix} №{state.world_id} {short_title(state.title, 28)} — {total}{suffix}{marker}")
    if current_world_id is not None and all(state.world_id != current_world_id for state in ranked[:limit]):
        position = next(
            (index for index, state in enumerate(ranked) if state.world_id == current_world_id), None
        )
        if position is not None:
            breakdown = authority.get(current_world_id)
            lines.append("…")
            lines.append(f"{position + 1}. №{current_world_id} — {breakdown.total if breakdown else 0} ← вы")
    lines.append("")
    lines.append("Авторитет = база союзов и войн ± давность союзов, блоки, санкции, предательства, битвы, туризм, дары и суд.")
    return "\n".join(lines)


def split_text(text: str, limit: int = TELEGRAM_TEXT_LIMIT - 96) -> list[str]:
    """Split by lines so long lists never hit Telegram's 4096-character limit."""
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    current: list[str] = []
    length = 0
    for line in text.splitlines():
        while len(line) > limit:
            if current:
                chunks.append("\n".join(current))
                current, length = [], 0
            chunks.append(line[:limit])
            line = line[limit:]
        if current and length + len(line) + 1 > limit:
            chunks.append("\n".join(current))
            current, length = [], 0
        current.append(line)
        length += len(line) + 1
    if current:
        chunks.append("\n".join(current))
    return chunks


def human_until(moment: datetime, now: datetime | None = None) -> str:
    current = now or datetime.now(timezone.utc)
    delta = moment - current
    seconds = max(0, int(delta.total_seconds()))
    hours, rest = divmod(seconds, 3600)
    minutes = rest // 60
    if hours:
        return f"{hours} ч {minutes} мин"
    return f"{max(1, minutes)} мин"
