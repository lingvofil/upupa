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


def joined(wallet=0):
    session = make(wallet)
    press(session, CLIENT, "join")
    return session


def seated(wallet=0):
    session = joined(wallet)
    for action in ("q:love", "q:tax", "q:hope"):
        press(session, CLIENT, action)
    return session


def voice_first(session):
    return press(session, HOST, f"i:{session.pending.offered[0]}")


def predict(session, who="tax", what="lose", when="week"):
    press(session, HOST, "go")
    for slot, tag in (("who", who), ("what", what), ("when", when)):
        press(session, HOST, f"s:{slot}:{tag}")


def to_verdict(confidence, wallet=0):
    session = seated(wallet)
    press(session, HOST, "m:tarot")
    voice_first(session)
    predict(session)
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

    assert session.client.name == "Гоша" and session.phase == "intake" and session.joined_at == 30.0
    assert session.idle_limit == rules.IDLE_TIMEOUT_S and session.sign == "", "знак клиент назовёт сам"


def test_client_builds_the_story_with_buttons():
    session = joined()
    assert live.build_view(session).prompt == "Гоша, о чём беда?" and live.build_view(session).intake_filled == 0
    assert ids(session) == ["q:love", "q:work", "q:money", "q:family", "skip"]

    result = press(session, CLIENT, "q:money")
    assert session.phase == "intake" and result.view.intake_filled == 1 and result.events == ()
    assert result.metrics == (("action", {"kind": "intake", "mode": "live", "slot": "sphere", "value": "money"}),)
    assert result.view.prompt == "Гоша, кто замешан?" and "q:tax" in ids(session)

    press(session, CLIENT, "q:ex")
    result = press(session, CLIENT, "q:fear")

    assert session.phase == "divine" and session.intake == {"sphere": "money", "who": "ex", "emotion": "fear"}
    assert "пиши прямо в чат" in result.events[-1].text and "Бот отворачивается" in result.events[-1].text
    assert result.metrics[-1][1]["slot"] == "emotion" and "m:tarot" in ids(session)


def test_client_gets_a_private_confirmation():
    session = joined()
    result = press(session, CLIENT, "q:money")
    assert "деньги" in result.notice and "Мадам этого не видит" in result.notice
    assert press(session, HOST, "skip").notice is None


def test_intake_answers_stay_hidden_until_the_verdict():
    session = joined()
    seen = []

    def watch(result):
        seen.append(str(result.view.to_dict()))
        seen.extend(event.text for event in result.events)
        seen.append(textui.render_intake(result.view)[0])
        return result

    for action in ("q:love", "q:tax", "q:hope"):
        result = watch(press(session, CLIENT, action))
    watch(press(session, HOST, "m:palm"))
    watch(voice_first(session))
    watch(press(session, HOST, "go"))
    for action in ("s:who:friend", "s:what:gain", "s:when:today"):
        watch(press(session, HOST, action))
    result = watch(press(session, HOST, "a:maybe"))

    assert [text for text in seen if "Беда:" in text or "Кто замешан:" in text or "На душе:" in text] == []
    assert "intake" not in result.view.to_dict(), "ответов нет ни в одном виде для интерфейса"

    result = press(session, CLIENT, "v:partial")
    reveal = result.events[-1]
    assert reveal.kind == "reveal" and all(word in reveal.text for word in ("отношения", "налоговая", "надежда"))
    assert result.metrics[-1][1]["intake"] == {"sphere": "love", "who": "tax", "emotion": "hope"}


def test_only_the_client_fills_the_intake():
    session = joined()
    assert "Анкету заполняет клиент" in live.refusal(session, HOST, "q:love")
    assert "смотри и учись" in live.refusal(session, STRANGER, "q:love").lower()
    assert live.refusal(session, CLIENT, "q:love") is None
    for actor in (HOST, STRANGER):
        with pytest.raises(PermissionError):
            press(session, actor, "q:love")
    assert session.intake == {}


def test_divination_waits_for_the_intake():
    session = joined()
    with pytest.raises(ValueError):
        press(session, HOST, "m:tarot")
    with pytest.raises(ValueError):
        press(session, CLIENT, "q:tax")


def test_host_can_start_without_the_intake_when_the_client_is_silent():
    session = joined()
    press(session, CLIENT, "q:work")
    assert "запасной выход" in live.refusal(session, CLIENT, "skip")
    with pytest.raises(PermissionError):
        press(session, CLIENT, "skip")

    result = press(session, HOST, "skip")

    assert session.phase == "divine" and "m:tarot" in ids(session)
    assert result.events[0].text == C.LIVE_INTAKE_SKIP_LINE
    assert result.metrics == (("action", {"kind": "intake_skip", "mode": "live", "answered": 1}),)
    assert session.intake == {"sphere": "work"}, "записанное остаётся в анкете"


def test_silent_client_at_intake_is_bounded_by_the_usual_idle_timeout():
    session = joined()
    assert not rules.is_idle(session, now=1.0 + rules.IDLE_TIMEOUT_S - 1) and rules.is_idle(session, now=1.0 + rules.IDLE_TIMEOUT_S)
    assert live.abandon_summary(session, "timeout", now=1.0 + rules.IDLE_TIMEOUT_S)["phase"] == "intake"


@pytest.mark.parametrize("answers,rows", [
    ((), [2, 2, 1]),
    (("q:love",), [2, 2, 2, 2, 1, 1]),
    (("q:love", "q:tax"), [2, 2, 1]),
])
def test_intake_keyboard_fits_telegram(answers, rows):
    session = joined()
    for action in answers:
        press(session, CLIENT, action)
    view = live.build_view(session)
    keyboard = textui.keyboard(view)
    assert [len(row) for row in keyboard] == rows and keyboard[-1][0][0] == C.LIVE_INTAKE_SKIP_LABEL
    for row in keyboard:
        for _, data in row:
            assert len(data.replace(view.session_id, "f" * 32, 1).encode()) <= 64
            assert textui.parse(data).kind == "act"


def test_intake_message_counts_answers_and_loses_its_buttons_at_the_end():
    session = joined()
    press(session, CLIENT, "q:love")
    text, rows = textui.render_intake(live.build_view(session))
    assert "Анкета: Гоша" in text and "Заполнено: 1 из 3" in text and "👉 Гоша, кто замешан?" in text and rows
    assert "отношения" not in text

    press(session, CLIENT, "q:self")
    press(session, CLIENT, "q:guilt")
    text, rows = textui.render_intake(live.build_view(session))
    assert "лицом вниз" in text and "👉" not in text and rows is None


def test_silent_client_leaves_nothing_to_reveal():
    session = joined()
    press(session, HOST, "skip")
    press(session, HOST, "go")
    for action in ("s:who:friend", "s:what:gain", "s:when:today"):
        press(session, HOST, action)
    press(session, HOST, "a:maybe")
    assert not any(event.kind == "reveal" for event in press(session, CLIENT, "v:hit").events)


def test_buttons_belong_to_their_owners():
    session = seated()
    assert live.refusal(session, CLIENT, "m:tarot") is not None
    assert "смотри и учись" in live.refusal(session, STRANGER, "m:tarot").lower()
    assert live.refusal(session, HOST, "m:tarot") is None

    predict(session)
    assert "Реквизит" in live.refusal(session, CLIENT, "a:see")
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

    assert session.sign == "Дева" and session.phase == "interpret"
    ritual, omen = result.events
    assert ritual.kind == "ritual" and omen.replace and C.ZODIAC["Дева"][1] in omen.text
    assert "Паспорт" not in omen.text, "клиент назвал знак сам, звёзды не вмешиваются"
    assert all(C.WHAT[tag].forecast in omen.text for tag in session.pending.offered)
    event, data = result.metrics[-1]
    assert event == "action" and data["method"] == "horoscope" and data["slot"] == "what" and data["sign_by"] == "client"
    assert result.view.prompt == "Мадам, что озвучить?"
    voice_first(session)
    assert session.phase == "divine" and "m:horoscope" not in ids(session)


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

    assert session.phase == "interpret" and session.sign in C.ZODIAC and session.pending.slot == "what"
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
        voice_first(session)
        assert f"m:{method}" not in ids(session) and session.readings_left == live.LIVE_READINGS - number
    assert ids(session) == ["go"] and "пора к предсказанию" in live.build_view(session).prompt
    with pytest.raises(ValueError):
        press(session, HOST, "m:horoscope")


@pytest.mark.parametrize("method,slot,table", [
    ("palm", "sphere", C.SPHERES),
    ("tarot", "who", C.WHO),
    ("energy", "emotion", C.EMOTIONS),
])
def test_host_picks_which_interpretation_to_voice(method, slot, table):
    session = seated()
    press(session, HOST, f"m:{method}", random.Random(5))

    assert session.phase == "interpret" and session.pending.slot == slot and len(session.pending.offered) == live.LIVE_OPTIONS
    actions = live.available_actions(session)
    assert [a.kind for a in actions] == ["interpretation"] * live.LIVE_OPTIONS
    assert live.build_view(session).prompt == "Мадам, что озвучить?"

    tag = session.pending.offered[1]
    assert "Реквизит" in live.refusal(session, CLIENT, f"i:{tag}")
    result = press(session, HOST, f"i:{tag}")

    assert session.phase == "divine" and session.pending is None
    voice = result.events[0]
    assert voice.kind == "voice" and voice.text.startswith("— Мадам: «") and table[tag].voice.rstrip(".") in voice.text
    assert result.metrics == (("action", {"kind": "interpretation", "mode": "live", "method": method, "slot": slot, "voiced": tag}),)


def test_tarot_cards_match_the_offered_interpretations():
    session = seated()
    result = press(session, HOST, "m:tarot", random.Random(5))
    art = {C.TAROT_CARDS[C.WHO[tag].card].art for tag in session.pending.offered}
    assert set(result.events[-1].data["cards"]) == art


@pytest.mark.parametrize("method,answer", [("palm", "love"), ("tarot", "tax"), ("energy", "hope")])
def test_client_answer_appears_among_interpretations_often_but_not_always(method, answer):
    runs = 800
    found = 0
    for seed in range(runs):
        session = seated()
        press(session, HOST, f"m:{method}", random.Random(seed))
        found += answer in session.pending.offered
    assert found / runs == pytest.approx(live.LIVE_TRUTH_CHANCE, abs=0.06)


def test_horoscope_leans_towards_the_clients_trouble():
    runs = 400
    in_sphere = 0
    for seed in range(runs):
        session = seated()  # беда: отношения
        press(session, HOST, "m:horoscope", random.Random(seed))
        press(session, CLIENT, "z:leo", random.Random(seed))
        in_sphere += any(C.WHAT[tag].sphere == "love" for tag in session.pending.offered)
    assert in_sphere / runs > 0.7


def test_without_intake_interpretations_are_blind():
    session = joined()
    press(session, HOST, "skip")
    result = press(session, HOST, "m:tarot", random.Random(5))
    assert session.pending.quality == "blind" and result.metrics[-1][1]["quality"] == "blind"


def test_host_builds_the_prediction_with_buttons():
    session = seated()
    result = press(session, HOST, "go")
    assert session.phase == "who" and result.view.prompt == "Мадам, кто?" and len(ids(session)) == len(C.WHO)
    assert result.view.preview == "Кто: … · Что: … · Когда: …"

    assert "Реквизит" in live.refusal(session, CLIENT, "s:who:tax")
    press(session, HOST, "s:who:tax")
    assert session.phase == "what" and len(ids(session)) == len(C.WHAT)
    press(session, HOST, "s:what:lose")
    assert session.phase == "when" and len(ids(session)) == len(C.WHEN)
    result = press(session, HOST, "s:when:week")

    assert session.phase == "announce" and result.view.prompt == "Мадам, как объявить?"
    assert result.view.preview == "«До конца недели налоговая потеряет крупную сумму»"


def test_client_judges_a_concrete_prediction():
    session = seated()
    predict(session, who="friend", what="gain", when="today")
    result = press(session, HOST, "a:see")

    announce = result.events[0]
    assert announce.kind == "announce" and "Я отчётливо вижу: сегодня же близкий друг принесёт деньги" in announce.text
    assert session.phase == "verdict" and "Гоша" in result.view.prompt

    event, data = press(session, CLIENT, "v:hit").metrics[-1]
    assert event == "finish" and data["picks"] == {"who": "friend", "what": "gain", "when": "today"}


def test_constructor_message_edits_in_place():
    session = seated()
    press(session, HOST, "go")
    text, rows = textui.render_live_constructor(live.build_view(session))
    assert text.startswith("🔮 Предсказание") and "👉 Мадам, кто?" in text and [len(row) for row in rows] == [2, 2, 2, 2, 1]


@pytest.mark.parametrize("prefix,steps", [
    ("who", ()),
    ("what", ("s:who:tax",)),
    ("when", ("s:who:tax", "s:what:propose")),
])
def test_constructor_buttons_fit_telegram(prefix, steps):
    session = seated()
    press(session, HOST, "go")
    for action in steps:
        press(session, HOST, action)
    view = live.build_view(session)
    assert view.phase == prefix
    for row in textui.keyboard(view):
        for _, data in row:
            assert len(data.replace(view.session_id, "f" * 32, 1).encode()) <= 64 and textui.parse(data).kind == "act"


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
        "mode": "live", "reason": "timeout", "phase": "interpret", "idle_s": 900.0, "client_joined": True,
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
