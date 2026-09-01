"""Presentation helpers for World of Upupa state cards."""

from __future__ import annotations

from features.world.ledger import WorldDetails
from features.world.models import WorldProfile, WorldState


def _ids(states: tuple[WorldState, ...]) -> str:
    return ", ".join(f"№{state.world_id}" for state in states) or "—"


def format_world_profile(
    profile: WorldProfile,
    population: int | None = None,
    *,
    details: WorldDetails | None = None,
    authority: int | None = None,
    most_active: str | None = None,
    alliance_names: dict[int, str] | None = None,
    identity_rationale: str | None = None,
) -> str:
    population_text = str(population) if population is not None else "неизвестно"
    lines = [
        f"🏳 Государство №{profile.state.world_id}",
        profile.state.title,
        f"👥 Долбоебов: {population_text}",
        f"🏛 Основано: {profile.state.created_at.strftime('%d.%m.%Y')}",
    ]
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
    if most_active:
        lines.append(f"🗣 Самый активный долбоеб за 7 дней: {most_active}")
    if authority is not None:
        lines.append(f"🌐 Международный авторитет: {authority}")

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
