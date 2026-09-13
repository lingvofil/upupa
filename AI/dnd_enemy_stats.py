"""Display lightweight HP/AC stats for NPCs that attack participant heroes."""
from __future__ import annotations


NPC_STATS_MARKER = "БОЕВЫЕ ПАРАМЕТРЫ НАПАДАЮЩИХ NPC"
NPC_STATS_RULES = f"""
{NPC_STATS_MARKER}: когда NPC или монстр использует ACTION:ENEMY_ATTACK, обязательно указывай его короткое имя,
текущее здоровье и класс брони в том же теге:
[ACTION:ENEMY_ATTACK;TARGETS:12345;POWER:MEDIUM;ENEMY:орк;HP:18;AC:13;REASON:орк рубит Алису тесаком]
ENEMY — короткое имя нападающего, HP — его текущее здоровье, AC — класс брони. Для уже показанного NPC сохраняй те же
значения между его атаками, если сюжет явно их не изменил. Не придумывай отдельные полные листы характеристик: игрокам
достаточно видеть HP и КБ нападающего.
""".strip()

_DEFAULTS = {
    "LOW": (8, 10),
    "MEDIUM": (16, 12),
    "HIGH": (28, 14),
    "DEADLY": (45, 16),
}


def _safe_int(value, default: int, *, low: int, high: int) -> int:
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        return default
    return max(low, min(high, parsed))


def _parse_fields(raw: str) -> dict[str, str]:
    fields = {}
    for part in str(raw or "").split(";"):
        key, sep, value = part.partition(":")
        if sep and value.strip():
            fields[key.strip().upper()] = value.strip()
    return fields


def enrich_attack(combat, text: str, attack: dict | None) -> dict | None:
    if not attack:
        return attack
    match = combat._ACTION_ATTACK_RE.search(str(text or ""))
    fields = _parse_fields(match.group(1)) if match else {}
    power = str(attack.get("power") or "MEDIUM").upper()
    default_hp, default_ac = _DEFAULTS.get(power, _DEFAULTS["MEDIUM"])
    result = dict(attack)
    result["enemy_name"] = str(fields.get("ENEMY") or "Враг").strip()[:80] or "Враг"
    result["enemy_hp"] = _safe_int(fields.get("HP"), default_hp, low=1, high=999)
    result["enemy_ac"] = _safe_int(fields.get("AC"), default_ac, low=5, high=30)
    return result


def decorate_attack_result(attack: dict, result: tuple[str, str, bool]) -> tuple[str, str, bool]:
    summary, prompt, all_dead = result
    name = str(attack.get("enemy_name") or "Враг")
    hp = int(attack.get("enemy_hp") or _DEFAULTS["MEDIUM"][0])
    ac = int(attack.get("enemy_ac") or _DEFAULTS["MEDIUM"][1])
    stat_line = f"👹 {name} — ❤️ {hp} HP · 🛡 КБ {ac}"
    return (
        stat_line + "\n" + summary,
        f"Нападающий {name}: HP {hp}, КБ {ac}. " + prompt,
        all_dead,
    )


def install_dnd_enemy_stats(dnd) -> None:
    from AI import dnd_combat as combat

    if getattr(combat, "_upupa_dnd_enemy_stats_installed", False):
        return

    original_parse_attack = combat._parse_attack
    original_resolve_attack = combat._resolve_enemy_attack

    def parse_attack(text):
        return enrich_attack(combat, text, original_parse_attack(text))

    def resolve_enemy_attack(session, attack):
        return decorate_attack_result(attack, original_resolve_attack(session, attack))

    combat._parse_attack = parse_attack
    combat._resolve_enemy_attack = resolve_enemy_attack
    if NPC_STATS_MARKER not in combat.COMBAT_RULES:
        combat.COMBAT_RULES = f"{combat.COMBAT_RULES}\n{NPC_STATS_RULES}"
    if NPC_STATS_MARKER not in dnd.DND_SYSTEM_PROMPT:
        dnd.DND_SYSTEM_PROMPT = f"{dnd.DND_SYSTEM_PROMPT.rstrip()}\n\n{NPC_STATS_RULES}"
    combat._upupa_dnd_enemy_stats_installed = True
