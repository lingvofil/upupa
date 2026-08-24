import hashlib
import hmac
import json
from urllib.parse import urlencode

from tests import test_smoke_imports  # noqa: F401
from games import crocodile


BOT_TOKEN = "123456789:test-socket-token"
NOW = 1_800_000_000
ROOM = "m1001707530786"
CHAT_ID = "-1001707530786"
DRAWER_ID = 424242


class FakeSio:
    def __init__(self):
        self.sessions = {}
        self.entered_rooms = []
        self.emitted = []

    async def save_session(self, sid, data):
        self.sessions[sid] = dict(data)

    async def get_session(self, sid):
        return dict(self.sessions.get(sid, {}))

    async def enter_room(self, sid, room):
        self.entered_rooms.append((sid, room))

    async def emit(self, event, data, **kwargs):
        self.emitted.append((event, data, kwargs))


def _signed_init_data(user_id=DRAWER_ID, *, start_param=ROOM):
    fields = {
        "auth_date": str(NOW - 30),
        "query_id": "socket-auth-test",
        "start_param": start_param,
        "user": json.dumps(
            {"id": user_id, "first_name": "Drawer"},
            separators=(",", ":"),
        ),
    }
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


def _install_fake_socket(monkeypatch):
    fake = FakeSio()
    monkeypatch.setattr(crocodile, "sio", fake)
    monkeypatch.setattr(crocodile, "API_TOKEN", BOT_TOKEN)
    monkeypatch.setattr(crocodile.time, "time", lambda: NOW)
    crocodile.game_sessions.clear()
    return fake


def test_verified_active_drawer_can_join_and_emit_sanitized_draw(monkeypatch):
    fake = _install_fake_socket(monkeypatch)
    crocodile.game_sessions[CHAT_ID] = {"drawer_id": DRAWER_ID}

    async def scenario():
        assert await crocodile.connect(
            "sid-1", {}, {"initData": _signed_init_data()}
        ) is True
        assert await crocodile.join_room("sid-1", {"room": ROOM}) == {"ok": True}

        await crocodile.draw_step(
            "sid-1",
            {
                "room": ROOM,
                "px": 1,
                "py": 2,
                "x": 3,
                "y": 4,
                "color": "#000000",
                "unexpected": "must not be rebroadcast",
            },
        )

    import asyncio

    asyncio.run(scenario())

    assert fake.entered_rooms == [("sid-1", ROOM)]
    assert fake.emitted == [
        (
            "draw_data",
            {"px": 1, "py": 2, "x": 3, "y": 4, "color": "#000000"},
            {"room": ROOM, "skip_sid": "sid-1"},
        )
    ]


def test_verified_other_user_cannot_join_active_drawer_room(monkeypatch):
    fake = _install_fake_socket(monkeypatch)
    crocodile.game_sessions[CHAT_ID] = {"drawer_id": DRAWER_ID}

    async def scenario():
        assert await crocodile.connect(
            "sid-2", {}, {"initData": _signed_init_data(user_id=999999)}
        ) is True
        assert await crocodile.join_room("sid-2", {"room": ROOM}) == {
            "ok": False,
            "error": "unauthorized",
        }

    import asyncio

    asyncio.run(scenario())

    assert fake.entered_rooms == []
    assert fake.emitted == []


def test_bound_socket_cannot_switch_to_another_room(monkeypatch):
    fake = _install_fake_socket(monkeypatch)
    other_room = "m1001707530999"
    other_chat_id = "-1001707530999"
    crocodile.game_sessions[CHAT_ID] = {"drawer_id": DRAWER_ID}
    crocodile.game_sessions[other_chat_id] = {"drawer_id": DRAWER_ID}

    async def scenario():
        assert await crocodile.connect(
            "sid-3", {}, {"initData": _signed_init_data()}
        ) is True
        assert await crocodile.join_room("sid-3", {"room": ROOM}) == {"ok": True}

        await crocodile.draw_step(
            "sid-3",
            {
                "room": other_room,
                "px": 1,
                "py": 2,
                "x": 3,
                "y": 4,
                "color": "#000000",
            },
        )

    import asyncio

    asyncio.run(scenario())

    assert fake.entered_rooms == [("sid-3", ROOM)]
    assert fake.emitted == []


def test_tampered_init_data_is_rejected_at_socket_connect(monkeypatch):
    fake = _install_fake_socket(monkeypatch)
    init_data = _signed_init_data().replace("424242", "424243")

    async def scenario():
        assert await crocodile.connect(
            "sid-4", {}, {"initData": init_data}
        ) is False

    import asyncio

    asyncio.run(scenario())

    assert fake.sessions == {}
    assert fake.entered_rooms == []
