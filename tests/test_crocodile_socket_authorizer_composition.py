import ast
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from tests import test_smoke_imports  # noqa: F401  (fake env + heavy-library mocks)
from games import crocodile


ROOT = Path(__file__).resolve().parents[1]


class FakeSio:
    def __init__(self, session):
        self.session = dict(session)
        self.saved = []

    async def get_session(self, _sid):
        return dict(self.session)

    async def save_session(self, sid, data):
        self.session = dict(data)
        self.saved.append((sid, dict(data)))


def _attribute_assignments(path: Path, attribute: str) -> list[int]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    lines = []
    for node in ast.walk(tree):
        targets = []
        if isinstance(node, (ast.Assign, ast.AugAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        for target in targets:
            if (
                isinstance(target, ast.Attribute)
                and target.attr == attribute
                and isinstance(target.value, ast.Name)
                and target.value.id == "crocodile"
            ):
                lines.append(node.lineno)
    return lines


def test_modes_authorizer_delegates_regular_room_to_next_handler():
    from games import crocodile_modes as modes

    expected = ("m42", "-42", {"drawer_id": 1})
    downstream = AsyncMock(return_value=expected)
    data = {"room": "m42"}

    result = asyncio.run(
        modes.authorize_socket_room_with_modes(
            "sid-regular",
            data,
            downstream,
            bind_room=True,
        )
    )

    assert result == expected
    downstream.assert_awaited_once_with(
        "sid-regular",
        data,
        bind_room=True,
    )


def test_modes_authorizer_binds_and_revalidates_synthetic_room(monkeypatch):
    from games import crocodile_modes as modes

    key = "-42:d1"
    session = {"drawer_id": 222, "mode": "duel"}
    modes.canvas_sessions[key] = session
    fake_sio = FakeSio(
        {
            "telegram_user_id": 222,
            "start_param": "m42_d1",
        }
    )
    monkeypatch.setattr(modes.crocodile, "sio", fake_sio)
    downstream = AsyncMock(side_effect=AssertionError("synthetic room must not delegate"))
    data = {"room": "m42_d1"}

    try:
        bound = asyncio.run(
            modes.authorize_socket_room_with_modes(
                "sid-synthetic",
                data,
                downstream,
                bind_room=True,
            )
        )
        assert bound == ("m42_d1", key, session)
        downstream.assert_not_awaited()
        assert fake_sio.session["room"] == "m42_d1"
        assert fake_sio.session["chat_id"] == key
        assert fake_sio.session["crocodile_mode_token"] == session["_canvas_token"]

        rebound = asyncio.run(
            modes.authorize_socket_room_with_modes(
                "sid-synthetic",
                data,
                downstream,
            )
        )
        assert rebound == ("m42_d1", key, session)
        downstream.assert_not_awaited()

        fake_sio.session["crocodile_mode_token"] = "expired-token"
        with pytest.raises(modes.WebAppAuthError, match="expired Crocodile canvas"):
            asyncio.run(
                modes.authorize_socket_room_with_modes(
                    "sid-synthetic",
                    data,
                    downstream,
                )
            )
    finally:
        modes.canvas_sessions.pop(key, None)


def test_round_token_authorizer_binds_and_revalidates_current_round(monkeypatch):
    from games import crocodile_persistence as persistence

    session = {"drawer_id": 222}
    expected = ("m42", "-42", session)
    fake_sio = FakeSio({"telegram_user_id": 222, "room": "m42"})
    monkeypatch.setattr(persistence.crocodile, "sio", fake_sio)
    downstream = AsyncMock(return_value=expected)
    data = {"room": "m42"}

    bound = asyncio.run(
        persistence.authorize_socket_room_for_current_round(
            "sid-regular",
            data,
            downstream,
            bind_room=True,
        )
    )

    assert bound == expected
    downstream.assert_awaited_once_with("sid-regular", data, bind_room=True)
    round_token = session["_runtime_session_token"]
    assert fake_sio.session["crocodile_round_token"] == round_token
    assert fake_sio.saved[-1][0] == "sid-regular"

    downstream.reset_mock()
    rebound = asyncio.run(
        persistence.authorize_socket_room_for_current_round(
            "sid-regular",
            data,
            downstream,
        )
    )
    assert rebound == expected
    downstream.assert_awaited_once_with("sid-regular", data, bind_room=False)

    fake_sio.session["crocodile_round_token"] = "expired-token"
    downstream.reset_mock()
    with pytest.raises(
        persistence.crocodile.WebAppAuthError,
        match="expired Crocodile round",
    ):
        asyncio.run(
            persistence.authorize_socket_room_for_current_round(
                "sid-regular",
                data,
                downstream,
            )
        )
    downstream.assert_awaited_once_with("sid-regular", data, bind_room=False)


def test_socket_authorizer_configurator_drives_stable_entrypoint():
    original = crocodile.get_socket_room_authorizer()

    async def configured(_sid, _data, *, bind_room=False):
        return ("configured", str(bind_room), {})

    try:
        crocodile.configure_socket_room_authorizer(configured)
        assert asyncio.run(
            crocodile._authorize_socket_room("sid", {"room": "m42"}, bind_room=True)
        ) == ("configured", "True", {})
    finally:
        crocodile.configure_socket_room_authorizer(original)


def test_runtime_owns_socket_authorizer_entrypoint_and_wrapper_order():
    violations = []
    for path in sorted((ROOT / "games").glob("*.py")):
        relative = path.relative_to(ROOT).as_posix()
        for line in _attribute_assignments(path, "_authorize_socket_room"):
            violations.append(f"{relative}:{line}")

    assert not violations, (
        "crocodile._authorize_socket_room нельзя заменять прямым присваиванием; "
        "используй configure_socket_room_authorizer(): " + ", ".join(violations)
    )

    modes_source = (ROOT / "games" / "crocodile_modes.py").read_text(encoding="utf-8")
    assert "_original_authorize_socket_room" not in modes_source
    assert not _attribute_assignments(
        ROOT / "games" / "crocodile_modes.py", "_authorize_socket_room"
    )
    assert "authorize_socket_room_with_modes(" in modes_source
    assert "next_handler" in modes_source

    persistence_source = (ROOT / "games" / "crocodile_persistence.py").read_text(
        encoding="utf-8"
    )
    assert "_original_authorize_socket_room" not in persistence_source
    assert not _attribute_assignments(
        ROOT / "games" / "crocodile_persistence.py", "_authorize_socket_room"
    )
    assert "authorize_socket_room_for_current_round(" in persistence_source
    assert "next_handler" in persistence_source

    runtime_source = (ROOT / "games" / "crocodile_runtime.py").read_text(
        encoding="utf-8"
    )
    crocodile_source = (ROOT / "games" / "crocodile.py").read_text(
        encoding="utf-8"
    )
    assert "def get_socket_room_authorizer(" in crocodile_source
    assert "def configure_socket_room_authorizer(" in crocodile_source
    assert "crocodile._authorize_socket_room =" not in runtime_source

    wiring = "crocodile.configure_socket_room_authorizer("
    assert runtime_source.count(wiring) == 1

    raw_capture = runtime_source.index(
        "raw_authorize_socket_room = crocodile.get_socket_room_authorizer()"
    )
    persistence_install = runtime_source.index("persistence.configure_crocodile_runtime()")
    modes_install = runtime_source.index("configure_crocodile_modes()")
    wiring_pos = runtime_source.index(wiring)
    raw_handler = runtime_source.index("raw_authorize_socket_room,", wiring_pos)
    round_wrapper = runtime_source.index(
        "persistence.authorize_socket_room_for_current_round,", wiring_pos
    )
    modes_wrapper = runtime_source.index(
        "authorize_socket_room_with_modes,", wiring_pos
    )

    assert raw_capture < persistence_install < modes_install < wiring_pos
    assert wiring_pos < raw_handler < round_wrapper < modes_wrapper
