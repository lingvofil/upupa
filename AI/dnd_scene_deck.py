"""Scene director: a three-act structure with a shuffled deck per act.

Before: every scene drew a random type out of eight, avoiding only the last
two. Adventures had no arc, the twist could come three times or never, and
after the first fight the director never asked for another one, whatever
«схваток за сюжет» said (dead enemies stay registered, so «no enemies» was
true only once).

Now the story span (scenes before the denouement) is split into acts. Each act
has its own deck, drawn without replacement and reshuffled when it runs out or
the act changes. The twist is dealt exactly once around the middle, and fights
are scheduled evenly across the span by the settings, counting only fights
that actually happened (alive enemies appeared).

The deck lives in ``session.scene_deck`` next to ``recent_scene_types`` and is
part of the result-recovery snapshot, so a rewound failed generation also
returns the drawn card.
"""
from __future__ import annotations

import random

DENOUEMENT_SCENES = 4
FIRST_FIGHT_SCENE = 3
TWIST = "сюжетный поворот"
AMBUSH = "засада"

SCENE_BRIEFS = {
    "исследование": "герои изучают новое место; в нём есть что найти и чем рискнуть",
    "социальная сцена": "яркий NPC со своей целью; разговор что-то меняет",
    "находка": "полезная или опасная вещь, улика или союзник — с последствием",
    "загадка": "задача с решением внутри мира; несколько способов подступиться",
    "торг": "сделка или обмен, где у каждой стороны есть рычаг",
    "опасность": "угроза среды или обстоятельств, а не враг с HP",
    "погоня": "кто-то убегает или догоняет; на кону время и место",
    "конфликт": "столкновение интересов; силой, хитростью или словом",
    "моральный выбор": "два плохих варианта или цена за успех; решают игроки",
    AMBUSH: "противник нападает первым; героям нужно среагировать",
    "передышка": "короткий спокойный момент: разговоры героев, подготовка, последствия",
    TWIST: "открывается то, что меняет смысл цели; без метафизики и «всё было ненастоящим»",
}

ACTS = (
    {"id": "I", "title": "Завязка", "from": 0.0,
     "brief": "знакомь с миром, NPC и целью; ставки пока личные, ошибки ещё прощаются",
     "deck": ("исследование", "социальная сцена", "находка", "загадка", "торг")},
    {"id": "II", "title": "Усложнение", "from": 0.34,
     "brief": "план ломается, у противника появляется лицо, решения начинают стоить",
     "deck": ("опасность", "погоня", "загадка", "конфликт", "моральный выбор", AMBUSH,
              "социальная сцена", "находка", "передышка")},
    {"id": "III", "title": "Кульминация", "from": 0.67,
     "brief": "всё сходится к цели; возвращаются последствия прежних решений и старые NPC",
     "deck": ("конфликт", "опасность", "погоня", "моральный выбор", AMBUSH)},
)


def _scene(session) -> int:
    return int(getattr(session, "scene_count", 0) or 0)


def story_span(session) -> int:
    from AI.dnd_adventure import adventure_limit

    return max(1, adventure_limit(session) - DENOUEMENT_SCENES)


def act_of(session) -> dict:
    progress = _scene(session) / story_span(session)
    current = ACTS[0]
    for act in ACTS:
        if progress >= act["from"]:
            current = act
    return current


def twist_scene(session) -> int:
    return max(2, round(story_span(session) * 0.5))


def _monsters(session) -> int:
    try:
        from AI.dnd_settings import settings_for

        return int(settings_for(session).get("monsters") or 0)
    except Exception:
        return 0


def fight_schedule(session) -> list[int]:
    """Scenes from which each planned fight becomes due, spread over the span."""
    count = _monsters(session)
    span = story_span(session)
    if count <= 0 or span <= FIRST_FIGHT_SCENE:
        return []
    step = max(2, (span - FIRST_FIGHT_SCENE) // count)
    return [scene for scene in (FIRST_FIGHT_SCENE + index * step for index in range(count)) if scene < span]


def _alive_enemies(session) -> bool:
    for row in (getattr(session, "enemy_combatants", None) or {}).values():
        if not isinstance(row, dict):
            continue
        try:
            hp = int(row.get("hp", 1))
        except (TypeError, ValueError):
            hp = 1
        if row.get("status") not in {"dead", "fled", "retreated", "defeated"} and hp > 0:
            return True
    return False


_DEFAULTS = {"act": None, "cards": [], "twist_done": False, "fights_seen": 0, "in_fight": False, "last": None,
             "enemy_keys": []}


def _state(session, *, write=True) -> dict:
    raw = getattr(session, "scene_deck", None)
    state = raw if isinstance(raw, dict) else {}
    if not write:
        return {**_DEFAULTS, **state, "cards": list(state.get("cards") or []),
                "enemy_keys": list(state.get("enemy_keys") or [])}
    for key, value in _DEFAULTS.items():
        state.setdefault(key, list(value) if isinstance(value, list) else value)
    session.scene_deck = state
    return state


def observe_fights(session, *, write=True) -> dict:
    """Count a fight once, when enemies the deck has never seen show up.

    Dead enemies stay registered, so a fight that started and ended between two
    observations is still counted by its new names. Newcomers joining a fight
    that was already going (reinforcements) do not start another one.
    With ``write=False`` the observation is computed on a copy, so prompt
    building (turn contract) stays free of side effects.
    """
    state = _state(session, write=write)
    keys = {str(key) for key, row in (getattr(session, "enemy_combatants", None) or {}).items() if isinstance(row, dict)}
    known = set(state["enemy_keys"])
    if keys - known and not state["in_fight"]:
        state["fights_seen"] = int(state["fights_seen"]) + 1
    state["enemy_keys"] = sorted(known | keys)
    state["in_fight"] = _alive_enemies(session)
    return state


def fight_due(session, *, write=False) -> int | None:
    """Number (1-based) of the planned fight that is due now, if any."""
    if getattr(session, "mode", None) != "participants":
        return None
    state = observe_fights(session, write=write)
    if state["in_fight"]:
        return None
    schedule = fight_schedule(session)
    seen = int(state["fights_seen"])
    if seen < len(schedule) and _scene(session) >= schedule[seen] and _scene(session) < story_span(session):
        return seen + 1
    return None


def _shuffled(session, act, rng) -> list[str]:
    """Fresh act deck; the last two scene types go to the bottom, never on top."""
    cards = [card for card in act["deck"] if not (card == AMBUSH and _monsters(session) <= 0)]
    rng.shuffle(cards)
    recent = set((getattr(session, "recent_scene_types", None) or [])[-2:])
    return [card for card in cards if card not in recent] + [card for card in cards if card in recent]


def draw(session, *, rng=None) -> dict:
    """Next scene direction: {'type', 'brief', 'act', 'kind'}; mutates the deck."""
    rng = rng or random
    state = observe_fights(session)
    scene = _scene(session)
    if scene >= story_span(session):
        return {"kind": "denouement", "type": "развязка основной цели",
                "brief": "без новых врагов и побочных задач; сведи последствия к цели", "act": None}
    number = fight_due(session, write=True)
    if number is not None:
        total = len(fight_schedule(session))
        return {"kind": "fight", "type": f"сюжетная схватка {number} из {total}",
                "brief": ("подготовь столкновение с противником из темы сюжета, дай игрокам выбрать действия; "
                          "атаки разрешай боевыми тегами"), "act": act_of(session)}
    act = act_of(session)
    if not state["twist_done"] and scene >= twist_scene(session):
        state["twist_done"] = True
        state["last"] = TWIST
        return {"kind": "twist", "type": TWIST, "brief": SCENE_BRIEFS[TWIST], "act": act}
    if state["act"] != act["id"] or not state["cards"]:
        state["act"] = act["id"]
        state["cards"] = _shuffled(session, act, rng)
    card = state["cards"].pop(0)
    state["last"] = card
    return {"kind": "scene", "type": card, "brief": SCENE_BRIEFS.get(card, ""), "act": act}


def direction_text(direction: dict) -> str:
    act = direction.get("act")
    act_text = f" АКТ {act['id']} «{act['title']}»: {act['brief']}." if act else ""
    brief = f" — {direction['brief']}" if direction.get("brief") else ""
    return f"РЕЖИССЁР СЦЕНЫ: {direction['type']}{brief}.{act_text}"


def contract_note(session) -> str:
    """Pacing hint for the turn contract; read-only (does not draw)."""
    number = fight_due(session)
    if number is None:
        return ""
    return (f"По плану сюжета пора к схватке {number} из {len(fight_schedule(session))}: "
            "подведи к противнику из темы сюжета в ближайшем эпизоде и дай героям действовать.")


__all__ = ["ACTS", "SCENE_BRIEFS", "act_of", "contract_note", "direction_text", "draw", "fight_due",
           "fight_schedule", "story_span", "twist_scene"]
