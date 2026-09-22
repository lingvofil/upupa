import copy
from types import SimpleNamespace

from AI import dnd_campaign as campaign
from AI import dnd_session_canon as canon
from AI import dnd_state_commands as state_commands


def _session():
    return SimpleNamespace(
        chat_id=-3001,
        campaign_id="canon-campaign",
        state_revision=12,
        selected_plot="Украсть карту до рассвета",
        participants={
            "1": {"user_id": 1, "name": "Алиса"},
            "2": {"user_id": 2, "name": "Боря"},
        },
        character_sheets={
            "1": {"hp": 7, "max_hp": 10, "status": "alive"},
            "2": {"hp": 0, "max_hp": 8, "status": "dead"},
        },
        player_positions={
            "1": {"location": "крыша склада", "detail": "держит люк"},
            "2": {"location": "двор", "detail": "лежит у ворот"},
        },
        inventories={
            "1": [
                {"name": "Медный ключ", "kind": "artifact"},
                {"name": "верёвка", "kind": "item", "quantity": 2},
            ],
            "2": [],
        },
        reputations={
            "1": ["спаситель пристани"],
            "2": ["известен как человек, который полез первым"],
        },
        conditions={
            "1": [{"name": "оглушена", "effect": "CHECK_DISADVANTAGE"}],
            "2": [],
        },
        npc_memory={
            "капитан ржа": {
                "name": "Капитан Ржа",
                "event": "герои спасли его корабль",
                "attitude": "благодарен",
                "obligation_kind": "NPC_OWES_PLAYERS",
                "obligation": "обещал безопасный проход",
                "wants": "вернуть карту",
                "unresolved": "герои обещали найти матроса",
                "notes": ["встречались у порта"],
            }
        },
        scene_clocks={
            "alarm": {
                "name": "Тревога",
                "value": 3,
                "max": 4,
                "kind": "DANGER",
                "full": False,
            }
        },
        threat={"name": "Шум", "level": 2, "max": 6},
        scene_log=[
            "Старая художественная сцена ошибочно утверждает, что Боря жив и у Алисы нет ключа."
        ],
        conversation=[
            {
                "role": "assistant",
                "content": "Боря жив, а Медный ключ потерян.",
            }
        ],
    )


def test_session_canon_is_built_only_from_structured_state():
    session = _session()

    snapshot = canon.build_session_canon(session)

    alice = next(row for row in snapshot["heroes"] if row["id"] == "1")
    boris = next(row for row in snapshot["heroes"] if row["id"] == "2")

    assert snapshot["campaign_id"] == "canon-campaign"
    assert snapshot["revision"] == 12
    assert alice["position"]["location"] == "крыша склада"
    assert alice["inventory"][0]["name"] == "Медный ключ"
    assert boris["status"] == "dead"
    assert boris["hp"] == 0
    assert snapshot["npcs"][0]["obligation"] == "обещал безопасный проход"
    assert snapshot["npcs"][0]["unresolved"] == "герои обещали найти матроса"

    rendered = canon.render_canon_snapshot(snapshot, active=True)
    assert "Медный ключ" in rendered
    assert "HP 0/8, dead" in rendered
    assert "обещал безопасный проход" in rendered
    assert "Боря жив" not in rendered
    assert "ключ потерян" not in rendered.casefold()


def test_active_canon_command_prefers_live_structured_session(monkeypatch):
    session = _session()
    monkeypatch.setattr(campaign, "_load_archive", lambda _dnd: None)

    dnd = SimpleNamespace(
        dnd_sessions={session.chat_id: session},
    )

    text = canon.render_session_canon(dnd, session.chat_id)

    assert text.startswith("📜 SESSION CANON")
    assert "Текущая игра" in text
    assert "крыша склада" in text
    assert "Капитан Ржа" in text
    assert "Тревога: 3/4" in text


def test_legacy_completed_campaign_is_rendered_without_inventing_missing_fields(monkeypatch):
    chat = {
        "players": {
            "1": {"name": "Алиса"},
            "2": {"name": "Боря"},
        },
        "campaigns": [
            {
                "completed_at": "2026-09-20T19:00:00+00:00",
                "selected_plot": "Склад у гавани",
                "profiles": {"1": {}, "2": {}},
                "inventories": {
                    "1": [{"name": "Ключ", "kind": "item"}],
                    "2": [],
                },
                "reputations": {"1": ["поджигатель склада"]},
                "npc_memory": {
                    "сторож": {
                        "name": "Сторож",
                        "event": "пропустил героев",
                    }
                },
                "dead_user_ids": [2],
                "threat": {"name": "Погоня", "level": 4, "max": 6},
            }
        ],
    }
    monkeypatch.setattr(campaign, "_load_archive", lambda _dnd: None)
    monkeypatch.setattr(campaign, "_chat_history", lambda _chat_id: chat)

    text = canon.render_session_canon(
        SimpleNamespace(dnd_sessions={}),
        -3002,
    )

    assert "Архив старого формата" in text
    assert "Склад у гавани" in text
    assert "Алиса" in text
    assert "Боря — dead" in text
    assert "Ключ" in text
    assert "Сторож" in text
    assert "Погоня: 4/6" in text


def test_saved_session_canon_wins_over_legacy_archive_fields(monkeypatch):
    saved = canon.build_session_canon(_session())
    chat = {
        "players": {},
        "campaigns": [
            {
                "completed_at": "2026-09-21T19:00:00+00:00",
                "selected_plot": "СТАРОЕ НАЗВАНИЕ",
                "inventories": {},
                "session_canon": copy.deepcopy(saved),
            }
        ],
    }
    monkeypatch.setattr(campaign, "_load_archive", lambda _dnd: None)
    monkeypatch.setattr(campaign, "_chat_history", lambda _chat_id: chat)

    text = canon.render_session_canon(
        SimpleNamespace(dnd_sessions={}),
        -3003,
    )

    assert "Украсть карту до рассвета" in text
    assert "СТАРОЕ НАЗВАНИЕ" not in text
    assert "Архив старого формата" not in text


def test_archive_wrapper_persists_structured_session_canon(monkeypatch):
    session = _session()
    store = {"players": {}, "campaigns": []}
    saves = []

    def base_archive(_dnd, current, _finale, _epilogue):
        store["campaigns"].append(
            {
                "completed_at": "2026-09-22T19:00:00+00:00",
                "selected_plot": current.selected_plot,
            }
        )
        return "archived"

    original_archive = campaign._archive_campaign
    had_flag = hasattr(campaign, "_upupa_dnd_session_canon_installed")
    old_flag = getattr(campaign, "_upupa_dnd_session_canon_installed", None)
    try:
        campaign._archive_campaign = base_archive
        if had_flag:
            delattr(campaign, "_upupa_dnd_session_canon_installed")
        monkeypatch.setattr(
            campaign,
            "_chat_history",
            lambda _chat_id, create=False: store,
        )
        monkeypatch.setattr(
            campaign,
            "_save_archive",
            lambda _dnd: saves.append(True) or True,
        )

        canon.install_dnd_session_canon_archive(SimpleNamespace())
        result = campaign._archive_campaign(
            SimpleNamespace(),
            session,
            "финал",
            "эпилог",
        )

        assert result == "archived"
        saved = store["campaigns"][-1]["session_canon"]
        assert saved["version"] == canon.CANON_VERSION
        assert saved["campaign_id"] == "canon-campaign"
        assert saved["revision"] == 12
        assert any(hero["status"] == "dead" for hero in saved["heroes"])
        assert saves
    finally:
        campaign._archive_campaign = original_archive
        if had_flag:
            campaign._upupa_dnd_session_canon_installed = old_flag
        elif hasattr(campaign, "_upupa_dnd_session_canon_installed"):
            delattr(campaign, "_upupa_dnd_session_canon_installed")


def test_state_command_routes_dnd_canon_to_session_canon(monkeypatch):
    session = _session()
    monkeypatch.setattr(campaign, "_load_archive", lambda _dnd: None)

    dnd = SimpleNamespace(dnd_sessions={session.chat_id: session})

    assert state_commands.command_kind("днд канон") == "canon"
    text = state_commands.render_state_command(
        "canon",
        dnd,
        session.chat_id,
        1,
        "Алиса",
    )
    assert text.startswith("📜 SESSION CANON")
    assert "Медный ключ" in text
