"""Сеанс с Людмилой: мадам сама толкует знаки, а финал зависит от того, что и как она сказала."""

import pytest

from games.guru import content as C
from games.guru import palm_art, seance, textui

HOST = 1


def make(**kwargs):
    return seance.new_session(session_id="ab12cd34ef56", chat_id=-1, host_id=HOST, host_name="Зоя", now=0.0, **kwargs)


def press(session, *actions, actor=HOST):
    return [seance.act(session, actor, action, now=float(session.step + 1)) for action in actions][-1]


def ids(session):
    return [action.id for action in seance.available_actions(session)]


def say(session, code, mode="idea"):
    """Провести гадание до конца и произнести прочтение ``code``."""
    reading = seance.READINGS[code]
    if reading.method == "tarot":
        pair = next(key for key, (_, codes) in seance.TAROT_PAIRS.items() if code in codes)
        press(session, "tarot", f"p:{pair}")
    elif reading.method == "palm":
        areas = next(pair for pair, codes in seance.PALM_PAIRS.items() if code in codes)
        press(session, "palm", *(f"a:{area}" for area in sorted(areas)))
    else:
        press(session, "horo")
    return press(session, f"r:{code}", f"v:{mode}")


def finish(session, farewell="own"):
    return press(session, "bye", f"f:{farewell}")


def texts(result):
    return [event.text for event in result.events]


# --- содержание ---------------------------------------------------------------

def test_every_reading_is_reachable_and_complete():
    by_method = {method: [r.code for r in seance.READINGS.values() if r.method == method] for method in seance.METHODS}
    assert {method: len(codes) for method, codes in by_method.items()} == {"tarot": 6, "palm": 6, "horo": 3}
    reachable = {code for _, codes in seance.TAROT_PAIRS.values() for code in codes}
    reachable |= {code for codes in seance.PALM_PAIRS.values() for code in codes} | set(seance.HORO_CODES)
    assert reachable == set(seance.READINGS)
    for reading in seance.READINGS.values():
        assert reading.voice and reading.reply.startswith("—") and reading.reply_claim.startswith("—") and reading.link
        assert not reading.voice.endswith("."), "точку ставит оформление"
        assert set(reading.plan) <= {"press", "terms", "wait"}


def test_palm_pairs_cover_every_two_areas_and_match_the_drawing():
    assert set(seance.AREAS) == set(palm_art.AREAS)
    assert len(seance.PALM_PAIRS) == 3 and all(len(pair) == seance.PALM_LIMIT for pair in seance.PALM_PAIRS)
    assert set(seance.CARDS) <= {card.art for card in C.TAROT_CARDS.values()}


def test_buttons_fit_telegram():
    session = make()
    for actions in (["ask"], ["back", "tarot", "p:ha", "r:t1"], ["back", "back", "p:al", "r:t6", "v:draft", "palm", "a:head", "a:thumb"],
                    ["r:l5", "v:idea", "horo"], ["r:g3", "v:claim", "bye"]):
        press(session, *actions)
        view = seance.build_view(session)
        for row in textui.keyboard(view):
            for label, data in row:
                assert len(data.encode()) <= 64 and len(label) <= 32 and textui.parse(data).kind == "act"
        assert len(textui.render_seance(view)[0]) < 1000


# --- ход приёма ---------------------------------------------------------------

def test_story_is_told_in_beats_with_the_salon_scene():
    session = make(items=("tea",), gifts=("granny",))
    arrive, *told = seance.arrival_events(session)
    assert arrive.data["scene"] == ("jealous", ("tea",), ("granny",)) and "Людмила" in arrive.text
    assert [event.kind for event in told] == ["tell"] * 4 and all(event.pause > 0 for event in told)
    assert ids(session) == ["ask", "tarot", "horo", "palm", "heard", "bye"]


def test_two_questions_out_of_four():
    session = make()
    result = press(session, "ask", "q:sing")
    assert [event.kind for event in result.events] == ["ask", "answer", "status"] and "услышь меня" in result.events[1].text
    press(session, "ask", "q:want")
    assert "ask" not in ids(session) and session.asked == ["sing", "want"]
    with pytest.raises(ValueError):
        press(session, "q:seen")


def test_heard_lines_can_be_reread_without_losing_the_turn():
    session = make()
    press(session, "ask", "q:party")
    step = session.step
    result = press(session, "heard")
    assert session.step == step and not result.fresh and result.events == ()
    text = seance.heard_text(session)
    assert all(line in text for line in seance.TOLD) and "тамаду" in text
    say(session, "t4")
    assert seance.READINGS["t4"].reply in seance.heard_text(session), "ответы на гадания тоже можно перечитать"
    assert "вас не замечают" not in seance.heard_text(session).lower(), "слов мадам и выводов в списке нет"


def test_no_method_is_required():
    session = make()
    result = finish(session, "terms")
    assert session.finished and session.ending == "terms" and session.closed == []
    assert any(event.kind == "reveal" and "под яблоней" in event.text for event in result.events)


def test_tarot_links_two_cards_and_offers_two_readings():
    session = make()
    opened = press(session, "tarot")
    assert opened.events[-1].data["cards"] == list(seance.CARDS) and "Фарфоровая голова" in opened.view.screen
    assert ids(session) == ["p:ha", "p:hl", "p:al"]
    picked = press(session, "p:ha")
    assert not picked.fresh and session.options == ("t1", "t2") and "стесняется показать" in picked.view.screen
    assert ids(session) == ["r:t1", "r:t2", "back"]
    press(session, "back", "p:al")
    assert session.options == ("t5", "t6"), "связь можно передумать, пока ничего не сказано"
    mode = press(session, "r:t6")
    assert ids(session) == ["v:idea", "v:claim", "v:draft", "back"] and "Версия" in mode.view.screen


def test_palm_lets_you_inspect_two_areas_of_three():
    session = make()
    opened = press(session, "palm")
    assert opened.events[-1].data["drawing"] == ("study", ())
    first = press(session, "a:heart")
    assert not first.fresh and "расходится на две ветви" in first.view.screen and "Голова:" not in first.view.screen
    assert ids(session) == ["a:head", "a:thumb"]
    second = press(session, "a:thumb")
    assert second.events[-1].data["drawing"] == ("study", ("heart", "thumb")) and session.options == ("l3", "l4")
    assert "промежуток" in second.view.screen and "Голова:" not in second.view.screen, "третью область мадам не видела"


def test_horoscope_chooses_a_way_to_a_known_date():
    session = make()
    opened = press(session, "horo")
    assert session.options == seance.HORO_CODES and "суббота" in opened.view.screen and ids(session) == ["r:g1", "r:g2", "r:g3"]
    result = press(session, "r:g2", "v:idea")
    assert "Звёзды допускают такой путь" in texts(result)[0] and seance.plan_of(session) == "terms"


def test_horoscope_can_be_changed_once_and_it_costs_trust():
    session = make()
    say(session, "g1", "claim")
    trust = session.trust
    assert "horo" in ids(session)
    press(session, "horo")
    assert session.options == ("g2", "g3")
    result = press(session, "r:g3", "v:idea")
    assert seance.HORO_CHANGE_VOICE in texts(result)[0] and seance.HORO_CHANGE_REPLY in texts(result)
    assert session.trust == trust + seance.HORO_CHANGE_TRUST + seance.READINGS["g3"].trust
    assert [voiced.code for voiced in session.voiced] == ["g3"] and "horo" not in ids(session)
    assert seance.plan_of(session) == "wait", "в силе только новый прогноз"


def test_draft_is_not_voiced_and_changes_nothing():
    session = make()
    trust, lines = session.trust, len(session.heard)
    result = say(session, "t2", "draft")
    assert texts(result) == [seance.DRAFT_LINE] and session.voiced == [] and session.drafts == ["t2"]
    assert (session.trust, len(session.heard)) == (trust, lines) and "tarot" not in ids(session)


def test_version_and_statement_sound_different():
    idea, claim = make(), make()
    first, second = say(idea, "t2", "idea"), say(claim, "t2", "claim")
    assert "версию" in texts(first)[0] and "Карты говорят прямо" in texts(second)[0]
    assert seance.READINGS["t2"].reply in texts(first) and seance.READINGS["t2"].reply_claim in texts(second)
    assert idea.trust == claim.trust > seance.TRUST_START, "неверная версия тоже может поднять доверие"


# --- реакции учитывают последовательность --------------------------------------

def test_reaction_remembers_what_was_asked():
    plain, primed = make(), make()
    press(primed, "ask", "q:sing")
    assert "— «Услышь меня»… Он же это и поёт." in texts(say(primed, "t3"))
    assert "— «Услышь меня»… Он же это и поёт." not in texts(say(plain, "t3"))
    assert primed.trust > plain.trust


def test_contradiction_is_noticed():
    session = make()
    say(session, "l5", "claim")
    trust = session.trust
    result = say(session, "t2", "idea")
    assert not any("Вы только что говорили другое" in text for text in texts(result)), "разные темы не спорят"

    session = make()
    say(session, "t1", "claim")
    trust = session.trust
    result = say(session, "g1")
    assert session.trust == trust + seance.READINGS["g1"].trust


def test_two_statements_that_argue_cost_trust_but_two_versions_do_not():
    hard = make()
    say(hard, "l3", "claim")
    trust = hard.trust
    hard.closed.remove("palm")  # в сеансе ладонь одна; здесь проверяем само правило
    result = say(hard, "l4", "idea")
    assert any("Вы только что говорили другое" in text and seance.READINGS["l3"].voice in text for text in texts(result))
    assert hard.trust == trust + seance.CONFLICT_TRUST + seance.READINGS["l4"].trust

    soft = make()
    say(soft, "l3", "idea")
    soft.closed.remove("palm")
    assert seance.CONFLICT_SOFT in texts(say(soft, "l4", "idea"))


def test_reactions_build_on_each_other():
    session = make()
    say(session, "t2", "claim")
    assert "— И про ту, другую, я его тоже спрошу. С порога." in texts(say(session, "l6"))


# --- финалы -------------------------------------------------------------------

def ending(script, farewell="own"):
    session = make()
    for code, mode in script:
        say(session, code, mode)
    finish(session, farewell)
    return session


def test_endings_are_different_and_all_reachable():
    cases = {
        "scandal": ending([("t2", "claim"), ("l6", "idea")]),
        "talk": ending([("t1", "idea"), ("l5", "idea"), ("g1", "idea")]),
        "terms": ending([("t3", "idea"), ("l3", "claim"), ("g2", "idea")]),
        "restaurant": ending([("t4", "idea"), ("g3", "idea")]),
        "nothing": ending([("t2", "draft")]),
    }
    assert {key: session.ending for key, session in cases.items()} == {key: key for key in cases}
    assert set(cases) == set(seance.ENDINGS)
    assert len({seance.resolve(session).text for session in cases.values()}) == 5


def test_a_wrong_statement_costs_more_than_a_wrong_version():
    claimed, supposed = ending([("t2", "claim")], "press"), ending([("t2", "idea")], "press")
    assert claimed.ending == supposed.ending == "scandal"
    assert claimed.fee < supposed.fee and "Гонорар пришлось вернуть" in seance.resolve(claimed).text
    assert "Гонорар пришлось вернуть" not in seance.resolve(supposed).text


def test_a_careful_version_is_not_punished_but_weighs_less():
    careful = ending([("t2", "idea"), ("l5", "idea"), ("g2", "idea")])
    assert careful.ending == "terms", "осторожные слова не тянут за собой скандал"
    bold = ending([("t1", "claim"), ("g2", "idea")])
    assert bold.fee > ending([("t1", "idea"), ("g2", "idea")]).fee, "верное утверждение запоминают лучше версии"
    assert "она видит" in seance.resolve(bold).text


def test_farewell_can_take_her_objection_into_account():
    stubborn = ending([("l4", "claim")])
    assert stubborn.ending == "restaurant" and "как улыбаются на паспорт" in seance.resolve(stubborn).text
    heard = ending([("l4", "claim")], "terms")
    assert heard.ending == "terms", "мадам услышала «опять потерпеть» и предложила другое"


def test_saying_nothing_is_cheap_for_everyone():
    session = ending([])
    assert session.ending == "nothing" and session.fee == seance.IDLE_FEE
    assert ending([("t4", "idea")]).fee > session.fee


def test_finish_reports_what_was_said():
    session = make()
    press(session, "ask", "q:want")
    say(session, "l1", "claim")
    result = finish(session, "terms")
    (name, data), = [metric for metric in result.metrics if metric[0] == "finish"]
    assert data["mode"] == "seance" and data["ending"] == "terms" and data["voiced"] == ["l1:claim"] and data["farewell"] == "terms"
    assert data["questions"] == ["want"] and data["fee"] == session.fee == data["paid"] + data["tip"]
    view = result.view
    assert view.finished and view.ending_title == "Песня под яблоней" and view.wallet == session.fee
    assert "Песня под яблоней" in textui.render_seance_result(view, 2, 5) and "2 из 5" in textui.render_seance_result(view, 2, 5)


def test_only_the_host_plays_and_stale_actions_are_refused():
    session = make()
    with pytest.raises(PermissionError):
        press(session, "tarot", actor=2)
    with pytest.raises(ValueError):
        press(session, "r:t1")
    finish(session)
    with pytest.raises(ValueError):
        press(session, "tarot")


def test_abandon_summary():
    session = make()
    say(session, "g2")
    data = seance.abandon_summary(session, "timeout", now=1000.0)
    assert session.finished and data["mode"] == "seance" and data["voiced"] == ["g2:idea"] and data["phase"] == "hub"
