"""Салон «Гуру»: баланс v0.2 на настоящем движке (симуляция стратегий).

Стратегии видят то же, что игрок: блокнот, варианты конструктора, остаток приёмов.
Правду историй и правдивость подсказок они не подглядывают.
"""

import random
import statistics

import pytest

from games.guru import content as C
from games.guru import rules
from games.guru.model import CLIENT_SOURCES

PARTIES = 400


def slot_scores(client, slot):
    scores = {}
    for option in client.options[slot]:
        score = 0.0
        for k in client.knowledge:
            if k.source in CLIENT_SOURCES:
                if k.slot == slot and option in k.tags:
                    score += 2
                continue
            if not k.positive:
                if slot == "what" and k.slot == "sphere" and C.WHAT[option].sphere in k.tags:
                    score -= 3
                continue
            if k.supports(slot, option):
                if k.slot == "emotion":
                    score += 2 if k.clear else 1
                else:
                    score += 3 if k.clear else 1.2
        scores[option] = score
    return scores


def best(client, slot, rng):
    scores = slot_scores(client, slot)
    top = max(scores.values())
    return rng.choice([option for option, value in scores.items() if value == top]), top


class Investigator:
    """Адаптивный игрок: добывает недостающие слоты и клянётся только при сильных уликах."""

    confidence = "adaptive"
    aware = True

    def research(self, client, session, rng):
        strength = {}
        for slot in ("what", "who", "when"):
            option, top = best(client, slot, rng)
            only_words = top > 0 and not any(
                k.source not in CLIENT_SOURCES and k.supports(slot, option) for k in client.knowledge
            )
            strength[slot] = top - (1.5 if only_words else 0)
        for slot in sorted(strength, key=strength.get):
            if strength[slot] >= 3:
                break
            method = {"who": "tarot", "when": "horoscope", "what": "palm"}[slot]
            if slot == "what" and method in client.used_methods:
                method = "energy"
            if client.kind.suspicion and method == "horoscope":
                continue
            if method not in client.used_methods:
                return f"m:{method}"
        return "go"

    def palm_line(self, client, rng):
        option, _ = best(client, "what", rng)
        return f"p:{C.LINE_FOR_SPHERE[C.WHAT[option].sphere]}"

    def question_slot(self, client):
        return "q:who"

    def announce(self, client, rng):
        strong = 0
        for slot in C.SLOTS:
            top = slot_scores(client, slot)[client.picks[slot]]
            if top >= 3 or (top >= 2 and client.kind.echo == "delight"):
                strong += 1
        if self.confidence != "adaptive":
            return self.confidence_for(strong, client)
        echo = [slot for slot in C.SLOTS if rules.is_echo(client, slot, client.picks[slot])]
        level = "swear" if strong == 3 else "see" if strong == 2 else "maybe"
        if self.aware and client.kind.echo == "expose" and echo:
            level = "maybe"
        if (self.aware and client.kind.suspicion and level == "see"
                and client.suspicion >= rules.SKEPTIC_SEE_MISS_AT):
            level = "maybe"
        if self.aware and client.kind.echo == "discount" and len(echo) >= 2 and level == "swear":
            level = "see"
        return level

    def confidence_for(self, strong, client):
        return self.confidence

    def choose(self, session, rng):
        client = session.client
        if client.phase == "research":
            return self.research(client, session, rng)
        if client.phase == "palm":
            return self.palm_line(client, rng)
        if client.phase == "ask":
            return self.question_slot(client)
        if client.phase in C.SLOTS:
            option, _ = best(client, client.phase, rng)
            return f"s:{client.phase}:{option}"
        return f"a:{self.announce(client, rng)}"


class Cautious(Investigator):
    confidence = "maybe"


class Bold(Investigator):
    confidence = "swear"


class Greedy(Investigator):
    confidence = "greedy"

    def confidence_for(self, strong, client):
        echo = [slot for slot in C.SLOTS if rules.is_echo(client, slot, client.picks[slot])]
        if strong >= 2 and not (client.kind.echo == "expose" and echo):
            return "swear"
        return "maybe"


class Questioner(Investigator):
    """Спрашивает обо всём, что не сказано, и объявляет «Вижу»."""

    smart = False

    def research(self, client, session, rng):
        if "question" not in client.used_methods and client.actions_left > 0:
            return "m:question"
        return "go"

    def question_slot(self, client):
        said = {k.slot for k in client.knowledge if k.source in CLIENT_SOURCES}
        for slot in C.SLOTS:
            if slot not in said:
                return f"q:{slot}"
        return "q:when"

    def announce(self, client, rng):
        echo = [slot for slot in C.SLOTS if rules.is_echo(client, slot, client.picks[slot])]
        if self.smart and client.kind.echo == "expose" and echo:
            return "maybe"
        return "see"


class SmartQuestioner(Questioner):
    smart = True


class MasteryHunter(Investigator):
    """Как адаптивный, но следит, чтобы за смену прозвучали все три основных метода."""

    def research(self, client, session, rng):
        missing = [m for m in C.MAIN_METHODS if m not in session.methods_used and m not in client.used_methods]
        clients_left = session.clients_total - session.client_no
        if missing and client.actions_left > 0 and len(missing) >= clients_left:
            return f"m:{missing[0]}"
        return super().research(client, session, rng)


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
        "exposure": outcomes.count("exposure") / len(outcomes),
        "streak2": sum(s.max_streak >= 2 for s in sessions) / parties,
        "early": sum(len(s.reports) < s.clients_total for s in sessions) / parties,
        "mastery": sum(s.mastery for s in sessions) / parties,
    }


@pytest.fixture(scope="module")
def results():
    return {
        name: stats(policy)
        for name, policy in {
            "adaptive": Investigator(),
            "cautious": Cautious(),
            "bold": Bold(),
            "greedy": Greedy(),
            "questioner": Questioner(),
            "smart_questioner": SmartQuestioner(),
            "mastery": MasteryHunter(),
        }.items()
    }


def test_adaptive_play_beats_fixed_confidence(results):
    adaptive = results["adaptive"]["score"]
    assert adaptive > results["cautious"]["score"], "осторожность не должна быть оптимальной"
    assert adaptive > results["bold"]["score"], "максимальная уверенность не должна быть оптимальной"
    assert adaptive > results["greedy"]["score"], "клятва на двух уликах из трёх не должна окупаться"


def test_luck_still_matters(results):
    assert 0.25 <= results["adaptive"]["hit"] <= 0.55
    assert results["bold"]["exposure"] > 0.10


def test_streaks_happen_often_enough_to_feel(results):
    assert 0.15 <= results["adaptive"]["streak2"] <= 0.45


def test_source_of_information_matters(results):
    assert results["smart_questioner"]["score"] > results["questioner"]["score"]
    assert results["questioner"]["exposure"] > results["smart_questioner"]["exposure"]


def test_mastery_is_an_incentive_not_a_tax(results):
    assert results["mastery"]["mastery"] > 0.9
    assert results["mastery"]["score"] >= results["adaptive"]["score"]


def test_legend_rarely_ends_careful_shift_early():
    assert stats(Investigator(), parties=200)["early"] <= 0.02
    assert stats(Investigator(), parties=100, legend=3)["early"] == 0, "при 3 запасах досрочный конец невозможен"
