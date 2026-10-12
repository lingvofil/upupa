"""Three-act scene deck: arc, single twist, fights per settings, recoverable state."""
import copy
import random
from types import SimpleNamespace

import pytest

from tests import test_smoke_imports  # noqa: F401

from AI import dnd
from AI import dnd_scene_deck as deck


@pytest.fixture
def monsters(monkeypatch):
    value = {"monsters": 1}
    monkeypatch.setattr("AI.dnd_settings.settings_for", lambda session=None: dict(value))
    return value


def game(length="short", **extra):
    values = dict(mode="participants", adventure_length=length, scene_count=0, enemy_combatants={},
                  recent_scene_types=[], scene_deck={})
    values.update(extra)
    return SimpleNamespace(**values)


def play(current, *, fight_scenes=(), rng=None):
    """Draw one direction per scene; a due fight spawns enemies that die next scene."""
    rng = rng or random.Random(7)
    directions = []
    limit = 12 if current.adventure_length == "short" else 36
    for scene in range(limit):
        current.scene_count = scene
        for row in current.enemy_combatants.values():
            row["hp"] = 0  # previous fight is over
        direction = deck.draw(current, rng=rng)
        if direction["kind"] == "fight":
            current.enemy_combatants[f"enemy-{scene}"] = {"hp": 10}
        directions.append(direction)
    return directions


def test_short_adventure_has_three_acts_one_twist_and_denouement(monsters):
    directions = play(game())
    span = deck.story_span(game())
    assert span == 8
    acts = [row["act"]["id"] for row in directions[:span] if row["act"]]
    assert acts == sorted(acts, key=["I", "II", "III"].index) and {"I", "II", "III"} <= set(acts)
    assert sum(row["kind"] == "twist" for row in directions) == 1
    assert all(row["kind"] == "denouement" for row in directions[span:])
    assert "засада" not in [row["type"] for row in directions if row["act"] and row["act"]["id"] == "I"]


def test_fights_follow_settings_and_count_only_real_ones(monsters):
    monsters["monsters"] = 2
    current = game()
    assert deck.fight_schedule(current) == [3, 5]
    directions = play(current)
    fights = [index for index, row in enumerate(directions) if row["kind"] == "fight"]
    assert fights == [3, 5]
    assert directions[3]["type"] == "сюжетная схватка 1 из 2"

    # A requested fight that the master never delivered stays due.
    current = game()
    for scene in range(3, 6):
        current.scene_count = scene
        assert deck.draw(current, rng=random.Random(1))["kind"] in {"fight", "twist"}


def test_no_fight_directive_while_enemies_are_alive_or_monsters_off(monsters):
    current = game(scene_count=4, enemy_combatants={"ogre": {"hp": 5}})
    assert deck.draw(current, rng=random.Random(2))["kind"] != "fight"
    monsters["monsters"] = 0
    current = game(scene_count=4)
    assert deck.fight_schedule(current) == []
    directions = play(current)
    assert all(row["kind"] != "fight" and row["type"] != deck.AMBUSH for row in directions)


def test_long_adventure_spreads_three_fights(monsters):
    monsters["monsters"] = 3
    assert deck.fight_schedule(game("long")) == [3, 12, 21]
    directions = play(game("long"))
    assert [i for i, row in enumerate(directions) if row["kind"] == "fight"] == [3, 12, 21]
    assert sum(row["kind"] == "twist" for row in directions) == 1


def test_deck_never_repeats_recent_scene_types(monsters):
    monsters["monsters"] = 0
    current = game()
    picked = []
    for scene in range(8):
        current.scene_count = scene
        picked.append(dnd.choose_next_scene_type(current))
    for index, scene_type in enumerate(picked):
        assert scene_type not in picked[max(0, index - 2):index]


def test_contract_note_is_pure_and_direction_text_names_the_act(monsters):
    current = game(scene_count=3)
    before = copy.deepcopy(vars(current))
    assert "схватке 1 из 1" in deck.contract_note(current)
    assert vars(current) == before
    text = deck.direction_text(deck.draw(game(scene_count=6), rng=random.Random(3)))
    assert text.startswith("РЕЖИССЁР СЦЕНЫ:") and "АКТ" in text


def test_deck_state_survives_record_roundtrip_and_is_in_recovery_snapshot(monsters):
    from AI import dnd_result_recovery

    session = dnd.GameSession(-100, "Маша", conversation=[])
    session.mode = "participants"
    session.adventure_length = "short"
    session.scene_count = 2
    session.enemy_combatants = {}
    deck.draw(session, rng=random.Random(4))
    assert session.scene_deck["cards"]
    restored = dnd.GameSession.from_record(session.to_record())
    assert restored.scene_deck == session.scene_deck and restored.scene_deck is not session.scene_deck
    assert "scene_deck" in dnd_result_recovery._CORE_SNAPSHOT_FIELDS
