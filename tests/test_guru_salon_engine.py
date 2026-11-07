"""Салон «Гуру»: игровое ядро без Telegram (правила, рендер, баланс)."""

import ast
import random
import string
from pathlib import Path

import pytest

from games.guru import content, engine, render
from games.guru.engine import (
    CLIENTS_PER_SALON,
    EXPOSURE_AT,
    MOVES,
    START_REPUTATION,
    VAGUE_TRUST_CAP,
    SalonRegistry,
    abandon_salon,
    apply_move,
    is_idle,
    new_client,
    new_salon,
    ritual_chance,
    ritual_price,
    salon_summary,
    salon_title,
    session_fee,
    specific_chance,
)

CHAT = -100777


class ScriptedRandom(random.Random):
    """``random()`` отдаёт броски по сценарию (потом 0.5); выбор и выборка — без перемешивания."""

    def __init__(self, rolls=()):
        super().__init__(0)
        self.rolls = list(rolls)

    def random(self):
        return self.rolls.pop(0) if self.rolls else 0.5

    def choice(self, seq):
        return seq[0]

    def sample(self, population, k):
        return list(population)[:k]


HIT, MISS = 0.0, 0.999


def make_salon(kind_key=None, rng=None, now=0.0):
    rng = rng or ScriptedRandom()
    salon = new_salon(session_id="abc123", chat_id=CHAT, player_id=7, player_name="Зоя", rng=rng, now=now)
    if kind_key is not None:
        rest = tuple(key for key in salon.plan if key != kind_key)
        salon.plan = (kind_key, *rest)
        salon.client = new_client(content.KINDS_BY_KEY[kind_key], rng, reputation=salon.reputation, buzz=0)
    return salon


def play(salon, moves, rng):
    return [apply_move(salon, move, rng, now=float(index)) for index, move in enumerate(moves, start=1)]


# --- создание ---------------------------------------------------------------

def test_new_salon_has_every_client_kind_once():
    salon = new_salon(session_id="s", chat_id=CHAT, player_id=1, player_name="Зоя", rng=random.Random(5), now=10.0)

    assert sorted(salon.plan) == sorted(kind.key for kind in content.CLIENT_KINDS)
    assert salon.clients_total == CLIENTS_PER_SALON
    assert salon.reputation == START_REPUTATION and salon.money == 0
    assert salon.client.kind.key == salon.plan[0]
    assert salon.client.revealed == [0], "первая зацепка видна сразу"
    assert salon.started_at == salon.last_activity == 10.0


def test_new_salon_rejects_bad_client_count():
    with pytest.raises(ValueError):
        new_salon(session_id="s", chat_id=CHAT, player_id=1, player_name="Зоя", rng=random.Random(), now=0, clients=0)


def test_skeptic_starts_suspicious_and_reputation_shifts_starting_trust():
    rng = ScriptedRandom()
    skeptic = content.KINDS_BY_KEY["skeptic"]

    assert new_client(skeptic, rng, reputation=50, buzz=0).suspicion == engine.SKEPTIC_START_SUSPICION
    assert new_client(skeptic, rng, reputation=50, buzz=0).trust == skeptic.base_trust
    assert new_client(skeptic, rng, reputation=100, buzz=2).trust == skeptic.base_trust + 10 + 6
    assert new_client(skeptic, rng, reputation=0, buzz=0).trust == 5  # не ниже пола


# --- общие слова ------------------------------------------------------------

def test_barnum_raises_trust_with_diminishing_returns_up_to_cap():
    rng = ScriptedRandom()
    salon = make_salon("desperate", rng)
    salon.client.trust = 30
    salon.client.patience = 10

    gains = []
    for _ in range(4):
        before = salon.client.trust
        apply_move(salon, "barnum", rng, now=1)
        gains.append(salon.client.trust - before)

    assert gains[0] > gains[1] > gains[2] >= gains[3] >= 0
    salon.client.vague_uses = 0
    salon.client.trust = VAGUE_TRUST_CAP - 1
    apply_move(salon, "barnum", rng, now=2)
    assert salon.client.trust == VAGUE_TRUST_CAP, "общими словами выше потолка не поднять"


def test_businessman_hates_vague_talk():
    rng = ScriptedRandom()
    salon = make_salon("businessman", rng)
    before = salon.client.trust

    result = apply_move(salon, "barnum", rng, now=1)

    assert salon.client.trust == before - 8
    assert result.hit is None and not result.clue
    assert "доверие -8" in salon.client.lines[-1]


def test_rainbow_can_reveal_clue():
    rng = ScriptedRandom([0.0])
    salon = make_salon("desperate", rng)

    result = apply_move(salon, "rainbow", rng, now=1)

    assert result.clue
    assert salon.client.fresh_clues == 2
    assert any(salon.client.facts[1].clue in line for line in salon.client.lines)


# --- зацепки и подозрение ---------------------------------------------------

def test_leading_question_always_gives_clue_but_costs_trust():
    rng = ScriptedRandom()
    salon = make_salon("jealous", rng)
    before = salon.client.trust

    result = apply_move(salon, "leading", rng, now=1)

    assert result.clue and salon.client.fresh_clues == 2
    assert salon.client.trust == before - 8


def test_shotgun_miss_costs_trust_and_clues_run_out():
    rng = ScriptedRandom([MISS, HIT, HIT, HIT])
    salon = make_salon("desperate", rng)
    salon.client.patience = 10
    before = salon.client.trust

    first, second, third, fourth = play(salon, ["shotgun"] * 4, rng)

    assert not first.clue and salon.client.trust < before
    assert second.clue and third.clue
    assert not fourth.clue, "у клиента всего три факта, первый виден сразу"
    assert salon.client.fresh_clues == len(salon.client.facts)


def test_skeptic_exposes_salon_when_suspicion_overflows():
    rng = ScriptedRandom([MISS])  # дробь не попала
    salon = make_salon("skeptic", rng)

    results = play(salon, ["barnum", "leading", "shotgun"], rng)

    assert [r.client_result for r in results] == [None, None, "exposed"]
    exposed = results[-1]
    assert exposed.finished_client is not None and exposed.finished_client.suspicion >= EXPOSURE_AT
    assert salon.exposures == 1
    assert salon.reputation == START_REPUTATION - 20
    assert salon.client_no == 1 and salon.client.kind.key != "skeptic"
    assert any("Подозрение" not in line for line in exposed.finished_client.lines)


def test_skeptic_hit_calms_suspicion_and_boosts_reputation():
    rng = ScriptedRandom([HIT])
    salon = make_salon("skeptic", rng)
    salon.client.suspicion = 70

    result = apply_move(salon, "specific", rng, now=1)

    assert result.hit is True
    assert salon.client.suspicion == 20
    assert salon.reputation == START_REPUTATION + 12
    assert salon.buzz == 2


# --- конкретика -------------------------------------------------------------

def test_specific_chance_grows_with_fresh_clues_and_trust():
    salon = make_salon("jealous")
    client = salon.client
    client.trust = 50

    assert specific_chance(client) == pytest.approx(0.32)
    client.revealed = [0, 1, 2]
    assert specific_chance(client) == pytest.approx(0.76)
    client.used = {0, 1, 2}
    assert specific_chance(client) == pytest.approx(0.10)
    client.trust = 0
    assert specific_chance(client) == pytest.approx(0.05)
    client.trust, client.used = 100, set()
    assert specific_chance(client) == pytest.approx(0.90)


def test_specific_hit_pays_tip_spends_clue_and_spreads_word():
    rng = ScriptedRandom([HIT])
    salon = make_salon("desperate", rng)
    trust = salon.client.trust

    result = apply_move(salon, "specific", rng, now=1)

    client = salon.client
    assert result.hit is True
    assert client.trust == min(100, trust + 30)
    assert client.fresh_clues == 0 and client.used == {0}
    assert salon.money == result.money_delta == 350  # половина бюджета 700
    assert (salon.buzz, salon.reputation) == (1, START_REPUTATION + 4)
    assert client.facts[0].hit.rstrip(".") in client.lines[0]


def test_specific_miss_hurts_trust_and_reputation():
    rng = ScriptedRandom([MISS])
    salon = make_salon("jealous", rng)
    trust = salon.client.trust

    result = apply_move(salon, "specific", rng, now=1)

    decoy = salon.client.decoys[0]
    assert result.hit is False
    assert salon.client.trust == trust - 25
    assert salon.reputation == START_REPUTATION - 6
    assert decoy.guess.rstrip(".") in salon.client.lines[0]
    assert decoy.retort.rstrip(".") in salon.client.lines[1]


def test_blogger_doubles_reputation_swings():
    rng = ScriptedRandom([MISS, HIT])
    salon = make_salon("blogger", rng)

    apply_move(salon, "specific", rng, now=1)
    assert salon.reputation == START_REPUTATION - 12
    apply_move(salon, "specific", rng, now=2)
    assert salon.buzz == 2


def test_zero_reputation_closes_salon_mid_session():
    rng = ScriptedRandom([MISS])
    salon = make_salon("jealous", rng)
    salon.reputation = 5

    result = apply_move(salon, "specific", rng, now=1)

    assert result.client_result == "mob" and result.salon_over
    assert salon.finish_reason == "closed"
    assert salon_title(salon) == content.CLOSED_TITLE


def test_trust_at_zero_makes_client_leave():
    rng = ScriptedRandom([MISS])
    salon = make_salon("jealous", rng)
    salon.client.trust = 10

    result = apply_move(salon, "specific", rng, now=1)

    assert result.client_result == "left_angry"
    assert result.finished_client.paid == 0


# --- закрытие клиента -------------------------------------------------------

def test_bill_pays_by_trust_and_happy_client_spreads_word():
    rng = ScriptedRandom()
    salon = make_salon("jealous", rng)
    salon.client.trust = 80
    expected = session_fee(salon, salon.client)

    result = apply_move(salon, "bill", rng, now=1)

    assert expected == 1450  # 1800 * 0.8, округление до 50
    assert result.client_result == "paid" and result.money_delta == expected
    assert salon.buzz == 1
    assert salon.served == 1 and salon.client_no == 1


def test_unhappy_client_hurts_reputation_on_bill():
    rng = ScriptedRandom()
    salon = make_salon("jealous", rng)
    salon.client.trust = 10

    apply_move(salon, "bill", rng, now=1)

    assert salon.reputation == START_REPUTATION - 4


def test_patience_runs_out_and_client_pays_anyway():
    rng = ScriptedRandom()
    salon = make_salon("jealous", rng)  # терпение 3
    salon.client.trust = 60

    results = play(salon, ["barnum", "barnum", "barnum"], rng)

    assert [r.client_result for r in results] == [None, None, "walked_out"]
    assert results[-1].money_delta > 0


def test_ritual_success_sells_big():
    rng = ScriptedRandom([HIT])
    salon = make_salon("desperate", rng)
    salon.client.trust = 90
    expected = session_fee(salon, salon.client) + ritual_price(salon, salon.client)

    result = apply_move(salon, "ritual", rng, now=1)

    assert result.client_result == "ritual_sold"
    assert result.money_delta == expected
    assert salon.rituals_sold == 1


def test_ritual_failure_costs_session_and_reputation():
    rng = ScriptedRandom([MISS])
    salon = make_salon("desperate", rng)

    result = apply_move(salon, "ritual", rng, now=1)

    assert result.client_result == "ritual_failed"
    assert result.money_delta == 0
    assert salon.reputation == START_REPUTATION - 10
    assert salon.rituals_failed == 1


def test_failed_ritual_on_skeptic_is_exposure():
    rng = ScriptedRandom([MISS])
    salon = make_salon("skeptic", rng)

    result = apply_move(salon, "ritual", rng, now=1)

    assert result.client_result == "exposed"
    assert salon.exposures == 1


def test_ritual_chance_depends_on_trust():
    client = make_salon("desperate").client
    client.trust = 50
    low = ritual_chance(client)
    client.trust = VAGUE_TRUST_CAP
    vague_best = ritual_chance(client)
    client.trust = 100

    assert low == pytest.approx(0.05)
    assert 0.4 < vague_best < 0.5, "одними общими словами ритуал — лотерея"
    assert ritual_chance(client) == pytest.approx(0.95)


# --- партия целиком ---------------------------------------------------------

def test_full_salon_completes_after_every_client():
    rng = ScriptedRandom()
    salon = make_salon(rng=rng)

    results = play(salon, ["bill"] * CLIENTS_PER_SALON, rng)

    assert results[-1].salon_over and salon.finished
    assert salon.finish_reason == "completed"
    assert [r.client_no for r in results] == list(range(CLIENTS_PER_SALON))
    assert salon.served == CLIENTS_PER_SALON
    with pytest.raises(ValueError):
        apply_move(salon, "bill", rng, now=99)


def test_unknown_move_is_rejected():
    with pytest.raises(ValueError):
        apply_move(make_salon(), "tarot", ScriptedRandom(), now=1)


def test_summary_has_everything_for_the_log():
    rng = ScriptedRandom()
    salon = make_salon(rng=rng, now=100.0)
    play(salon, ["bill"] * CLIENTS_PER_SALON, rng)

    summary = salon_summary(salon, now=163.44)

    assert summary["money"] == salon.money
    assert summary["reputation"] == salon.reputation
    assert summary["clients"] == summary["clients_total"] == CLIENTS_PER_SALON
    assert summary["duration_s"] == 63.4
    assert summary["reason"] == "completed"
    assert summary["rules"] == engine.RULES_VERSION
    assert {"exposures", "hits", "misses", "rituals_sold", "moves", "title"} <= summary.keys()


def test_move_result_log_data():
    rng = ScriptedRandom([HIT])
    salon = make_salon("desperate", rng)

    data = apply_move(salon, "specific", rng, now=1).log_data()

    assert data["move"] == "specific" and data["client"] == "desperate"
    assert data["hit"] is True and data["money_delta"] == 350
    assert data["client_no"] == 0 and data["turn"] == 0 and data["result"] is None


def test_same_seed_same_game():
    def run(seed):
        rng = random.Random(seed)
        salon = new_salon(session_id="s", chat_id=CHAT, player_id=1, player_name="Зоя", rng=rng, now=0)
        while not salon.finished:
            apply_move(salon, rng.choice(MOVES), rng, now=1)
        return salon_summary(salon, now=1)

    assert run(42) == run(42)


@pytest.mark.parametrize(
    ("money", "title"),
    [(0, "Гадалка у перехода"), (4000, "Потомственная"), (9500, "Гуру районного"), (30000, "секты")],
)
def test_titles_follow_money(money, title):
    salon = make_salon()
    salon.money = money
    assert title in salon_title(salon)


def test_two_exposures_beat_money_title():
    salon = make_salon()
    salon.money, salon.exposures = 50000, 2
    assert salon_title(salon) == content.EXPOSED_TITLE


# --- брошенные партии и реестр ----------------------------------------------

def test_idle_and_abandon():
    salon = make_salon(now=0.0)

    assert not is_idle(salon, now=179.0)
    assert is_idle(salon, now=180.0)
    abandon_salon(salon, "timeout")
    abandon_salon(salon, "quit")
    assert salon.finish_reason == "timeout"
    assert not is_idle(salon, now=999.0)


def test_move_resets_idle_clock():
    rng = ScriptedRandom()
    salon = make_salon("desperate", rng, now=0.0)

    apply_move(salon, "barnum", rng, now=170.0)

    assert not is_idle(salon, now=300.0)
    assert is_idle(salon, now=350.0)


def test_registry_one_salon_per_chat():
    registry = SalonRegistry()
    first = make_salon(now=0.0)
    second = make_salon(now=0.0)

    registry.add(first)
    with pytest.raises(ValueError):
        registry.add(second)
    assert registry.get(CHAT) is first
    assert not registry.remove(second)
    assert registry.idle(now=500.0) == [first]
    assert registry.remove(first) and len(registry) == 0


# --- рендер и кнопки --------------------------------------------------------

def test_keyboard_has_every_move_once_and_short_callbacks():
    salon = make_salon()
    keyboard = render.client_keyboard(salon)
    buttons = [button for row in keyboard for button in row]

    assert sorted(render.parse_callback(data).move for _, data in buttons) == sorted(MOVES)
    assert all(len(data.encode()) <= 64 for _, data in buttons)


def test_callback_round_trip_and_staleness():
    rng = ScriptedRandom()
    salon = make_salon("desperate", rng)
    data = render.move_callback(salon, "barnum")
    parsed = render.parse_callback(data)

    assert parsed == render.SalonCallback("move", "abc123", 0, 0, "barnum")
    assert render.is_current(salon, parsed)
    apply_move(salon, "barnum", rng, now=1)
    assert not render.is_current(salon, parsed), "второе нажатие той же кнопки не играет"
    other = render.SalonCallback("move", "ffffff", 0, 1, "barnum")
    assert not render.is_current(salon, other)


@pytest.mark.parametrize("data", [None, "", "guru", "guru:abc:0:0:tarot", "guru:XYZ:0:0:bill", "croc:abc:0:0:bill"])
def test_parse_callback_rejects_garbage(data):
    assert render.parse_callback(data) is None


def test_parse_again_callback():
    assert render.parse_callback(render.AGAIN_CALLBACK) == render.SalonCallback("again")


def test_render_client_shows_status_and_chances():
    salon = make_salon("skeptic")

    text = render.render_client(salon, salon.client, client_no=0)

    assert "клиент 1/5" in text and salon.client.name in text
    assert salon.client.facts[0].clue in text
    assert "Подозрение 20/100" in text
    assert "Попадание ~" in text and "Ритуал ~" in text
    assert "Касса 0 ₽" in text


def test_render_finished_client_shows_result_not_status():
    rng = ScriptedRandom()
    salon = make_salon("jealous", rng)
    result = apply_move(salon, "bill", rng, now=1)

    text = render.render_client(salon, result.finished_client, client_no=result.client_no)

    assert render.RESULT_LABELS["paid"] in text
    assert "Попадание" not in text
    assert "клиент 1/5" in text


def test_render_summary_and_abandoned():
    rng = ScriptedRandom()
    salon = make_salon(rng=rng)
    abandon_salon(salon, "timeout")

    assert render.ABANDON_LABELS["timeout"] in render.render_abandoned_client(salon)
    summary = render.render_summary(salon)
    assert "брошен" in summary and "Звание:" in summary


def test_rules_explain_every_button():
    text = render.render_rules(make_salon())
    for label in render.MOVE_LABELS.values():
        assert label.split()[0] in text
    assert "3 мин" in text


def test_money_and_quotes_formatting():
    assert engine.rub(0) == "0 ₽"
    assert engine.rub(12500) == "12 500 ₽"
    assert engine._quote("Вижу воду.") == "«Вижу воду»"
    assert engine._quote("Кто-то пожилой...") == "«Кто-то пожилой...»"
    assert engine._quote("Что случилось?") == "«Что случилось?»"


# --- контент ----------------------------------------------------------------

def test_content_is_complete():
    for kind in content.CLIENT_KINDS:
        assert len(kind.facts) == 3, kind.key
        assert len(kind.decoys) >= 3, kind.key
        assert kind.names and kind.intros
    for outcome, pool in content.REACTIONS.items():
        if outcome in {"exposed", "suspicious"}:
            assert "skeptic" in pool
            continue
        for kind in content.CLIENT_KINDS:
            assert pool.get(kind.key) or pool.get("*"), (outcome, kind.key)


def test_templates_only_use_known_placeholders():
    allowed = {"name", "amount", "price"}
    lines = [line for pool in content.REACTIONS.values() for variants in pool.values() for line in variants]
    lines += [line for variants in content.PLAYER_LINES.values() for line in variants]
    for line in lines:
        fields = {name for _, name, _, _ in string.Formatter().parse(line) if name}
        assert fields <= allowed, line


# --- баланс -----------------------------------------------------------------

def _vague_policy(salon, client):
    if client.patience <= 1 or client.trust >= VAGUE_TRUST_CAP:
        return "bill"
    return "barnum" if client.vague_uses % 2 == 0 else "rainbow"


def _reckless_policy(salon, client):
    return "ritual" if client.trust >= 75 else "specific"


def _cold_reader_policy(salon, client):
    if client.kind.skeptic:
        if client.fresh_clues < 2 and client.suspicion + 35 < EXPOSURE_AT and client.patience > 1:
            return "leading"
        if client.hits == 0 and specific_chance(client) >= 0.3 and client.suspicion + 45 < EXPOSURE_AT:
            return "specific"
        return "bill"
    if client.hits and ritual_chance(client) >= 0.6:
        return "ritual"
    if client.fresh_clues < 2 and client.patience > 1:
        return "shotgun" if client.kind.gullibility >= 1 else "leading"
    if specific_chance(client) >= 0.45:
        return "specific"
    if ritual_chance(client) >= 0.6:
        return "ritual"
    return "bill"


def _average(policy, games=300):
    money, closed = 0, 0
    for seed in range(games):
        rng = random.Random(seed)
        salon = new_salon(session_id="s", chat_id=CHAT, player_id=1, player_name="Зоя", rng=rng, now=0)
        while not salon.finished:
            apply_move(salon, policy(salon, salon.client), rng, now=1)
        money += salon.money
        closed += salon.finish_reason == "closed"
    return money / games, closed / games


def test_balance_rewards_reading_the_client_over_safe_or_reckless_play():
    """Главное решение — конкретность: выгоднее добыть зацепки и рискнуть, чем мямлить или палить наугад."""
    vague_money, vague_closed = _average(_vague_policy)
    reckless_money, reckless_closed = _average(_reckless_policy)
    reader_money, reader_closed = _average(_cold_reader_policy)

    assert vague_closed == 0, "осторожная игра не сжигает салон"
    assert reckless_closed > 0.5, "конкретика наугад сжигает салон"
    assert reader_money > 1.5 * vague_money
    assert reader_money > 1.5 * reckless_money
    assert reader_closed < 0.15


# --- архитектура ------------------------------------------------------------

ROOT = Path(__file__).resolve().parents[1]


def _imported_modules(path):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            yield from (alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            yield node.module


def test_core_does_not_know_about_telegram():
    forbidden = ("aiogram", "handlers", "features", "core.loader", "infrastructure")
    for path in (ROOT / "games" / "guru").glob("*.py"):
        bad = [name for name in _imported_modules(path) if name.startswith(forbidden)]
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
