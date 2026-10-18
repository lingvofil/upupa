"""Hero experience and levels for participant-mode DnD.

Milestone XP is derived from canonical state changes, never from prose:
narrated scenes, overcome enemies, completed progress clocks, accepted growth
marks and the mission outcome. Accounting uses
watermarks stored in the same canonical state, so a durable replay that
restores the pre-apply snapshot also restores the XP bookkeeping and nothing
is ever awarded twice.

Progress persists across campaigns in the campaign archive for the same living
hero (same profile). A dead hero or a newly created profile starts at level 1.
"""
from __future__ import annotations

import copy
import logging

ABILITY_KEYS = ("STR", "DEX", "CON", "INT", "WIS", "CHA")
ABILITY_LABELS = {"STR": "Сила", "DEX": "Ловкость", "CON": "Телосложение",
                  "INT": "Интеллект", "WIS": "Мудрость", "CHA": "Харизма"}
# Calibrated pace (typical party mix): ~1 short adventure (12 scenes, ~290 XP)
# to reach level 2, ~8 short / ~3 long adventures to level 5, ~33 short / ~12
# long to level 10. Step to the next level grows by ~200 XP each time.
LEVEL_THRESHOLDS = (0, 250, 700, 1350, 2200, 3250, 4500, 6000, 7700, 9600)
MAX_LEVEL = len(LEVEL_THRESHOLDS)
LEVEL_TITLES = {
    1: "Новичок", 2: "Бывалый", 3: "Закалённый", 4: "Ветеран", 5: "Гроза таверн",
    6: "Гроза окрестностей", 7: "Живая легенда", 8: "Герой баллад", 9: "Почти бог", 10: "Легенда Упупы",
}
ASI_LEVELS = (4, 8)
ASI_AMOUNT = 2
ABILITY_CAP = 20

SCENE_XP = 5
CLOCK_XP = 15
GROWTH_XP = 10
MISSION_XP = {"success": 80, "failure": 30}
ENEMY_XP = {"LOW": 10, "MEDIUM": 20, "HIGH": 40, "DEADLY": 70}
MIN_SCENE_STORY_CHARS = 40


# ---------------------------------------------------------------- level math

def level_for_xp(xp) -> int:
    xp = max(0, int(xp or 0))
    level = 1
    for index, threshold in enumerate(LEVEL_THRESHOLDS, start=1):
        if xp >= threshold:
            level = index
    return min(MAX_LEVEL, level)


def next_threshold(level) -> int | None:
    level = int(level or 1)
    return LEVEL_THRESHOLDS[level] if level < MAX_LEVEL else None


def attack_bonus_for_level(level) -> int:
    level = int(level or 1)
    return 2 if level >= 9 else 1 if level >= 5 else 0


def ability_charges_for_level(level) -> int:
    """Uses per adventure of an archetype ability (see dnd_archetype_abilities)."""
    level = int(level or 1)
    return 1 + (level >= 5) + (level >= 9)


def hp_per_level(con_score) -> int:
    """Max HP per level above 1. Enemy damage does not scale with level, so
    growth stays modest: CON 8-9 → +1, 10-13 → +2, 14-17 → +3, 18-20 → +4."""
    con_mod = (int(con_score or 10) - 10) // 2
    return max(1, 2 + con_mod // 2)


def hp_bonus_for_level(level, con_score) -> int:
    return hp_per_level(con_score) * (max(1, int(level or 1)) - 1)


def asi_count_for_level(level) -> int:
    return sum(1 for value in ASI_LEVELS if int(level or 1) >= value)


def progress_bar(xp, level, width=10) -> str:
    level = int(level or 1)
    upper = next_threshold(level)
    if upper is None:
        return "▰" * width
    lower = LEVEL_THRESHOLDS[level - 1]
    filled = int(round(width * (int(xp) - lower) / max(1, upper - lower)))
    filled = max(0, min(width, filled))
    return "▰" * filled + "▱" * (width - filled)


# ---------------------------------------------------------------- state

def _default_progress() -> dict:
    return {"xp": 0, "level": 1, "ability_increases": {}, "asi_pending": 0,
            "campaign_xp": 0, "start_level": 1}


def _clean_progress(raw) -> dict:
    row = _default_progress()
    if not isinstance(raw, dict):
        return row
    try:
        row["xp"] = max(0, int(raw.get("xp") or 0))
    except (TypeError, ValueError):
        row["xp"] = 0
    row["level"] = level_for_xp(row["xp"])
    increases = raw.get("ability_increases") if isinstance(raw.get("ability_increases"), dict) else {}
    row["ability_increases"] = {
        key: max(0, int(value)) for key, value in increases.items()
        if key in ABILITY_KEYS and str(value).lstrip("-").isdigit() and int(value) > 0
    }
    spent = sum(row["ability_increases"].values()) // ASI_AMOUNT
    try:
        pending = int(raw.get("asi_pending") or 0)
    except (TypeError, ValueError):
        pending = 0
    # Never more pending choices than the level actually grants.
    row["asi_pending"] = max(0, min(pending, asi_count_for_level(row["level"]) - spent))
    for key in ("campaign_xp", "start_level"):
        try:
            row[key] = max(0, int(raw.get(key) or row[key]))
        except (TypeError, ValueError):
            pass
    return row


def ensure(session) -> None:
    if not isinstance(getattr(session, "hero_progress", None), dict):
        session.hero_progress = {}
    if not isinstance(getattr(session, "progress_marks", None), dict):
        session.progress_marks = {}


def _participant_keys(session) -> list[str]:
    keys = []
    for key, row in (getattr(session, "participants", {}) or {}).items():
        if isinstance(row, dict) and row.get("user_id") is not None:
            keys.append(str(int(row["user_id"])))
        elif str(key).lstrip("-").isdigit():
            keys.append(str(int(key)))
    return keys


def _name(session, key) -> str:
    row = (getattr(session, "participants", {}) or {}).get(str(key)) or {}
    return row.get("name") or f"Герой {key}"


def _alive_active(session, key) -> bool:
    row = (getattr(session, "participants", {}) or {}).get(str(key)) or {}
    if isinstance(row, dict) and row.get("active", True) is False:
        return False
    sheet = (getattr(session, "character_sheets", {}) or {}).get(str(key))
    if isinstance(sheet, dict):
        try:
            hp = int(sheet.get("hp", 1))
        except (TypeError, ValueError):
            hp = 1
        if str(sheet.get("status") or "") == "dead" or hp <= 0:
            return False
    return True


def archived_progress(session, user_id) -> dict | None:
    """Progress of the same living hero from the campaign archive, if any."""
    from AI import dnd_campaign as campaign
    from AI.dnd_death_legacy import history_is_dead

    try:
        history = campaign._player_history(session.chat_id, int(user_id)) or {}
    except Exception:
        return None
    if not history or not isinstance(history.get("progress"), dict):
        return None
    if history_is_dead(campaign, session.chat_id, int(user_id)):
        return None
    current = (getattr(session, "character_profiles", {}) or {}).get(str(int(user_id))) or {}
    if current and history.get("profile") and current != history.get("profile"):
        return None
    return _clean_progress(history["progress"])


def hero_progress(session, user_id, *, create=True) -> dict | None:
    ensure(session)
    key = str(int(user_id))
    row = session.hero_progress.get(key)
    if isinstance(row, dict):
        return row
    if not create:
        return archived_progress(session, key)
    row = archived_progress(session, key) or _default_progress()
    row["campaign_xp"] = 0
    row["start_level"] = row["level"]
    session.hero_progress[key] = row
    return row


def level_of(session, user_id) -> int:
    row = hero_progress(session, user_id, create=False)
    return int(row["level"]) if row else 1


def attack_bonus(session, user_id) -> int:
    return attack_bonus_for_level(level_of(session, user_id))


def ability_increases(session, user_id) -> dict[str, int]:
    row = hero_progress(session, user_id, create=False)
    return dict(row.get("ability_increases") or {}) if row else {}


# ---------------------------------------------------------------- sheet sync

def sync_sheet(session, user_id) -> bool:
    """Apply level HP to the live combat sheet; ability increases flow through
    the artifact-stats effective-stat sync (registered bonus source)."""
    key = str(int(user_id))
    sheet = (getattr(session, "character_sheets", {}) or {}).get(key)
    row = (getattr(session, "hero_progress", {}) or {}).get(key)
    if not isinstance(sheet, dict) or not isinstance(row, dict):
        return False
    base = sheet.get("base_stats") if isinstance(sheet.get("base_stats"), dict) else sheet.get("stats") or {}
    con = min(ABILITY_CAP, int(base.get("CON", 10) or 10) + int((row.get("ability_increases") or {}).get("CON", 0)))
    desired = hp_bonus_for_level(row["level"], con)
    try:
        applied = int(sheet.get("level_hp_bonus") or 0)
        max_hp = int(sheet.get("max_hp") or 0)
        hp = int(sheet.get("hp") or 0)
    except (TypeError, ValueError):
        return False
    delta = desired - applied
    if not delta:
        return False
    sheet["max_hp"] = max(1, max_hp + delta)
    alive = str(sheet.get("status") or "alive") != "dead" and hp > 0
    if alive:
        # Levelling up heals by the gained amount; never above the new maximum.
        sheet["hp"] = max(1, min(sheet["max_hp"], hp + max(0, delta)))
    sheet["level_hp_bonus"] = desired
    return True


def sync_party(session) -> bool:
    changed = False
    for key in list((getattr(session, "character_sheets", {}) or {}).keys()):
        if str(key).lstrip("-").isdigit():
            hero_progress(session, key)
            changed = sync_sheet(session, key) or changed
    return changed


def _sync_stats(session, user_id) -> None:
    try:
        from AI.dnd_artifact_stats import sync_character_sheet
        sync_character_sheet(session, int(user_id))
    except Exception:
        logging.exception("DnD progression stat sync failed user_id=%s", user_id)


# ---------------------------------------------------------------- awards

def _award(session, key, amount, gains) -> None:
    if amount <= 0:
        return
    row = hero_progress(session, key)
    row["xp"] = int(row["xp"]) + int(amount)
    row["campaign_xp"] = int(row.get("campaign_xp") or 0) + int(amount)
    gains[key] = gains.get(key, 0) + int(amount)


def _enemy_defeated(session, key, row) -> float:
    retreated = ((getattr(session, "local_enemy_rules", {}) or {}).get(str(key)) or {}).get("retreated")
    try:
        hp = int(row.get("hp", 1))
    except (TypeError, ValueError):
        hp = 1
    if str(row.get("status") or "") == "dead" or hp <= 0:
        return 1.0
    if retreated:
        return 0.5
    return 0.0


def _completed_progress_clock(row) -> bool:
    if not isinstance(row, dict):
        return False
    if str(row.get("kind") or "").upper() not in {"PROGRESS", ""}:
        return False
    if row.get("completed"):
        return True
    try:
        return int(row.get("value", 0)) >= int(row.get("max", 0)) > 0
    except (TypeError, ValueError):
        return False


def _growth_total(session, key) -> int:
    counts = (getattr(session, "growth_counts", {}) or {}).get(key) or {}
    return sum(int(value or 0) for value in counts.values()) if isinstance(counts, dict) else 0


def _current_marks(session) -> dict:
    enemies = {str(key) for key, row in (getattr(session, "enemy_combatants", {}) or {}).items()
               if isinstance(row, dict) and _enemy_defeated(session, key, row)}
    clocks = {str(key) for key, row in (getattr(session, "scene_clocks", {}) or {}).items()
              if _completed_progress_clock(row)}
    keys = _participant_keys(session)
    return {
        "enemies": sorted(enemies), "clocks": sorted(clocks),
        "growth": {key: _growth_total(session, key) for key in keys},
        "mission": str(getattr(session, "mission_outcome", "pending") or "pending") in MISSION_XP,
    }


def account(session, *, story: str = "") -> list[str]:
    """Award milestone XP since the last watermark; return public notices."""
    if getattr(session, "mode", None) != "participants":
        return []
    ensure(session)
    keys = _participant_keys(session)
    if not keys:
        return []
    for key in keys:
        hero_progress(session, key)
    marks = session.progress_marks
    current = _current_marks(session)
    if not marks.get("baseline"):
        # First observation (new campaign or a session that predates XP):
        # adopt the present as the starting point, award nothing retroactively.
        session.progress_marks = {**current, "baseline": True}
        return []

    before_levels = {key: int(hero_progress(session, key)["level"]) for key in keys}
    gains: dict[str, int] = {}
    receivers = [key for key in keys if _alive_active(session, key)]

    if len(str(story or "").strip()) >= MIN_SCENE_STORY_CHARS:
        for key in receivers:
            _award(session, key, SCENE_XP, gains)

    credited = set(marks.get("enemies") or [])
    for enemy_key in current["enemies"]:
        if enemy_key in credited:
            continue
        row = (getattr(session, "enemy_combatants", {}) or {}).get(enemy_key) or {}
        share = _enemy_defeated(session, enemy_key, row)
        amount = int(ENEMY_XP.get(str(row.get("power") or "MEDIUM").upper(), ENEMY_XP["MEDIUM"]) * share)
        for key in receivers:
            _award(session, key, amount, gains)

    old_clocks = set(marks.get("clocks") or [])
    for _clock in set(current["clocks"]) - old_clocks:
        for key in receivers:
            _award(session, key, CLOCK_XP, gains)

    for key in keys:
        growth_delta = current["growth"].get(key, 0) - int((marks.get("growth") or {}).get(key, current["growth"].get(key, 0)))
        _award(session, key, GROWTH_XP * max(0, growth_delta), gains)

    if current["mission"] and not marks.get("mission"):
        amount = MISSION_XP[str(session.mission_outcome)]
        for key in receivers:
            _award(session, key, amount, gains)

    # Watermarks follow the live state: an enemy key or clock that disappeared
    # or revived can be credited again in a later fight.
    session.progress_marks = {**current, "baseline": True,
                              "mission": bool(current["mission"] or marks.get("mission"))}

    notices = []
    for key in keys:
        row = session.hero_progress[key]
        new_level = level_for_xp(row["xp"])
        old_level = before_levels[key]
        if new_level <= old_level:
            continue
        row["level"] = new_level
        row["asi_pending"] = int(row.get("asi_pending") or 0) + sum(
            1 for value in ASI_LEVELS if old_level < value <= new_level)
        hp_before = (getattr(session, "character_sheets", {}) or {}).get(key, {}).get("max_hp")
        sync_sheet(session, key)
        hp_after = (getattr(session, "character_sheets", {}) or {}).get(key, {}).get("max_hp")
        parts = [f"🎉 {_name(session, key)} достигает {new_level} уровня — «{LEVEL_TITLES[new_level]}»!"]
        if isinstance(hp_before, int) and isinstance(hp_after, int) and hp_after > hp_before:
            parts.append(f"❤️ Макс. HP +{hp_after - hp_before}.")
        if attack_bonus_for_level(new_level) > attack_bonus_for_level(old_level):
            parts.append(f"⚔️ Бонус атаки +{attack_bonus_for_level(new_level)}.")
        if row["asi_pending"]:
            parts.append("⬆️ Можно повысить характеристику: «герой» в меню.")
        notices.append(" ".join(parts))
    return notices


def apply_progress_metadata(session, original_text, cleaned, notices):
    try:
        extra = account(session, story=str(cleaned or ""))
    except Exception:
        logging.exception("DnD progression accounting failed chat_id=%s", getattr(session, "chat_id", None))
        extra = []
    return cleaned, list(notices or []) + extra


# ---------------------------------------------------------------- ability choice

def choose_ability_increase(session, user_id, ability) -> str:
    key = str(int(user_id))
    ability = str(ability or "").upper()
    if ability not in ABILITY_KEYS:
        raise ValueError("Неизвестная характеристика.")
    row = hero_progress(session, key)
    if int(row.get("asi_pending") or 0) <= 0:
        raise ValueError("Сейчас нет доступного повышения характеристики.")
    sheet = (getattr(session, "character_sheets", {}) or {}).get(key) or {}
    base = sheet.get("base_stats") if isinstance(sheet.get("base_stats"), dict) else sheet.get("stats") or {}
    current = int(base.get(ability, 10) or 10) + int((row.get("ability_increases") or {}).get(ability, 0))
    if current >= ABILITY_CAP:
        raise ValueError(f"{ABILITY_LABELS[ability]} уже на максимуме {ABILITY_CAP}.")
    gain = min(ASI_AMOUNT, ABILITY_CAP - current)
    increases = dict(row.get("ability_increases") or {})
    increases[ability] = int(increases.get(ability, 0)) + gain
    row["ability_increases"] = increases
    row["asi_pending"] = int(row["asi_pending"]) - 1
    _sync_stats(session, key)
    sync_sheet(session, key)
    return f"⬆️ {_name(session, key)}: {ABILITY_LABELS[ability]} +{gain}."


def level_stat_bonuses(session, user_id) -> dict[str, int]:
    """Bonus source consumed by the artifact-stats effective stat sync."""
    return {key: value for key, value in ability_increases(session, user_id).items() if value > 0}


# ---------------------------------------------------------------- presentation

def progress_lines(progress: dict | None) -> list[str]:
    if not isinstance(progress, dict):
        return []
    row = _clean_progress(progress)
    level = row["level"]
    upper = next_threshold(level)
    xp_text = f"{row['xp']}/{upper}" if upper else f"{row['xp']} (макс.)"
    lines = [f"⭐ Уровень {level} — «{LEVEL_TITLES[level]}»",
             f"   {progress_bar(row['xp'], level)} опыт {xp_text}"]
    bonus = attack_bonus_for_level(level)
    if bonus:
        lines.append(f"   ⚔️ Бонус атаки уровня +{bonus}")
    increases = row.get("ability_increases") or {}
    if increases:
        lines.append("   ⬆️ Повышения: " + ", ".join(f"{ABILITY_LABELS[k]} +{v}" for k, v in increases.items()))
    if row.get("asi_pending"):
        lines.append(f"   ⬆️ Доступно повышений характеристики: {row['asi_pending']}")
    return lines


def report_section(session) -> str:
    rows = []
    for key in _participant_keys(session):
        row = (getattr(session, "hero_progress", {}) or {}).get(key)
        if not isinstance(row, dict):
            continue
        gained = int(row.get("campaign_xp") or 0)
        start, level = int(row.get("start_level") or 1), int(row.get("level") or 1)
        level_text = f"ур. {start} → {level}" if level > start else f"ур. {level}"
        rows.append(f"{_name(session, key)}: +{gained} опыта ({level_text})")
    return ("📈 Опыт за партию:\n" + "\n".join(rows)) if rows else ""


def context_bit(session, user_id) -> str:
    row = (getattr(session, "hero_progress", {}) or {}).get(str(int(user_id)))
    return f"уровень {int(row['level'])}" if isinstance(row, dict) else ""


# ---------------------------------------------------------------- archive

_archive_hooks: list = []


def register_archive_hook(hook) -> None:
    """Other hero-owned state (e.g. the portrait) archived next to progression."""
    if hook not in _archive_hooks:
        _archive_hooks.append(hook)


def archive_progress(session, players: dict) -> None:
    for key in _participant_keys(session):
        row = (getattr(session, "hero_progress", {}) or {}).get(key)
        history = players.get(key)
        if isinstance(row, dict) and isinstance(history, dict):
            history["progress"] = {k: copy.deepcopy(row[k]) for k in ("xp", "level", "ability_increases", "asi_pending")}


def prepare_party_levels(session) -> None:
    """Load archived progress for every hero and apply it to fresh sheets."""
    ensure(session)
    for key in _participant_keys(session):
        hero_progress(session, key)
        _sync_stats(session, key)
        sync_sheet(session, key)


def _restore(session, _data=None) -> None:
    ensure(session)
    for key in list((getattr(session, "hero_progress", {}) or {}).keys()):
        session.hero_progress[key] = {**_clean_progress(session.hero_progress[key]),
                                      "campaign_xp": int(session.hero_progress[key].get("campaign_xp") or 0),
                                      "start_level": int(session.hero_progress[key].get("start_level") or 1)}


def install_dnd_progression(dnd, *, state_policy, metadata_policy) -> None:
    from AI import dnd_campaign as campaign
    from AI.dnd_adventure import register_report_section
    from AI.dnd_artifact_stats import register_stat_bonus_source

    if getattr(dnd, "_upupa_dnd_progression_installed", False):
        return
    state_policy.add_ensure_hook(ensure)
    state_policy.add_state_field("hero_progress", lambda s: copy.deepcopy(getattr(s, "hero_progress", {}) or {}))
    state_policy.add_state_field("progress_marks", lambda s: copy.deepcopy(getattr(s, "progress_marks", {}) or {}))
    state_policy.add_restore_hook(_restore)
    # Registered after growth/combat/clock postprocessors, so it observes the
    # state they already applied for this response.
    metadata_policy.add_postprocessor(apply_progress_metadata)
    register_stat_bonus_source(level_stat_bonuses)
    register_report_section(report_section)

    original_archive = campaign._archive_campaign

    def archive_campaign(dnd_module, session, finale, epilogue):
        original_archive(dnd_module, session, finale, epilogue)
        chat = campaign._chat_history(session.chat_id, True)
        players = chat.get("players") or {}
        archive_progress(session, players)
        for hook in list(_archive_hooks):
            try:
                hook(session, players)
            except Exception:
                logging.exception("DnD hero archive hook failed chat_id=%s", getattr(session, "chat_id", None))
        campaign._save_archive(dnd_module)

    campaign._archive_campaign = archive_campaign
    dnd._upupa_dnd_progression_installed = True


def archived_hero_lines(history) -> list[str]:
    if not isinstance(history, dict) or history.get("dead") or not isinstance(history.get("progress"), dict):
        return []
    return progress_lines(history["progress"])


__all__ = [
    "ABILITY_LABELS", "ASI_LEVELS", "LEVEL_THRESHOLDS", "LEVEL_TITLES", "MAX_LEVEL",
    "ability_charges_for_level", "account", "apply_progress_metadata", "archive_progress",
    "archived_hero_lines", "attack_bonus", "attack_bonus_for_level", "choose_ability_increase",
    "context_bit", "hero_progress", "level_for_xp", "level_of", "level_stat_bonuses",
    "install_dnd_progression", "prepare_party_levels", "progress_lines", "report_section",
    "sync_party", "sync_sheet",
]
