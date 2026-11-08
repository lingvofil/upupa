"""Салон «Гуру»: ядро без Telegram — контент, правила приёма, контракт, текстовый адаптер."""

import ast
import json
import random
import string
from pathlib import Path

import pytest

from games.guru import content as C
from games.guru import rules, textui
from games.guru.model import WHEN_TRUTH, Knowledge
from games.guru.stories import STORIES, STORIES_BY_ID, stories_of

ROOT = Path(__file__).resolve().parents[1]
SID = "ab12cd34ef56"


class ScriptedRandom(random.Random):
    """``random()`` отдаёт броски по сценарию (потом 0.5); выбор — первый, без перемешивания."""

    def __init__(self, rolls=()):
        super().__init__(0)
        self.rolls = list(rolls)

    def random(self):
        return self.rolls.pop(0) if self.rolls else 0.5

    def choice(self, seq):
        return seq[0]

    def sample(self, population, k):
        return list(population)[:k]

    def shuffle(self, x):
        return None


def make(*story_ids, rolls=(), now=0.0):
    rng = ScriptedRandom(rolls)
    session = rules.new_session(session_id=SID, chat_id=-1, player_id=7, player_name="Зоя", rng=rng, now=now,
                                plan=story_ids or None)
    return session, rng


def act(session, rng, *actions, actor=7):
    return [rules.act(session, actor, action, rng, now=float(session.step + 1)) for action in actions]


def read(session, rng, method, tag):
    return act(session, rng, f"m:{method}", f"i:{tag}")[-1]


def to_constructor(session, rng):
    client = session.client
    if client.phase == "clarify":
        act(session, rng, "c:stop")
    if client.phase == "divine":
        act(session, rng, "go")


def predict(session, rng, who, what, when, confidence):
    to_constructor(session, rng)
    return act(session, rng, f"s:who:{who}", f"s:what:{what}", f"s:when:{when}", f"a:{confidence}")[-1]


def wrong(client, slot):
    """Вариант мимо правды, не задевающий джекпот и ловушку."""
    combos = {tag for combo in (client.story.jackpot, client.story.trap) if combo for s, tag in combo.picks if s == slot}
    return next(t for t in client.options[slot] if t != client.truth[slot] and t not in combos)


def miss(session, rng, confidence):
    client = session.client
    return predict(session, rng, wrong(client, "who"), wrong(client, "what"), wrong(client, "when"), confidence)


def hit(session, rng, confidence):
    truth = session.client.truth
    return predict(session, rng, truth["who"], truth["what"], truth["when"], confidence)


def next_client(session, rng):
    if not session.finished:
        act(session, rng, "next")


def report_of(result):
    return result.finished_client.report


def metrics(results, name):
    return [data for result in results for event, data in result.metrics if event == name]


def texts(result):
    return "\n".join(event.text for event in result.events)


# --- контент ----------------------------------------------------------------

def test_every_archetype_has_stories_with_a_twist():
    for archetype in rules.ARCHETYPE_ORDER:
        stories = stories_of(archetype)
        assert len(stories) >= 2, archetype
        assert any(story.twist for story in stories), archetype
    assert any(not story.twist for story in STORIES)


def test_most_stories_are_about_relationships():
    assert sum(story.theme == "relationships" for story in STORIES) / len(STORIES) >= 0.5


@pytest.mark.parametrize("story", STORIES, ids=lambda story: story.id)
def test_story_is_consistent(story):
    assert story.who not in story.who_decoys and len(set(story.who_decoys)) == 3
    assert set(story.who_decoys) <= set(C.WHO) and story.what in C.WHAT
    assert len(story.when_options) == 2 and set(story.when_options) <= set(C.WHEN)
    assert 2 <= len(story.told) <= 3, "клиент рассказывает историю несколькими репликами"
    for when in story.when_options:
        assert "{" not in story.reveal.format(when_lead=C.WHEN[when].lead)
    for combo in (story.jackpot, story.trap):
        if combo is None:
            continue
        picks = dict(combo.picks)
        assert picks["who"] in (story.who, *story.who_decoys)
        assert picks["what"] == story.what or C.WHAT[picks["what"]].sphere != story.sphere
    if story.jackpot and story.trap:
        assert C.WHAT[dict(story.jackpot.picks)["what"]].sphere != C.WHAT[dict(story.trap.picks)["what"]].sphere


@pytest.mark.parametrize("story", STORIES, ids=lambda story: story.id)
def test_questions_create_a_dilemma(story):
    kinds = [q.kind for q in story.questions]
    assert len(story.questions) == 4 > rules.CLARIFICATIONS, "вопросов больше, чем можно задать"
    assert "check" in kinds and "open" in kinds, "и проверка версии, и неожиданная деталь"
    assert len({q.id for q in story.questions}) == 4
    assert all(q.points or q.doubts for q in story.questions), "ответ должен что-то давать внимательному слушателю"


@pytest.mark.parametrize("story", STORIES, ids=lambda story: story.id)
def test_deadline_has_to_be_earned(story):
    assert "when" not in story.said, "срок не сказан в рассказе: за него платят вопросом или гороскопом"
    assert any(("when", WHEN_TRUTH) in q.points for q in story.questions)


def _all_claims(story):
    claims = dict(story.said)
    for question in story.questions:
        claims.update({k: v for k, v in question.said.items() if v != WHEN_TRUTH})
    return claims


def test_options_hold_truth_claims_and_combos():
    for story in STORIES:
        for seed in range(30):
            client = rules.new_client(story, random.Random(seed), 0)
            options = client.options["what"]
            assert sorted(C.WHAT[tag].sphere for tag in options) == sorted(C.SPHERES)
            assert story.what in options
            claims = _all_claims(story)
            if "what" in claims:
                assert claims["what"] in options
            if "who" in claims:
                assert claims["who"] in client.options["who"]
            for combo in (story.jackpot, story.trap):
                if combo:
                    assert dict(combo.picks)["what"] in options
                    assert dict(combo.picks)["who"] in client.options["who"]


def test_every_tag_has_an_interpretation_with_a_short_button():
    for table in (C.WHO, C.WHAT, C.SPHERES, C.EMOTIONS):
        for tag in table.values():
            assert tag.voice and 0 < len(tag.button) <= 32


def test_methods_cover_their_slots():
    assert {m.key: m.slot for m in C.METHODS.values()} == {
        "palm": "sphere", "tarot": "who", "horoscope": "what", "energy": "emotion",
    }
    assert all(len(m.rituals) >= 2 for m in C.METHODS.values())


def test_every_tarot_card_has_a_picture_and_a_meaning():
    for tag in C.WHO.values():
        assert tag.card in C.TAROT_CARDS
    assert len({tag.card for tag in C.WHO.values()}) == len(C.WHO), "карта указывает только на одного «кто»"
    assert len(C.TAROT_CARDS) >= len(C.TAROT_POSITIONS) + 1
    for card in C.TAROT_CARDS.values():
        assert card.symbols and (ROOT / "assets" / "guru_tarot" / f"{card.art}.jpg").is_file(), card.name


def test_palm_uses_the_line_of_mind():
    assert "линия ума" in C.SPHERES["work"].omen


def test_zodiac_covers_every_sign():
    assert len(C.ZODIAC) == 12 and all(symbol and trend for symbol, trend in C.ZODIAC.values())


def test_templates_only_use_known_placeholders():
    allowed = {"name", "sign", "left", "when_mark", "when_lead"}
    texts_ = [C.MERCURY_LINE, C.LEGEND_LOST_LINE, C.SUSPICION_EXIT_LINE]
    for kind in C.ARCHETYPES.values():
        texts_ += [kind.sign_answer, *kind.echo_lines.values(), *kind.outcomes.values(), *kind.farewells.values()]
        texts_ += [line for lines in kind.reactions.values() for line in lines]
        texts_ += [line for lines in kind.statuses.values() for line in lines]
        texts_ += [line for _, line in kind.suspicion_lines]
    for story in STORIES:
        texts_ += [story.arrival, *story.told, story.reveal, *(q.answer for q in story.questions)]
    for text in texts_:
        fields = {name for _, name, _, _ in string.Formatter().parse(text) if name}
        assert fields <= allowed, text


def test_archetypes_have_all_reactions_and_statuses():
    for kind in C.ARCHETYPES.values():
        assert kind.reactions["warm"] and kind.reactions["cold"]
        assert set(kind.statuses) == {"up", "down", "asked"}
        assert set(kind.outcomes) == {"hit", "partial", "miss", "exposure"}
        assert set(kind.farewells) == {"pleased", "mixed", "sour"}


# --- приход клиента ---------------------------------------------------------

def test_new_session_has_three_different_archetypes():
    for seed in range(20):
        session = rules.new_session(session_id=SID, chat_id=1, player_id=1, player_name="Зоя",
                                    rng=random.Random(seed), now=0)
        archetypes = [STORIES_BY_ID[story_id].archetype for story_id in session.plan]
        assert len(archetypes) == len(set(archetypes)) == rules.CLIENTS_PER_SESSION
        assert set(archetypes) <= set(rules.ARCHETYPE_ORDER)
        assert session.legend == rules.LEGEND_START == 2


def test_skeptic_comes_only_in_some_shifts():
    shifts = [rules.shift_archetypes(random.Random(seed)) for seed in range(3000)]
    assert all(len(set(kinds)) == len(kinds) == rules.CLIENTS_PER_SESSION for kinds in shifts)
    with_skeptic = [kinds for kinds in shifts if "skeptic" in kinds]
    assert len(with_skeptic) / len(shifts) == pytest.approx(rules.SKEPTIC_CHANCE, abs=0.03)
    assert {kinds.index("skeptic") for kinds in with_skeptic} == {0, 1, 2}, "скептик приходит в любом месте очереди"


@pytest.mark.parametrize("chance,expected", [(0.0, False), (1.0, True)])
def test_skeptic_chance_is_a_single_constant(monkeypatch, chance, expected):
    monkeypatch.setattr(rules, "SKEPTIC_CHANCE", chance)
    assert {"skeptic" in rules.shift_archetypes(random.Random(seed)) for seed in range(100)} == {expected}


def test_client_tells_the_story_in_beats():
    session, _ = make("jealous.bookie")
    events = rules.arrival_events(session)
    assert [e.kind for e in events] == ["arrive", "tell", "tell"]
    assert events[0].pause == 0 and all(e.pause > 0 for e in events[1:])
    assert "Наташа" in events[0].text and "Бывшая" in events[2].text
    assert not any("{" in e.text for e in events)


def test_nothing_is_written_down_for_the_player():
    session, _ = make("jealous.lena")
    view = rules.build_view(session)
    payload = json.dumps(view.to_dict(), ensure_ascii=False)
    assert "Коллега" not in payload, "выводы из рассказа игрок делает сам"
    assert session.client.phase == "clarify" and session.client.questions_left == 2


def test_deadline_changes_between_parties():
    story = STORIES_BY_ID["jealous.lena"]
    seen = {rules.new_client(story, random.Random(seed), 0).truth["when"] for seed in range(30)}
    assert seen == set(story.when_options)


def test_unknown_story_is_rejected():
    with pytest.raises(ValueError):
        make("nope")


# --- уточнения --------------------------------------------------------------

def test_two_questions_then_divination():
    session, rng = make("jealous.bookie")
    ids = {a.id for a in rules.available_actions(session)}
    assert ids == {"c:seen", "c:sure", "c:whisper", "c:bracelet", "c:stop"}
    first = act(session, rng, "c:whisper")[0]
    assert "тотал" in texts(first) and first.events[0].pause > 0
    assert "c:whisper" not in {a.id for a in rules.available_actions(session)}
    act(session, rng, "c:seen")
    assert session.client.phase == "divine"


def test_player_may_stop_asking():
    session, rng = make("jealous.bookie")
    act(session, rng, "c:stop")
    assert session.client.phase == "divine" and session.client.questions_left == 2


def test_questions_change_client_state_and_show_status():
    session, rng = make("businessman.partner")
    result = act(session, rng, "c:trust")[0]
    assert session.client.trust == C.BUSINESSMAN.base_trust + C.BUSINESSMAN.question_trust
    assert [e.kind for e in result.events] == ["ask", "answer", "status"]

    session, rng = make("skeptic.mom")
    act(session, rng, "c:why")
    assert session.client.suspicion == rules.SKEPTIC_START_SUSPICION + C.SKEPTIC.question_suspicion


def test_answer_records_client_words_for_echo_only():
    session, rng = make("jealous.lena")
    act(session, rng, "c:party")
    said = [k for k in session.client.knowledge if k.source == "question"]
    assert [(k.slot, k.tag) for k in said] == [("when", session.client.truth["when"])]


def test_question_metrics():
    session, rng = make("jealous.bookie")
    (data,) = metrics(act(session, rng, "c:whisper"), "action")
    assert data["kind"] == "question" and data["question"] == "whisper" and data["question_kind"] == "open"


# --- гадания и толкования ---------------------------------------------------

@pytest.mark.parametrize(("roll", "quality", "size", "truth_in"), [
    (0.0, "sharp", 2, True), (0.5, "plain", 3, True), (0.99, "off", 3, False),
])
def test_reading_offers_interpretations_by_quality(roll, quality, size, truth_in):
    session, rng = make("businessman.partner", rolls=[roll])
    act(session, rng, "c:stop", "m:tarot")
    reading = session.client.pending
    assert (reading.quality, len(reading.offered), "partner" in reading.offered) == (quality, size, truth_in)
    assert session.client.phase == "interpret"
    assert {a.kind for a in rules.available_actions(session)} == {"interpretation"}


def test_ritual_pauses_then_omen_replaces_it():
    session, rng = make("businessman.partner")
    result = act(session, rng, "c:stop", "m:palm")[-1]
    ritual, omen = result.events
    assert ritual.kind == "ritual" and "ладонь" in ritual.text.lower()
    assert omen.kind == "omen" and omen.replace and omen.pause >= rules.PACE_RITUAL[0] and "Ладонь" in omen.text


def test_tarot_keeps_the_ritual_and_brings_pictures():
    session, rng = make("businessman.partner")
    result = act(session, rng, "c:stop", "m:tarot")[-1]
    ritual, omen = result.events
    assert ritual.kind == "ritual" and "колод" in ritual.text
    assert not omen.replace and omen.pause >= rules.PACE_RITUAL[0] and "Расклад" in omen.text
    cards = omen.data["cards"]
    assert len(cards) == len(set(cards)) == 3
    arts = {card.art for card in C.TAROT_CARDS.values()}
    assert set(cards) <= arts
    bubble = textui.bubbles(result.events, result.view)[-1]
    assert bubble.cards == tuple(cards)


def test_tarot_shows_what_is_drawn_and_what_it_may_mean():
    session, rng = make("businessman.partner")
    omen = act(session, rng, "c:stop", "m:tarot")[-1].events[-1].text
    for position in C.TAROT_POSITIONS:
        assert position in omen
    assert omen.count("Образы:") == 3
    assert C.WHO["partner"].card in omen


def test_horoscope_describes_the_sign_not_a_date():
    session, rng = make("businessman.partner")
    omen = act(session, rng, "c:stop", "m:horoscope")[-1].events[-1].text
    assert C.ZODIAC[session.client.sign][1] in omen and "Совет звёзд" in omen
    assert C.WHAT["betray"].forecast in omen
    for tag in C.WHEN.values():
        assert tag.phrase.rstrip(",") not in omen, "гороскоп не называет срок"
    assert session.client.pending.slot == "what"


def test_each_method_reads_its_own_slot():
    expected = {"palm": "sphere", "tarot": "who", "horoscope": "what", "energy": "emotion"}
    for method, slot in expected.items():
        session, rng = make("businessman.partner")
        act(session, rng, "c:stop", f"m:{method}")
        assert session.client.pending.slot == slot


def test_honest_client_reacts_truthfully_and_status_follows():
    session, rng = make("businessman.partner", rolls=[0.5, 0.0])
    before = session.client.trust
    result = act(session, rng, "c:stop", "m:tarot", "i:partner")[-1]
    assert session.client.trust == before + round(rules.TRUST_WARM * (0.5 + C.BUSINESSMAN.belief / 100))
    assert result.events[-1].kind == "status" and "телефон" in result.events[-1].text
    assert session.client.readings[-1].reaction == "warm"

    session, rng = make("businessman.partner", rolls=[0.5, 0.0])
    before = session.client.trust
    wrong_tag = next(t for t in session.client.options["who"] if t != "partner")
    act(session, rng, "c:stop", "m:tarot", f"i:{wrong_tag}")
    assert session.client.trust == before + rules.TRUST_COLD
    assert session.client.readings[-1].reaction == "cold"


def test_honest_client_sometimes_misleads():
    session, rng = make("businessman.partner", rolls=[0.5, 0.95])
    act(session, rng, "c:stop", "m:tarot", "i:partner")
    assert session.client.readings[-1].reaction == "cold", "правду можно принять за промах"


def test_jealous_overreacts_to_anything():
    session, rng = make("jealous.lena", rolls=[0.5])
    wrong_tag = next(t for t in session.client.options["who"] if t != "colleague")
    result = act(session, rng, "c:stop", "m:tarot", f"i:{wrong_tag}")[-1]
    assert session.client.readings[-1].reaction == "warm"
    assert "ЗНАЛА" in texts(result)


def test_skeptic_has_only_a_faint_tell():
    session, rng = make("skeptic.mom", rolls=[0.5, 0.9])
    act(session, rng, "c:stop", "m:tarot", "i:relative")
    assert session.client.readings[-1].reaction == "cold"
    session, rng = make("skeptic.mom", rolls=[0.5, 0.1])
    act(session, rng, "c:stop", "m:tarot", "i:relative")
    assert session.client.readings[-1].reaction == "warm"


def test_two_readings_then_constructor_each_method_once():
    session, rng = make("businessman.partner")
    act(session, rng, "c:stop")
    read(session, rng, "tarot", "partner")
    assert "m:tarot" not in {a.id for a in rules.available_actions(session)}
    assert session.client.phase == "divine"
    read(session, rng, "energy", "anger")
    assert session.client.phase == "who"


def test_belief_makes_trust_easier_to_win():
    gains = {}
    for story_id in ("skeptic.mom", "romantic.ex"):
        session, rng = make(story_id, rolls=[0.5, 0.0])
        before = session.client.trust
        act(session, rng, "c:stop", "m:tarot", f"i:{session.client.truth['who']}")
        gains[story_id] = session.client.trust - before
    assert gains["romantic.ex"] > gains["skeptic.mom"]


def test_can_go_to_prediction_early():
    session, rng = make("businessman.partner")
    act(session, rng, "c:stop", "go")
    assert session.client.phase == "who"


def test_suspicion_crossing_replaces_status():
    session, rng = make("skeptic.mom")
    session.client.suspicion = 35
    result = act(session, rng, "c:why")[0]
    assert "щурится" in texts(result)


def test_suspicion_overflow_exposes_mid_session():
    session, rng = make("skeptic.mom", "jealous.lena")
    session.client.suspicion = 90
    result = act(session, rng, "c:why")[0]
    assert report_of(result).outcome == "exposure" and report_of(result).reasons == ("suspicion",)
    assert session.legend == 1 and session.client.phase == "done"


# --- предсказание и исходы --------------------------------------------------

def test_hit_partial_miss():
    plan = ("businessman.partner",) * 3
    session, rng = make(*plan)
    outcomes = [report_of(hit(session, rng, "maybe")).outcome]
    next_client(session, rng)
    outcomes.append(report_of(predict(session, rng, "partner", "betray", "sudden", "maybe")).outcome)
    next_client(session, rng)
    outcomes.append(report_of(predict(session, rng, "colleague", "betray", "sudden", "maybe")).outcome)
    assert outcomes == ["hit", "partial", "miss"]


def test_prediction_sentence_agrees_with_self():
    assert rules.prediction_sentence({"who": "self", "what": "secret", "when": "sudden"}, "maybe") == (
        "Мне кажется, когда вы меньше всего будете ждать, вы сами раскроете тайну."
    )


def test_echo_is_clients_word_without_own_reading():
    session, rng = make("businessman.partner")
    client = session.client
    assert rules.is_echo(client, "who", "partner")
    assert not rules.is_echo(client, "what", "betray"), "не говорил — не эхо"
    act(session, rng, "c:stop")
    read(session, rng, "tarot", "partner")
    assert not rules.is_echo(client, "who", "partner"), "озвучено через Таро — переупаковка, не эхо"


def test_reading_supports_event_through_sphere_and_emotion():
    assert Knowledge("sphere", "work", "palm", True).supports("what", "betray")
    assert Knowledge("emotion", "anger", "energy", True).supports("what", "betray")
    assert not Knowledge("sphere", "love", "palm", False).supports("what", "betray")


def test_skeptic_echo_with_see_is_critical_but_maybe_is_not():
    plan = ("skeptic.believer", "skeptic.believer")
    session, rng = make(*plan)
    report = report_of(predict(session, rng, "colleague", "secret", session.client.truth["when"], "see"))
    assert report.outcome == "exposure" and report.reasons[0] == "skeptic_echo" and session.legend == 1
    next_client(session, rng)
    report = report_of(predict(session, rng, "colleague", "secret", session.client.truth["when"], "maybe"))
    assert report.outcome == "partial" and report.echo_slots == ("who",) and session.legend == 1


def test_sworn_miss_is_critical_for_every_archetype():
    for story_id in ("jealous.lena", "businessman.partner", "skeptic.mom"):
        session, rng = make(story_id, story_id)
        assert report_of(miss(session, rng, "swear")).reasons[0] == "sworn_miss", story_id
        assert session.legend == 1


def test_skeptic_confident_miss_depends_on_suspicion():
    session, rng = make("skeptic.mom", "skeptic.mom")
    session.client.suspicion = 60
    assert report_of(miss(session, rng, "see")).reasons == ("skeptic_confident_miss",)
    next_client(session, rng)
    session.client.suspicion = 59
    assert report_of(miss(session, rng, "see")).outcome == "miss" and session.legend == 1


def test_businessman_sworn_echo_and_soft_discount():
    session, rng = make("businessman.partner")
    act(session, rng, "c:rush")
    report = report_of(predict(session, rng, "partner", "betray", session.client.truth["when"], "swear"))
    assert report.reasons == ("sworn_echo",) and set(report.echo_slots) == {"who", "when"}

    session, rng = make("businessman.partner")
    session.client.trust = 50
    session.client.satisfaction = 25  # после попадания станет 50, множитель гонорара равен 1
    report = report_of(hit(session, rng, "see"))
    assert report.outcome == "hit" and report.echo_slots == ("who",)
    assert report.fee == rules._round_money(5000 * rules.FEE["hit"][1] * rules.ECHO_DISCOUNT)


def test_jealous_echo_is_free():
    session, rng = make("jealous.lena")
    result = hit(session, rng, "swear")
    assert report_of(result).outcome == "hit" and set(report_of(result).echo_slots) == {"who", "what"}
    assert "Я же говорила" in texts(result)


def test_trap_only_with_confidence():
    session, rng = make("businessman.partner", "businessman.partner")
    assert report_of(predict(session, rng, "tax", "secret", "sudden", "see")).reasons == ("trap",)
    next_client(session, rng)
    assert report_of(predict(session, rng, "tax", "secret", "sudden", "maybe")).outcome == "miss"
    assert session.legend == 1


def test_jackpot_counts_as_hit():
    session, rng = make("jealous.lena")
    result = predict(session, rng, "animal", "secret", "sudden", "maybe")
    assert report_of(result).outcome == "hit" and report_of(result).jackpot and session.streak == 1
    assert "серёжку" in texts(result)


def _horoscope(session, rng):
    act(session, rng, "c:stop")
    read(session, rng, "horoscope", session.client.truth["what"])


def test_mercury_forgives_when_once_and_only_after_horoscope():
    session, rng = make("businessman.partner", "businessman.partner", "businessman.partner")
    _horoscope(session, rng)
    first = report_of(predict(session, rng, "partner", "betray", "sudden", "maybe"))
    assert first.outcome == "hit" and any(slot.mercury for slot in first.slots)
    next_client(session, rng)
    _horoscope(session, rng)
    second = report_of(predict(session, rng, "partner", "betray", "sudden", "maybe"))
    assert second.outcome == "partial", "Меркурий — один раз за партию"


def test_mercury_does_not_rescue_total_miss():
    session, rng = make("businessman.partner")
    _horoscope(session, rng)
    assert report_of(miss(session, rng, "maybe")).outcome == "miss" and session.mercury_left


def test_several_reasons_cost_one_legend():
    session, rng = make("skeptic.believer", "skeptic.believer")
    result = predict(session, rng, "colleague", "betray", "sudden", "swear")
    assert report_of(result).reasons == ("skeptic_echo", "sworn_miss") and session.legend == 1
    (lost,) = metrics([result], "legend_lost")
    assert lost["reason"] == "skeptic_echo" and len(lost["reasons"]) == 2


@pytest.mark.parametrize(("story_id", "picks", "confidence"), [
    ("jealous.lena", None, "maybe"),
    ("jealous.lena", None, "see"),
    ("businessman.partner", ("partner", "betray", "sudden"), "swear"),
    ("businessman.partner", ("tax", "secret", "sudden"), "maybe"),
])
def test_ordinary_failures_keep_the_legend(story_id, picks, confidence):
    session, rng = make(story_id, story_id)
    result = miss(session, rng, confidence) if picks is None else predict(session, rng, *picks, confidence)
    assert report_of(result).outcome != "exposure" and session.legend == 2


def test_sworn_partial_costs_a_point():
    session, rng = make("businessman.partner", "businessman.partner")
    report = report_of(predict(session, rng, "partner", "betray", "sudden", "swear"))
    assert report.outcome == "partial" and report.points == -1


# --- ход партии -------------------------------------------------------------

def test_next_client_comes_only_when_asked():
    session, rng = make("jealous.lena", "businessman.partner")
    result = hit(session, rng, "maybe")
    assert session.client.phase == "done" and session.client.story.id == "jealous.lena"
    assert [a.id for a in result.view.actions] == ["next"]
    arrival = act(session, rng, "next")[0]
    assert session.client.story.id == "businessman.partner" and arrival.events[0].kind == "arrive"


def test_last_client_finishes_without_next():
    session, rng = make("jealous.lena")
    result = hit(session, rng, "maybe")
    assert session.finished and session.finish_reason == "completed"
    assert metrics([result], "finish")[0]["clients"] == 1


def test_two_criticals_close_the_salon_early():
    session, rng = make("jealous.lena", "businessman.partner", "skeptic.mom")
    miss(session, rng, "swear")
    next_client(session, rng)
    result = miss(session, rng, "swear")
    assert session.finished and session.finish_reason == "exposed" and len(session.reports) == 2
    assert rules.title(session) == C.EXPOSED_TITLE
    (finish,) = metrics([result], "finish")
    assert finish["reason"] == "exposed" and finish["legend_left"] == 0 and finish["exposures"] == 2


def test_three_legends_cannot_end_a_three_client_shift_early():
    session, rng = make("jealous.lena", "businessman.partner", "skeptic.mom")
    session.legend = session.legend_start = 3
    miss(session, rng, "swear")
    next_client(session, rng)
    miss(session, rng, "swear")
    assert not session.finished
    next_client(session, rng)
    miss(session, rng, "swear")
    assert session.finished and len(session.reports) == 3


def test_only_ordinary_misses_finish_the_shift_intact():
    session, rng = make("jealous.lena", "businessman.partner", "skeptic.mom")
    for _ in range(3):
        miss(session, rng, "maybe")
        next_client(session, rng)
    assert session.finish_reason == "completed" and session.legend == 2 and session.max_streak == 0


def test_streak_rules_and_bonus():
    session, rng = make(*("businessman.partner",) * 3)
    first = hit(session, rng, "maybe")
    next_client(session, rng)
    partial = predict(session, rng, "partner", "betray", "sudden", "maybe")
    next_client(session, rng)
    third = hit(session, rng, "maybe")
    assert [report_of(r).streak_after for r in (first, partial, third)] == [1, 1, 2]
    assert report_of(third).streak_bonus == rules.STREAK_BONUS[2]
    assert [(u["before"], u["after"]) for u in metrics([first, partial, third], "streak_updated")] == [(0, 1), (1, 2)]


def test_streak_of_three_and_reset():
    session, rng = make(*("jealous.lena",) * 3)
    for _ in range(3):
        hit(session, rng, "maybe")
        next_client(session, rng)
    assert session.max_streak == 3 and C.STREAK_ACHIEVEMENT in rules.achievements(session)
    assert session.score == 3 * (rules.SCORE["hit"][0] + rules.PLEASED_POINTS) + 2 + 3

    session, rng = make("jealous.lena", "jealous.lena")
    hit(session, rng, "maybe")
    next_client(session, rng)
    miss(session, rng, "maybe")
    assert session.streak == 0 and session.max_streak == 1


def test_streak_does_not_pay_money():
    session, rng = make("jealous.lena", "jealous.lena")
    first = report_of(hit(session, rng, "see"))
    next_client(session, rng)
    second = report_of(hit(session, rng, "see"))
    assert second.streak_bonus == 2 and second.fee == first.fee


def test_mastery_bonus_once_for_three_main_methods():
    session, rng = make("jealous.lena", "businessman.partner", "skeptic.mom")
    act(session, rng, "c:stop")
    results = [read(session, rng, "palm", "love"), read(session, rng, "energy", "guilt")]
    results.append(hit(session, rng, "maybe"))
    next_client(session, rng)
    act(session, rng, "c:stop")
    results.append(read(session, rng, "tarot", "partner"))
    results.append(miss(session, rng, "maybe"))
    assert not session.mastery, "энергетика не считается"
    next_client(session, rng)
    act(session, rng, "c:stop")
    results.append(read(session, rng, "horoscope", session.client.truth["what"]))
    assert session.mastery
    (bonus,) = metrics(results, "mastery_bonus")
    assert bonus == {"client_no": 2, "method": "horoscope", "points": rules.MASTERY_BONUS}


def test_ritual_itself_pleases_even_when_it_misses():
    session, rng = make("jealous.lena")
    before = session.client.satisfaction
    act(session, rng, "c:stop")
    read(session, rng, "palm", "love")
    assert session.client.satisfaction == before + rules.SATISFACTION_WARM

    session, rng = make("businessman.partner", rolls=[0.5, 0.0])
    before = session.client.satisfaction
    wrong_tag = next(t for t in session.client.options["who"] if t != "partner")
    act(session, rng, "c:stop", "m:tarot", f"i:{wrong_tag}")
    assert session.client.satisfaction == before + rules.SATISFACTION_COLD


def test_exposed_but_pleased_client_tips_and_says_so():
    session, rng = make("skeptic.believer", "skeptic.believer")
    session.client.satisfaction = 90
    result = predict(session, rng, "colleague", "secret", session.client.truth["when"], "see")
    report = report_of(result)
    assert report.outcome == "exposure" and report.parting == "pleased"
    assert report.fee == rules._round_money(1500 * rules.EXPOSURE_TIP) and report.points == -4 + rules.PLEASED_POINTS
    assert C.SKEPTIC.farewells["pleased"].format(name=session.client.name) in texts(result)
    assert session.legend == 1, "понравился сеанс или нет, разоблачение стоит легенды"


def test_parting_reflects_satisfaction():
    for satisfaction, parting in [(70, "pleased"), (45, "mixed"), (5, "sour")]:
        session, rng = make("businessman.partner")
        session.client.satisfaction = satisfaction
        assert report_of(miss(session, rng, "maybe")).parting == parting


def test_titles():
    session, _ = make("jealous.lena")
    for score, expected in [(-3, C.LOW_SCORE_TITLE), (2, "шарлатан"), (6, "Магистр"), (10, "провидец"), (15, "академик")]:
        session.score, session.money = score, 0
        assert expected in rules.title(session)
    session.score, session.money = 3, rules.CROOK_MONEY
    assert rules.title(session) == C.CROOK_TITLE


def test_report_explains_the_episode():
    session, rng = make("businessman.wife", rolls=[0.5, 0.0])
    act(session, rng, "c:talk")
    act(session, rng, "c:stop")
    read(session, rng, "tarot", "relative")
    result = predict(session, rng, "relative", "gain", session.client.truth["when"], "see")
    report = report_of(result)
    assert [(s.slot, s.matched) for s in report.slots] == [("who", True), ("what", False), ("when", True)]
    assert report.outcome == "partial" and report.echo_slots[0] == "what"
    assert report.readings[0].correct and report.questions == ("talk",)
    assert ("what", "gain", False) in report.said
    (reveal,) = metrics([result], "client_reveal")
    assert reveal["outcome"] == "partial" and reveal["story"] == "businessman.wife"


def test_reading_metrics():
    session, rng = make("businessman.partner", rolls=[0.5, 0.0])
    act(session, rng, "c:stop")
    (data,) = metrics([read(session, rng, "tarot", "partner")], "action")
    assert data["kind"] == "reading" and data["method"] == "tarot" and data["correct"] is True
    assert data["quality"] == "plain" and data["reaction"] == "warm" and "partner" in data["offered"]


def test_summary_has_everything_for_the_log():
    session, rng = make("jealous.lena", "businessman.partner", "skeptic.mom")
    act(session, rng, "c:stop")
    read(session, rng, "tarot", "colleague")
    for _ in range(3):
        miss(session, rng, "maybe")
        next_client(session, rng)
    data = rules.summary(session, now=123.0)
    for key in ("money", "score", "legend_left", "max_streak", "hits", "partials", "misses", "exposures", "pleased",
                "methods_used", "mastery", "clients", "duration_s", "reason", "title", "rules"):
        assert key in data, key
    assert data["methods_used"] == ["tarot"] and data["clients"] == 3 and data["rules"] == rules.RULES_VERSION == 6


def test_participants_only_and_invalid_actions():
    session, rng = make("jealous.lena")
    with pytest.raises(PermissionError):
        rules.act(session, 999, "c:stop", rng, now=1)
    with pytest.raises(ValueError):
        rules.act(session, 7, "a:swear", rng, now=1)


def test_same_seed_same_party():
    def run(seed):
        rng = random.Random(seed)
        session = rules.new_session(session_id=SID, chat_id=1, player_id=1, player_name="Зоя", rng=rng, now=0)
        while not session.finished:
            rules.act(session, 1, rng.choice([a.id for a in rules.available_actions(session)]), rng, now=1)
        return rules.summary(session, now=1)

    assert run(42) == run(42)


def test_idle_is_fifteen_minutes():
    session, rng = make("jealous.lena", now=0.0)
    assert rules.IDLE_TIMEOUT_S == 900
    assert not rules.is_idle(session, now=899.0) and rules.is_idle(session, now=900.0)
    act(session, rng, "c:stop")
    data = rules.abandon_summary(session, "timeout", now=2000.0)
    assert data["reason"] == "timeout" and data["idle_s"] == 1999.0 and data["phase"] == "divine"
    rules.abandon(session, "quit")
    assert session.finish_reason == "timeout"


def test_registry_one_session_per_chat():
    registry = rules.SessionRegistry()
    first, _ = make("jealous.lena")
    second, _ = make("jealous.lena")
    registry.add(first)
    with pytest.raises(ValueError):
        registry.add(second)
    assert not registry.remove(second) and registry.get(-1) is first
    assert registry.idle(now=5000.0) == [first]
    assert registry.remove(first) and len(registry) == 0


# --- контракт ---------------------------------------------------------------

def test_view_matches_phase_and_serializes():
    session, rng = make("jealous.lena")
    view = rules.build_view(session)
    assert {a.kind for a in view.actions} == {"question", "control"} and view.preview is None
    json.dumps(view.to_dict(), ensure_ascii=False)
    to_constructor(session, rng)
    act(session, rng, "s:who:colleague")
    view = rules.build_view(session)
    assert view.preview.startswith("Кто: Коллега")
    act(session, rng, "s:what:cheat", "s:when:week")
    view = rules.build_view(session)
    assert view.client.phase == "announce" and view.preview.startswith("«До конца недели")
    json.dumps(view.to_dict(), ensure_ascii=False)


# --- текстовый адаптер ------------------------------------------------------

def _all_views(seed):
    rng = random.Random(seed)
    session = rules.new_session(session_id=SID, chat_id=1, player_id=1, player_name="Зоя", rng=rng, now=0)
    views = [rules.build_view(session)]
    results = []
    while not session.finished:
        result = rules.act(session, 1, rng.choice([a.id for a in rules.available_actions(session)]), rng, now=1)
        views.append(result.view)
        results.append(result)
    return views, results


def test_every_button_fits_telegram_and_round_trips():
    for seed in range(15):
        views, _ = _all_views(seed)
        for view in views:
            for row in textui.keyboard(view):
                for label, data in row:
                    assert len(data.encode()) <= 64 and label
                    parsed = textui.parse(data)
                    assert parsed.kind == "act" and parsed.session_id == SID and parsed.step == view.step
            assert {textui.parse(d).action for row in textui.keyboard(view) for _, d in row} == {a.id for a in view.actions}


def test_special_callbacks_round_trip():
    for answer in ("yes", "no"):
        parsed = textui.parse(textui.again_data(SID, answer))
        assert (parsed.kind, parsed.answer, parsed.session_id) == ("again", answer, SID)
    parsed = textui.parse(textui.report_data(SID, 2))
    assert (parsed.kind, parsed.client_no, parsed.session_id) == ("report", 2, SID)


@pytest.mark.parametrize("data", [None, "", "guru", "guru:XYZ:1:go", "guru:abc:x:go", "croc:abc:1:go"])
def test_parse_rejects_garbage(data):
    assert textui.parse(data) is None


def test_events_split_into_short_messages():
    session, rng = make("jealous.bookie")
    view = rules.build_view(session)
    bubbles = textui.bubbles(rules.arrival_events(session), view)
    assert len(bubbles) == 3 and bubbles[0].text.startswith("Клиент 1/1")
    assert bubbles[0].pause == 0 and bubbles[1].pause == rules.PACE_TELL[0] and bubbles[1].spread == rules.PACE_TELL[1]
    result = act(session, rng, "c:stop", "m:palm")[-1]
    ritual, omen = textui.bubbles(result.events, result.view)
    assert not ritual.replace and omen.replace and omen.pause > 0


def test_messages_are_short_and_hide_numbers():
    for seed in range(15):
        _, results = _all_views(seed)
        for result in results:
            for bubble in textui.bubbles(result.events, result.view):
                assert len(bubble.text) <= 1000, bubble.text
                assert "%" not in bubble.text and "Доверие" not in bubble.text


def test_verdict_and_report_are_separate():
    session, rng = make("jealous.lena")
    result = miss(session, rng, "maybe")
    report = report_of(result)
    verdict = textui.render_verdict(result.view, report)
    assert verdict.splitlines()[0].startswith("💨 Промах") and "Легенда" in verdict
    detailed = textui.render_report(report)
    assert "Разбор" in detailed and "было:" in detailed and len(detailed) <= textui.MESSAGE_LIMIT


def test_verdict_explains_what_exposure_costs():
    session, rng = make("skeptic.mom", "skeptic.mom")
    session.client.suspicion = 60
    result = miss(session, rng, "see")
    verdict = textui.render_verdict(result.view, report_of(result))
    assert "Разоблачение" in verdict and "провал" in verdict and "легенда" in verdict.lower()


def test_client_done_keyboard_offers_report_and_next():
    session, rng = make("jealous.lena", "jealous.lena")
    result = hit(session, rng, "maybe")
    labels = [label for row in textui.client_done_keyboard(result.view, report_of(result)) for label, _ in row]
    assert labels == [textui.REPORT_BUTTON, "🚪 Следующий клиент"]


def test_constructor_shows_preview_and_mood():
    session, rng = make("jealous.lena")
    to_constructor(session, rng)
    act(session, rng, "s:who:colleague")
    text, rows = textui.render_constructor(rules.build_view(session))
    assert "Кто: Коллега" in text and session.client.name in text and rows


def test_summary_and_abandon_note():
    session, _ = make("jealous.lena")
    rules.abandon_summary(session, "timeout", now=999.0)
    text, rows = textui.render_summary(rules.build_view(session))
    assert "брошен" in text and "Звание" in text
    assert [label for row in rows for label, _ in row] == [textui.AGAIN_YES, textui.AGAIN_NO]
    assert textui.render_abandoned("timeout") == textui.ABANDON_NOTES["timeout"]


def test_intro_is_short():
    text = textui.render_intro(rules.build_view(make("jealous.lena")[0]))
    assert len(text) < 400 and "Клянусь" in text and "15 минут" in text


# --- деньги -------------------------------------------------------------------

def test_money_formats_a_minus_sign():
    assert rules.rub(-1500) == "−1 500 ₽" and rules.signed_rub(-1500) == "−1 500 ₽"
    assert rules.signed_rub(1500) == "+1 500 ₽" and rules.signed_rub(0) == "0 ₽"


def test_confidence_is_a_stake_in_money():
    low, mid, high = rules.FEE["hit"]
    assert low < mid < high
    for outcome in ("miss", "exposure"):
        low, mid, high = rules.FEE[outcome]
        assert low > mid > high and high < 0


def test_cautious_miss_costs_nothing():
    assert rules.FEE["miss"][0] >= 0 and rules.FEE["partial"][0] >= 0


def test_confident_miss_returns_money_regardless_of_mood():
    session, rng = make("jealous.lena")
    session.client.trust = session.client.satisfaction = 100
    report = report_of(miss(session, rng, "see"))
    assert report.outcome == "miss" and report.fee == rules._round_money(2000 * rules.FEE["miss"][1]) < 0
    assert session.money == report.fee


def test_exposure_is_a_fine():
    session, rng = make("skeptic.mom", "skeptic.mom")
    session.client.suspicion = 60
    session.client.satisfaction = 0
    report = report_of(miss(session, rng, "see"))
    assert report.outcome == "exposure" and report.parting != "pleased"
    assert report.fee == rules.flat_fee(session.client.story.fee, "exposure", 1) < 0


def test_discount_for_echo_does_not_halve_a_refund():
    session, rng = make("businessman.partner")
    act(session, rng, "c:rush")
    client = session.client
    report = report_of(predict(session, rng, "partner", wrong(client, "what"), wrong(client, "when"), "see"))
    assert report.outcome == "miss" and "who" in report.echo_slots
    assert report.fee == rules.flat_fee(5000, "miss", 1)


def test_wallet_includes_what_was_earned_in_the_party():
    session, rng = make("jealous.lena")
    session.wallet_start = 1000
    result = miss(session, rng, "see")
    assert result.view.money == report_of(result).fee and result.view.wallet == 1000 + report_of(result).fee


def test_wallet_may_go_below_zero():
    session, rng = make("jealous.lena")
    session.wallet_start = 0
    result = miss(session, rng, "see")
    assert result.view.wallet < 0 and "−" in textui.render_verdict(result.view, report_of(result))


def test_verdict_explains_a_loss_and_shows_the_wallet():
    session, rng = make("jealous.lena")
    session.wallet_start = 5000
    result = miss(session, rng, "see")
    report = report_of(result)
    verdict = textui.render_verdict(result.view, report)
    assert C.LOSS_LINES["miss"] in verdict and rules.rub(5000 + report.fee) in verdict


def test_summary_separates_shift_from_wallet():
    session, _ = make("jealous.lena")
    session.wallet_start = 2000
    session.money = -500
    rules.abandon_summary(session, "timeout", now=999.0)
    text, _ = textui.render_summary(rules.build_view(session))
    assert "За смену: −500 ₽" in text and "В кассе: 1 500 ₽" in text


# --- архитектура ------------------------------------------------------------

def _imports(path):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            yield from (alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            yield node.module


def test_core_does_not_know_about_telegram():
    forbidden = ("aiogram", "handlers", "features", "core.loader", "infrastructure")
    for path in (ROOT / "games" / "guru").glob("*.py"):
        bad = [name for name in _imports(path) if name.startswith(forbidden)]
        assert not bad, f"{path.name}: {bad}"


def test_transport_does_not_create_unowned_tasks():
    path = ROOT / "handlers" / "guru_salon.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    calls = [
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name) and node.func.value.id == "asyncio"
    ]
    assert "create_task" not in calls and "ensure_future" not in calls
