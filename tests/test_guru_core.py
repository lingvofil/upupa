"""Салон «Гуру» v0.2: ядро без Telegram — контент, правила, контракт, текстовый адаптер."""

import ast
import json
import random
import string
from pathlib import Path

import pytest

from games.guru import content as C
from games.guru import rules, textui
from games.guru.model import CLIENT_SOURCES, Knowledge
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
    results = []
    for action in actions:
        results.append(rules.act(session, actor, action, rng, now=float(session.step + 1)))
    return results


def predict(session, rng, who, what, when, confidence):
    return act(session, rng, "go", f"s:who:{who}", f"s:what:{what}", f"s:when:{when}", f"a:{confidence}")[-1]


def wrong(client, slot):
    """Вариант мимо правды, не задевающий джекпот и ловушку."""
    combos = {tag for combo in (client.story.jackpot, client.story.trap) if combo for s, tag in combo.picks if s == slot}
    return next(t for t in client.options[slot] if t != client.truth[slot] and t not in combos)


def miss(session, rng, confidence):
    client = session.client
    return predict(session, rng, wrong(client, "who"), wrong(client, "what"), wrong(client, "when"), confidence)


def metrics(results, name):
    return [data for result in results for event, data in result.metrics if event == name]


# --- контент ----------------------------------------------------------------

def test_two_stories_per_archetype_one_with_a_twist():
    for archetype in rules.ARCHETYPE_ORDER:
        stories = stories_of(archetype)
        assert len(stories) == 2, archetype
        assert sorted(story.twist for story in stories) == [False, True], archetype


@pytest.mark.parametrize("story", STORIES, ids=lambda story: story.id)
def test_story_is_consistent(story):
    assert story.who not in story.who_decoys and len(set(story.who_decoys)) == 3
    assert set(story.who_decoys) <= set(C.WHO)
    assert story.what in C.WHAT
    assert len(story.when_options) == 2 and set(story.when_options) <= set(C.WHEN)
    for combo in (story.jackpot, story.trap):
        if combo is None:
            continue
        picks = dict(combo.picks)
        assert picks["who"] in (story.who, *story.who_decoys)
        sphere = C.WHAT[picks["what"]].sphere
        assert picks["what"] == story.what or sphere != story.sphere, "событие комбинации должно влезть в варианты"
    if story.jackpot and story.trap:
        assert C.WHAT[dict(story.jackpot.picks)["what"]].sphere != C.WHAT[dict(story.trap.picks)["what"]].sphere
    for when in story.when_options:
        assert "{" not in story.reveal.format(when_lead=C.WHEN[when].lead)
        client = rules.new_client(story, ScriptedRandom(), 0)
        assert client.knowledge[-1].source == "observation" and client.knowledge[-1].truthful
        assert "{" not in client.observation_text


def test_what_options_are_one_per_sphere_with_truth_and_combos():
    for story in STORIES:
        for seed in range(30):
            client = rules.new_client(story, random.Random(seed), 0)
            options = client.options["what"]
            assert sorted(C.WHAT[tag].sphere for tag in options) == sorted(C.SPHERES)
            assert story.what in options
            assert story.belief("what", story.what) in options, "убеждение клиента должно быть среди вариантов"
            assert story.belief("who", story.who) in client.options["who"]
            for combo in (story.jackpot, story.trap):
                if combo:
                    assert dict(combo.picks)["what"] in options
                    assert dict(combo.picks)["who"] in client.options["who"]


def test_templates_only_use_known_placeholders():
    allowed = {"name", "value", "sign", "left", "when_mark", "when_lead"}
    texts = [C.MERCURY_LINE, C.LEGEND_LOST_LINE, C.SUSPICION_EXIT_LINE]
    for kind in C.ARCHETYPES.values():
        texts += [*kind.answers.values(), kind.sign_answer, *kind.echo_lines.values(), *kind.outcomes.values()]
        texts += [line for lines in kind.reactions.values() for line in lines]
        texts += [line for _, line in kind.suspicion_lines]
    texts += [story.reveal for story in STORIES] + [story.observation.text for story in STORIES]
    for text in texts:
        fields = {name for _, name, _, _ in string.Formatter().parse(text) if name}
        assert fields <= allowed, text


def test_reactions_exist_for_each_style():
    styles = {"poker": "neutral", "overreact": "warm", "honest": "cold"}
    for kind in C.ARCHETYPES.values():
        assert kind.reactions[styles[kind.style]]
        assert set(kind.outcomes) == {"hit", "partial", "miss", "exposure"}


# --- партия и приход клиента ------------------------------------------------

def test_new_session_has_one_story_per_archetype():
    for seed in range(20):
        session = rules.new_session(session_id=SID, chat_id=1, player_id=1, player_name="Зоя",
                                    rng=random.Random(seed), now=0)
        archetypes = [STORIES_BY_ID[story_id].archetype for story_id in session.plan]
        assert sorted(archetypes) == sorted(rules.ARCHETYPE_ORDER)
        assert session.legend == rules.LEGEND_START == 2
        assert [p.user_id for p in session.participants] == [1]


def test_client_arrives_with_complaint_and_observation():
    session, _ = make("jealous.lena")
    client = session.client
    sources = [(k.source, k.slot, k.tags) for k in client.knowledge]
    assert ("disclosed", "who", ("colleague",)) in sources
    assert ("disclosed", "what", ("cheat",)) in sources
    assert ("observation", "when", (client.truth["when"],)) in sources
    assert client.actions_left == 2 and client.phase == "research"


def test_when_truth_varies_between_parties():
    story = STORIES_BY_ID["jealous.lena"]
    seen = {rules.new_client(story, random.Random(seed), 0).truth["when"] for seed in range(30)}
    assert seen == set(story.when_options)


def test_unknown_story_is_rejected():
    with pytest.raises(ValueError):
        make("nope")


# --- инфо-приёмы ------------------------------------------------------------

def test_two_actions_then_constructor_and_each_method_once():
    session, rng = make("businessman.partner")
    act(session, rng, "m:tarot")
    ids = {a.id for a in rules.available_actions(session)}
    assert "m:tarot" not in ids and "m:horoscope" in ids
    act(session, rng, "m:energy")
    assert session.client.phase == "who"
    with pytest.raises(ValueError):
        act(session, rng, "m:palm")


def test_palm_speaks_on_true_sphere_and_lies_rarely():
    session, rng = make("jealous.bookie", rolls=[0.5, 0.5])  # правда — деньги
    act(session, rng, "m:palm", "p:fate")
    palm = session.client.knowledge[-1]
    assert (palm.slot, palm.tags, palm.positive, palm.truthful) == ("sphere", ("money",), True, True)

    session, rng = make("jealous.bookie", rolls=[0.05, 0.5])  # бросок ошибки
    act(session, rng, "m:palm", "p:heart")
    palm = session.client.knowledge[-1]
    assert palm.positive and not palm.truthful, "ладонь соврала: «линия говорит» на неверной сфере"
    assert palm.supports("what", "cheat")


@pytest.mark.parametrize(("roll", "clear", "truthful", "size"),
                         [(0.0, True, True, 1), (0.6, False, True, 2), (0.95, True, False, 1)])
def test_tarot_hint_quality(roll, clear, truthful, size):
    session, rng = make("businessman.partner", rolls=[roll, 0.5, 0.5])
    act(session, rng, "m:tarot")
    hint = session.client.knowledge[-1]
    assert (hint.source, hint.slot, hint.clear, hint.truthful, len(hint.tags)) == ("tarot", "who", clear, truthful, size)
    assert ("partner" in hint.tags) == truthful


def test_horoscope_hints_when_and_charms_everyone_but_skeptic():
    session, rng = make("jealous.lena", rolls=[0.0])
    act(session, rng, "m:horoscope")
    client = session.client
    assert client.knowledge[-1].tags == (client.truth["when"],) and client.horoscope_used
    assert client.trust == C.JEALOUS.base_trust + 10

    session, rng = make("skeptic.mom", rolls=[0.0])
    act(session, rng, "m:horoscope")
    assert session.client.trust == C.SKEPTIC.base_trust - 10
    assert session.client.suspicion == rules.SKEPTIC_START_SUSPICION + 20


def test_energy_points_to_emotion_of_true_event():
    session, rng = make("businessman.wife", rolls=[0.0])
    act(session, rng, "m:energy")
    hint = session.client.knowledge[-1]
    assert hint.tags == ("fear",) and hint.supports("what", "leave") and hint.supports("what", "lose")


def test_question_returns_belief_and_jealous_blurts():
    session, rng = make("jealous.bookie")
    act(session, rng, "m:question", "q:when")
    words = [k for k in session.client.knowledge if k.source == "question"]
    assert {k.slot for k in words} == {"when"}, "о «кто» и «что» она уже сказала сама — выбалтывать нечего"
    assert session.client.trust == C.JEALOUS.base_trust - 10

    session, rng = make("businessman.wife")
    act(session, rng, "m:question", "q:who")
    answer = session.client.knowledge[-1]
    assert answer.tags == ("partner",) and not answer.truthful, "в истории с подвохом клиент заблуждается"


def test_skeptic_question_raises_suspicion_and_shows_it():
    session, rng = make("skeptic.mom")
    act(session, rng, "m:question", "q:who")
    assert session.client.suspicion == 55
    assert any("щурится" in line for line in session.client.lines)


def test_reactions_by_style():
    session, rng = make("businessman.partner", rolls=[0.95, 0.5, 0.5, 0.0])  # ложное Таро, честная реакция
    act(session, rng, "m:tarot")
    assert any(line in session.client.lines[-1] for line in ("Мимо", "за воду"))

    session, rng = make("jealous.lena", rolls=[0.95, 0.5, 0.5])
    act(session, rng, "m:tarot")
    warm = [line.format(name=session.client.name) for line in C.JEALOUS.reactions["warm"]]
    assert session.client.lines[-1] in warm, "ревнивая бурно реагирует даже на ложь"


def test_suspicion_overflow_exposes_mid_session():
    session, rng = make("skeptic.mom", "jealous.lena")
    session.client.suspicion = 90
    result = act(session, rng, "m:tarot")[0]
    assert result.finished_client.report.outcome == "exposure"
    assert result.finished_client.report.reasons == ("suspicion",)
    assert session.legend == 1 and session.client.story.id == "jealous.lena"


# --- предсказание и исходы --------------------------------------------------

def test_hit_partial_miss():
    session, rng = make("businessman.partner", "businessman.partner", "businessman.partner")
    truth = session.client.truth
    hit = predict(session, rng, "partner", "betray", truth["when"], "maybe")
    partial = predict(session, rng, "partner", "betray", "sudden", "maybe")
    miss = predict(session, rng, "colleague", "betray", "sudden", "maybe")
    assert [r.finished_client.report.outcome for r in (hit, partial, miss)] == ["hit", "partial", "miss"]


def test_prediction_sentence_agrees_with_self():
    assert rules.prediction_sentence({"who": "self", "what": "secret", "when": "sudden"}, "maybe") == (
        "Мне кажется, когда вы меньше всего будете ждать, вы сами раскроете тайну."
    )
    assert rules.prediction_sentence({"who": "colleague", "what": "cheat", "when": "week"}, "swear") == (
        "Клянусь своим третьим глазом: до конца недели человек из рабочего окружения изменит."
    )


def test_echo_is_clients_word_without_own_support():
    session, _ = make("businessman.partner")
    client = session.client
    assert rules.is_echo(client, "who", "partner"), "партнёра назвал сам клиент"
    assert not rules.is_echo(client, "what", "betray"), "не говорил — не эхо"
    client.knowledge.append(Knowledge("who", ("partner", "colleague"), "tarot", False, True))
    assert not rules.is_echo(client, "who", "partner"), "подтверждено Таро — переупаковка, не эхо"


def test_observation_is_own_source_not_echo():
    session, _ = make("skeptic.mom")
    session.client.knowledge.append(Knowledge("who", ("relative",), "question", True, True))
    assert not rules.is_echo(session.client, "who", "relative")


def test_skeptic_echo_with_see_is_critical_but_maybe_is_not():
    session, rng = make("skeptic.believer", "skeptic.believer")
    result = predict(session, rng, "colleague", "secret", session.client.truth["when"], "see")
    report = result.finished_client.report
    assert report.outcome == "exposure" and report.reasons[0] == "skeptic_echo" and session.legend == 1

    result = predict(session, rng, "colleague", "secret", session.client.truth["when"], "maybe")
    report = result.finished_client.report
    assert report.outcome == "partial" and report.echo_slots == ("who",) and session.legend == 1


def test_sworn_miss_is_critical_for_every_archetype():
    for story_id in ("jealous.lena", "businessman.partner", "skeptic.mom"):
        session, rng = make(story_id, story_id)
        result = miss(session, rng, "swear")
        assert result.finished_client.report.reasons[0] == "sworn_miss", story_id
        assert session.legend == 1


def test_skeptic_confident_miss_depends_on_suspicion():
    session, rng = make("skeptic.mom", "skeptic.mom")
    session.client.suspicion = 60
    assert miss(session, rng, "see").finished_client.report.reasons == ("skeptic_confident_miss",)
    session.client.suspicion = 59
    report = miss(session, rng, "see").finished_client.report
    assert report.outcome == "miss" and session.legend == 1


def test_businessman_sworn_echo_and_soft_discount():
    session, rng = make("businessman.partner")
    act(session, rng, "m:question", "q:when")
    result = act(session, rng, "go", "s:who:partner", "s:what:betray", f"s:when:{session.client.truth['when']}",
                 "a:swear")[-1]
    report = result.finished_client.report
    assert report.reasons == ("sworn_echo",) and set(report.echo_slots) == {"who", "when"}

    session, rng = make("businessman.wife")
    act(session, rng, "m:question", "q:when")
    result = act(session, rng, "go", "s:who:colleague", "s:what:gain", f"s:when:{session.client.truth['when']}",
                 "a:swear")[-1]
    reasons = result.finished_client.report.reasons
    assert reasons[0] == "sworn_miss" and "sworn_echo" in reasons, "мимо и под клятву — первой идёт клятва"

    session, rng = make("businessman.partner")
    session.client.trust = 50
    full = rules._round_money(5000 * 1.0 * rules.FEE["hit"][1])
    result = predict(session, rng, "partner", "betray", session.client.truth["when"], "see")
    report = result.finished_client.report
    assert report.outcome == "hit" and report.echo_slots == ("who",)
    assert report.fee == rules._round_money(full * rules.ECHO_DISCOUNT)


def test_jealous_echo_is_free():
    session, rng = make("jealous.lena")
    result = predict(session, rng, "colleague", "cheat", session.client.truth["when"], "swear")
    report = result.finished_client.report
    assert report.outcome == "hit" and set(report.echo_slots) == {"who", "what"}
    assert "Я же говорила" in " ".join(result.finished_client.dialog)


def test_trap_only_with_confidence():
    session, rng = make("businessman.partner", "businessman.partner")
    result = predict(session, rng, "tax", "secret", session.client.truth["when"], "see")
    assert result.finished_client.report.reasons == ("trap",)
    result = predict(session, rng, "tax", "secret", session.client.truth["when"], "maybe")
    assert result.finished_client.report.outcome == "miss" and session.legend == 1


def test_jackpot_counts_as_hit():
    session, rng = make("jealous.lena")
    result = predict(session, rng, "animal", "secret", "sudden", "maybe")
    report = result.finished_client.report
    assert report.outcome == "hit" and report.jackpot and session.streak == 1
    assert "серёжку" in " ".join(result.finished_client.dialog)


def test_mercury_forgives_when_once_and_only_after_horoscope():
    session, rng = make("businessman.partner", "businessman.partner", "businessman.partner", rolls=[0.95, 0.5])
    act(session, rng, "m:horoscope")
    first = predict(session, rng, "partner", "betray", "sudden", "maybe").finished_client.report
    assert first.outcome == "hit" and any(slot.mercury for slot in first.slots)
    act(session, rng, "m:horoscope")
    second = predict(session, rng, "partner", "betray", "sudden", "maybe").finished_client.report
    assert second.outcome == "partial", "Меркурий — один раз за партию"
    third = predict(session, rng, "partner", "betray", "sudden", "maybe").finished_client.report
    assert third.outcome == "partial", "без гороскопа на этом клиенте Меркурий не помогает"


def test_mercury_does_not_rescue_total_miss():
    session, rng = make("businessman.partner", rolls=[0.95, 0.5])
    act(session, rng, "m:horoscope")
    report = miss(session, rng, "maybe").finished_client.report
    assert report.outcome == "miss" and session.mercury_left


def test_several_reasons_cost_one_legend():
    session, rng = make("skeptic.believer", "skeptic.believer")
    session.client.suspicion = 70
    result = predict(session, rng, "colleague", "betray", "sudden", "swear")
    report = result.finished_client.report
    assert report.reasons == ("skeptic_echo", "sworn_miss")
    assert session.legend == 1
    (lost,) = metrics([result], "legend_lost")
    assert lost["reason"] == "skeptic_echo" and len(lost["reasons"]) >= 2


@pytest.mark.parametrize(("story_id", "picks", "confidence"), [
    ("jealous.lena", None, "maybe"),  # промах с «Мне кажется»
    ("jealous.lena", None, "see"),  # промах с «Вижу» у не-скептика
    ("businessman.partner", ("partner", "betray", "sudden"), "swear"),  # частичное под клятву
    ("businessman.partner", ("tax", "secret", "sudden"), "maybe"),  # ловушка без уверенности
])
def test_ordinary_failures_keep_the_legend(story_id, picks, confidence):
    session, rng = make(story_id, story_id)
    result = miss(session, rng, confidence) if picks is None else predict(session, rng, *picks, confidence)
    report = result.finished_client.report
    assert report.outcome != "exposure" and session.legend == 2


def test_sworn_partial_costs_a_point():
    session, rng = make("businessman.partner", "businessman.partner")
    report = predict(session, rng, "partner", "betray", "sudden", "swear").finished_client.report
    assert report.outcome == "partial" and report.points == -1


def test_two_criticals_close_the_salon_early():
    session, rng = make("jealous.lena", "businessman.partner", "skeptic.mom")
    miss(session, rng, "swear")
    result = miss(session, rng, "swear")
    assert session.finished and session.finish_reason == "exposed"
    assert len(session.reports) == 2
    assert rules.title(session) == C.EXPOSED_TITLE
    (finish,) = metrics([result], "finish")
    assert finish["reason"] == "exposed" and finish["legend_left"] == 0 and finish["exposures"] == 2


def test_three_legends_cannot_end_a_three_client_shift_early():
    session, rng = make("jealous.lena", "businessman.partner", "skeptic.mom")
    session.legend = session.legend_start = 3
    miss(session, rng, "swear")
    miss(session, rng, "swear")
    assert not session.finished
    miss(session, rng, "swear")
    assert session.finished and len(session.reports) == 3


def test_only_ordinary_misses_finish_the_shift_intact():
    session, rng = make("jealous.lena", "businessman.partner", "skeptic.mom")
    for _ in range(3):
        miss(session, rng, "see")
    assert session.finish_reason == "completed" and session.legend == 2 and session.max_streak == 0


# --- серия, мастерство, итог ------------------------------------------------

def test_streak_rules_and_bonus():
    session, rng = make("businessman.partner", "businessman.partner", "businessman.partner")
    first = predict(session, rng, "partner", "betray", session.client.truth["when"], "maybe")
    partial = predict(session, rng, "partner", "betray", "sudden", "maybe")
    third = predict(session, rng, "partner", "betray", session.client.truth["when"], "maybe")
    assert [r.finished_client.report.streak_after for r in (first, partial, third)] == [1, 1, 2]
    assert third.finished_client.report.streak_bonus == rules.STREAK_BONUS[2]
    assert third.finished_client.report.points == rules.SCORE["hit"][0] + rules.STREAK_BONUS[2]
    updates = metrics([first, partial, third], "streak_updated")
    assert [(u["before"], u["after"]) for u in updates] == [(0, 1), (1, 2)]


def test_streak_of_three_and_reset():
    session, rng = make("jealous.lena", "jealous.lena", "jealous.lena")
    for _ in range(3):
        predict(session, rng, "colleague", "cheat", session.client.truth["when"], "maybe")
    assert session.max_streak == 3
    assert C.STREAK_ACHIEVEMENT in rules.achievements(session)
    assert session.score == 3 * rules.SCORE["hit"][0] + 2 + 3

    session, rng = make("jealous.lena", "jealous.lena")
    predict(session, rng, "colleague", "cheat", session.client.truth["when"], "maybe")
    miss(session, rng, "maybe")
    assert session.streak == 0 and session.max_streak == 1


def test_streak_does_not_pay_money():
    session, rng = make("jealous.lena", "jealous.lena")
    first = predict(session, rng, "colleague", "cheat", session.client.truth["when"], "see").finished_client.report
    second = predict(session, rng, "colleague", "cheat", session.client.truth["when"], "see").finished_client.report
    assert second.streak_bonus == 2 and second.fee == first.fee


def test_mastery_bonus_once_for_three_main_methods():
    session, rng = make("jealous.lena", "businessman.partner", "skeptic.mom")
    results = act(session, rng, "m:palm", "p:heart", "m:energy", "s:who:colleague", "s:what:cheat",
                  f"s:when:{session.client.truth['when']}", "a:maybe")
    results += act(session, rng, "m:tarot", "m:question", "q:what")
    assert not session.mastery, "энергетика и вопрос не считаются"
    results += act(session, rng, "s:who:partner", "s:what:betray", "s:when:sudden", "a:maybe", "m:horoscope")
    assert session.mastery
    (bonus,) = metrics(results, "mastery_bonus")
    assert bonus == {"client_no": 2, "method": "horoscope", "points": rules.MASTERY_BONUS}
    assert C.MASTERY_LINE in session.client.lines[-1] or any(C.MASTERY_LINE in line for line in session.client.lines)


def test_titles():
    session, _ = make("jealous.lena")
    for score, expected in [(-3, C.LOW_SCORE_TITLE), (2, "шарлатан"), (6, "Магистр"), (10, "провидец"), (15, "академик")]:
        session.score, session.money = score, 0
        assert expected in rules.title(session)
    session.score, session.money = 3, rules.CROOK_MONEY
    assert rules.title(session) == C.CROOK_TITLE


def test_report_and_metrics_explain_the_episode():
    session, rng = make("businessman.wife", rolls=[0.0, 0.0])
    results = act(session, rng, "m:tarot", "m:question", "q:what")
    results.append(predict_after_research(session, rng, "relative", "gain", "sudden", "see"))
    report = results[-1].finished_client.report
    assert [(s.slot, s.matched) for s in report.slots] == [("who", True), ("what", False), ("when", True)]
    assert report.outcome == "partial" and report.echo_slots == ("what",)
    hints = {(h.source, h.slot): h.truthful for h in report.hints}
    assert hints[("tarot", "who")] is True and hints[("question", "what")] is False
    assert report.legend_before == report.legend_after == 2
    (reveal,) = metrics(results, "client_reveal")
    assert reveal["outcome"] == "partial" and reveal["echo_slots"] == ["what"] and reveal["story"] == "businessman.wife"
    info = metrics(results, "action")
    assert [d["kind"] for d in info] == ["info", "info", "prediction"]
    assert info[0]["method"] == "tarot" and info[0]["quality"] == "clear" and info[0]["truthful"] is True


def predict_after_research(session, rng, who, what, when, confidence):
    return act(session, rng, f"s:who:{who}", f"s:what:{what}", f"s:when:{when}", f"a:{confidence}")[-1]


def test_summary_has_everything_for_the_log():
    session, rng = make("jealous.lena", "businessman.partner", "skeptic.mom")
    act(session, rng, "m:tarot")
    for _ in range(3):
        if session.client.phase == "research":
            act(session, rng, "go")
        act(session, rng, "s:who:" + session.client.options["who"][0], "s:what:" + session.client.options["what"][0],
            "s:when:sudden", "a:maybe")
    data = rules.summary(session, now=123.0)
    for key in ("money", "score", "legend_left", "max_streak", "hits", "partials", "misses", "exposures",
                "methods_used", "mastery", "clients", "duration_s", "reason", "title", "rules"):
        assert key in data, key
    assert data["methods_used"] == ["tarot"] and data["clients"] == 3 and data["rules"] == rules.RULES_VERSION


def test_participants_only_and_invalid_actions():
    session, rng = make("jealous.lena")
    with pytest.raises(PermissionError):
        rules.act(session, 999, "go", rng, now=1)
    with pytest.raises(ValueError):
        rules.act(session, 7, "a:swear", rng, now=1)


def test_same_seed_same_party():
    def run(seed):
        rng = random.Random(seed)
        session = rules.new_session(session_id=SID, chat_id=1, player_id=1, player_name="Зоя", rng=rng, now=0)
        while not session.finished:
            actions = [a.id for a in rules.available_actions(session)]
            rules.act(session, 1, rng.choice(actions), rng, now=1)
        return rules.summary(session, now=1)

    assert run(42) == run(42)


def test_idle_and_abandon():
    session, rng = make("jealous.lena", now=0.0)
    assert not rules.is_idle(session, now=179.0) and rules.is_idle(session, now=180.0)
    act(session, rng, "m:tarot")
    data = rules.abandon_summary(session, "timeout", now=500.0)
    assert data["reason"] == "timeout" and data["idle_s"] == 499.0 and data["phase"] == "research"
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
    assert registry.idle(now=500.0) == [first]
    assert registry.remove(first) and len(registry) == 0


# --- контракт ---------------------------------------------------------------

def test_view_matches_phase_and_serializes():
    session, rng = make("jealous.lena")
    view = rules.build_view(session)
    assert {a.kind for a in view.actions} == {"method", "control"}
    assert view.client.observation and view.preview is None
    json.dumps(view.to_dict(), ensure_ascii=False)

    act(session, rng, "go", "s:who:colleague")
    view = rules.build_view(session)
    assert {a.id for a in view.actions if a.kind == "option"} == {f"s:what:{t}" for t in session.client.options["what"]}
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
    finished = []
    while not session.finished:
        result = rules.act(session, 1, rng.choice([a.id for a in rules.available_actions(session)]), rng, now=1)
        views.append(result.view)
        if result.finished_client:
            finished.append((result.view, result.finished_client))
    return views, finished


def test_every_button_fits_telegram_and_round_trips():
    for seed in range(15):
        views, _ = _all_views(seed)
        for view in views:
            for row in textui.keyboard(view):
                for _, data in row:
                    assert len(data.encode()) <= 64
                    parsed = textui.parse(data)
                    assert parsed.kind == "act" and parsed.session_id == SID and parsed.step == view.step
            assert {textui.parse(d).action for row in textui.keyboard(view) for _, d in row} == {a.id for a in view.actions}


def test_again_buttons_round_trip():
    for answer in ("yes", "no"):
        parsed = textui.parse(textui.again_data(SID, answer))
        assert (parsed.kind, parsed.answer, parsed.session_id) == ("again", answer, SID)


@pytest.mark.parametrize("data", [None, "", "guru", "guru:XYZ:1:go", "guru:abc:x:go", "croc:abc:1:go"])
def test_parse_rejects_garbage(data):
    assert textui.parse(data) is None


def test_texts_fit_and_hide_numbers():
    for seed in range(15):
        views, finished = _all_views(seed)
        for view in views:
            if view.client is None:
                continue
            text, _ = textui.render_view(view)
            assert len(text) <= 4096
            assert "%" not in text and "Доверие" not in text and "подозрение" not in text.lower()
        for view, client in finished:
            text = textui.render_finished(view, client)
            assert len(text) <= 4096 and "Разбор" in text and client.report.reveal[:20] in text


def test_long_dialog_is_trimmed_from_the_top():
    session, _ = make("jealous.lena")
    session.client.lines.extend(["очень длинная реплика " * 10] * 40)
    text, _ = textui.render_view(rules.build_view(session))
    assert len(text) <= textui.MESSAGE_LIMIT and "👉" in text and "…" in text


def test_summary_and_abandoned_texts():
    session, rng = make("jealous.lena")
    data = rules.abandon_summary(session, "timeout", now=999.0)
    view = rules.build_view(session)
    text, rows = textui.render_summary(view)
    assert "брошен" in text and "Звание" in text and data["reason"] == "timeout"
    assert [label for row in rows for label, _ in row] == [textui.AGAIN_YES, textui.AGAIN_NO]
    abandoned = textui.render_abandoned(view, rules.current_client_view(session), "timeout")
    assert textui.ABANDON_NOTES["timeout"] in abandoned


def test_intro_explains_the_rules():
    text = textui.render_intro(rules.build_view(make("jealous.lena")[0]))
    for word in ("ладонь", "Таро", "гороскоп", "Клянусь", "Легенда"):
        assert word in text


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


def test_client_sources_are_distinct_from_own():
    from games.guru.model import OWN_SOURCES

    assert not (CLIENT_SOURCES & OWN_SOURCES)
    assert set(C.SOURCE_LABELS) == CLIENT_SOURCES | OWN_SOURCES
