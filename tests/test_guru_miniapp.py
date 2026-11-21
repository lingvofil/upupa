import asyncio
from itertools import permutations
import json
import sqlite3
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from games.guru import miniapp, seance
from games.guru_webapp import ASSETS, guru_api
from infrastructure.persistence.guru_miniapp import GuruVisits, StaleTurn


ORDERS = list(permutations(("tarot", "palm", "horoscope")))


def choose(view, order):
    actions = [a["id"] for a in view["actions"]]
    for method in order:
        for prefix in ("begin:", "next:"):
            if prefix + method in actions:
                return prefix + method
    for action in ("scene", "sup", "next", "rd:0:0", "st:0", "tone:firm", "words", "wd:0", "zn:0",
                   "pl:0", "fc:0", "adv:0"):
        if action in actions:
            return action
    return actions[0]


@pytest.mark.parametrize("story", seance.STORY_IDS)
def test_every_guest_completes_a_visit_and_returns(tmp_path, story):
    db = GuruVisits(tmp_path / "salon.db")
    order = ORDERS[seance.STORY_IDS.index(story) % len(ORDERS)]
    state = db.request(11, {"op": "start", "rev": 0, "story": story})
    assert state["current"]["story"] == story
    picture = "base.webp" if story == seance.LUDMILA else "guest-" + story.replace(".", "-") + ".jpg"
    assert (ASSETS / "art" / picture).is_file()
    state = db.request(11, {"op": "move", "rev": state["rev"], "action": "q:0"})
    assert state == GuruVisits(db.path).request(11, {"op": "home"})
    with pytest.raises(StaleTurn):
        db.request(11, {"op": "move", "rev": 1, "action": "q:1"})
    for _ in range(50):
        if not state["current"]:
            break
        state = db.request(11, {"op": "move", "rev": state["rev"], "action": choose(state["current"], order)})
    record = state["record"]
    assert record["cards"] and record["epilogue"] and record["letter"]
    assert record["title"] == state["history"][0]["title"]
    assert state["history"][0]["waiting"]
    assert db.request(22, {})["history"] == []
    with pytest.raises(ValueError):
        db.request(22, {"op": "record", "id": record["id"]})
    payload = {"op": "share", "id": record["id"], "slot": 0}
    shared = db.request(11, payload)
    assert db.request(11, payload) == shared
    assert db.request(22, {"op": "card", "token": shared["token"]}) == {"card": record["cards"][0]}
    assert (ASSETS / "art" / f"card-{shared['card']['art']}.jpg").is_file()
    result = db.request(11, {"op": "reply", "id": record["id"], "rev": state["rev"], "choice": "admit"})
    assert result["history"][0]["waiting"] == 0 and result["record"]["reply"] == miniapp.second_visit(record, "admit")
    with sqlite3.connect(db.path) as conn:
        assert all(conn.execute("SELECT started,first_reading,finished,returned,shared FROM journey").fetchone())
    assert miniapp.second_visit(record, "listen")[-1] != result["record"]["reply"][-1]
    with pytest.raises(ValueError):
        db.request(11, {"op": "reply", "id": record["id"], "rev": result["rev"], "choice": "ask"})


def test_production_api_requires_telegram_identity():
    from aiohttp import web
    from aiohttp.test_utils import make_mocked_request
    with pytest.raises(web.HTTPUnauthorized):
        asyncio.run(guru_api(make_mocked_request("POST", "/game?view=guru"), None))


def test_signed_telegram_request_and_group_share(tmp_path, monkeypatch):
    from core import settings
    from games import guru_webapp
    from tests.test_webapp_auth import BOT_TOKEN, _signed_init_data
    store = GuruVisits(tmp_path / "live.db")
    monkeypatch.setattr(guru_webapp, "STORE", store)
    monkeypatch.setattr(settings, "API_TOKEN", BOT_TOKEN)
    monkeypatch.setattr(settings, "GURU_PUBLIC_GAME_URL", "https://example.org/game")
    request = SimpleNamespace(headers={"X-Telegram-Init-Data": _signed_init_data(auth_date=int(time.time()))},
                              json=AsyncMock(return_value={"op": "home"}))
    bot = SimpleNamespace(save_prepared_inline_message=AsyncMock(return_value=SimpleNamespace(id="prepared")))
    assert asyncio.run(guru_webapp.guru_api(request, bot)).status == 200
    state = store.request(424242, {"op": "start", "rev": 0})
    while state["current"]:
        state = store.request(424242, {"op": "move", "rev": state["rev"], "action": choose(state["current"], ORDERS[0])})
    shared = store.request(424242, {"op": "share", "id": state["record"]["id"], "slot": 0})
    request.json.return_value = {"op": "prepare", "token": shared["token"], "native": True, "option": 2}
    response = asyncio.run(guru_webapp.guru_api(request, bot))
    assert response.status == 200 and '"prepared": "prepared"' in response.text
    args = bot.save_prepared_inline_message.await_args.kwargs
    assert args["user_id"] == 424242 and args["allow_group_chats"]
    assert args["result"].photo_url.startswith("https://example.org/game?view=guru&asset=card-")
    assert args["result"].reply_markup.inline_keyboard[0][0].url.endswith(f"guru_c_{shared['token']}_2")
    assert shared["card"]["variants"][2]["phrase"] in args["result"].caption
    monkeypatch.setattr(settings, "GURU_PUBLIC_GAME_URL", "")
    response = asyncio.run(guru_webapp.guru_api(request, bot))
    assert json.loads(response.text)["url"] == args["result"].reply_markup.inline_keyboard[0][0].url
    assert "prepared" not in json.loads(response.text)
    bot.save_prepared_inline_message.assert_awaited_once()
