from AI.dnd_death_legacy import _sheet_is_dead, backfill_death_flags


def test_sheet_death_detection_uses_status_or_zero_hp():
    assert _sheet_is_dead({"hp": 0, "status": "alive"}) is True
    assert _sheet_is_dead({"hp": 5, "status": "dead"}) is True
    assert _sheet_is_dead({"hp": 5, "status": "alive"}) is False


def test_backfill_death_flags_uses_latest_campaign_state():
    archive = {
        "chats": {
            "-100": {
                "players": {
                    "1": {"profile": {"style": "старый герой"}},
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
