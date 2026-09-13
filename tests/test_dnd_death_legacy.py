from types import SimpleNamespace

from AI.dnd_death_legacy import (
    _dead_archive_inventory_locked,
    _dead_archive_transfer_block_reason,
    _sheet_is_dead,
    backfill_death_flags,
)


def test_sheet_death_detection_uses_status_or_zero_hp():
    assert _sheet_is_dead({"hp": 0, "status": "alive"}) is True
    assert _sheet_is_dead({"hp": 5, "status": "dead"}) is True
    assert _sheet_is_dead({"hp": 5, "status": "alive"}) is False


def test_backfill_death_flags_uses_latest_campaign_state():
    archive = {
        "chats": {
            "-100": {
                "players": {
                    "1": {"profile": {"style": "старый герой"}, "dead": True},
                    "2": {"profile": {"style": "погибший герой"}},
                },
                "campaigns": [
                    {
                        "character_sheets": {
                            "1": {"hp": 0, "status": "dead"},
                            "2": {"hp": 0, "status": "dead"},
                        }
                    },
                    {
                        "character_sheets": {
                            "1": {"hp": 7, "status": "alive"},
                        }
                    },
                ],
            }
        }
    }

    assert backfill_death_flags(archive) is True
    players = archive["chats"]["-100"]["players"]
    assert players["1"]["dead"] is False
    assert players["2"]["dead"] is True
    assert backfill_death_flags(archive) is False


def test_explicit_dead_user_ids_are_respected():
    archive = {
        "chats": {
            "-100": {
                "players": {"3": {"profile": {"style": "герой"}}},
                "campaigns": [{"character_sheets": {}, "dead_user_ids": [3]}],
            }
        }
    }

    assert backfill_death_flags(archive) is True
    assert archive["chats"]["-100"]["players"]["3"]["dead"] is True


def _campaign_with_histories(histories):
    return SimpleNamespace(
        _player_history=lambda _chat_id, user_id: histories.get(str(int(user_id)))
    )


def test_dead_archived_inventory_is_hidden_and_cannot_be_sent_or_received():
    campaign = _campaign_with_histories(
        {
            "1": {"dead": True, "inventory": [{"name": "Ботинок Истины"}]},
            "2": {"dead": False, "inventory": []},
            "3": {"dead": True, "inventory": []},
        }
    )
    dnd = SimpleNamespace(dnd_sessions={})

    assert _dead_archive_inventory_locked(campaign, dnd, -100, 1) is True
    assert "погибшего героя" in _dead_archive_transfer_block_reason(campaign, dnd, -100, 1, 2)
    assert "герой получателя погиб" in _dead_archive_transfer_block_reason(campaign, dnd, -100, 2, 3)
    assert _dead_archive_transfer_block_reason(campaign, dnd, -100, 2, 2) is None


def test_new_active_hero_is_not_blocked_by_previous_hero_tombstone():
    campaign = _campaign_with_histories(
        {
            "1": {"dead": True, "inventory": [{"name": "старый меч"}]},
            "2": {"dead": False, "inventory": []},
        }
    )
    session = SimpleNamespace(
        mode="participants",
        participants={
            "1": {"user_id": 1, "name": "Новый герой"},
            "2": {"user_id": 2, "name": "Живой герой"},
        },
    )
    dnd = SimpleNamespace(dnd_sessions={-100: session})

    assert _dead_archive_inventory_locked(campaign, dnd, -100, 1) is False
    assert _dead_archive_transfer_block_reason(campaign, dnd, -100, 1, 2) is None
