from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

from tests import test_smoke_imports  # noqa: F401

from features.social_graph.ai import narrate_relationship_history


def _view():
    return SimpleNamespace(
        user_a_name="Alice",
        user_b_name="Bob",
        level=5,
        archetype="односторонний сериал",
        trend="резко сближаются",
    )


def _item(*, event_type: str, source: str, title: str, summary: str):
    return SimpleNamespace(
        timestamp=datetime(2026, 9, 13, tzinfo=timezone.utc),
        event_type=event_type,
        source=source,
        title=title,
        summary=summary,
    )


def test_dnd_only_history_does_not_generate_relationship_narrative():
    called = False

    async def generator(prompt: str, chat_id: str) -> str:
        nonlocal called
        called = True
        return "Это не должно быть сгенерировано."

    timeline = (
        _item(
            event_type="game_dnd",
            source="dnd_archive",
            title="D&D: Поезд смерти",
            summary="Герои выбрались из поезда и пережили финал кампании.",
        ),
    )

    result = asyncio.run(
        narrate_relationship_history(_view(), timeline, "-1001", generator=generator)
    )

    assert result is None
    assert called is False


def test_chronicle_dnd_is_excluded_but_real_event_still_drives_narrative():
    prompts: list[str] = []

    async def generator(prompt: str, chat_id: str) -> str:
        prompts.append(prompt)
        return "В Летописи уже есть реальный совместный эпизод."

    timeline = (
        _item(
            event_type="chronicle_game",
            source="chronicle:dnd",
            title="Поезд смерти",
            summary="Вымышленный сюжет D&D.",
        ),
        _item(
            event_type="chronicle_conflict",
            source="chronicle:live",
            title="Спор за кондиционер",
            summary="Пара устроила заметный спор в чате.",
        ),
    )

    result = asyncio.run(
        narrate_relationship_history(_view(), timeline, "-1001", generator=generator)
    )

    assert result == "В Летописи уже есть реальный совместный эпизод."
    assert len(prompts) == 1
    assert "Спор за кондиционер" in prompts[0]
    assert "Поезд смерти" not in prompts[0]
    assert "Вымышленный сюжет D&D" not in prompts[0]
