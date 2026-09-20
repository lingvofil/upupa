from types import SimpleNamespace

from AI import dnd_player_positions as positions


def _session():
    return SimpleNamespace(
        participants={
            "196920885": {"user_id": 196920885, "name": "М&M"},
            "126386976": {"user_id": 126386976, "name": "Детектор"},
        },
        player_positions={},
        scene_count=12,
    )


def test_position_survives_when_only_another_player_moves():
    session = _session()

    cleaned, notices = positions.apply_player_position_metadata(
        session,
        "ИМакс залез в банку. [POSITION:SET;PLAYER:196920885;LOCATION:банка с пивом;DETAIL:ползёт внутри банки]",
        "ИМакс залез в банку. [POSITION:SET;PLAYER:196920885;LOCATION:банка с пивом;DETAIL:ползёт внутри банки]",
        [],
    )
    assert cleaned == "ИМакс залез в банку."
    assert notices == []
    assert session.player_positions["196920885"]["location"] == "банка с пивом"

    positions.apply_player_position_metadata(
        session,
        "Детектор полез в лаз. [POSITION:SET;PLAYER:126386976;LOCATION:узкий лаз;DETAIL:ползёт первым]",
        "Детектор полез в лаз. [POSITION:SET;PLAYER:126386976;LOCATION:узкий лаз;DETAIL:ползёт первым]",
        [],
    )

    assert session.player_positions["196920885"]["location"] == "банка с пивом"
    assert session.player_positions["126386976"]["location"] == "узкий лаз"

    context = positions.render_player_position_context(session)
    assert "М&M (ID 196920885): банка с пивом; ползёт внутри банки" in context
    assert "Детектор (ID 126386976): узкий лаз; ползёт первым" in context


def test_unknown_player_position_is_ignored_and_clear_is_explicit():
    session = _session()
    positions.apply_player_position_metadata(
        session,
        "[POSITION:SET;PLAYER:196920885;LOCATION:банка]",
        "[POSITION:SET;PLAYER:196920885;LOCATION:банка]",
        [],
    )

    positions.apply_player_position_metadata(
        session,
        "[POSITION:SET;PLAYER:999;LOCATION:крыша][POSITION:CLEAR;PLAYER:196920885]",
        "[POSITION:SET;PLAYER:999;LOCATION:крыша][POSITION:CLEAR;PLAYER:196920885]",
        [],
    )

    assert "999" not in session.player_positions
    assert "196920885" not in session.player_positions


def test_restore_keeps_persisted_position_details():
    session = _session()
    positions._restore(
        session,
        {
            "player_positions": {
                "196920885": {
                    "location": "банка с пивом",
                    "detail": "застрял по пояс",
                    "updated_scene": 9,
                }
            }
        },
    )

    assert session.player_positions == {
        "196920885": {
            "location": "банка с пивом",
            "detail": "застрял по пояс",
            "updated_scene": 9,
        }
    }
