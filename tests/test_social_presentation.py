from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta, timezone
from io import BytesIO
from types import SimpleNamespace

from PIL import Image

from tests import test_smoke_imports  # noqa: F401

from features.social_graph import cards, digest
from features.social_graph.cards import (
    CARD_HEIGHT,
    CARD_WIDTH,
    DossierCardData,
    DossierRelation,
    PairCardData,
    render_dossier_card,
    render_pair_card,
)
from infrastructure.persistence.sqlite_social_graph import SQLiteSocialGraphRepository
from tests.test_relationship_history import _event


def _png_size(data: bytes) -> tuple[int, int]:
    return Image.open(BytesIO(data)).size


def _avatar_bytes() -> bytes:
    out = BytesIO()
    Image.new("RGB", (400, 300), (10, 200, 30)).save(out, format="JPEG")
    return out.getvalue()


# --- cards --------------------------------------------------------------------

def test_pair_card_renders_with_and_without_avatars():
    base = dict(
        user_a_id=1, user_b_id=2, user_a_name="Евгений", user_b_name="Очень Длинное Имя Участника Чата",
        level=9, archetype="заклятые друзья", affinity=68, tension=54, reciprocity=0.82,
        trend="конфликт набирает обороты", weekly=(0, 2, 5, 3, 8, 12), phase="на подъёме",
        main_event="Великая война за кондиционер",
    )
    assert _png_size(render_pair_card(PairCardData(**base))) == (CARD_WIDTH, CARD_HEIGHT)
    with_avatar = render_pair_card(PairCardData(**base, avatar_a=_avatar_bytes(), avatar_b=b"broken"))
    assert _png_size(with_avatar) == (CARD_WIDTH, CARD_HEIGHT)


def test_dossier_card_handles_empty_and_full_data():
    empty = DossierCardData(
        user_id=5, name="Кто-то", role_title=None, role_blurb=None, faction_name=None,
        faction_color=None, connections=0, period_days=30,
    )
    full = DossierCardData(
        user_id=5, name="Евгений", role_title="Мост",
        role_blurb="связывает компании, которые без него почти не пересекаются",
        faction_name="Клан ночного треда", faction_color=(66, 133, 244), connections=7, period_days=30,
        relations=(DossierRelation("Саня", 9, "заклятые друзья"), DossierRelation("Маша", 6, "мемный дуэт")),
        antagonist=DossierRelation("Олег", 3, "спорщики"), antagonist_tension=48,
        chronicle_count=5, latest_event="Великая война за кондиционер", avatar=_avatar_bytes(),
    )
    assert _png_size(render_dossier_card(empty)) == (CARD_WIDTH, CARD_HEIGHT)
    assert _png_size(render_dossier_card(full)) == (CARD_WIDTH, CARD_HEIGHT)


def test_avatar_fetch_is_cached_and_tolerates_hidden_photos():
    cards._avatar_cache.clear()
    calls = []

    class Bot:
        async def get_user_profile_photos(self, user_id, limit):
            calls.append(user_id)
            if user_id == 2:
                return SimpleNamespace(photos=[])
            return SimpleNamespace(photos=[[
                SimpleNamespace(file_id="small", width=160, height=160),
                SimpleNamespace(file_id="mid", width=320, height=320),
                SimpleNamespace(file_id="big", width=640, height=640),
            ]])

        async def download(self, file_id):
            assert file_id == "mid"
            return BytesIO(b"avatar")

    bot = Bot()
    assert asyncio.run(cards.fetch_avatar(bot, 1)) == b"avatar"
    assert asyncio.run(cards.fetch_avatar(bot, 1)) == b"avatar"
    assert asyncio.run(cards.fetch_avatar(bot, 2)) is None
    assert asyncio.run(cards.fetch_avatar(None, 3)) is None
    assert calls == [1, 2]


def test_relationship_card_is_sent_as_photo_with_caption(monkeypatch):
    import handlers.social_graph as handler
    from tests.test_relationship_history import _view

    view = _view((_event(when=datetime(2026, 5, 1, tzinfo=timezone.utc)),))

    async def fake_relationship(*_args):
        return view

    async def no_ai(*_args, **_kwargs):
        return "Саркастичный комментарий."

    async def no_dynamics(*_args):
        return None

    async def no_avatar(_bot, _user_id):
        return None

    sent = []

    async def reply_photo(photo, **kwargs):
        sent.append(("photo", kwargs))

    async def reply(text, **kwargs):
        sent.append(("text", text))

    monkeypatch.setattr(handler, "get_relationship", fake_relationship)
    monkeypatch.setattr(handler, "interpret_relationship", no_ai)
    monkeypatch.setattr(handler, "load_relationship_dynamics", no_dynamics)
    monkeypatch.setattr(handler, "fetch_avatar", no_avatar)
    message = SimpleNamespace(bot=object(), reply_photo=reply_photo, reply=reply)

    asyncio.run(handler.send_relationship_card(message, -1001707530786, 1, 2))

    kind, kwargs = sent[0]
    assert kind == "photo"
    assert "Саркастичный комментарий." in kwargs["caption"]
    assert "https://t.me/c/1707530786/123" in kwargs["caption"]
    assert len(kwargs["caption"]) <= handler.CAPTION_LIMIT
    assert kwargs["reply_markup"] is not None

    # Without a bot (e.g. tests, unusual contexts) the old text card is used.
    sent.clear()
    asyncio.run(handler.send_relationship_card(SimpleNamespace(reply=reply), -1001707530786, 1, 2))
    assert sent[0][0] == "text" and "Уровень" in sent[0][1]


# --- digest -------------------------------------------------------------------

TODAY = date(2026, 10, 11)


def _row(a, b, day_offset, count, first=None, names=("A", "B")):
    return {
        "user_a_id": a, "user_b_id": b,
        "activity_date": (TODAY - timedelta(days=day_offset)).isoformat(),
        "event_count": count, "user_a_name": f"{names[0]} (@{names[0].lower()})", "user_b_name": names[1],
        "first_interaction_at": first,
    }


def test_digest_facts_pick_pair_rising_cooling_new_and_person():
    old = (TODAY - timedelta(days=90)).isoformat()
    new = (TODAY - timedelta(days=2)).isoformat()
    rows = [
        # pair of the week: busy now and before
        *[_row(1, 2, d, 3, old, ("Alice", "Bob")) for d in range(0, 6)],
        *[_row(1, 2, d, 2, old, ("Alice", "Bob")) for d in range(8, 20, 2)],
        # rising: quiet before, active now
        _row(1, 3, 10, 1, old, ("Alice", "Carol")),
        *[_row(1, 3, d, 2, old, ("Alice", "Carol")) for d in range(0, 4)],
        # cooling: active before, silent now
        *[_row(2, 4, d, 2, old, ("Bob", "Dan")) for d in range(8, 26)],
        # new pair this week
        *[_row(1, 5, d, 2, new, ("Alice", "Eve")) for d in range(0, 2)],
    ]
    events = [_event(when=datetime(2026, 10, 9, tzinfo=timezone.utc)), _event(when=datetime(2026, 9, 1, tzinfo=timezone.utc))]

    facts = digest.compute_digest_facts(-100, rows, events, today=TODAY)

    assert (facts.pair_of_week.user_a_id, facts.pair_of_week.user_b_id) == (1, 2)
    assert facts.pair_of_week.user_a_name == "Alice"  # username tag stripped
    assert (facts.rising.user_a_id, facts.rising.user_b_id) == (1, 3)
    assert (facts.cooling.user_a_id, facts.cooling.user_b_id) == (2, 4)
    assert [(p.user_a_id, p.user_b_id) for p in facts.new_pairs] == [(1, 5)]
    assert facts.person_of_week[0] == 1 and facts.person_of_week[2] == 3
    assert len(facts.events) == 1
    assert facts.is_meaningful
    assert facts.week_label == "5–11 октября"

    lines = digest.fact_lines(facts)
    assert any(line.startswith("Пара недели: Alice и Bob — 18 взаимодействий") for line in lines)
    assert any("3 собеседника" in line for line in lines)


def test_quiet_week_is_not_meaningful():
    facts = digest.compute_digest_facts(-100, [_row(1, 2, 1, 2)], [], today=TODAY)
    assert not facts.is_meaningful


def test_digest_html_uses_ai_and_falls_back_to_facts():
    rows = [_row(1, 2, d, 3, None, ("Alice", "Bob")) for d in range(0, 5)]
    event = _event(when=datetime(2026, 10, 10, tzinfo=timezone.utc))
    facts = digest.compute_digest_facts(-1001707530786, rows, [event], today=TODAY)
    prompts = []

    async def ai(prompt, _chat_id):
        prompts.append(prompt)
        return "Alice и Bob снова не расставались <ни на день>."

    html = asyncio.run(digest.write_digest_html(facts, generator=ai))
    assert html.startswith("🥂 <b>Светская хроника</b>")
    assert "&lt;ни на день&gt;" in html
    assert 'href="https://t.me/c/1707530786/123"' in html
    assert "Используй ТОЛЬКО факты" in prompts[0]

    async def broken(_prompt, _chat_id):
        raise RuntimeError("down")

    fallback = asyncio.run(digest.write_digest_html(facts, generator=broken))
    assert "• Пара недели: Alice и Bob" in fallback


def test_digest_is_posted_once_per_week_on_sunday_evening(monkeypatch):
    from core import state
    import features.chat_settings as chat_settings_module

    monkeypatch.setattr(state, "chat_settings", {"-1": {}, "-2": {"social_digest_enabled": False}})
    monkeypatch.setattr(digest, "chat_settings", state.chat_settings)
    monkeypatch.setattr(chat_settings_module, "save_chat_settings", lambda *a, **k: None)

    async def active(*, days):
        return [-1, -2]

    async def facts(chat_id, *, today):
        rows = [_row(1, 2, d, 3) for d in range(0, 5)]
        return digest.compute_digest_facts(chat_id, rows, [], today=today)

    async def no_ai(facts):
        return "text"

    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr(digest, "list_active_chats", active)
    monkeypatch.setattr(digest, "is_social_graph_enabled", lambda _chat_id: True)
    monkeypatch.setattr(digest, "build_digest_facts", facts)
    monkeypatch.setattr(digest, "write_digest_html", no_ai)
    monkeypatch.setattr(digest.asyncio, "sleep", no_sleep)

    sent = []

    class Bot:
        async def send_message(self, chat_id, text, **kwargs):
            sent.append(chat_id)

    sunday_evening = datetime(2026, 10, 11, 19, 30)
    saturday = datetime(2026, 10, 10, 20, 0)
    assert asyncio.run(digest.run_due_digests(Bot(), now=saturday)) == 0
    assert asyncio.run(digest.run_due_digests(Bot(), now=sunday_evening)) == 1
    assert asyncio.run(digest.run_due_digests(Bot(), now=sunday_evening + timedelta(hours=1))) == 0
    assert sent == [-1]
    assert state.chat_settings["-1"]["social_digest_week"] == "2026-W41"


def test_repository_loads_chat_daily_with_names(tmp_path):
    repo = SQLiteSocialGraphRepository(tmp_path / "stats.db")
    repo.init_schema()
    now = datetime.now(timezone.utc)
    repo.record_message_bundle(-100, 1, now, (1, "Alice", "alice"), ((2, "Bob", None),), ((2, "reply", 3.0),))

    rows = repo.load_chat_daily(-100, (now - timedelta(days=1)).date().isoformat())
    assert len(rows) == 1
    assert rows[0]["user_a_name"].startswith("Alice") and rows[0]["user_b_name"] == "Bob"
    assert rows[0]["first_interaction_at"] is not None
    assert repo.load_chat_daily(-100, (now + timedelta(days=2)).date().isoformat()) == []
