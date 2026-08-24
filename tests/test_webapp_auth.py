import hashlib
import hmac
import json
from urllib.parse import urlencode

import pytest

from games.webapp_auth import (
    MAX_INIT_DATA_LENGTH,
    WebAppAuthError,
    authorize_crocodile_drawer,
    normalize_crocodile_room,
    validate_telegram_init_data,
)


BOT_TOKEN = "123456789:test-token"
NOW = 1_800_000_000


def _signed_init_data(**overrides) -> str:
    fields = {
        "auth_date": str(NOW - 30),
        "query_id": "AAHdF6IQAAAAAN0XohDhrOrc",
        "user": json.dumps(
            {"id": 424242, "first_name": "Tester"},
            separators=(",", ":"),
            ensure_ascii=False,
        ),
    }
    fields.update({key: str(value) for key, value in overrides.items()})
    data_check_string = "\n".join(
        f"{key}={value}" for key, value in sorted(fields.items())
    )
    secret_key = hmac.new(
        b"WebAppData",
        BOT_TOKEN.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    fields["hash"] = hmac.new(
        secret_key,
        data_check_string.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return urlencode(fields)


def test_telegram_reference_vector_validates():
    """Reference vector published by the Telegram Mini Apps project."""
    reference_token = "5768337691:AAH5YkoiEuPk8-FZa32hStHTqXiLPtAEhx8"
    fields = {
        "auth_date": "1662771648",
        "query_id": "AAHdF6IQAAAAAN0XohDhrOrc",
        "user": (
            '{"id":279058397,"first_name":"Vladislav","last_name":"Kibenko",'
            '"username":"vdkfrost","language_code":"ru","is_premium":true}'
        ),
        "hash": "c501b71e775f74ce10e377dea85a7ea24ecd640b223ea86dfe453e0eaed2e2b2",
    }

    identity = validate_telegram_init_data(
        urlencode(fields),
        reference_token,
        now=1662771648,
    )

    assert identity.user_id == 279058397


def test_modern_telegram_reference_vector_with_signature_validates():
    """The bot-token HMAC also covers Telegram's newer signature field."""
    reference_token = "7342037359:AAHI25ES9xCOMPokpYoz-p8XVrZUdygo2J4"
    init_data = (
        "user=%7B%22id%22%3A279058397%2C%22first_name%22%3A%22Vladislav%20%2B%20-%20%3F%20%5C%2F%22%2C%22last_name%22%3A%22Kibenko%22%2C%22username%22%3A%22vdkfrost%22%2C%22language_code%22%3A%22ru%22%2C%22is_premium%22%3Atrue%2C%22allows_write_to_pm%22%3Atrue%2C%22photo_url%22%3A%22https%3A%5C%2F%5C%2Ft.me%5C%2Fi%5C%2Fuserpic%5C%2F320%5C%2F4FPEE4tmP3ATHa57u6MqTDih13LTOiMoKoLDRG4PnSA.svg%22%7D"
        "&chat_instance=8134722200314281151&chat_type=private&auth_date=1733509682"
        "&signature=TYJxVcisqbWjtodPepiJ6ghziUL94-KNpG8Pau-X7oNNLNBM72APCpi_RKiUlBvcqo5L-LAxIc3dnTzcZX_PDg"
        "&hash=a433d8f9847bd6addcc563bff7cc82c89e97ea0d90c11fe5729cae6796a36d73"
    )

    identity = validate_telegram_init_data(
        init_data,
        reference_token,
        now=1733509682,
    )

    assert identity.user_id == 279058397


def test_valid_init_data_returns_verified_identity():
    identity = validate_telegram_init_data(
        _signed_init_data(start_param="m1001707530786"),
        BOT_TOKEN,
        now=NOW,
    )

    assert identity.user_id == 424242
    assert identity.auth_date == NOW - 30
    assert identity.start_param == "m1001707530786"


def test_tampered_init_data_is_rejected():
    init_data = _signed_init_data().replace("424242", "424243")

    with pytest.raises(WebAppAuthError, match="hash"):
        validate_telegram_init_data(init_data, BOT_TOKEN, now=NOW)


def test_expired_init_data_is_rejected():
    init_data = _signed_init_data(auth_date=NOW - 90_000)

    with pytest.raises(WebAppAuthError, match="expired"):
        validate_telegram_init_data(init_data, BOT_TOKEN, now=NOW)


def test_future_init_data_is_rejected():
    init_data = _signed_init_data(auth_date=NOW + 120)

    with pytest.raises(WebAppAuthError, match="future"):
        validate_telegram_init_data(init_data, BOT_TOKEN, now=NOW)


def test_duplicate_fields_are_rejected():
    init_data = _signed_init_data() + "&user=%7B%22id%22%3A1%7D"

    with pytest.raises(WebAppAuthError, match="duplicate"):
        validate_telegram_init_data(init_data, BOT_TOKEN, now=NOW)


def test_oversized_init_data_is_rejected_before_parsing():
    with pytest.raises(WebAppAuthError, match="too large"):
        validate_telegram_init_data(
            "x" * (MAX_INIT_DATA_LENGTH + 1),
            BOT_TOKEN,
            now=NOW,
        )


@pytest.mark.parametrize(
    ("room", "canonical_room", "chat_id"),
    [
        ("m1001707530786", "m1001707530786", "-1001707530786"),
        ("-1001707530786", "m1001707530786", "-1001707530786"),
        ("12345", "12345", "12345"),
    ],
)
def test_room_normalization(room, canonical_room, chat_id):
    assert normalize_crocodile_room(room) == (canonical_room, chat_id)


@pytest.mark.parametrize("room", ["", "abc", "m-123", "12.3", "0", "m0"])
def test_invalid_rooms_are_rejected(room):
    with pytest.raises(WebAppAuthError):
        normalize_crocodile_room(room)


def test_verified_drawer_is_authorized_for_active_room():
    sessions = {"-1001707530786": {"drawer_id": 424242}}

    assert authorize_crocodile_drawer(
        "m1001707530786", 424242, sessions
    ) == ("m1001707530786", "-1001707530786")


def test_other_verified_user_cannot_take_over_room():
    sessions = {"-1001707530786": {"drawer_id": 424242}}

    with pytest.raises(WebAppAuthError, match="active drawer"):
        authorize_crocodile_drawer("m1001707530786", 999999, sessions)


def test_room_without_active_game_is_rejected():
    with pytest.raises(WebAppAuthError, match="not active"):
        authorize_crocodile_drawer("m1001707530786", 424242, {})
