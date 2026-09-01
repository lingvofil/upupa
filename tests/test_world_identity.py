import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

import features.world.identity as identity
from features.world.authority import calculate_authority
from features.world.service import WorldService
from infrastructure.persistence.sqlite_world import SQLiteWorldRepository


def _run(coro):
    return asyncio.run(coro)


def _service(tmp_path):
    repo = SQLiteWorldRepository(tmp_path / "world.db")
    repo.init_schema()
    return WorldService(repo)


def _messages(count=30):
    result = []
    for index in range(count):
        result.append(
            {
                "display_name": "Вася" if index % 2 else "Петя",
                "username": "vasya" if index % 2 else "petya",
                "text": (
                    "опять голосуем, куда идти вечером и спорим про кальян"
                    if index % 3
                    else "админы решили, но чат всё равно устроил голосование"
                ),
            }
        )
    return result


def test_identity_is_generated_from_real_message_sample_and_cached(tmp_path, monkeypatch):
    service = _service(tmp_path)
    state = _run(service.enable_state(-1001, "Alpha"))
    calls = []

    async def fake_collect(_state, *, log_file_path, now):
        return _messages()

    async def fake_generate(prompt, chat_id):
        calls.append((prompt, chat_id))
        return (
            '{"government_form":"референдумная республика старожилов",'
            '"climate":"ироничный, шумный и вечерне-кальянный",'
            '"main_threat":"бесконечные голосования о досуге",'
            '"rationale":"Повестка постоянно проходит через коллективные голосования, споры и вечерние планы."}'
        )

    monkeypatch.setattr(identity, "_collect_messages", fake_collect)
    monkeypatch.setattr(identity, "_generate_identity", fake_generate)

    first = _run(identity.ensure_state_identity(service, state, force=True))
    second = _run(identity.ensure_state_identity(service, state))

    assert first is not None
    assert second is not None
    assert first.details.government_form == "референдумная республика старожилов"
    assert first.details.climate == "ироничный, шумный и вечерне-кальянный"
    assert first.details.main_threat == "бесконечные голосования о досуге"
    assert second.details == first.details
    assert len(calls) == 1
    assert "голосуем, куда идти вечером" in calls[0][0]
    assert calls[0][1] == str(state.chat_id)

    events = _run(
        service.list_events(
            limit=10,
            world_id=state.world_id,
            event_types={"state_identity_analyzed"},
        )
    )
    assert len(events) == 1
    assert events[0].payload["source"] == "analysis"
    assert events[0].payload["message_count"] == 30


def test_identity_uses_explicit_placeholder_when_chat_has_too_little_data(tmp_path, monkeypatch):
    service = _service(tmp_path)
    state = _run(service.enable_state(-1001, "Quiet"))

    async def fake_collect(_state, *, log_file_path, now):
        return _messages(4)

    async def should_not_generate(_prompt, _chat_id):
        raise AssertionError("AI must not run with insufficient history")

    monkeypatch.setattr(identity, "_collect_messages", fake_collect)
    monkeypatch.setattr(identity, "_generate_identity", should_not_generate)

    result = _run(
        identity.ensure_state_identity(
            service,
            state,
            now=datetime.now(timezone.utc),
            force=True,
        )
    )

    assert result is not None
    assert result.source == "insufficient_data"
    assert result.details.government_form == "ещё не сформировался"
    assert result.details.climate == "недостаточно наблюдений"
    assert result.details.main_threat == "статистическая неопределённость"


def test_authority_has_no_upper_cap():
    profile = SimpleNamespace(allies=tuple(range(7)), wars=())
    assert calculate_authority(profile) == 106

    bigger = SimpleNamespace(allies=tuple(range(20)), wars=())
    assert calculate_authority(bigger) == 210
