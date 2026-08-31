import asyncio
from datetime import datetime

from features.world.activity import get_top_active_citizen
from features.world.rendering import render_world_map_png
from features.world.service import WorldService, calculate_authority
from infrastructure.persistence.sqlite_world import SQLiteWorldRepository


def _run(coro):
    return asyncio.run(coro)


def _service(tmp_path):
    repo = SQLiteWorldRepository(tmp_path / "world.db")
    repo.init_schema()
    return repo, WorldService(repo)


def test_world_details_are_stable_and_foundation_is_journaled_once(tmp_path):
    _, service = _service(tmp_path)
    alpha = _run(service.enable_state(-1001, "Alpha"))

    first = _run(service.get_details(alpha.world_id))
    second = _run(service.get_details(alpha.world_id))
    events = _run(service.list_events(limit=20))

    assert first == second
    assert first is not None
    assert first.government_form
    assert first.climate
    assert first.main_threat
    assert [event.event_type for event in events].count("state_founded") == 1


def test_alliance_name_authority_and_journal_follow_diplomacy(tmp_path):
    _, service = _service(tmp_path)
    alpha = _run(service.enable_state(-1001, "Alpha"))
    beta = _run(service.enable_state(-1002, "Beta"))

    proposal = _run(service.propose_alliance(-1001, "Alpha", beta.world_id))
    accepted = _run(service.resolve_request(proposal.request.request_id, -1002, "accepted"))
    assert accepted.status == "accepted"

    status, _source, _target, name = _run(
        service.name_alliance(-1001, "Alpha", beta.world_id, "Пивной пакт")
    )
    assert status == "named"
    assert name == "Пивной пакт"
    assert _run(service.get_alliance_name(alpha.world_id, beta.world_id)) == "Пивной пакт"

    profile = _run(service.get_profile(-1001, "Alpha"))
    assert calculate_authority(profile) == 58

    war = _run(service.declare_war(-1001, "Alpha", beta.world_id))
    assert war.status == "declared"
    assert _run(service.get_alliance_name(alpha.world_id, beta.world_id)) is None
    profile = _run(service.get_profile(-1001, "Alpha"))
    assert calculate_authority(profile) == 45

    event_types = {event.event_type for event in _run(service.list_events(limit=50))}
    assert {"alliance_proposed", "alliance_formed", "alliance_named", "war_declared"} <= event_types


def test_ambassador_is_persisted_in_world_details(tmp_path):
    _, service = _service(tmp_path)
    alpha = _run(service.enable_state(-1001, "Alpha"))

    details = _run(service.set_ambassador(alpha.world_id, 777, "Вася Дипломатов"))

    assert details is not None
    assert details.ambassador_user_id == 777
    assert details.ambassador_name == "Вася Дипломатов"
    assert _run(service.is_ambassador(-1001, 777))
    assert not _run(service.is_ambassador(-1001, 778))


def test_world_map_renders_png_with_current_relations(tmp_path):
    _, service = _service(tmp_path)
    alpha = _run(service.enable_state(-1001, "Alpha"))
    beta = _run(service.enable_state(-1002, "Beta"))
    gamma = _run(service.enable_state(-1003, "Gamma"))
    proposal = _run(service.propose_alliance(-1001, "Alpha", beta.world_id))
    _run(service.resolve_request(proposal.request.request_id, -1002, "accepted"))
    _run(service.declare_war(-1001, "Alpha", gamma.world_id))

    states = _run(service.list_all_states())
    relations = _run(service.list_relations(active_only=True))
    png = render_world_map_png(states, relations)

    assert png.startswith(b"\x89PNG\r\n\x1a\n")
    assert {relation.relation for relation in relations} == {"allied", "war"}


def test_top_active_citizen_uses_existing_message_log_format(tmp_path):
    log = tmp_path / "user_messages.log"
    log.write_text(
        "\n".join(
            [
                "2026-08-31T23:00:00.000000 - Chat -1001 (Alpha) - User 1 (vasya) [Вася]: раз",
                "2026-08-31T23:10:00.000000 - Chat -1001 (Alpha) - User 2 (petya) [Петя]: два",
                "2026-08-31T23:20:00.000000 - Chat -1001 (Alpha) - User 1 (vasya) [Вася]: три",
                "2026-08-31T23:30:00.000000 - Chat -9999 (Other) - User 3 (x) [Чужой]: мимо",
            ]
        ),
        encoding="utf-8",
    )

    result = _run(
        get_top_active_citizen(
            -1001,
            log_file_path=log,
            now=datetime(2026, 9, 1, 0, 0, 0),
        )
    )

    assert result == ("Вася", 2)
