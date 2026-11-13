"""Сеанс по схеме Guru-Web: движок, авторская история Людмилы и общий шаблон для историй смены."""

import random

import pytest

from games.guru import content as C
from games.guru import palm_art, seance, seance_casebook, seance_ludmila, textui
from games.guru import seance_engine as E

HOST = 1
SOOTHING, ALARMING = "fox.mirrors.fear", "fox.mirrors.faces"
BENIGN, DARK = "lighthouse.seahorse.study", "lighthouse.light.other"
WAIT, ASK, GRAND = "porcelain.crack.wait", "porcelain.crack.ask", "porcelain.garden.riches"


def make(story_id=seance.LUDMILA, seed=1, **kwargs):
    return seance.new_session(session_id="ab12cd34ef56", chat_id=-1, host_id=HOST, host_name="Зоя", now=0.0,
                              story_id=story_id, rng=random.Random(seed), **kwargs)


def press(session, *actions, actor=HOST):
    return [seance.act(session, actor, action, now=float(session.step + 1)) for action in actions][-1]


def ids(session):
    return [action.id for action in seance.available_actions(session)]


def texts(result):
    return [event.text for event in result.events]


def tarot(session, readings, tone=E.FIRM):
    """Рассмотреть нужные образы, выбрать по прочтению на карту и произнести."""
    if session.phase == "questions":
        press(session, "begin")
    for reading_id in readings:
        card, symbol, reading = E.find_reading(session.story, reading_id)
        index = card.symbols.index(symbol)
        if f"st:{index}" in ids(session):
            press(session, f"st:{index}")
        press(session, f"rd:{index}:{symbol.readings.index(reading)}")
    return press(session, f"tone:{tone}")


def palm(session, word, extra=()):
    content = session.content
    word_index = next(index for index, item in enumerate(content.words) if item.id == word)
    for zone in (content.words[word_index].zone, *extra):
        press(session, f"zn:{next(index for index, item in enumerate(content.zones) if item.id == zone)}")
    if session.phase == "palm":
        press(session, "words")
    return press(session, f"wd:{word_index}")


def horoscope(session, forecast, tone=E.FIRM):
    tendencies = session.content.tendencies
    planet = next(index for index, tendency in enumerate(tendencies) if any(item.id == forecast for item in tendency.forecasts))
    number = next(index for index, item in enumerate(tendencies[planet].forecasts) if item.id == forecast)
    return press(session, f"pl:{planet}", f"fc:{number}", f"tone:{tone}")


def onward(session):
    """Досмотреть сцены поступков и перейти к следующему гаданию или к напутствию."""
    while "scene" in ids(session):
        press(session, "scene")
    return press(session, "next")


def finish(session, advice=0):
    """Доиграть до конца, уклоняясь во всех оставшихся гаданиях."""
    if session.phase == "questions":
        press(session, "begin")
    while not session.finished:
        if session.phase in E.METHODS or session.phase in ("tone", "word", "forecast"):
            press(session, "hush")
        elif session.phase == "reaction":
            onward(session)
        else:
            press(session, f"adv:{advice}")
    return session.outcome


def events_of(session):
    return [event.id for event in session.state.events]


# --- содержание ---------------------------------------------------------------

def test_every_story_of_the_shift_plays_as_a_seance():
    assert len(seance.STORY_IDS) == 37 and seance.LUDMILA in seance.STORY_IDS
    for story_id in seance.STORY_IDS:
        story = seance.make_story(story_id, random.Random(3))
        content = story.content
        assert len(content.cards) == 3 and len(content.zones) == 6 and len(content.tendencies) == 5
        assert {card.art for card in content.cards} <= {card.art for card in C.TAROT_CARDS.values()}
        readings = [reading.id for card in content.cards for symbol in card.symbols for reading in symbol.readings]
        assert len(readings) == len(set(readings)) and all(len(card.symbols) > content.study_limit - 1 for card in content.cards)
        assert all(len(symbol.readings) >= 2 for card in content.cards for symbol in card.symbols)
        assert {zone.id for zone in content.zones} == set(palm_art.ZONES)
        assert {word.zone for word in content.words} <= {zone.id for zone in content.zones}
        assert all(len(tendency.forecasts) == 2 for tendency in content.tendencies)
        assert set(story.endings) <= set(content.endings) and "own" in story.endings and len(story.endings) >= 3
        assert len(content.questions) == 4 and content.intro and "{" not in content.arrival
        assert all("{" not in line and not line.startswith("—") for line, _ in content.intro)


def test_ludmila_keeps_the_content_of_the_web_version():
    content = seance_ludmila.CONTENT
    assert [card.art for card in content.cards] == ["fox", "lighthouse", "porcelain"]
    assert [len(card.symbols) for card in content.cards] == [3, 4, 3] and content.study_limit == 2 and content.inspect_limit == 3
    assert len(content.words) == 11 and sum(len(tendency.forecasts) for tendency in content.tendencies) == 10
    assert set(seance_ludmila.STORY.endings) == {"early", "grand", "chorus", "duet", "challenge", "own"}
    assert set(seance_ludmila.RECOGNITION) <= set(seance_ludmila.META) | {f.id for t in content.tendencies for f in t.forecasts}


@pytest.mark.parametrize("story_id", [seance.LUDMILA, "skeptic.mom", "tycoon.waitress", "granny.scam"])
def test_buttons_and_screens_fit_telegram(story_id):
    for seed in range(12):
        session, rng = make(story_id, seed), random.Random(seed)
        while not session.finished:
            view = seance.build_view(session)
            for row in textui.keyboard(view):
                for label, data in row:
                    assert len(data.encode()) <= 64 and len(label) <= 40 and textui.parse(data).kind == "act", (label, data)
            assert len(textui.render_seance(view)[0]) < 1500
            press(session, rng.choice([action.id for action in view.actions if action.id != "heard"]))
        assert len(textui.render_seance_result(seance.build_view(session), 1)) < 3500


# --- ход приёма ---------------------------------------------------------------

def test_story_is_told_in_beats_with_the_salon_scene():
    session = make(items=("tea",), gifts=("granny",))
    arrive, *told = seance.arrival_events(session)
    assert arrive.data["scene"] == ("jealous", ("tea",), ("granny",)) and "Людмила" in arrive.text
    assert [event.kind for event in told] == ["tell", "line"] * 3 and all(event.text.startswith("— ") for event in told[::2])
    assert ids(session) == ["q:0", "q:1", "q:2", "q:3", "begin", "heard"]


def test_two_questions_out_of_four_and_they_are_optional():
    session = make()
    result = press(session, "q:2")
    assert [event.kind for event in result.events] == ["voice", "answer"] and "услышь меня" in result.events[1].text
    press(session, "q:0")
    assert ids(session) == ["begin", "heard"] and session.state.questions == ["sing", "seen"]
    assert make().state.questions == [] and "begin" in ids(make()), "к картам можно перейти и без вопросов"


def test_heard_lines_can_be_reread_without_losing_the_turn():
    session = make()
    press(session, "q:3")
    step = session.step
    result = press(session, "heard")
    assert session.step == step and not result.fresh and result.events == ()
    text = seance.heard_text(session)
    assert all(line in text for line, _ in session.content.intro) and "столик заказала" in text
    tarot(session, (SOOTHING, BENIGN, WAIT))
    assert "Учится? Чему можно учиться" in seance.heard_text(session), "ответы на гадания тоже можно перечитать"
    assert "Карты легли ясно" not in seance.heard_text(session), "слов мадам и выводов в списке нет"


def test_tarot_reads_what_was_studied_on_each_place():
    session = make()
    opened = press(session, "begin")
    assert opened.events[-1].data["cards"] == ["fox", "lighthouse", "porcelain"] and opened.events[0].kind == "answer"
    assert "То, что видно — Лиса и зеркала" in opened.view.screen and "Четверо держат" not in opened.view.screen
    assert ids(session) == ["st:0", "st:1", "st:2", "hush"], "пока образ не рассмотрен, сказать о нём нечего"
    looked = press(session, "st:0")
    assert not looked.fresh and "Четверо держат по зеркалу" in looked.view.screen
    assert ids(session) == ["rd:0:0", "rd:0:1", "st:1", "st:2", "hush"]
    press(session, "st:2")
    assert ids(session) == ["rd:0:0", "rd:0:1", "rd:2:0", "rd:2:1", "hush"], "на карте рассматривают два образа, третий остаётся неизвестным"
    press(session, "rd:2:0")
    assert session.card == 1 and "То, что скрыто — Маяк на дне океана" in seance.build_view(session).screen
    assert "✔ То, что видно: Лис в костюме" in seance.build_view(session).screen and "prev" in ids(session)
    press(session, "st:3", "rd:3:0", "st:0", "rd:0:0")
    view = seance.build_view(session)
    assert session.phase == "tone" and ids(session) == ["tone:soft", "tone:firm", "redo", "hush"]
    assert "Раковина на дне — на месте скрытого: он прячет не встречу, а звук" in view.screen
    result = press(session, "tone:soft")
    assert texts(result)[0].startswith("— Ты: «Вот что я читаю в раскладе. Это знак, а не приговор. Лис в костюме — на месте того, что видно")
    assert session.state.last.parts == ["fox.suit.clumsy", "lighthouse.shell.sound", "porcelain.crack.wait"]
    assert session.phase == "reaction" and result.events[-1].kind == "status"


def test_palm_shows_only_what_was_inspected_and_lets_you_speak_about_it():
    session = make()
    press(session, "begin", "hush")
    opened = onward(session)
    assert opened.events[-1].data["drawing"] == ("zones", ()) and session.phase == "palm"
    assert "words" not in ids(session) and len([item for item in ids(session) if item.startswith("zn:")]) == 6
    first = press(session, "zn:2")
    assert [event.kind for event in first.events] == ["hint", "answer", "omen"] and "Холм Марса" in first.events[0].text
    assert first.events[-1].data["drawing"] == ("zones", ("mars",)) and "words" in ids(session)
    press(session, "words")
    assert ids(session) == ["wd:0", "wd:1", "back", "hush"], "сказать можно только о том участке, который осмотрен"
    press(session, "back", "zn:0", "zn:5")
    assert session.phase == "word" and "back" not in ids(session), "руку дают осмотреть трижды"
    assert "Линия судьбы" in seance.build_view(session).screen and "Холм Венеры" not in seance.build_view(session).screen
    result = press(session, "wd:0")
    assert "не отправляйте, не звоните и не решайте до утра" in texts(result)[0] and session.state.world.tempered["impulse"]


def test_horoscope_picks_one_forecast_and_its_tone():
    session = make()
    press(session, "begin", "hush")
    onward(session)
    press(session, "hush")
    opened = onward(session)
    assert session.phase == "horoscope" and "♎ Весы" in opened.view.screen and "Венера" in opened.view.screen
    picked = press(session, "pl:0")
    assert not picked.fresh and ids(session) == ["fc:0", "fc:1", "back"] and "признаются в любви" in picked.view.screen
    press(session, "fc:0")
    assert "Венера ведёт вашу неделю" in seance.build_view(session).screen
    result = press(session, "tone:soft")
    assert "Звёзды к этому склоняют, хотя и не приказывают:" in texts(result)[0]
    assert onward(session).view.phase == "final" and len(ids(session)) == 3


def test_only_the_host_plays_and_stale_actions_are_refused():
    session = make()
    with pytest.raises(PermissionError):
        press(session, "begin", actor=2)
    with pytest.raises(ValueError):
        press(session, "tone:firm")
    finish(session)
    with pytest.raises(ValueError):
        press(session, "begin")


# --- Людмила: слова становятся поступками ---------------------------------------

def test_firm_dark_words_are_acted_on_at_once_and_shown_as_a_scene():
    session = make()
    result = tarot(session, (ALARMING, DARK, WAIT), E.FIRM)
    assert events_of(session) == ["accuse"] and "Сейчас напишу им обоим" in " ".join(texts(result))
    assert not any("✉️" in text for text in texts(result)), "сцена ждёт, пока мадам на неё посмотрит"
    assert ids(session) == ["scene"] and "Она уже печатает" in seance.available_actions(session)[0].label
    scene = press(session, "scene")
    assert "🎬 Сообщение ушло" in texts(scene)[0] and any("✉️ Людмила → мужу и Алле Борисовне" in text for text in texts(scene))
    assert "next" in ids(session) and "sup" not in ids(session)
    outcome = finish(session)
    assert outcome.ending == "challenge" and outcome.refund and outcome.client_state == "displeased"
    assert "Мадам сказала уверенно" in outcome.text and session.fee == -1000
    assert outcome.predictions[0].verdict == "failed" and outcome.chain[0][1].startswith("Мадам сказала уверенно")


def test_soft_dark_words_leave_an_intent_the_madam_can_support_or_hold():
    supported, held, ignored = make(), make(), make()
    for session in (supported, held, ignored):
        tarot(session, (ALARMING, DARK, WAIT), E.SOFT)
        assert events_of(session) == [] and {"sup", "hold"} <= set(ids(session)) and session.state.last.misheard
    press(supported, "sup")
    assert events_of(supported) == ["accuse"] and supported.state.events[0].how == "support" and ids(supported) == ["scene"]
    assert "нажимали вдвоём" in finish(supported).quote
    press(held, "hold")
    assert finish(held).ending != "challenge" or held.state.events[0].later, "удержанный замысел сам не стреляет в салоне"
    onward(ignored)
    palm(ignored, "heart.mend")
    onward(ignored)
    horoscope(ignored, "moon.forget", E.SOFT)
    outcome = finish(ignored)
    assert ignored.state.events[0].how == "own-intent" and ignored.state.events[0].later and outcome.ending == "challenge"
    assert "провисел в телефоне до вечера" in outcome.text and not outcome.refund


def test_a_word_about_the_hand_changes_what_she_does_with_the_rest():
    session = make()
    tarot(session, (ALARMING, DARK, WAIT), E.SOFT)
    onward(session)
    result = palm(session, "mars.temper")
    assert "Черновик полежит" in " ".join(texts(result)) and session.state.world.intents == {}
    onward(session)
    horoscope(session, "mars.rival", E.FIRM)
    assert events_of(session) == [], "порыв придержан: уверенные слова больше не исполняются сразу"
    outcome = finish(session)
    assert outcome.ending == "own" and events_of(session) == []
    assert "к утру передумала" in outcome.predictions[1].note


def test_evading_every_reading_sends_her_to_find_out_alone():
    session = make()
    outcome = finish(session)
    assert [st.hollow for st in session.state.statements] == [True] * 3 and outcome.ending == "challenge"
    assert session.state.events[0].how == "own-frustration" and outcome.client_state == "displeased" and not outcome.refund
    assert "трижды" in outcome.quote and session.fee == 400
    assert all(prediction.verdict == "empty" for prediction in outcome.predictions)

    patient = make()
    press(patient, "begin", "hush")
    onward(patient)
    palm(patient, "fate.temper")
    assert finish(patient).ending == "own", "ей дали дело: слушать и запоминать"


def test_a_true_cautious_reading_is_recognized_and_pays_best():
    session = make()
    press(session, "q:2")
    result = tarot(session, (SOOTHING, BENIGN, WAIT), E.SOFT)
    assert "Как урок зубрит" in " ".join(texts(result)), "прочтение связалось с тем, что она рассказала сама"
    outcome = finish(session)
    assert outcome.ending == "own" and outcome.recognized and outcome.client_state == "content"
    assert "Полгода он учился ради трёх минут" in outcome.quote and session.fee == 3000
    assert outcome.predictions[0].verdict == "confirmed" and outcome.predictions[0].origin == "planned"


def test_asking_directly_spoils_the_surprise():
    session = make()
    tarot(session, (SOOTHING, BENIGN, ASK), E.FIRM)
    assert events_of(session) == ["reveal"]
    assert any("Пою я. Полгода уже" in text for text in texts(press(session, "scene")))
    assert "Хотя главное я узнала по телефону" in " ".join(texts(onward(session)))
    palm(session, "heart.mend")
    onward(session)
    horoscope(session, "mercury.shy", E.SOFT)
    outcome = finish(session)
    assert outcome.ending == "early" and outcome.client_state == "mixed" and session.fee == 1200
    assert make_evasive_after_reveal().client_state == "displeased", "узнала сама, а от мадам так ничего и не услышала"


def make_evasive_after_reveal():
    session = make()
    tarot(session, (SOOTHING, BENIGN, ASK), E.FIRM)
    return finish(session)


def test_a_promise_of_luxury_is_remembered():
    firm = make()
    tarot(firm, (SOOTHING, BENIGN, GRAND), E.FIRM)
    assert events_of(firm) == ["boast"] and finish(firm).ending == "grand"

    hinted = make()
    tarot(hinted, (SOOTHING, BENIGN, GRAND), E.SOFT)
    assert events_of(hinted) == []
    onward(hinted)
    press(hinted, "hush")
    onward(hinted)
    horoscope(hinted, "venus.gift", E.SOFT)
    assert events_of(hinted) == ["boast"] and hinted.state.events[0].how == "hints"
    assert "два раза намекнули" in finish(hinted).quote


def test_a_forecast_can_come_true_because_it_was_said():
    session = make()
    tarot(session, (SOOTHING, BENIGN, WAIT), E.SOFT)
    onward(session)
    press(session, "hush")
    onward(session)
    horoscope(session, "uranus.stage", E.FIRM)
    assert events_of(session) == ["reserve"]
    outcome = finish(session)
    assert outcome.ending == "chorus" and outcome.predictions[-1].verdict == "confirmed" and outcome.predictions[-1].origin == "caused"
    assert "из-за слов мадам" in seance.epilogue(outcome)


def test_corrections_are_three_different_acts_and_none_undoes_what_is_done():
    acted = make()
    tarot(acted, (ALARMING, DARK, WAIT), E.FIRM)
    press(acted, "scene")
    result = press(acted, "fix:admit")
    assert "Он это уже прочитал" in " ".join(texts(result)) and events_of(acted) == ["accuse"]
    assert not any(item.startswith("fix:") for item in ids(acted)), "исправить можно один раз"
    assert "Сделанного это не отменило" in finish(acted).corrections[0]

    admitted = make()
    tarot(admitted, (SOOTHING, BENIGN, WAIT), E.FIRM)
    press(admitted, "fix:admit")
    assert admitted.state.world.credibility == "shaken" and admitted.state.world.belief == "suspects"
    onward(admitted)
    press(admitted, "hush")
    onward(admitted)
    result = horoscope(admitted, "mars.rival", E.FIRM)
    assert events_of(admitted) == [] and "В прошлый раз вы тоже говорили уверенно" in " ".join(texts(result))

    retracted = make()
    tarot(retracted, (SOOTHING, BENIGN, WAIT), E.FIRM)
    press(retracted, "fix:retract")
    assert retracted.state.world.hollow == 1 and retracted.state.last.status == "retracted"
    assert finish(retracted).predictions[0].verdict == "withdrawn" and "А оно было верным" in retracted.outcome.predictions[0].note

    clarified = make()
    tarot(clarified, (ALARMING, DARK, WAIT), E.SOFT)
    press(clarified, "fix:clarify")
    assert clarified.state.world.intents == {} and clarified.state.last.status == "clarified" and "sup" not in ids(clarified)


def test_she_notices_when_the_madam_contradicts_herself():
    session = make()
    tarot(session, (SOOTHING, BENIGN, WAIT), E.FIRM)
    onward(session)
    press(session, "hush")
    onward(session)
    result = horoscope(session, "mars.rival", E.FIRM)
    assert "Вы уж определитесь, мадам" in " ".join(texts(result)) and session.state.world.credibility == "shaken"
    assert events_of(session) == [] and session.state.last.intent_state == "open", "на слово ей больше не верят"


def test_what_already_happened_changes_every_later_reaction():
    session = make()
    tarot(session, (ALARMING, DARK, WAIT), E.FIRM)
    opened = onward(session)
    assert "Она только что отправила такое" in " ".join(texts(opened))
    palm(session, "heart.mend")
    onward(session)
    result = horoscope(session, "venus.confession", E.FIRM)
    assert "Я уже отправила то сообщение" in " ".join(texts(result)) and session.state.mood != "hopeful"
    outcome = finish(session)
    assert outcome.ending == "challenge" and "Мириться она пошла первой" in outcome.afterword


# --- остальные клиенты: общий шаблон -------------------------------------------

def by_meaning(session, lens, who, what):
    """Найти прочтения шаблонной истории по смыслу: расклад у каждой партии свой."""
    found = []
    for card, value in zip(session.content.cards, (lens, who, what)):
        found.append(next(reading.id for symbol in card.symbols for reading in symbol.readings if reading.meta["value"] == value))
    return found


def test_the_same_firm_words_are_carried_out_differently_by_different_clients():
    hasty = make("jealous.lena")
    result = tarot(hasty, by_meaning(hasty, "alarm", "colleague", "cheat"), E.FIRM)
    assert events_of(hasty) == ["confront"] and "Сейчас же и выясню" in " ".join(texts(result))
    outcome = finish(hasty)
    assert outcome.ending == "caught" and outcome.recognized and hasty.story.reveal in outcome.text
    assert hasty.fee == round(hasty.content.fee * 1.5)

    careful = make("skeptic.mom")
    assert "impulse" not in careful.story.traits
    result = tarot(careful, by_meaning(careful, "alarm", "relative", "lose"), E.FIRM)
    assert events_of(careful) == [] and "с наскока не решаю" in " ".join(texts(result))
    assert {"sup", "hold"} <= set(ids(careful))


def test_naming_the_wrong_person_ends_in_a_scandal():
    session = make("jealous.lena")
    wrong = next(tag for tag in session.story.tale.who_decoys)
    tarot(session, by_meaning(session, "alarm", wrong, "cheat"), E.FIRM)
    scene = press(session, "scene")
    assert any("Что? Ты вообще о чём?" in text or "молчит" in text for text in texts(scene))
    outcome = finish(session)
    assert outcome.ending == "scandal" and outcome.refund and session.fee < 0 and "тут ни при чём" in outcome.text
    assert outcome.quote == C.ARCHETYPES["jealous"].outcomes["exposure"].format(name=session.client)


def test_a_calming_lens_holds_back_even_a_hasty_client():
    session = make("jealous.lena")
    result = tarot(session, by_meaning(session, "calm", "colleague", "cheat"), E.FIRM)
    assert events_of(session) == [] and "Сгоряча не пойду" in " ".join(texts(result))


def test_a_promise_is_banked_and_checked_against_what_really_happened():
    lucky = make("tycoon.waitress")
    assert lucky.story.truth_what == "propose" and "festive" in lucky.story.traits
    tarot(lucky, by_meaning(lucky, "calm", "newbie", "propose"), E.FIRM)
    assert events_of(lucky) == ["bank"]
    outcome = finish(lucky)
    assert outcome.ending == "jackpot" and outcome.client_state == "content" and outcome.predictions[0].verdict == "confirmed"

    session = make("blogger.capybara")
    good = next(reading.meta["value"] for symbol in session.content.cards[2].symbols for reading in symbol.readings
                if reading.meta["value"] in seance_casebook.GOOD)
    tarot(session, by_meaning(session, "calm", "friend", good), E.FIRM)
    assert events_of(session) == ["bank"] and finish(session).ending == "letdown"


def test_palm_tells_who_is_sitting_at_the_table():
    hasty, careful = make("jealous.lena"), make("skeptic.mom")
    zones = lambda session: {zone.id: zone.finding for zone in session.content.zones}  # noqa: E731
    assert "Рука быстрая" in zones(hasty)["mars"] and "сгоряча она не действует" in zones(careful)["mars"]
    assert zones(hasty)["heart"] == zones(careful)["heart"]
    assert {story_id.split(".")[0] for story_id in seance.STORY_IDS} == set(seance_casebook.ARCHETYPE_TRAITS)


def test_template_lines_do_not_assume_the_clients_gender():
    gendered = ("сама ", "сам ", "ошибся", "пошла", "пошёл", "готова", "готов ", "сказала", "сказал ", "рада", "должна", "должен")
    for key, variants in seance_casebook.LINES.items():
        for line in variants:
            assert not any(word in line.lower() for word in gendered), (key, line)


def test_random_seances_never_break_and_end_only_where_the_story_allows():
    seen = set()
    for run in range(1500):
        rng = random.Random(run)
        session = make(seance.STORY_IDS[run % len(seance.STORY_IDS)], run)
        while not session.finished:
            choices = [action for action in seance.available_actions(session) if action.id != "heard"]
            weights = [0.2 if action.id in ("prev", "redo", "back") else 1 for action in choices]
            result = press(session, rng.choices(choices, weights)[0].id)
            assert all("{" not in event.text for event in result.events)
        outcome = session.outcome
        assert outcome.ending in session.story.endings and outcome.text and outcome.quote and outcome.truth
        assert len(outcome.predictions) == 3 and seance.epilogue(outcome)
        seen.add(outcome.ending)
    assert seen >= {"challenge", "own", "scandal", "caught", "early", "letdown", "jackpot"}


# --- касса, выбор клиента и журнал ----------------------------------------------

def test_the_first_client_is_ludmila_and_then_whoever_was_seen_least():
    rng = random.Random(5)
    assert seance.pick_story((), rng) == seance.LUDMILA
    assert seance.pick_story(("terms", "nothing"), rng) == seance.LUDMILA, "развязки прежней механики в счёт не идут"
    done = [seance.ending_key(seance.LUDMILA, "own")]
    picked = {seance.pick_story(done, rng) for _ in range(200)}
    assert seance.LUDMILA not in picked and len(picked) > 20
    everyone = [seance.ending_key(story_id, "own") for story_id in seance.STORY_IDS if story_id != "granny.scam"]
    assert seance.pick_story(everyone, rng) == "granny.scam"
    assert seance.seen_endings([*done, seance.ending_key(seance.LUDMILA, "early"), "skeptic.mom:own"], seance.LUDMILA) == 2


def test_fee_depends_on_how_the_client_left():
    def outcome(state, recognized=False, refund=False):
        return E.Outcome("own", "", "", "", "", "", "", state, "", recognized, (), (), (), "", refund)

    assert [seance.fee_of(2000, outcome(state)) for state in ("content", "mixed", "displeased")] == [2000, 1200, 400]
    assert seance.fee_of(2000, outcome("content", recognized=True)) == 3000
    assert seance.fee_of(2000, outcome("displeased", refund=True)) == -1000


def test_finish_reports_what_was_said_and_what_came_of_it():
    session = make()
    press(session, "q:1")
    tarot(session, (ALARMING, DARK, WAIT), E.SOFT)
    press(session, "sup", "scene")
    while not session.finished:
        result = (press(session, "hush") if session.phase in E.METHODS else onward(session) if session.phase == "reaction"
                  else press(session, "adv:1"))
    (name, data), = [metric for metric in result.metrics if metric[0] == "finish"]
    assert data["mode"] == "seance" and data["mechanic"] == "web_v2" and data["story"] == seance.LUDMILA
    assert data["ending"] == "challenge" and data["events"] == [{"id": "accuse", "how": "support", "later": False}]
    assert data["questions"] == ["sure"] and data["advice"] == "apologize" and data["fee"] == session.fee == -1000
    assert [item["hollow"] for item in data["statements"]] == [False, True, True] and data["statements"][0]["intent"] == "supported"
    view = result.view
    assert view.finished and view.ending_title == "Алла Борисовна принимает вызов" and view.endings_total == 6
    shown = textui.render_seance_result(view, 2)
    assert "Развязка: «Алла Борисовна принимает вызов»" in shown and "открыто: 2 из 6" in shown
    assert "🔮 Что сбылось" in shown and "🧷 Почему так" in shown and "📜 Как было на самом деле" in shown


def test_abandon_summary():
    session = make()
    tarot(session, (SOOTHING, BENIGN, WAIT), E.SOFT)
    data = seance.abandon_summary(session, "timeout", now=1000.0)
    assert session.finished and data["mode"] == "seance" and data["statements"] == ["tarot:soft"] and data["phase"] == "reaction"
    assert data["story"] == seance.LUDMILA
