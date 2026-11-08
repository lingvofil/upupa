"""Салон «Гуру»: баланс на настоящем движке (симуляция стратегий).

Стратегии видят то, что понял бы внимательный игрок: разметку ``points`` и ``doubts``
рассказа и ответов, предложенные толкования и реакцию клиента. Правду историй и
качество расклада они не подглядывают.
"""

import random
import statistics
import zlib
from collections import Counter

import pytest

from games.guru import content as C
from games.guru import rules
from games.guru.model import CLIENT_SOURCES, Knowledge

PARTIES = 150
UNDERSTOOD = 65  # какую долю намёков улавливает внимательный игрок, %
REACTION_WEIGHT = {"honest": 1.0, "poker": 0.4, "overreact": 0.0}


def understood(client, key):
    """Живой игрок ловит намёк не всегда: детерминированно по клиенту и намёку."""
    return zlib.crc32(f"{client.name}{client.sign}{client.truth['when']}{key}".encode()) % 100 < UNDERSTOOD


def evidence(client):
    scores = Counter()
    for slot, tag in client.story.points:
        if understood(client, (slot, tag)):
            scores[(slot, client.resolve(tag))] += 1.5
    for k in client.knowledge:
        if k.source in CLIENT_SOURCES:
            scores[(k.slot, k.tag)] += 1.5
    for question_id in client.asked:
        question = client.story.question(question_id)
        for slot, tag in question.points:
            if understood(client, (question_id, slot, tag)):
                scores[(slot, client.resolve(tag))] += 2
        for slot, tag in question.doubts:
            if understood(client, (question_id, slot, tag)):
                scores[(slot, client.resolve(tag))] -= 3
    weight = REACTION_WEIGHT[client.kind.style]
    for reading in client.readings:
        for tag in reading.offered:
            scores[(reading.slot, tag)] += 0.5
        scores[(reading.slot, reading.voiced)] += (3 if reading.reaction == "warm" else -3) * weight
    return scores


def option_score(client, scores, slot, option):
    if slot != "what":
        return scores[(slot, option)]
    tag = C.WHAT[option]
    return scores[("what", option)] + scores[("sphere", tag.sphere)] + 0.7 * scores[("emotion", tag.emotion)]


def ranked(client, slot, rng):
    scores = evidence(client)
    values = [(option_score(client, scores, slot, option), rng.random(), option) for option in client.options[slot]]
    values.sort(reverse=True)
    return values


def reading_score(client, scores, slot, tag):
    if slot in ("who", "when"):
        return scores[(slot, tag)]
    tags = [t for t in C.WHAT.values() if t.key in client.options["what"]
            and (t.sphere if slot == "sphere" else t.emotion) == tag]
    return scores[(slot, tag)] + max((scores[("what", t.key)] for t in tags), default=0)


class Investigator:
    """Внимательный игрок: уточняет, гадает о слабом слоте, клянётся только при сильных уликах."""

    confidence = "adaptive"
    questions = ("check", "open")

    def clarify(self, client):
        remaining = [q for q in client.story.questions if q.id not in client.asked]
        wanted = self.questions[len(client.asked)] if len(client.asked) < len(self.questions) else None
        for question in remaining:
            if question.kind == wanted:
                return f"c:{question.id}"
        return "c:stop"

    def divine(self, client, session, rng):
        gaps = {}
        for slot in C.SLOTS:
            top = ranked(client, slot, rng)
            gaps[slot] = top[0][0] - top[1][0]
        for slot in sorted(gaps, key=gaps.get):
            if gaps[slot] >= 3:
                break
            for method in {"who": ("tarot",), "when": ("horoscope",), "what": ("palm", "energy")}[slot]:
                if method not in client.used_methods and not (client.kind.suspicion and method == "horoscope"):
                    return f"m:{method}"
        return "go"

    def interpret(self, client, rng):
        reading = client.pending
        scores = evidence(client)
        options = [(reading_score(client, scores, reading.slot, tag), rng.random(), tag) for tag in reading.offered]
        return f"i:{max(options)[2]}"

    def announce(self, client, rng):
        strong = 0
        for slot in C.SLOTS:
            top = ranked(client, slot, rng)
            if top[0][2] == client.picks[slot] and top[0][0] - top[1][0] >= 2:
                strong += 1
        echo = [slot for slot in C.SLOTS if rules.is_echo(client, slot, client.picks[slot])]
        return self.level(strong, echo, client)

    def level(self, strong, echo, client):
        level = "swear" if strong == 3 else "see" if strong == 2 else "maybe"
        if client.kind.echo == "expose" and echo:
            return "maybe"
        if client.kind.suspicion and level != "maybe" and client.suspicion >= rules.SKEPTIC_SEE_MISS_AT and strong < 3:
            return "maybe"
        if client.kind.echo == "discount" and len(echo) >= 2 and level == "swear":
            return "see"
        return level

    def choose(self, session, rng):
        client = session.client
        if client.phase == "clarify":
            return self.clarify(client)
        if client.phase == "divine":
            return self.divine(client, session, rng)
        if client.phase == "interpret":
            return self.interpret(client, rng)
        if client.phase in C.SLOTS:
            return f"s:{client.phase}:{ranked(client, client.phase, rng)[0][2]}"
        if client.phase == "announce":
            return f"a:{self.announce(client, rng)}"
        return "next"


class Cautious(Investigator):
    def level(self, strong, echo, client):
        return "maybe"


class Bold(Investigator):
    def level(self, strong, echo, client):
        return "swear"


class Greedy(Investigator):
    def level(self, strong, echo, client):
        return "swear" if strong >= 2 and not (client.kind.echo == "expose" and echo) else "maybe"


class IgnoresSource(Investigator):
    """Не думает, откуда сведения: объявляет по уликам, даже если это слова клиента."""

    def level(self, strong, echo, client):
        return "swear" if strong == 3 else "see" if strong == 2 else "maybe"


class CheckOnly(Investigator):
    questions = ("check", "check")


class OpenOnly(Investigator):
    questions = ("open", "open")


class NoQuestions(Investigator):
    questions = ()


class MasteryHunter(Investigator):
    """Следит, чтобы за смену прозвучали все три основных метода."""

    def divine(self, client, session, rng):
        missing = [m for m in C.MAIN_METHODS if m not in session.methods_used and m not in client.used_methods]
        clients_left = session.clients_total - session.client_no
        if missing and len(missing) >= clients_left:
            return f"m:{missing[0]}"
        return super().divine(client, session, rng)


def play(policy, seed, legend=None):
    rng = random.Random(seed)
    policy_rng = random.Random(seed + 10_000)
    session = rules.new_session(session_id="ab12cd34ef56", chat_id=1, player_id=1, player_name="Зоя", rng=rng, now=0)
    if legend is not None:
        session.legend = session.legend_start = legend
    step = 0
    while not session.finished:
        step += 1
        rules.act(session, 1, policy.choose(session, policy_rng), rng, now=float(step))
    return session


def stats(policy, parties=PARTIES, legend=None):
    sessions = [play(policy, seed, legend) for seed in range(parties)]
    outcomes = [r.outcome for s in sessions for r in s.reports]
    return {
        "score": statistics.mean(s.score for s in sessions),
        "hit": outcomes.count("hit") / len(outcomes),
        "partial": outcomes.count("partial") / len(outcomes),
        "exposure": outcomes.count("exposure") / len(outcomes),
        "streak2": sum(s.max_streak >= 2 for s in sessions) / parties,
        "early": sum(len(s.reports) < s.clients_total for s in sessions) / parties,
        "mastery": sum(s.mastery for s in sessions) / parties,
        "moves": statistics.mean(s.step for s in sessions),
    }


POLICIES = {
    "adaptive": Investigator,
    "cautious": Cautious,
    "bold": Bold,
    "greedy": Greedy,
    "ignores_source": IgnoresSource,
    "check_only": CheckOnly,
    "open_only": OpenOnly,
    "no_questions": NoQuestions,
    "mastery": MasteryHunter,
}


@pytest.fixture(scope="module")
def results():
    return {name: stats(policy()) for name, policy in POLICIES.items()}


def test_adaptive_play_beats_fixed_confidence(results):
    adaptive = results["adaptive"]["score"]
    assert adaptive > results["cautious"]["score"], "осторожность не должна быть оптимальной"
    assert adaptive > results["bold"]["score"], "максимальная уверенность не должна быть оптимальной"


def test_luck_still_matters(results):
    assert 0.25 <= results["adaptive"]["hit"] <= 0.60
    assert results["bold"]["exposure"] > 0.10


def test_streaks_happen_often_enough_to_feel(results):
    assert 0.12 <= results["adaptive"]["streak2"] <= 0.65


def test_questions_are_worth_asking(results):
    assert results["adaptive"]["score"] > results["no_questions"]["score"]


def test_neither_kind_of_question_dominates(results):
    """Дилемма: проверить версию клиента или узнать новое — разрыв не должен быть решающим."""
    assert abs(results["check_only"]["score"] - results["open_only"]["score"]) < 2.5


def test_source_of_information_matters(results):
    assert results["ignores_source"]["exposure"] > results["adaptive"]["exposure"]
    assert results["ignores_source"]["score"] < results["adaptive"]["score"]


def test_mastery_is_an_incentive_not_a_tax(results):
    assert results["mastery"]["mastery"] > 0.9
    assert results["mastery"]["score"] >= results["adaptive"]["score"] - 0.3


def test_shift_has_room_to_think(results):
    assert 24 <= results["adaptive"]["moves"] <= 42


def test_legend_rarely_ends_careful_shift_early():
    assert stats(Investigator(), parties=100)["early"] <= 0.05
    assert stats(Investigator(), parties=50, legend=3)["early"] == 0, "при 3 запасах досрочный конец невозможен"


def test_knowledge_is_hidden_engine_data():
    assert Knowledge("who", "self", "tarot", True).supports("who", "self")
