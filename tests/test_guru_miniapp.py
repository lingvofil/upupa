import asyncio
from itertools import permutations
import json
import random
import sqlite3
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from games.guru import ludmila_deck, miniapp, rules, salon, seance, seance_engine, seance_natal
from games.guru.stories import STORIES_BY_ID
from games.guru_webapp import ASSETS, guru_api
from infrastructure.persistence.guru_miniapp import GuruVisits, StaleTurn


ORDERS = list(permutations(("tarot", "palm", "horoscope")))


@pytest.fixture
def draw_guest(monkeypatch):
    def draw(story):
        monkeypatch.setattr(seance, "pick_least_visited", lambda counts, rng: story)
    return draw


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
def test_every_guest_completes_a_visit_and_returns(tmp_path, story, draw_guest):
    db = GuruVisits(tmp_path / "salon.db")
    slot = seance.STORY_IDS.index(story)
    order = ORDERS[slot % len(ORDERS)]
    draw_guest(story)
    hidden = {"catalog": [None] * len(seance.STORY_IDS), "bonusCatalog": [None] * 10, "bonusInterval": 3}
    state = db.request(11, {"op": "start", "rev": 0})
    assert state["current"]["story"] == story
    assert db.request(11, {"op": "catalog"}) == hidden
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
    payment = record["payment"]
    assert payment["base"] == STORIES_BY_ID[story].fee
    assert payment["rate"] in {-50, 20, 60, 100, 150}
    assert record["fee"] == rules._round_money(payment["base"] * payment["rate"] / 100) + payment["bonus"]
    expected = list(hidden["catalog"])
    expected[slot] = {"id": story, "name": record["client"]}
    assert GuruVisits(db.path).request(11, {"op": "catalog"}) == {**hidden, "catalog": expected}
    assert db.request(22, {"op": "catalog"}) == hidden
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


@pytest.mark.parametrize("story", seance.STORY_IDS)
def test_each_guest_has_two_stable_aspects_with_two_distinct_readings(story):
    anchor, aspects = seance_natal.CHARTS[story]
    assert len({anchor, *(aspect.planet for aspect in aspects)}) == 3
    visit = miniapp.new_visit(story)
    visit["moves"] = ["begin:natal"]
    chart = miniapp.scene(*miniapp.replay(visit))["natal"]
    second_visit = {**visit, "seed": visit["seed"] + 1}
    assert miniapp.scene(*miniapp.replay(second_visit))["natal"] == chart
    assert "note" not in chart and "quote" not in chart
    replies = set()
    for i, aspect in enumerate(aspects):
        visit["moves"] = ["begin:natal", f"na:{i}", "nq:0"]
        current = miniapp.scene(*miniapp.replay(visit))
        assert current["natal"]["selected"] == i + 1
        assert current["natal"]["note"] == aspect.note
        assert all(reading.reply not in json.dumps(current, ensure_ascii=False) for reading in aspect.readings)
        for j, reading in enumerate(aspect.readings):
            visit["moves"] = ["begin:natal", f"na:{i}", "nq:0", f"nr:{j}"]
            session, events = miniapp.replay(visit)
            assert session.state.done == {"horoscope": session.state.last.id}
            assert any(event.text == f"— {reading.reply}" for event in events)
            replies.add(reading.reply)
    assert len(replies) == 4


def test_natal_chart_resumes_switches_and_reaches_visit_record(tmp_path, draw_guest):
    draw_guest(seance.LUDMILA)
    store = GuruVisits(tmp_path / "salon.db")
    state = store.request(11, {"op": "start", "rev": 0})

    def move(action):
        nonlocal state
        state = store.request(11, {"op": "move", "rev": state["rev"], "action": action})
        return state["current"]

    move("q:0")
    clue = state["current"]["lines"][-1].removeprefix("— ")
    move("begin:horoscope")
    move("natal")
    move("na:0")
    assert move("weekly")["phase"] == "horoscope"
    move("natal")
    quotes = [action for action in state["current"]["actions"] if action["kind"] == "quote"]
    intro = seance.make_story(seance.LUDMILA, random.Random(0)).content.intro
    assert len(quotes) == len(intro) + 1
    assert clue in [action["label"] for action in quotes]
    with pytest.raises(ValueError, match="действие недоступно"):
        move("nr:1")
    move(next(action["id"] for action in quotes if action["label"] == clue))
    store = GuruVisits(store.path)
    assert store.request(11, {"op": "home"}) == state
    reading = seance_natal.CHARTS[seance.LUDMILA][1][0].readings[1]
    reaction = move("nr:1")
    assert f"— {reading.reply}" in reaction["lines"]
    assert reaction["finishedMethods"] == ["horoscope"]
    for action in ("next:natal", "next:horoscope", "nr:0", "fix:retract"):
        with pytest.raises(ValueError, match="действие недоступно"):
            move(action)
    for action in ("next:palm", "hush", "next:tarot", "turn:0", "hush", "next"):
        final = move(action)
    assert reading.reply in final["screen"]
    assert set(final["finishedMethods"]) == {"tarot", "palm", "horoscope"}
    move("adv:0")
    record = store.request(11, {"op": "record", "id": state["record"]["id"]})["record"]
    assert record["natal"]["quote"] == clue
    assert record["natal"]["question"] == reading.question
    assert record["natal"]["reply"] == reading.reply
    assert record["natal"]["afterword"] == reading.afterword
    assert reading.question not in record["epilogue"]


def test_greeting_persists_until_player_starts_the_conversation(tmp_path, draw_guest):
    store = GuruVisits(tmp_path / "salon.db")
    draw_guest(seance.LUDMILA)
    arrived = store.request(11, {"op": "start", "rev": 0})
    assert arrived["current"]["greetingPending"]
    assert GuruVisits(store.path).request(11, {"op": "home"}) == arrived
    greeted = store.request(11, {"op": "greet", "rev": arrived["rev"]})
    assert greeted["current"] == {**arrived["current"], "greetingPending": False}
    assert GuruVisits(store.path).request(11, {"op": "home"}) == greeted
    with pytest.raises(StaleTurn):
        store.request(11, {"op": "greet", "rev": arrived["rev"]})
    answered = store.request(11, {"op": "move", "rev": greeted["rev"], "action": "q:0"})
    assert not answered["current"]["greetingPending"]


def test_miniapp_uses_server_draw_even_if_old_client_sends_story(tmp_path, monkeypatch):
    store = GuruVisits(tmp_path / "salon.db")
    def select(counts, rng):
        assert not counts
        return "granny.balcony"
    monkeypatch.setattr(seance, "pick_least_visited", select)
    response = store.request(11, {"op": "start", "rev": 0, "story": seance.LUDMILA})
    assert response["current"]["story"] == "granny.balcony"
    store.request(11, {"op": "leave", "rev": response["rev"]})
    assert store.request(11, {"op": "catalog"})["catalog"] == [None] * len(seance.STORY_IDS)


def test_shop_preserves_existing_fees_and_does_not_charge_twice(tmp_path, draw_guest):
    store = GuruVisits(tmp_path / "salon.db")
    initial = store.request(11, {"op": "home"})
    assert initial["salon"]["balance"] == 0
    items = [item for group in store.request(11, {"op": "shop"})["shop"] for item in group["items"]]
    assert {item["id"] for item in items} == set(miniapp.ITEMS)
    assert set(salon.ITEMS) <= set(miniapp.ITEMS)
    assert all((ASSETS / "art" / item["picture"]).is_file() for item in items)
    assert all((ASSETS / "art" / f"display-{item['id']}.webp").is_file() for item in items)
    order = {"op": "buy", "item": "ledger", "rev": 0}
    with pytest.raises(ValueError, match="Не хватает"):
        store.request(11, order)
    old_record = {"fee": 15000, "type": "jealous", "keepsake": "бинокль", "reply": None}
    with sqlite3.connect(store.path) as db:
        db.execute("INSERT INTO visits VALUES (?, ?, ?, ?)", ("old", 11, seance.LUDMILA, json.dumps(old_record)))
    draw_guest(seance.LUDMILA)
    started = store.request(11, {"op": "start", "rev": 0})
    assert [guest for guest in store.request(11, {"op": "catalog"})["catalog"] if guest] == [
        {"id": seance.LUDMILA, "name": "Людмила"}
    ]
    before = store.request(11, {"op": "interior"})["salon"]
    assert before["rank"] == salon.rank(0) and before["balance"] == 15000
    assert [gift["name"] for gift in before["gifts"] if gift["story"]] == ["бинокль"]
    purchased = store.request(11, {**order, "rev": started["rev"]})
    assert purchased["salon"]["rank"] == salon.rank(1)
    assert purchased["salon"]["items"] == ["ledger"]
    assert purchased["salon"]["balance"] == 3000
    assert purchased["salon"]["fees"] == 15000 and purchased["salon"]["spent"] == 12000
    assert purchased["current"] == started["current"]
    with pytest.raises(StaleTurn):
        store.request(11, {**order, "rev": started["rev"]})
    with pytest.raises(ValueError, match="уже"):
        store.request(11, {**order, "rev": purchased["rev"]})
    assert store.request(22, {"op": "home"})["salon"] == initial["salon"]
    assert GuruVisits(store.path).request(11, {"op": "home"}) == purchased
    state = store.request(11, {"op": "leave", "rev": purchased["rev"]})
    state = store.request(11, {"op": "start", "rev": state["rev"]})
    for action in ("q:0", "q:1"):
        state = store.request(11, {"op": "move", "rev": state["rev"], "action": action})
    assert any(action["kind"] == "question" for action in state["current"]["actions"])


def test_local_shop_can_place_every_item_without_affecting_regular_prices(tmp_path):
    paid = GuruVisits(tmp_path / "paid.db")
    with pytest.raises(ValueError, match="Не хватает"):
        paid.request(11, {"op": "buy", "item": "orrery", "rev": 0, "freeShop": True})
    assert not paid.request(11, {"op": "shop"})["freeShop"]
    preview = GuruVisits(tmp_path / "preview.db", free_shop=True)
    catalog = preview.request(11, {"op": "shop"})
    assert catalog["freeShop"]
    state = preview.request(11, {"op": "home"})
    for group in catalog["shop"]:
        for item in group["items"]:
            assert item["price"] == 0
            state = preview.request(11, {"op": "buy", "item": item["id"], "rev": state["rev"]})
    assert {item["id"] for item in state["salon"]["collection"]} == set(miniapp.ITEMS)
    assert state["salon"]["balance"] == 0
    assert preview.request(11, {"op": "home"}) == state
    assert not preview.request(22, {"op": "home"})["salon"]["collection"]
    with pytest.raises(ValueError, match="уже"):
        preview.request(11, {"op": "buy", "item": "orrery", "rev": state["rev"]})


def test_miniapp_tools_expand_choices_without_mutating_the_original_story():
    visit = miniapp.new_visit(seance.LUDMILA, ("deck", "loupe", "ledger", "tea"))
    session, events = miniapp.replay(visit)
    assert any("чабрецом" in line for line in miniapp.scene(session, events)["lines"])
    visit.pop("items")
    base, _ = miniapp.replay(visit)
    assert session.content.study_limit == base.content.study_limit + 1
    assert session.content.inspect_limit == base.content.inspect_limit + 1
    assert session.content.question_limit == base.content.question_limit + 1


def test_palm_profiles_and_findings_follow_the_saved_guest(tmp_path, draw_guest):
    store = GuruVisits(tmp_path / "salon.db")
    profiles = set()
    for story in seance.STORY_IDS:
        session, _ = miniapp.replay(miniapp.new_visit(story))
        palm = miniapp.palm_view(session)
        profiles.add(palm["picture"])
        assert len(palm["zones"]) == 6 and not any("finding" in zone for zone in palm["zones"])
        assert all(0 < p < 100 for zone in palm["zones"] for p in zone["point"])
    assert len(profiles) == len(seance.STORY_IDS)
    assert "palm.webp" in profiles
    assert all((ASSETS / "art" / picture).is_file() for picture in profiles)
    draw_guest("granny.balcony")
    state = store.request(11, {"op": "start", "rev": 0})
    for action in ("begin:palm", "zn:0"):
        state = store.request(11, {"op": "move", "rev": state["rev"], "action": action})
    palm = state["current"]["palm"]
    assert palm["inspected"] == [palm["zones"][0]["id"]]
    assert palm["zones"][0]["finding"] and "finding" not in palm["zones"][1]
    assert GuruVisits(store.path).request(11, {"op": "home"}) == state


@pytest.mark.parametrize("story, inspected, next_zone, word_count", [
    (seance.LUDMILA, ("zn:1", "zn:2", "zn:4"), "zn:5", 8),
    ("chat.mixtape", ("zn:0", "zn:1", "zn:4"), "zn:2", 7),
])
def test_loupe_resumes_at_three_inspections_and_accepts_the_fourth(tmp_path, story, inspected, next_zone, word_count):
    store = GuruVisits(tmp_path / "salon.db")
    store.request(11, {"op": "home"})
    visit = miniapp.new_visit(story, ("loupe",))
    visit.pop("greeting_pending")
    visit["moves"] = ["begin:palm", *inspected]
    with sqlite3.connect(store.path) as db:
        db.execute("INSERT INTO salons VALUES (?, ?, ?)", (11, 4, json.dumps(visit)))

    before = store.request(11, {"op": "home"})
    assert before["current"]["phase"] == "palm" and len(before["current"]["palm"]["inspected"]) == 3
    fourth = store.request(11, {"op": "move", "rev": before["rev"], "action": next_zone})
    current = fourth["current"]
    assert current["phase"] == "word" and len(current["palm"]["inspected"]) == current["palm"]["limit"] == 4
    interpretations = [action for action in current["actions"] if action["id"].startswith("wd:")]
    assert len(interpretations) == word_count
    assert all(f"{i}. {action['label']}" in current["screen"] for i, action in enumerate(interpretations, 1))
    assert GuruVisits(store.path).request(11, {"op": "home"}) == fourth
    result = store.request(11, {"op": "move", "rev": fourth["rev"], "action": interpretations[-1]["id"]})
    assert result["current"]["phase"] == "reaction" and result["current"]["finishedMethods"] == ["palm"]


def test_horoscope_can_switch_planets_before_choosing_a_forecast(tmp_path, draw_guest):
    store = GuruVisits(tmp_path / "salon.db")
    draw_guest("granny.balcony")
    state = store.request(11, {"op": "start", "rev": 0})
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


def test_luxury_purchase_updates_room_but_not_ranking(tmp_path):
    store = GuruVisits(tmp_path / "salon.db")
    assert not store.request(1, {"op": "leaderboard"})["leaders"]
    with sqlite3.connect(store.path) as db:
        for owner, suffix, amount, days in ((1, "a", 170000, 1), (1, "b", -300, 1), (2, "c", 190000, 8), (3, "d", 5000, 0)):
            db.execute("INSERT INTO visits VALUES (?, ?, ?, ?)", (suffix, owner, seance.LUDMILA, json.dumps({"fee": amount})))
            db.execute("INSERT INTO journey(id, owner, finished) VALUES (?, ?, strftime('%s','now',?))",
                       (suffix, owner, f"-{days} days"))
    board = store.request(1, {"op": "rename", "name": "Салон  на чердаке"})
    assert board["leaders"][0] == {"name": "Салон на чердаке", "earned": 169700, "visits": 2, "self": True, "place": 1}
    before = store.request(1, {"op": "home"})
    bought = store.request(1, {"op": "buy", "item": "orrery", "rev": before["rev"]})
    assert bought["salon"]["balance"] == 29700
    assert bought["salon"]["picture"] == "salon-grand.jpg" and bought["salon"]["rank"] == salon.rank(12)
    assert bought["salon"]["collection"][0]["picture"] == "object-orrery.jpg"
    assert store.request(1, {"op": "leaderboard"}) == board
    all_time = store.request(1, {"op": "leaderboard", "period": "all"})
    assert all_time["own"]["place"] == 2
    assert all_time["leaders"][0]["earned"] == 190000 and "owner" not in all_time["leaders"][0]
    updated = store.request(1, {"op": "leaderboard"}, profile=("Зоя Петрова", 2))
    assert updated["profileName"] == "Зоя Петрова" and updated["own"]["name"] == "Салон на чердаке"
    default = store.request(1, {"op": "rename", "name": ""})["own"]["name"]
    assert default == "Зоя Петрова"
    stale = store.request(1, {"op": "leaderboard", "period": "all"}, profile=("Старое имя", 1))
    assert stale["own"]["name"] == default


def test_bonus_guests_wait_for_three_finished_ordinary_visits(tmp_path, draw_guest):
    store = GuruVisits(tmp_path / "salon.db")
    draw_guest(seance.LUDMILA)
    state = store.request(11, {"op": "home"})

    def do(op, **fields):
        nonlocal state
        state = store.request(11, {"op": op, "rev": state["rev"], **fields})
        return state["current"]

    assert not do("start", story="chat.alina")["bonus"]
    do("leave")
    for _ in range(3):
        assert not do("start")["bonus"]
        while state["current"]:
            do("move", action=choose(state["current"], ORDERS[0]))
    assert store.request(11, {"op": "catalog"})["bonusCatalog"] == [None] * 10
    first = do("start")
    assert first["bonus"] and state["openedCount"] == 2
    assert {"id": first["story"], "name": first["client"]} in store.request(11, {"op": "catalog"})["bonusCatalog"]
    do("leave")
    assert {"id": first["story"], "name": first["client"]} in store.request(11, {"op": "catalog"})["bonusCatalog"]
    assert not do("start")["bonus"]
    while state["current"]:
        do("move", action=choose(state["current"], ORDERS[0]))
    second = do("start")
    assert second["bonus"]
    while state["current"]:
        do("move", action=choose(state["current"], ORDERS[0]))
    assert state["record"]["bonus"]
    revealed = {guest["id"] for guest in store.request(11, {"op": "catalog"})["bonusCatalog"] if guest}
    assert revealed == {first["story"], second["story"]}
    assert not do("start")["bonus"]


@pytest.mark.parametrize("story, guest", miniapp.GUESTS.items())
def test_chat_guest_has_a_full_visit_and_dated_horoscope(tmp_path, story, guest):
    store = GuruVisits(tmp_path / "salon.db")
    store.request(11, {"op": "home"})
    with sqlite3.connect(store.path) as db:
        db.executemany("INSERT INTO visits VALUES (?, 11, ?, '{\"fee\":0}')",
                       [(key, key) for key in miniapp.GUESTS if key != story]
                       + [(f"old-{i}", key) for i, key in enumerate(seance.STORY_IDS[:30])])
    state = store.request(11, {"op": "start", "rev": 0})
    assert state["current"]["story"] == story and state["current"]["bonus"]
    assert state["current"]["greeting"] == guest.greeting
    assert (ASSETS / "art" / f"guest-{story.replace('.', '-')}.jpg").is_file()
    for action in ("q:0", "begin:horoscope", "natal", "na:0", "nq:0", "nr:0"):
        state = store.request(11, {"op": "move", "rev": state["rev"], "action": action})
        if action == "begin:horoscope":
            assert state["current"]["horoscope"]["sign"].endswith(guest.sign or "Гороскоп бота")
    assert GuruVisits(store.path).request(11, {"op": "home"}) == state
    for _ in range(50):
        if not state["current"]:
            break
        state = store.request(11, {"op": "move", "rev": state["rev"], "action": choose(state["current"], ORDERS[0])})
        if state["current"] and state["current"].get("palm"):
            assert (ASSETS / "art" / state["current"]["palm"]["picture"]).is_file()
    record = state["record"]
    assert record["bonus"] and record["cards"] and record["natal"] and record["letter"].endswith(guest.postscript)
    assert {"id": story, "name": record["client"]} in store.request(11, {"op": "catalog"})["bonusCatalog"]
    assert not store.request(11, {"op": "start", "rev": state["rev"]})["current"]["bonus"]


def test_pepe_only_says_pepe_and_gives_observable_clues():
    visit = miniapp.new_visit("chat.pepe")
    session, events = miniapp.replay(visit)
    first = miniapp.scene(session, events, greeting_pending=True)
    assert first["greeting"] == "пепе" and first["visualClues"]
    assert "−3 жетона" in " ".join(first["lines"])
    for step, action in enumerate(("q:0", "begin:natal", "na:0", "nq:2", "nr:0", "next:palm", "zn:0"), 1):
        result = seance.act(session, 1, action, now=step)
        if action == "q:0":
            assert any(e.kind == "hint" and "исходном стикере" in e.text for e in result.events)
            assert "исходном стикере" in seance.natal_quotes(session)[2]
        if action == "nr:0":
            assert any(e.kind == "hint" and "исходном стикере" in e.text for e in result.events)
    palm = miniapp.palm_view(session)
    assert all(zone.get("remark", "пепе") == "пепе" for zone in palm["zones"])
    for _ in range(40):
        if session.finished:
            break
        action = choose(miniapp.scene(session, result.events), ORDERS[0])
        result = seance.act(session, 1, action, now=session.step + 1)
    record = miniapp.close_visit(session, result.events)
    assert set(record["quote"].split()) == set(record["letter"].split()) == {"пепе"}
    assert set(record["natal"]["reply"].split()) == {"пепе"}
    for choice in miniapp.REPLIES:
        assert set(miniapp.second_visit(record, choice)[1].removeprefix("— ").split()) == {"пепе"}

    branch = seance_engine.new_state(session.story)
    statement = seance_engine.Statement("try", "tarot", [], "firm", False, "", "")
    session.story._confront(branch, "statement", statement, "friend")
    session.story._bank(branch, "statement", statement, "gain")
    spoken = [beat for beat in session.state.log + branch.log if beat.kind == "client" or beat.who.startswith("Pepe II")]
    assert {"client", "message", "voice"} <= {beat.kind for beat in spoken}
    assert all(set(beat.text.split()) == {"пепе"} for beat in spoken)


def test_seen_tarot_cards_rotate_even_after_leaving_and_reopening(tmp_path, draw_guest):
    store = GuruVisits(tmp_path / "salon.db")
    draw_guest("jealous.lena")
    state = store.request(11, {"op": "home"})
    with sqlite3.connect(store.path) as db:
        db.execute("INSERT INTO visits VALUES (?, ?, ?, ?)",
                   ("legacy", 11, seance.LUDMILA, json.dumps({"fee": 0, "cards": [{"art": "fox"}]})))

    def do(op, **fields):
        nonlocal state
        state = store.request(11, {"op": op, "rev": state["rev"], **fields})
        return state["current"]

    seen = {"fox"}
    for _ in range(12):
        do("start")
        do("move", action="begin:tarot")
        view = do("move", action="turn:0")
        assert view["art"] not in seen
        seen.add(view["art"])
        store = GuruVisits(store.path)
        assert store.request(11, {"op": "home"}) == state
        do("leave")


def test_legacy_casebook_visit_keeps_its_seeded_tarot():
    story = "granny.balcony"
    seed = 495
    original = seance.make_story(story, random.Random(seed), STORIES_BY_ID[story].names[0])
    saved = {"id": "old-active", "story": story, "seed": seed, "moves": ["begin:tarot", "turn:1", "st:0"]}
    session, events = miniapp.replay(saved)
    assert session.content.cards == original.content.cards
    assert miniapp.scene(session, events)["art"] == original.content.cards[1].art


def test_signed_telegram_request_and_group_share(tmp_path, monkeypatch, draw_guest):
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
    draw_guest(seance.LUDMILA)
    state = store.request(424242, {"op": "start", "rev": 0})
    while state["current"]:
        state = store.request(424242, {"op": "move", "rev": state["rev"], "action": choose(state["current"], ORDERS[0])})
    request.json.return_value = {"op": "leaderboard", "period": "all", "name": "Поддельное имя"}
    board = asyncio.run(guru_webapp.guru_api(request, bot))
    assert json.loads(board.text)["own"]["name"] == "Tester"
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
