import ast
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from tests import test_smoke_imports  # noqa: F401  (fake env + heavy-library mocks)


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


def test_runtime_owns_socket_authorizer_entrypoint_and_wrapper_order():
    violations = []
    for path in sorted((ROOT / "games").glob("*.py")):
        relative = path.relative_to(ROOT).as_posix()
        if relative == "games/crocodile_runtime.py":
            continue
        for line in _attribute_assignments(path, "_authorize_socket_room"):
            violations.append(f"{relative}:{line}")

    assert not violations, (
        "crocodile._authorize_socket_room должен собираться только в "
        "games/crocodile_runtime.py: " + ", ".join(violations)
    )

    modes_source = (ROOT / "games" / "crocodile_modes.py").read_text(encoding="utf-8")
    assert "_original_authorize_socket_room" not in modes_source
    assert "authorize_socket_room_with_modes(" in modes_source
    assert "next_handler" in modes_source

    runtime_source = (ROOT / "games" / "crocodile_runtime.py").read_text(
        encoding="utf-8"
    )
    assignment = (
        "crocodile._authorize_socket_room = _compose_socket_room_authorizer("
    )
    assert runtime_source.count(assignment) == 1

    raw_capture = runtime_source.index(
        "raw_authorize_socket_room = crocodile._authorize_socket_room"
    )
    modes_install = runtime_source.index("configure_crocodile_modes()")
    wiring = runtime_source.index(assignment)
    raw_handler = runtime_source.index("raw_authorize_socket_room,", wiring)
    modes_wrapper = runtime_source.index("authorize_socket_room_with_modes,", wiring)

    assert raw_capture < modes_install < wiring
    assert wiring < raw_handler < modes_wrapper
