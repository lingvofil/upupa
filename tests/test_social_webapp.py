from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlencode

import pytest
from aiohttp import web

from tests import test_smoke_imports  # noqa: F401

from features.social_graph import relationships, service, webapp
from games import social_webapp
from infrastructure.persistence.sqlite_social_graph import SQLiteSocialGraphRepository


ROOT = Path(__file__).resolve().parents[1]
BOT_TOKEN = "123456789:test-social-token"
CHAT_ID = -1001707530786


def _signed_init_data(user_id=1, *, start_param="sg_m1001707530786", auth_date=None):
    fields = {
        "auth_date": str(auth_date or int(datetime.now(timezone.utc).timestamp()) - 10),
        "query_id": "social-test",
        "user": json.dumps({"id": user_id, "first_name": "Alice"}, separators=(",", ":")),
    }
    if start_param is not None:
        fields["start_param"] = start_param
    data_check_string = "\n".join(f"{key}={value}" for key, value in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, data_check_string.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


class FakeBot:
    def __init__(self, statuses):
        self.statuses = statuses
        self.calls = 0

    async def get_chat_member(self, chat_id, user_id):
        self.calls += 1
        status = self.statuses.get(user_id)
        if status is None:
            raise RuntimeError("user not found")
        return SimpleNamespace(status=status, is_member=True)


def _request(init_data="", query=None):
    return SimpleNamespace(headers={"X-Telegram-Init-Data": init_data}, query=query or {})


@pytest.fixture(autouse=True)
def _setup(monkeypatch):
    monkeypatch.setattr(social_webapp, "API_TOKEN", BOT_TOKEN)
    monkeypatch.setattr(social_webapp, "is_social_graph_enabled", lambda chat_id: True)
    social_webapp._membership_cache.clear()


def test_start_param_round_trip_and_rejections():
    assert webapp.room_for_chat(CHAT_ID) == "m1001707530786"
    assert webapp.chat_from_start_param("sg_m1001707530786") == CHAT_ID
    assert webapp.chat_from_start_param("m1001707530786") is None
    assert webapp.chat_from_start_param("sg_1001707530786") is None
    assert webapp.chat_from_start_param("sg_m12;drop") is None
    assert webapp.chat_from_start_param(None) is None
    with pytest.raises(ValueError):
        webapp.room_for_chat(12345)
    link = webapp.mini_app_link(CHAT_ID)
    assert link.endswith("?startapp=sg_m1001707530786")
    assert webapp.mini_app_link(42) is None


def test_member_is_authorized_from_signed_start_param():
    bot = FakeBot({1: "member"})
    assert asyncio.run(social_webapp.authorize_social_request(_request(_signed_init_data()), bot)) == (CHAT_ID, 1)
    asyncio.run(social_webapp.authorize_social_request(_request(_signed_init_data()), bot))
    assert bot.calls == 1  # membership is cached


@pytest.mark.parametrize(
    ("init_data", "statuses", "error"),
    [
        ("", {1: "member"}, web.HTTPUnauthorized),
        ("hash=forged&user=%7B%22id%22%3A1%7D", {1: "member"}, web.HTTPUnauthorized),
        (_signed_init_data(start_param=None), {1: "member"}, web.HTTPBadRequest),
        (_signed_init_data(start_param="tokens"), {1: "member"}, web.HTTPBadRequest),
        (_signed_init_data(user_id=2), {2: "left"}, web.HTTPForbidden),
        (_signed_init_data(user_id=3), {}, web.HTTPForbidden),
    ],
)
def test_unauthorized_requests_are_rejected(init_data, statuses, error):
    with pytest.raises(error):
        asyncio.run(social_webapp.authorize_social_request(_request(init_data), FakeBot(statuses)))


def test_disabled_social_graph_is_forbidden(monkeypatch):
    monkeypatch.setattr(social_webapp, "is_social_graph_enabled", lambda chat_id: False)
    with pytest.raises(web.HTTPForbidden):
        asyncio.run(social_webapp.authorize_social_request(_request(_signed_init_data()), FakeBot({1: "member"})))


def test_api_validates_period_and_returns_payload(monkeypatch):
    calls = []

    async def fake_payload(chat_id, *, period_days, viewer_id):
        calls.append((chat_id, period_days, viewer_id))
        return {"ok": True}

    monkeypatch.setattr(social_webapp, "build_social_payload", fake_payload)
    bot = FakeBot({1: "administrator"})

    response = asyncio.run(social_webapp.social_api(_request(_signed_init_data(), {"period": "7"}), bot))
    assert json.loads(response.text) == {"ok": True}
    assert response.headers["Cache-Control"] == "no-store"
    assert calls == [(CHAT_ID, 7, 1)]

    with pytest.raises(web.HTTPBadRequest):
        asyncio.run(social_webapp.social_api(_request(_signed_init_data(), {"period": "365"}), bot))


def test_payload_contains_graph_roles_factions_and_clean_names(tmp_path, monkeypatch):
    repo = SQLiteSocialGraphRepository(tmp_path / "stats.db")
    repo.init_schema()
    monkeypatch.setattr(service, "_repository_instance", repo)
    now = datetime.now(timezone.utc)
    names = {1: "Alice", 2: "Bob", 3: "Carol", 4: "Dan", 5: "Eve", 6: "Fay"}
    message_id = 1
    for camp in ((1, 2, 3), (4, 5, 6)):
        for a in camp:
            for b in camp:
                if a != b:
                    for _ in range(3):
                        repo.record_message_bundle(
                            CHAT_ID, message_id, now - timedelta(days=1), (a, names[a], names[a].lower()),
                            ((b, names[b], names[b].lower()),), ((b, "reply", 3.0),),
                        )
                        message_id += 1
    repo.record_message_bundle(CHAT_ID, message_id, now, (3, "Carol", "carol"), ((4, "Dan", "dan"),), ((4, "reply", 3.0),))

    async def no_events(*_args, **_kwargs):
        return []

    monkeypatch.setattr(relationships, "list_chronicle_events", no_events)
    monkeypatch.setattr(webapp, "list_events", no_events)

    payload = asyncio.run(webapp.build_social_payload(CHAT_ID, period_days=30, viewer_id=1))

    assert payload["period_days"] == 30 and payload["viewer_id"] == 1
    assert {node["name"] for node in payload["nodes"]} == set(names.values())
    assert all(node["role"] and node["color"].startswith("#") for node in payload["nodes"])
    assert sorted(sorted(f["members"]) for f in payload["factions"]) == [[1, 2, 3], [4, 5, 6]]
    assert payload["liaison_id"] in {3, 4}
    assert any(edge["genre"] == "срач" for edge in payload["edges"])
    assert payload["relationships"] and all("(@" not in rel["a_name"] for rel in payload["relationships"])
    json.dumps(payload)  # serialisable


def test_mini_app_is_routed_and_page_is_safe():
    index = (ROOT / "index.html").read_text(encoding="utf-8")
    assert 'startParam.indexOf("sg_") === 0' in index
    assert 'target.searchParams.set("view", "social")' in index

    crocodile_source = (ROOT / "games" / "crocodile.py").read_text(encoding="utf-8")
    assert 'view == "social"' in crocodile_source and 'view == "social-api"' in crocodile_source

    page = (ROOT / "social.html").read_text(encoding="utf-8")
    assert "innerHTML" not in page  # all user strings go through textContent
    assert "view=social-api" in page and "X-Telegram-Init-Data" in page
    assert "cdnjs.cloudflare.com/ajax/libs/cytoscape/" in page


def test_map_keyboard_offers_interactive_map():
    import handlers.social_graph as handler

    rows = handler.map_keyboard(CHAT_ID).inline_keyboard
    assert rows[0][0].url.endswith("startapp=sg_m1001707530786")
    assert all(button.url is None for button in handler.map_keyboard().inline_keyboard[0])
