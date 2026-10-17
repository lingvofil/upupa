from __future__ import annotations

import asyncio
from types import SimpleNamespace

from tests import test_smoke_imports  # noqa: F401

from features.social_graph.analysis import GraphEdge, aggregate_edges
from features.social_graph.radio_structure import format_radio_structure
from features.social_graph.structure import assign_roles, detect_factions


def _edge(a, b, weight):
    return GraphEdge(a, b, weight / 2, weight / 2)


NAMES = {1: "Вася", 2: "Петя", 3: "Маша", 4: "Коля", 5: "Оля", 6: "Дима", 7: "Тихоня"}

INTERACTIONS = (
    [(1, 2, "reply", 5.0), (2, 1, "reply", 5.0)]
    + [(1, 3, "reply", 4.5), (3, 1, "reply", 4.5)]
    + [(2, 3, "reply", 4.0), (3, 2, "reply", 4.0)]
    + [(4, 5, "reply", 5.0), (5, 4, "reply", 5.0)]
    + [(4, 6, "reply", 4.5), (6, 4, "reply", 4.5)]
    + [(5, 6, "reply", 3.5), (6, 5, "reply", 3.5)]
    + [(3, 4, "reply", 1.0)]
)


def _structure():
    edges = aggregate_edges(INTERACTIONS)
    return SimpleNamespace(
        period_days=30,
        names=dict(NAMES),
        roles=assign_roles(INTERACTIONS, edges),
        factions=detect_factions(edges),
    )


def _view(a, b, archetype, trend, level=50):
    return SimpleNamespace(
        user_a_id=a,
        user_b_id=b,
        user_a_name=NAMES[a],
        user_b_name=NAMES[b],
        archetype=archetype,
        trend=trend,
        level=level,
    )


def _titles(structure):
    by_leader_camp = {}
    for faction in structure.factions.factions:
        by_leader_camp[faction.index] = "Партия Арбуза" if 1 in faction.members else "Ледяной Клан"
    return by_leader_camp


def test_structure_context_names_camps_cross_contacts_and_pairs():
    structure = _structure()
    window = [_edge(1, 2, 4), _edge(3, 4, 3), _edge(1, 7, 1)]
    relationships = [
        _view(1, 2, "заклятые друзья", "конфликт набирает обороты", level=80),
        _view(3, 4, "периодически пересекаются", "отношения стабильны"),
        _view(5, 6, "взаимная аллергия", "резко сближаются"),
    ]

    text = format_radio_structure(structure, _titles(structure), window, NAMES, relationships)

    assert text.startswith("ПОЛИТИЧЕСКАЯ КАРТА ЧАТА:")
    assert "«Партия Арбуза»" in text and "«Ледяной Клан»" in text
    assert "контакт через линию фронта" in text
    assert "Маша («Партия Арбуза»)" in text and "Коля («Ледяной Клан»)" in text
    assert "Вася и Петя: «заклятые друзья», сейчас — конфликт набирает обороты." in text
    # Boring pair and pairs absent from the episode window stay off the air.
    assert "периодически пересекаются" not in text
    assert "взаимная аллергия" not in text
    assert "%" not in text


def test_structure_context_is_empty_without_window_activity_or_camps():
    structure = _structure()
    assert format_radio_structure(structure, _titles(structure), [], NAMES) == ""

    tight_edges = [_edge(1, 2, 5), _edge(2, 3, 5), _edge(1, 3, 5)]
    tight = SimpleNamespace(
        period_days=30,
        names=dict(NAMES),
        roles={},
        factions=detect_factions(tight_edges),
    )
    assert format_radio_structure(tight, {}, tight_edges, NAMES) == ""


def test_radio_prompt_carries_political_map_and_rubric(monkeypatch):
    import features.radio.script as radio_script

    prompts = []

    async def fake_generate(prompt, chat_id, **kwargs):
        prompts.append(prompt)
        return " ".join(["эфир"] * 400)

    monkeypatch.setattr(radio_script, "_generate_with_active_model", fake_generate)
    messages = [
        {"display_name": "Вася", "username": "vasya", "text": "Арбуз надо заморозить"},
        {"display_name": "Коля", "username": "kolya", "text": "Нет, сначала нужен лёд"},
    ]
    structure_context = "ПОЛИТИЧЕСКАЯ КАРТА ЧАТА:\n- «Партия Арбуза»: лидер Вася; состав: Вася."

    asyncio.run(
        radio_script.generate_radio_script(
            "-1001",
            "Чятище",
            messages,
            24,
            structure_context=structure_context,
        )
    )

    prompt = prompts[0]
    assert "Политическая карта:\nПОЛИТИЧЕСКАЯ КАРТА ЧАТА:" in prompt
    assert "«Партия Арбуза»" in prompt
    assert "закреплённый канон Упупы" in prompt
    assert "«политическая хроника»" in prompt


def test_rubrics_add_political_chronicle_only_with_structure():
    from features.radio.script import _choose_rubrics

    class Rng:
        def randint(self, _a, _b):
            return 2

        def sample(self, values, k):
            return list(values)[:k]

    without = _choose_rubrics(rng=Rng())
    with_map = _choose_rubrics(structure_context="ПОЛИТИЧЕСКАЯ КАРТА ЧАТА: ...", rng=Rng())
    assert all(name != "политическая хроника" for name, _ in without)
    assert any(name == "политическая хроника" for name, _ in with_map)
