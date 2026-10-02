"""Explicit stat allocations chosen by the player, without an AI request."""
from __future__ import annotations

ABILITIES = ("STR", "DEX", "CON", "INT", "WIS", "CHA")
ARRAY = (16, 14, 13, 12, 10, 8)
ARCHETYPES = {
    "сила": ("STR", "CON", "DEX", "WIS", "CHA", "INT"),
    "ловкость": ("DEX", "CON", "WIS", "INT", "CHA", "STR"),
    "разум": ("INT", "DEX", "CON", "WIS", "CHA", "STR"),
    "мудрость": ("WIS", "CON", "DEX", "INT", "CHA", "STR"),
    "харизма": ("CHA", "DEX", "CON", "WIS", "INT", "STR"),
    "стойкость": ("CON", "STR", "WIS", "DEX", "CHA", "INT"),
}


def stats_for_profile(profile):
    profile = profile if isinstance(profile, dict) else {}
    manual = profile.get("stat_allocation")
    if (isinstance(manual, dict) and set(manual) == set(ABILITIES)
            and all(type(v) is int for v in manual.values()) and sorted(manual.values()) == sorted(ARRAY)):
        return dict(manual)
    order = ARCHETYPES.get(profile.get("archetype"), ABILITIES)
    return dict(zip(order, ARRAY))


def choose_archetype(session, actor_id, choice):
    """Only pre-game selection can change a stat allocation."""
    if getattr(session, "state", "") != "LOBBY" or str(actor_id) not in (getattr(session, "participants", {}) or {}):
        raise ValueError("Роль выбирает участник в лобби до старта игры.")
    choice = str(choice).strip().casefold()
    if choice not in ARCHETYPES:
        raise ValueError("Выбери: сила, ловкость, разум, мудрость, харизма или стойкость.")
    profile = session.character_profiles.setdefault(str(actor_id), {})
    profile["archetype"] = choice
    profile.pop("stat_allocation", None)
    return stats_for_profile(profile)
