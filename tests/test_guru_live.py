"""Живой приём: один игрок гадает другому (ядро, без Telegram)."""

import json
import random

import pytest

from games.guru import content as C
from games.guru import live, rules, textui

HOST = 1
CLIENT = 2
STRANGER = 3


def make(wallet=0):
    return live.new_session(session_id="ab12cd34ef56", chat_id=-1, host_id=HOST, host_name="Зоя", now=0.0, wallet=wallet)


def press(session, actor, action, rng=None, now=1.0):
    return live.act(session, actor, "Гоша" if actor == CLIENT else "Зоя", action, rng or random.Random(1), now=now)


def seated(wallet=0):
    session = make(wallet)
    press(session, CLIENT, "join")
    return session


def to_verdict(confidence, wallet=0):
    session = seated(wallet)
    press(session, HOST, "m:tarot")
    press(session, HOST, "go")
    press(session, HOST, f"a:{confidence}")
    return session


def ids(session):
    return [action.id for action in live.available_actions(session)]


def test_only_someone_else_can_become_the_client():
    session = make()
    assert ids(session) == ["join"] and session.idle_limit == live.WAIT_TIMEOUT_S
    assert "Самой себе" in live.refusal(session, HOST, "join")
    with pytest.raises(PermissionError):
        press(session, HOST, "join")

    press(session, CLIENT, "join", now=30.0)

    assert session.client.name == "Гоша" and session.phase == "divine" and session.joined_at == 30.0
    assert session.idle_limit == rules.IDLE_TIMEOUT_S and session.sign == "", "знак клиент назовёт сам"


def test_buttons_belong_to_their_owners():
    session = seated()
    assert live.refusal(session, CLIENT, "m:tarot") is not None
    assert "смотри и учись" in live.refusal(session, STRANGER, "m:tarot").lower()
    assert live.refusal(session, HOST, "m:tarot") is None

    press(session, HOST, "go")
    press(session, HOST, "a:see")
    assert "Приговор" in live.refusal(session, HOST, "v:hit")
    assert live.refusal(session, CLIENT, "v:hit") is None


def test_tarot_deals_three_different_pictures():
    session = seated()
    result = press(session, HOST, "m:tarot", random.Random(5))
    ritual, omen = result.events
    assert ritual.kind == "ritual" and "колод" in ritual.text
    cards = omen.data["cards"]
    assert len(cards) == len(set(cards)) == 3 and omen.text.count("Образы:") == 3 and not omen.replace


@pytest.mark.parametrize("method", ["palm", "energy"])
def test_other_methods_replace_the_ritual_with_props(method):
    session = seated()
    result = press(session, HOST, f"m:{method}", random.Random(5))
    assert result.events[-1].replace and not result.events[-1].data


def asked_for_sign():
    session = seated()
    press(session, HOST, "m:horoscope", random.Random(5), now=20.0)
    return session


def test_horoscope_asks_the_client_for_a_sign_first():
    session = seated()
    result = press(session, HOST, "m:horoscope", random.Random(5))

    assert session.phase == "sign" and session.sign == "" and session.readings_left == live.LIVE_READINGS - 1
    assert session.used_methods == ["horoscope"] and result.metrics == (), "гадание ещё не состоялось"
    assert "Гоша" in result.view.prompt and "знак" in result.view.prompt
    assert [a.id for a in result.view.actions if a.kind == "sign"] == [f"z:{key}" for key in C.ZODIAC_IDS]


def test_client_picks_the_sign_and_the_horoscope_uses_it():
    session = asked_for_sign()
    result = press(session, CLIENT, "z:virgo", random.Random(5))

    assert session.sign == "Дева" and session.phase == "divine"
    ritual, omen = result.events
    assert ritual.kind == "ritual" and omen.replace and C.ZODIAC["Дева"][1] in omen.text
    assert "Паспорт" not in omen.text, "клиент назвал знак сам, звёзды не вмешиваются"
    assert result.metrics == (("action", {"kind": "reading", "mode": "live", "method": "horoscope", "sign_by": "client"}),)
    assert "m:horoscope" not in ids(session) and "толкуй вслух" in result.view.prompt


def test_only_the_client_can_name_the_sign():
    session = asked_for_sign()
    assert "клиент" in live.refusal(session, HOST, "z:leo")
    assert "смотри и учись" in live.refusal(session, STRANGER, "z:leo").lower()
    assert live.refusal(session, CLIENT, "z:leo") is None
    with pytest.raises(PermissionError):
        press(session, HOST, "z:leo")
    with pytest.raises(PermissionError):
        press(session, STRANGER, "z:leo")
    assert session.phase == "sign" and session.sign == ""


def test_host_can_move_on_when_the_client_is_silent():
    session = asked_for_sign()
    assert "запасной выход" in live.refusal(session, CLIENT, "stars")
    with pytest.raises(PermissionError):
        press(session, CLIENT, "stars")

    result = press(session, HOST, "stars", random.Random(5), now=60.0)

    assert session.phase == "divine" and session.sign in C.ZODIAC and "go" in ids(session)
    assert "Паспорт" in result.events[-1].text and "Гоша" in result.events[-1].text
    assert result.metrics[-1][1]["sign_by"] == "stars"


def test_silent_client_is_bounded_by_the_usual_idle_timeout():
    session = asked_for_sign()
    assert not rules.is_idle(session, now=20.0 + rules.IDLE_TIMEOUT_S - 1)
    assert rules.is_idle(session, now=20.0 + rules.IDLE_TIMEOUT_S)
    data = live.abandon_summary(session, "timeout", now=20.0 + rules.IDLE_TIMEOUT_S)
    assert data["phase"] == "sign" and data["methods_used"] == ["horoscope"]


def test_every_sign_has_a_latin_id():
    assert set(C.ZODIAC_IDS.values()) == set(C.ZODIAC) and len(C.ZODIAC_IDS) == 12
    assert all(key.isascii() and key.islower() and key.isalpha() for key in C.ZODIAC_IDS)


def test_sign_keyboard_fits_telegram():
    view = live.build_view(asked_for_sign())
    rows = textui.keyboard(view)
    assert [len(row) for row in rows] == [3, 3, 3, 3, 1], "12 знаков по три и запасной выход отдельной строкой"
    assert rows[-1][0][0] == C.LIVE_SIGN_SKIP_LABEL
    for row in rows:
        for _, data in row:
            longest = data.replace(view.session_id, "f" * 32, 1)
            assert len(longest.encode()) <= 64, "запас на самый длинный id сессии, который принимает разбор"
            parsed = textui.parse(data)
            assert parsed.kind == "act" and parsed.session_id == view.session_id and parsed.step == view.step


def test_readings_are_limited_and_do_not_repeat():
    session = seated()
    methods = ["tarot", "palm", "energy"]
    for number, method in enumerate(methods, 1):
        press(session, HOST, f"m:{method}")
        assert f"m:{method}" not in ids(session) and session.readings_left == live.LIVE_READINGS - number
    assert ids(session) == ["go"] and "пора к предсказанию" in live.build_view(session).prompt
    with pytest.raises(ValueError):
        press(session, HOST, "m:horoscope")


def test_the_engine_never_interprets_for_the_host():
    session = seated()
    press(session, HOST, "m:tarot")
    assert not any(action.kind == "interpretation" for action in live.available_actions(session))
    assert "толкуй вслух" in live.build_view(session).prompt


@pytest.mark.parametrize("verdict,confidence,outcome,fee", [
    ("hit", "maybe", "hit", 900),
    ("hit", "see", "hit", 1500),
    ("hit", "swear", "hit", 2400),
    ("partial", "see", "partial", 600),
    ("partial", "swear", "partial", -300),
    ("miss", "maybe", "miss", 300),
    ("miss", "see", "miss", -450),
    ("miss", "swear", "exposure", -1500),
    ("exposure", "maybe", "exposure", -600),
    ("exposure", "swear", "exposure", -1500),
])
def test_client_verdict_moves_host_money(verdict, confidence, outcome, fee):
    session = to_verdict(confidence, wallet=1000)
    result = press(session, CLIENT, f"v:{verdict}", now=120.0)
    assert session.finished and session.outcome == outcome and session.fee == fee
    assert result.view.wallet == 1000 + fee
    event, data = result.metrics[-1]
    assert event == "finish" and data["verdict"] == verdict and data["outcome"] == outcome and data["fee"] == fee
    assert data["mode"] == "live" and data["duration_s"] == 120.0 and data["methods_used"] == ["tarot"]


def test_sworn_miss_is_called_out():
    session = to_verdict("swear")
    texts = [event.text for event in press(session, CLIENT, "v:miss").events]
    assert any("клялась" in text for text in texts)


def test_result_shows_money_and_explains_loss():
    session = to_verdict("swear", wallet=500)
    view = press(session, CLIENT, "v:exposure").view
    text = textui.render_live_result(view)
    assert "−1 500 ₽" in text and C.LOSS_LINES["exposure"] in text and "−1 000 ₽" in text and "Зоя" in text


def test_abandon_reports_phase_and_ends_session():
    session = seated()
    press(session, HOST, "m:palm", now=50.0)
    data = live.abandon_summary(session, "timeout", now=950.0)
    assert session.finished and session.finish_reason == "timeout"
    assert data == {
        "mode": "live", "reason": "timeout", "phase": "divine", "idle_s": 900.0, "client_joined": True,
        "methods_used": ["palm"], "duration_s": 950.0, "rules": rules.RULES_VERSION,
    }


def test_waiting_room_expires_quickly_but_seance_does_not():
    session = make()
    assert not rules.is_idle(session, now=live.WAIT_TIMEOUT_S - 1) and rules.is_idle(session, now=live.WAIT_TIMEOUT_S)
    press(session, CLIENT, "join", now=10.0)
    assert not rules.is_idle(session, now=10.0 + live.WAIT_TIMEOUT_S)
    assert rules.is_idle(session, now=10.0 + rules.IDLE_TIMEOUT_S)


def test_view_is_serializable_for_future_interfaces():
    view = live.build_view(seated())
    assert json.loads(json.dumps(view.to_dict(), ensure_ascii=False))["client"] == "Гоша"


def test_every_step_fits_callback_data():
    session = seated()
    view = live.build_view(session)
    assert all(len(textui.encode(view, action.id).encode()) <= 64 for action in view.actions)
    parsed = textui.parse(textui.live_new_data(session.session_id))
    assert parsed.kind == "livenew" and parsed.session_id == session.session_id


def test_open_call_and_abandon_texts():
    session = make()
    view = live.build_view(session)
    assert "Зоя" in textui.render_live_open(view) and len(textui.render_live_open(view)) < 400
    assert textui.render_live_abandoned(view, "timeout") == textui.LIVE_WAIT_EXPIRED
    press(session, CLIENT, "join")
    assert textui.render_live_abandoned(live.build_view(session), "quit") == textui.LIVE_ABANDON_NOTES["quit"]
