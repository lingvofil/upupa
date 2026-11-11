"""Новая механика на двоих: символы, ответственность за слова и реальные реакции."""

import copy
import random
from itertools import combinations

import pytest

from games.guru import content as C
from games.guru import live, live_readings as R, textui
from tests.test_guru_live import CLIENT, HOST, STRANGER, ids, make, predict, press, seated


def tarot(session, code="t1", mode="idea"):
    press(session, HOST, "m:tarot")
    pair = next(key for key, (_, codes) in R.PAIRS.items() if code in codes)
    press(session, HOST, f"pair:{pair}")
    press(session, HOST, f"r:{code}")
    return press(session, HOST, f"say:{mode}")


@pytest.mark.parametrize("code", [f"t{i}" for i in range(1, 7)])
def test_every_tarot_link_has_a_meaningful_choice_and_can_be_reconsidered(code):
    session = seated()
    result = press(session, HOST, "m:tarot")
    assert result.events[-1].data["cards"] == list(R.CARDS)
    assert "Что внутри" in result.view.screen and "Что между" in result.view.screen
    assert not session.options, "пока не выбрана связь, готовых ответов нет"
    pair = next(key for key, (_, codes) in R.PAIRS.items() if code in codes)
    press(session, HOST, f"pair:{pair}")
    offered = session.options
    assert len(offered) == 2 and R.READINGS[code].voice in live.build_view(session).screen
    press(session, HOST, f"r:{code}")
    press(session, HOST, "back")
    assert session.phase == "pick" and session.voiced == []
    press(session, HOST, "back")
    assert session.phase == "tarot" and session.used_methods == ["tarot"]
    press(session, HOST, f"pair:{pair}")
    assert session.options == offered


@pytest.mark.parametrize("areas", list(combinations(R.AREAS, 2)))
@pytest.mark.parametrize("hand", [None, *C.LIVE_HANDS])
def test_palm_inspects_two_distinct_areas_in_the_clients_chosen_hand(areas, hand):
    session = seated()
    press(session, HOST, "m:palm", hand=None)
    result = press(session, CLIENT if hand else HOST, f"h:{hand}" if hand else "anyhand")
    assert session.phase == "palm" and session.hand == (hand or "")
    assert result.events[-1].data["drawing"][:2] == ("study", ())
    if hand:
        assert result.events[-1].data["drawing"][2] == hand
    press(session, HOST, f"area:{areas[0]}")
    before = copy.deepcopy(session, {id(session.perks): session.perks})
    with pytest.raises(ValueError):
        press(session, HOST, f"area:{areas[0]}")
    assert session == before
    result = press(session, HOST, f"area:{areas[1]}")
    assert session.options == R.PALM_PAIRS[frozenset(areas)] and session.phase == "pick"
    assert result.events[-1].data["drawing"][1] == areas


@pytest.mark.parametrize("horizon", list(R.HORIZONS))
def test_horoscope_uses_a_known_horizon_and_offers_strategies(horizon):
    session = seated()
    press(session, HOST, "m:horoscope")
    result = press(session, CLIENT, "z:leo")
    assert session.phase == "horizon" and "Лев" in result.events[-1].text
    with pytest.raises(PermissionError):
        press(session, HOST, f"d:{horizon}")
    press(session, CLIENT, f"d:{horizon}")
    assert session.horizon == R.HORIZONS[horizon] and session.options == ("g1", "g2", "g3")
    assert session.horizon in live.build_view(session).screen
    assert not session.picks and "s:what:gain" not in ids(session)


def test_silent_client_has_explicit_fallbacks_without_fabricated_answers():
    session = seated()
    press(session, HOST, "ask")
    press(session, HOST, "question:want")
    assert "Бот не читает" in live.build_view(session).prompt
    for actor in (CLIENT, STRANGER):
        with pytest.raises(PermissionError):
            press(session, actor, "noanswer")
    result = press(session, HOST, "noanswer")
    assert not any(e.kind == "answer" for e in result.events)
    press(session, HOST, "m:horoscope")
    press(session, HOST, "stars")
    press(session, HOST, "nodate")
    press(session, HOST, "r:g1")
    press(session, HOST, "say:idea")
    result = press(session, HOST, "noreaction")
    assert not session.reactions and not any(e.kind == "answer" for e in result.events)
    assert "согласие клиента не записано" in result.events[0].text


def test_two_questions_are_optional_and_client_confirms_only_their_own_answer():
    session = seated()
    press(session, HOST, "ask")
    press(session, HOST, "back")
    assert session.asked == []
    for question in ("want", "limit"):
        press(session, HOST, "ask")
        press(session, HOST, f"question:{question}")
        with pytest.raises(PermissionError):
            press(session, HOST, "said")
        press(session, CLIENT, "said")
    assert "ask" not in ids(session) and session.asked == ["want", "limit"]
    with pytest.raises(ValueError):
        press(session, HOST, "ask")


@pytest.mark.parametrize("mode", R.MODES)
def test_only_spoken_readings_are_asked_about_and_count_towards_responsibility(mode):
    session = seated()
    result = tarot(session, mode=mode)
    if mode == "draft":
        assert session.phase == "divine" and not session.voiced and session.drafts == ["t1"]
        assert not any(e.kind == "voice" for e in result.events)
    else:
        assert session.phase == "reaction" and session.voiced == [("t1", mode)]
        with pytest.raises(PermissionError):
            press(session, HOST, "feedback:yes")
        with pytest.raises(PermissionError):
            press(session, CLIENT, "noreaction")
        press(session, CLIENT, "feedback:no")
        assert session.reactions == [("t1", "no")]
    predict(session)
    assert (R.READINGS["t1"].voice in live.build_view(session).screen) == (mode != "draft")
    press(session, HOST, "a:maybe")
    assert session.confidence == ("see" if mode == "claim" else "maybe")
    result = press(session, CLIENT, "v:miss")
    assert result.metrics[-1][1]["voiced"] == session.voiced
    assert session.outcome == "miss" and session.fee == (-450 if mode == "claim" else 300)


def test_final_remembers_all_spoken_content_and_the_clients_actual_reactions():
    session = seated()
    tarot(session, code="t5")
    press(session, CLIENT, "feedback:maybe")
    press(session, HOST, "m:horoscope")
    press(session, CLIENT, "z:leo")
    press(session, CLIENT, "d:week")
    press(session, HOST, "r:g1")
    press(session, HOST, "say:idea")
    press(session, CLIENT, "feedback:no")
    press(session, HOST, "m:palm")
    press(session, HOST, "area:heart")
    press(session, HOST, "area:thumb")
    press(session, HOST, "r:l3")
    press(session, HOST, "say:idea")
    press(session, CLIENT, "feedback:yes")
    assert session.readings_left == 0
    assert not any(a.startswith("m:") for a in ids(session))
    assert session.reactions == [("t5", "maybe"), ("g1", "no"), ("l3", "yes")]
    predict(session)
    screen = live.build_view(session).screen
    assert all(R.READINGS[code].voice in screen for code, _ in session.voiced)


def test_conflict_warning_uses_history_and_ignores_drafts():
    for mode in ("idea", "claim", "draft"):
        session = seated()
        tarot(session, "t5", mode)
        if mode != "draft":
            press(session, CLIENT, "feedback:yes")
        press(session, HOST, "m:palm")
        press(session, HOST, "area:head")
        press(session, HOST, "area:thumb")
        press(session, HOST, "r:l6")
        result = press(session, HOST, "say:idea")
        assert result.metrics[0][1]["conflict"] == (mode != "draft")
        warnings = [event.text for event in result.events if event.kind == "line"]
        assert bool(warnings) == (mode != "draft")
        assert not any(event.kind == "answer" for event in result.events)


def test_draws_do_not_depend_on_the_secret_intake_or_bought_tools():
    first, second = seated(), seated()
    second.intake = {"sphere": "work", "who": "self", "emotion": "guilt"}
    for method, setup in (("tarot", ()), ("palm", ("area:heart", "area:head")),
                          ("horoscope", ("z:leo", "d:week"))):
        for session in (first, second):
            press(session, HOST, f"m:{method}")
            for action in setup:
                press(session, CLIENT if action.startswith(("z:", "d:")) else HOST, action)
        assert live.build_view(first).screen == live.build_view(second).screen
        if method == "tarot":
            for session in (first, second):
                press(session, HOST, "pair:hl")
        for session in (first, second):
            press(session, HOST, f"r:{session.options[0]}")
            press(session, HOST, "say:draft")


def test_bought_question_and_rereading_preserve_earlier_words_and_limit_repeats():
    session = live.new_session(session_id="a12", chat_id=-1, host_id=HOST, host_name="Зоя", now=0,
                               items=("ledger", "ball"))
    press(session, CLIENT, "join")
    press(session, HOST, "skip")
    for question in ("want", "limit", "date"):
        press(session, HOST, "ask")
        press(session, HOST, f"question:{question}")
        press(session, CLIENT, "said")
    assert "ask" not in ids(session)
    tarot(session, "t1", "claim")
    press(session, CLIENT, "feedback:yes")
    tarot(session, "t2", "idea")
    press(session, CLIENT, "feedback:no")
    assert session.voiced == [("t1", "claim"), ("t2", "idea")]
    assert "m:tarot" not in ids(session) and "m:palm" in ids(session)
    predict(session)
    press(session, HOST, "a:maybe")
    assert session.confidence == "see"


def test_early_farewell_needs_no_method_or_question_and_only_client_can_finish():
    session = seated()
    predict(session)
    press(session, HOST, "a:maybe")
    assert not session.used_methods and not session.asked
    for actor in (HOST, STRANGER):
        with pytest.raises(PermissionError):
            press(session, actor, "v:hit")
    press(session, CLIENT, "v:hit")
    with pytest.raises(ValueError):
        press(session, CLIENT, "v:hit")


def test_all_new_phases_have_valid_callback_data_and_enforce_actor_rights():
    phases = set()
    for seed in range(50):
        session, rng = make(), random.Random(seed)
        for _ in range(120):
            if session.finished:
                break
            view = live.build_view(session)
            phases.add(view.phase)
            for row in textui.keyboard(view):
                for _, data in row:
                    longest = data.replace(view.session_id, "f" * 32, 1)
                    assert len(longest.encode()) <= 64 and textui.parse(longest).kind == "act"
            action = rng.choice(view.actions).id
            actors = [actor for actor in (HOST, CLIENT) if live.refusal(session, actor, action) is None]
            assert len(actors) == 1
            invalid = HOST if actors[0] == CLIENT else CLIENT
            before = copy.deepcopy(session, {id(session.perks): session.perks})
            with pytest.raises(PermissionError):
                press(session, invalid, action, hand=None)
            assert before == session, "чужая кнопка не меняет даже шаг и время"
            press(session, actors[0], action, rng, hand=None)
        assert session.finished
    assert {"answer", "tarot", "palm", "pick", "mode", "reaction", "horizon", "farewell", "verdict"} <= phases
