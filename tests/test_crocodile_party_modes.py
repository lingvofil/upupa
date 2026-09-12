from io import BytesIO
from types import SimpleNamespace

from PIL import Image

from tests import test_smoke_imports  # noqa: F401  (fake env + heavy-library mocks)


def test_parallel_room_names_map_to_isolated_session_keys():
    from games.webapp_auth import normalize_crocodile_room

    assert normalize_crocodile_room("m1001707530786_d1") == (
        "m1001707530786_d1",
        "-1001707530786:d1",
    )
    assert normalize_crocodile_room("-1001707530786_t3") == (
        "m1001707530786_t3",
        "-1001707530786:t3",
    )


def test_duo_session_authorizes_both_drawers_but_not_third_user():
    import pytest
    from games.webapp_auth import WebAppAuthError, authorize_crocodile_drawer

    sessions = {
        "-1001707530786": {
            "drawer_id": 111,
            "drawer_ids": [111, 222],
        }
    }
    assert authorize_crocodile_drawer("m1001707530786", 111, sessions)[1] == "-1001707530786"
    assert authorize_crocodile_drawer("m1001707530786", 222, sessions)[1] == "-1001707530786"
    with pytest.raises(WebAppAuthError, match="active drawer"):
        authorize_crocodile_drawer("m1001707530786", 333, sessions)


def test_synthetic_canvas_authorizes_only_assigned_player():
    import pytest
    from games.webapp_auth import WebAppAuthError, authorize_crocodile_drawer

    sessions = {"-42:d2": {"drawer_id": 222}}
    assert authorize_crocodile_drawer("m42_d2", 222, sessions) == ("m42_d2", "-42:d2")
    with pytest.raises(WebAppAuthError):
        authorize_crocodile_drawer("m42_d2", 111, sessions)


def test_party_mode_contracts_and_private_telephone_payload():
    from games import crocodile_modes

    assert crocodile_modes.DUEL_VOTE_SECONDS == 60
    assert crocodile_modes.TELEPHONE_MIN_PLAYERS == 3
    assert crocodile_modes.TELEPHONE_MAX_PLAYERS >= 6

    session = {
        "ui_mode": "text",
        "prompt": "Что нарисовано?",
        "reference_image": b"jpeg-ish",
    }
    payload = crocodile_modes.canvas_join_payload(session)
    assert payload["ui_mode"] == "text"
    assert payload["prompt"] == "Что нарисовано?"
    assert payload["reference_image"].startswith("data:image/jpeg;base64,")


def test_duo_drawer_helper_recognizes_both_artists():
    from games.crocodile_modes import is_session_drawer, session_artist_names

    session = {
        "drawer_id": 1,
        "drawer_ids": [1, 2],
        "drawer_names": ["Первый", "Второй"],
    }
    assert is_session_drawer(session, 1)
    assert is_session_drawer(session, 2)
    assert not is_session_drawer(session, 3)
    assert session_artist_names(session) == ["Первый", "Второй"]


def test_reverse_progressive_reveal_is_five_seconds_and_gets_fuller():
    from games import reverse_crocodile_modes as modes

    assert modes.REVEAL_INTERVAL_SECONDS == 5
    source = Image.new("RGB", (100, 120), (12, 34, 56))
    buf = BytesIO()
    source.save(buf, format="JPEG")
    order = list(range(modes.REVEAL_COLS * modes.REVEAL_ROWS))

    first = modes.build_reveal_frame(buf.getvalue(), 3, order=order)
    later = modes.build_reveal_frame(buf.getvalue(), 12, order=order)
    assert first != later
    assert len(first) > 100
    assert len(later) > 100


def test_reverse_mode_menu_contains_combined_proverbs_and_pun():
    from games.reverse_crocodile_modes import mode_keyboard

    buttons = [button for row in mode_keyboard().inline_keyboard for button in row]
    texts = [button.text for button in buttons]
    callbacks = [button.callback_data for button in buttons]

    assert "🧠 Пословицы/поговорки" in texts
    assert "🤡 Каламбур" in texts
    assert "🧠 Пословица" not in texts
    assert "🗣 Поговорка" not in texts
    assert "rcrocm_proverbs" in callbacks
    assert "rcrocm_pun" in callbacks
