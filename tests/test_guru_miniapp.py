import asyncio
from itertools import permutations
import json
import random
import sqlite3
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from games.guru import ludmila_deck, miniapp, salon, seance
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


def test_default_miniapp_visit_uses_the_same_selection_as_chat(tmp_path, monkeypatch):
    store = GuruVisits(tmp_path / "salon.db")
    def select(counts, rng):
        assert not counts
        return "granny.balcony"
    monkeypatch.setattr(seance, "pick_least_visited", select)
    response = store.request(11, {"op": "start", "rev": 0})
    assert response["current"]["story"] == "granny.balcony"


def test_shop_preserves_existing_fees_and_does_not_charge_twice(tmp_path):
    store = GuruVisits(tmp_path / "salon.db")
    initial = store.request(11, {"op": "home"})
    assert initial["salon"]["balance"] == 0
    assert {item["id"] for group in store.request(11, {"op": "shop"})["shop"] for item in group["items"]} == set(salon.ITEMS)
    order = {"op": "buy", "item": "ledger", "rev": 0}
    with pytest.raises(ValueError, match="Не хватает"):
        store.request(11, order)
    old_record = {"fee": 15000, "type": "jealous", "keepsake": "бинокль", "reply": None}
    with sqlite3.connect(store.path) as db:
        db.execute("INSERT INTO visits VALUES (?, ?, ?, ?)", ("old", 11, seance.LUDMILA, json.dumps(old_record)))
    started = store.request(11, {"op": "start", "rev": 0, "story": seance.LUDMILA})
    before = store.request(11, {"op": "interior"})["salon"]
    assert before["rank"] == salon.rank(0) and before["balance"] == 15000
    assert [gift["name"] for gift in before["gifts"] if gift["story"]] == ["бинокль"]
    purchased = store.request(11, {**order, "rev": started["rev"]})
    assert purchased["salon"]["rank"] == salon.rank(1)
    assert purchased["salon"]["items"] == ["ledger"]
    assert purchased["salon"]["balance"] == 3000
    assert purchased["current"] == started["current"]
    with pytest.raises(StaleTurn):
        store.request(11, {**order, "rev": started["rev"]})
    with pytest.raises(ValueError, match="уже"):
        store.request(11, {**order, "rev": purchased["rev"]})
    assert store.request(22, {"op": "home"})["salon"] == initial["salon"]
    assert GuruVisits(store.path).request(11, {"op": "home"}) == purchased
    state = store.request(11, {"op": "leave", "rev": purchased["rev"]})
    state = store.request(11, {"op": "start", "rev": state["rev"], "story": seance.LUDMILA})
    for action in ("q:0", "q:1"):
        state = store.request(11, {"op": "move", "rev": state["rev"], "action": action})
    assert any(action["kind"] == "question" for action in state["current"]["actions"])


def test_miniapp_tools_expand_choices_without_mutating_the_original_story():
    visit = miniapp.new_visit(seance.LUDMILA, ("deck", "loupe", "ledger", "tea"))
    session, events = miniapp.replay(visit)
    assert any("чабрецом" in line for line in miniapp.scene(session, events)["lines"])
    visit.pop("items")
    base, _ = miniapp.replay(visit)
    assert session.content.study_limit == base.content.study_limit + 1
    assert session.content.inspect_limit == base.content.inspect_limit + 1
    assert session.content.question_limit == base.content.question_limit + 1


def test_palm_profiles_and_findings_follow_the_saved_guest(tmp_path):
    store = GuruVisits(tmp_path / "salon.db")
    profiles = set()
    for story in seance.STORY_IDS:
        session, _ = miniapp.replay(miniapp.new_visit(story))
        palm = miniapp.palm_view(session)
        profiles.add(palm["picture"])
        assert len(palm["zones"]) == 6 and not any("finding" in zone for zone in palm["zones"])
        assert all(0 < p < 100 for zone in palm["zones"] for p in zone["point"])
    assert profiles == {"palm.webp", *(f"palm-{hand}.jpg" for hand in miniapp.HAND_TYPES)}
    assert all((ASSETS / "art" / picture).is_file() for picture in profiles)
    state = store.request(11, {"op": "start", "rev": 0, "story": "granny.balcony"})
    for action in ("begin:palm", "zn:0"):
        state = store.request(11, {"op": "move", "rev": state["rev"], "action": action})
    palm = state["current"]["palm"]
    assert palm["inspected"] == [palm["zones"][0]["id"]]
    assert palm["zones"][0]["finding"] and "finding" not in palm["zones"][1]
    assert GuruVisits(store.path).request(11, {"op": "home"}) == state


def test_horoscope_can_switch_planets_before_choosing_a_forecast(tmp_path):
    store = GuruVisits(tmp_path / "salon.db")
    state = store.request(11, {"op": "start", "rev": 0, "story": "granny.balcony"})
    for action in ("begin:horoscope", "pl:0", "pl:3"):
        state = store.request(11, {"op": "move", "rev": state["rev"], "action": action})
    scene = state["current"]
    assert scene["phase"] == "forecast" and scene["horoscope"]["selected"] == 3
    assert scene["horoscope"]["planets"][3]["name"] in scene["screen"]
    assert GuruVisits(store.path).request(11, {"op": "home"}) == state
    state = store.request(11, {"op": "move", "rev": state["rev"], "action": "fc:0"})
    assert state["current"]["phase"] == "tone" and state["current"]["horoscope"]["selected"] == 3


def test_ludmila_draw_varies_and_legacy_moves_replay(monkeypatch):
    rng = random.Random(160)
    monkeypatch.setattr(miniapp.secrets, "randbits", rng.getrandbits)
    draws = [miniapp.new_visit(seance.LUDMILA) for _ in range(95)]
    original = [card.art for card in ludmila_deck.CARDS]
    for i, variants in enumerate(ludmila_deck.ALTERNATIVES):
        assert {v["ludmila_deck"][i] for v in draws} == {original[i], *variants}
    for visit in draws:
        visit["moves"] = ["begin:tarot", "turn:1", "st:0", "rd:0:0", "turn:2", "st:0", "rd:0:0",
                          "turn:0", "st:0", "rd:0:0", "tone:soft"]
        session, _ = miniapp.replay(visit)
        assert [c.art for c in session.content.cards] == visit["ludmila_deck"]
        assert [c["art"] for c in miniapp.reading_cards(session)] == visit["ludmila_deck"]
        assert all((ASSETS / "art" / f"card-{art}.jpg").is_file() for art in visit["ludmila_deck"])
    legacy = draws[0]
    legacy.pop("ludmila_deck")
    session, _ = miniapp.replay(legacy)
    assert [card.art for card in session.content.cards] == original


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
    request.json.return_value = {"op": "interior"}
    response = asyncio.run(guru_webapp.guru_api(request, bot))
    assert response.content_type == "image/jpeg" and response.body.startswith(b"\xff\xd8")
    state = store.request(424242, {"op": "start", "rev": 0, "story": seance.LUDMILA})
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
