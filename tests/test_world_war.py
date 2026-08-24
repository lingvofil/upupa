import asyncio
import sqlite3

from features.world.service import WorldService, format_world_profile
from infrastructure.persistence.sqlite_world import SQLiteWorldRepository


def _service(tmp_path):
    repo = SQLiteWorldRepository(tmp_path / "world.db")
    repo.init_schema()
    return repo, WorldService(repo)


def _run(coro):
    return asyncio.run(coro)


def test_declaring_war_replaces_alliance_and_updates_profile(tmp_path):
    repo, service = _service(tmp_path)
    alpha = _run(service.enable_state(-1001, "Alpha"))
    beta = _run(service.enable_state(-1002, "Beta"))
    gamma = _run(service.enable_state(-1003, "Gamma"))

    proposal = _run(service.propose_alliance(-1001, "Alpha", beta.world_id))
    _run(service.resolve_request(proposal.request.request_id, -1002, "accepted"))
    assert repo.has_alliance(alpha.world_id, beta.world_id)

    war = _run(service.declare_war(-1001, "Alpha", beta.world_id))
    assert war.status == "declared"
    assert war.previous_relation == "allied"
    assert repo.get_relation(alpha.world_id, beta.world_id) == "war"
    assert not repo.has_alliance(alpha.world_id, beta.world_id)

    profile = _run(service.get_profile(-1001, "Alpha"))
    assert {state.world_id for state in profile.wars} == {beta.world_id}
    assert {state.world_id for state in profile.neutral} == {gamma.world_id}
    assert not profile.allies


def test_alliance_is_blocked_during_war_and_allowed_after_peace(tmp_path):
    repo, service = _service(tmp_path)
    alpha = _run(service.enable_state(-1001, "Alpha"))
    beta = _run(service.enable_state(-1002, "Beta"))

    declared = _run(service.declare_war(-1001, "Alpha", beta.world_id))
    assert declared.status == "declared"

    blocked = _run(service.propose_alliance(-1001, "Alpha", beta.world_id))
    assert blocked.status == "at_war"
    assert blocked.request is None

    repeated = _run(service.declare_war(-1002, "Beta", alpha.world_id))
    assert repeated.status == "already_at_war"

    peace = _run(service.end_war(-1002, "Beta", alpha.world_id))
    assert peace.status == "ended"
    assert repo.get_relation(alpha.world_id, beta.world_id) is None

    proposal = _run(service.propose_alliance(-1001, "Alpha", beta.world_id))
    assert proposal.status == "created"


def test_declaring_war_cancels_pending_alliance_request(tmp_path):
    _, service = _service(tmp_path)
    alpha = _run(service.enable_state(-1001, "Alpha"))
    beta = _run(service.enable_state(-1002, "Beta"))

    proposal = _run(service.propose_alliance(-1001, "Alpha", beta.world_id))
    assert proposal.status == "created"

    declared = _run(service.declare_war(-1002, "Beta", alpha.world_id))
    assert declared.status == "declared"

    stale = _run(service.resolve_request(proposal.request.request_id, -1002, "accepted"))
    assert stale.status == "already_resolved"
    assert stale.request.status == "cancelled"


def test_disabling_state_preserves_war_until_reactivation(tmp_path):
    repo, service = _service(tmp_path)
    alpha = _run(service.enable_state(-1001, "Alpha"))
    beta = _run(service.enable_state(-1002, "Beta"))
    _run(service.declare_war(-1001, "Alpha", beta.world_id))

    _run(service.disable_state(-1002))
    assert repo.get_relation(alpha.world_id, beta.world_id) == "war"

    profile = _run(service.get_profile(-1001, "Alpha"))
    assert not profile.wars
    assert {state.world_id for state in profile.inactive_wars} == {beta.world_id}

    reenabled = _run(service.enable_state(-1002, "Beta again"))
    assert reenabled.world_id == beta.world_id
    profile = _run(service.get_profile(-1001, "Alpha"))
    assert {state.world_id for state in profile.wars} == {beta.world_id}


def test_self_war_and_unknown_state_are_rejected(tmp_path):
    _, service = _service(tmp_path)
    alpha = _run(service.enable_state(-1001, "Alpha"))

    assert _run(service.declare_war(-1001, "Alpha", alpha.world_id)).status == "self"
    assert _run(service.declare_war(-1001, "Alpha", 999)).status == "unknown_target"
    assert _run(service.end_war(-1001, "Alpha", 999)).status == "unknown_target"


def test_world_profile_formats_current_population(tmp_path):
    _, service = _service(tmp_path)
    _run(service.enable_state(-1001, "Alpha"))
    profile = _run(service.get_profile(-1001, "Alpha"))

    text = format_world_profile(profile, population=37)

    assert "👥 Долбоебов: 37" in text
    assert "⚔️ Война: —" in text


def test_legacy_alliance_schema_migrates_to_support_war(tmp_path):
    db_path = tmp_path / "world.db"
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE world_states (
                world_id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id BIGINT NOT NULL UNIQUE,
                chat_title TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0, 1))
            );
            CREATE TABLE diplomatic_relations (
                state_a INTEGER NOT NULL,
                state_b INTEGER NOT NULL,
                relation TEXT NOT NULL CHECK(relation = 'allied'),
                updated_at TEXT NOT NULL,
                PRIMARY KEY (state_a, state_b),
                CHECK(state_a < state_b),
                FOREIGN KEY(state_a) REFERENCES world_states(world_id),
                FOREIGN KEY(state_b) REFERENCES world_states(world_id)
            );
            INSERT INTO world_states(world_id, chat_id, chat_title, created_at, updated_at, enabled)
            VALUES
                (1, -1001, 'Alpha', '2026-08-25T00:00:00+00:00', '2026-08-25T00:00:00+00:00', 1),
                (2, -1002, 'Beta', '2026-08-25T00:00:00+00:00', '2026-08-25T00:00:00+00:00', 1);
            INSERT INTO diplomatic_relations(state_a, state_b, relation, updated_at)
            VALUES (1, 2, 'allied', '2026-08-25T00:00:00+00:00');
            """
        )

    repo = SQLiteWorldRepository(db_path)
    repo.init_schema()
    service = WorldService(repo)

    assert repo.get_relation(1, 2) == "allied"
    result = _run(service.declare_war(-1001, "Alpha", 2))
    assert result.status == "declared"
    assert result.previous_relation == "allied"
    assert repo.get_relation(1, 2) == "war"
