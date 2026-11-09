"""Лавка салона и случайные события: эффекты покупок на настоящем движке, без Telegram."""

import random
import re

import pytest

from features.guru_wallets import WalletBook
from games.guru import content as C
from games.guru import live, rules, salon, textui
from games.guru.stories import STORIES, STORIES_BY_ID

SID = "ab12cd34ef56"


class MemoryRepository:
    def __init__(self, data=None):
        self.data = data

    def load(self):
        if self.data is None:
            raise FileNotFoundError
        return self.data

    def save(self, value):
        self.data = value


def shift(items=(), plan=("businessman.partner", "jealous.lena", "granny.petrovich"), seed=1):
    return rules.new_session(session_id=SID, chat_id=1, player_id=1, player_name="Зоя", rng=random.Random(seed), now=0, plan=plan, items=items)


def client(story, items=(), seed=1, **kwargs):
    return rules.new_client(STORIES_BY_ID[story], random.Random(seed), 0, perks=salon.perks_of(items), **kwargs)


# --- витрина ----------------------------------------------------------------

def test_items_are_well_formed():
    assert len(salon.ITEMS) >= 12
    for key, item in salon.ITEMS.items():
        assert re.fullmatch(r"[a-z_0-9]+", key) and item.key == key
        assert item.price > 0 and item.group in salon.GROUP_TITLES and item.note and 0 < len(item.short) <= 14
        assert len(textui.buy_data(key, 2 ** 40).encode()) <= 64


def test_every_group_has_goods():
    assert {item.group for item in salon.ITEMS.values()} == set(salon.GROUP_TITLES)


def test_perks_add_up_and_ignore_unknown_keys():
    perks = salon.perks_of(("flyers", "neon", "vanished_item"))
    assert perks.fee == pytest.approx(0.25)
    assert salon.perks_of(()) == salon.NO_PERKS


def test_rank_grows_with_purchases():
    ranks = [salon.rank(count) for count in (0, 1, 4, 8, 12)]
    assert len(set(ranks)) == 5 and salon.describe(()) is None and "🫖" in salon.describe(("tea",))


def test_refusal_explains_why():
    assert salon.refusal("tea", 5000, []) is None
    assert "уже стоит" in salon.refusal("tea", 5000, ["tea"])
    assert "Не хватает 1 000 ₽" in salon.refusal("tea", 2000, [])
    assert "Не хватает 3 000 ₽" in salon.refusal("tea", -500, [])
    assert "нет" in salon.refusal("unknown", 5000, [])


def test_shop_text_and_keyboard():
    text = textui.render_shop("Зоя", 3000, ["tea"])
    assert "Лавка салона мадам Зоя" in text and "✅" in text and len(text) < textui.MESSAGE_LIMIT
    rows = textui.shop_keyboard(["tea"], 7)
    assert all(len(row) <= 2 for row in rows) and sum(len(row) for row in rows) == len(salon.ITEMS) - 1
    parsed = textui.parse(rows[0][0][1])
    assert parsed.kind == "buy" and parsed.owner == 7 and parsed.item in salon.ITEMS


# --- касса ------------------------------------------------------------------

def test_wallet_buy_is_atomic_and_is_not_a_loss():
    book = WalletBook(MemoryRepository())
    book.load()
    book.apply(1, 2, "Зоя", 5000)
    assert book.buy(1, 2, "Зоя", "tea", 3000)
    wallet = book.get(1, 2)
    assert (wallet.balance, wallet.spent, wallet.lost, wallet.items) == (2000, 3000, 0, ["tea"])
    assert not book.buy(1, 2, "Зоя", "tea", 3000), "второй раз не списываем"
    assert not book.buy(1, 2, "Зоя", "neon", 8000), "в минус купить нельзя"
    assert book.get(1, 2).balance == 2000


def test_old_wallet_file_loads_without_purchases():
    book = WalletBook(MemoryRepository({"1": {"2": {"name": "Зоя", "balance": 700, "earned": 700, "lost": 0}}}))
    book.load()
    assert book.get(1, 2).items == [] and book.get(1, 2).spent == 0 and book.balance(1, 2) == 700


def test_purchases_survive_a_restart():
    repository = MemoryRepository()
    book = WalletBook(repository)
    book.load()
    book.apply(1, 2, "Зоя", 5000)
    book.buy(1, 2, "Зоя", "tea", 3000)
    repository.data = book.snapshot()
    again = WalletBook(repository)
    again.load()
    assert again.get(1, 2).items == ["tea"] and again.get(1, 2).spent == 3000


# --- эффекты покупок --------------------------------------------------------

def test_comfort_changes_mood_at_the_door():
    base = client("jealous.lena")
    assert client("jealous.lena", ("candles",)).trust == base.trust + 15
    assert client("jealous.lena", ("tea",)).satisfaction == base.satisfaction + 18


def test_owned_comfort_is_shown_when_the_client_arrives():
    session = shift(("tea",))
    assert any("чай с чабрецом" in event.text for event in rules.arrival_events(session))
    assert not any("чабрецом" in event.text for event in rules.arrival_events(shift()))


def test_ledger_and_ball_raise_limits():
    plain, full = shift(), shift(("ledger", "ball"))
    assert full.client.questions_left == plain.client.questions_left + 1
    assert full.client.readings_left == plain.client.readings_left + 1


def test_reviews_bring_a_fourth_client_and_zina_keeps_skeptics_out():
    for seed in range(30):
        session = rules.new_session(session_id=SID, chat_id=1, player_id=1, player_name="Зоя", rng=random.Random(seed), now=0, items=("reviews",))
        assert session.clients_total == rules.CLIENTS_PER_SESSION + 1
    with_zina = sum("skeptic" in rules.shift_archetypes(random.Random(seed), skeptic_factor=0.5) for seed in range(4000))
    assert with_zina / 4000 == pytest.approx(rules.SKEPTIC_CHANCE / 2, abs=0.03)


def test_advertising_raises_earnings_but_not_refunds():
    assert rules.flat_fee(1000, "hit", 1, 0.25) == 1250
    assert rules.flat_fee(1000, "exposure", 1, 0.25) == rules.flat_fee(1000, "exposure", 1)


@pytest.mark.parametrize("item,method", [("deck", "tarot"), ("loupe", "palm"), ("almanac", "horoscope"), ("bowl", "energy")])
def test_a_good_tool_almost_never_misleads(item, method):
    plain = [rules._deal(client("jealous.lena", seed=seed), C.METHODS[method], random.Random(seed)).quality for seed in range(600)]
    tool = [rules._deal(client("jealous.lena", (item,), seed=seed), C.METHODS[method], random.Random(seed)).quality for seed in range(600)]
    assert "off" in plain and "off" not in tool
    assert tool.count("sharp") > plain.count("sharp") * 2


def test_almanac_names_a_deadline():
    session = shift(("almanac",))
    session.client.phase = "divine"
    text = rules.act(session, 1, "m:horoscope", random.Random(3), now=1.0).events[-1].text
    assert "Альманах подсказывает срок" in text
    assert any(k.slot == "when" and k.source == "horoscope" for k in session.client.knowledge)


def test_mirror_makes_reactions_more_honest():
    cases = [client("businessman.partner", seed=seed) for seed in range(500)]
    with_mirror = [client("businessman.partner", ("mirror",), seed=seed) for seed in range(500)]
    plain = sum(rules._reaction(case, True, random.Random(index)) == "warm" for index, case in enumerate(cases))
    better = sum(rules._reaction(case, True, random.Random(index)) == "warm" for index, case in enumerate(with_mirror))
    assert better > plain


def test_armchair_keeps_questions_from_hurting():
    def after_question(items):
        session = shift(items)
        trust = session.client.trust
        question = session.client.story.questions[0].id
        rules.act(session, 1, f"c:{question}", random.Random(1), now=1.0)
        return session.client.trust - trust

    assert after_question(()) < 0 and after_question(("armchair",)) == 0


def _expose(session):
    """Довести текущего клиента до разоблачения: клятва мимо всего."""
    rng = random.Random(1)
    rules.act(session, 1, "c:stop", rng, now=1.0)
    rules.act(session, 1, "go", rng, now=2.0)
    client_ = session.client
    for slot in C.SLOTS:
        wrong = next(tag for tag in client_.options[slot] if tag != client_.truth[slot])
        rules.act(session, 1, f"s:{slot}:{wrong}", rng, now=3.0)
    return rules.act(session, 1, "a:swear", rng, now=4.0).finished_client.report


NO_JACKPOT = ("student.kebab", "student.crypto", "granny.petrovich")


def test_cat_covers_the_first_exposure_only():
    session = shift(("cat",), plan=NO_JACKPOT)
    first = _expose(session)
    assert first.outcome == "exposure" and first.fee >= 0 and first.legend_after == rules.LEGEND_START
    rules.act(session, 1, "next", random.Random(1), now=5.0)
    second = _expose(session)
    assert second.outcome == "exposure" and second.fee < 0 and second.legend_after == rules.LEGEND_START - 1


def test_without_cat_exposure_costs_money_and_legend():
    report = _expose(shift(plan=NO_JACKPOT))
    assert report.fee < 0 and report.legend_after == rules.LEGEND_START - 1


def test_live_reception_uses_earnings_readings_and_tools():
    session = live.new_session(session_id=SID, chat_id=-1, host_id=1, host_name="Зоя", now=0.0, items=("flyers", "ball", "deck"))
    assert session.readings_left == live.LIVE_READINGS + 1
    live.act(session, 2, "Гоша", "join", random.Random(1), now=1.0)
    for action in ("q:love", "q:tax", "q:hope"):
        live.act(session, 2, "Гоша", action, random.Random(1), now=2.0)
    live.act(session, 1, "Зоя", "go", random.Random(1), now=3.0)
    for action in ("s:who:tax", "s:what:lose", "s:when:week"):
        live.act(session, 1, "Зоя", action, random.Random(1), now=4.0)
    live.act(session, 1, "Зоя", "a:see", random.Random(1), now=5.0)
    live.act(session, 2, "Гоша", "v:hit", random.Random(1), now=6.0)
    assert session.fee == 1650


def test_live_tool_shows_the_truth_more_often_but_never_always():
    runs = 600
    found = 0
    for seed in range(runs):
        session = live.new_session(session_id=SID, chat_id=-1, host_id=1, host_name="Зоя", now=0.0, items=("deck",))
        session.client, session.intake, session.phase = rules.Participant(2, "Гоша"), {"sphere": "love", "who": "tax", "emotion": "hope"}, "divine"
        live.act(session, 1, "Зоя", "m:tarot", random.Random(seed), now=1.0)
        found += "tax" in session.pending.offered
    assert live.LIVE_TRUTH_CHANCE < found / runs <= live.LIVE_TRUTH_MAX + 0.03


# --- случайные события ------------------------------------------------------

def test_every_event_is_well_formed():
    assert len({event.key for event in C.CLIENT_EVENTS}) == len(C.CLIENT_EVENTS) >= 12
    for event in C.CLIENT_EVENTS:
        assert event.line and event.fee > 0
        event.line.format(name="Зоя")


@pytest.mark.parametrize("event", C.CLIENT_EVENTS, ids=lambda event: event.key)
def test_event_shifts_the_client(monkeypatch, event):
    monkeypatch.setattr(C, "CLIENT_EVENTS", (event,))
    base = client("jealous.lena")
    hit = client("jealous.lena", event_chance=1.0)
    assert hit.event is event
    assert hit.trust == base.trust + event.trust and hit.satisfaction == base.satisfaction + event.satisfaction
    assert hit.questions_left == base.questions_left + event.questions and hit.readings_left == base.readings_left + event.readings
    assert hit.questions_left >= 1 and hit.readings_left >= 1, "приём не должен остаться без вопросов и гаданий"


def test_given_plan_has_no_random_events():
    for seed in range(40):
        session = shift(seed=seed)
        assert session.event_chance == 0.0 and session.client.event is None


def test_random_shifts_do_get_events():
    events = 0
    for seed in range(300):
        session = rules.new_session(session_id=SID, chat_id=1, player_id=1, player_name="Зоя", rng=random.Random(seed), now=0)
        events += session.client.event is not None
    assert events / 300 == pytest.approx(rules.EVENT_CHANCE, abs=0.08)


def test_event_is_visible_and_logged(monkeypatch):
    event = next(item for item in C.CLIENT_EVENTS if item.key == "debt")
    monkeypatch.setattr(C, "CLIENT_EVENTS", (event,))
    monkeypatch.setattr(rules, "EVENT_CHANCE", 1.0)
    session = rules.new_session(session_id=SID, chat_id=1, player_id=1, player_name="Зоя", rng=random.Random(2), now=0)
    assert any(event.line in item.text for item in rules.arrival_events(session))
    report = _expose_or_finish(session)
    assert report.event == "debt" and report.log_data()["incident"] == "debt"


def _expose_or_finish(session):
    rng = random.Random(1)
    rules.act(session, 1, "c:stop", rng, now=1.0)
    rules.act(session, 1, "go", rng, now=2.0)
    for slot in C.SLOTS:
        rules.act(session, 1, f"s:{slot}:{session.client.truth[slot]}", rng, now=3.0)
    return rules.act(session, 1, "a:maybe", rng, now=4.0).finished_client.report


def test_every_archetype_has_several_stories():
    counts = {archetype: sum(story.archetype == archetype for story in STORIES) for archetype in rules.ARCHETYPE_ORDER}
    assert min(counts.values()) >= 3 and len(STORIES) >= 37
