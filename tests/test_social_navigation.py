from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta, timezone
from io import BytesIO
import re
from types import SimpleNamespace

from PIL import Image

from tests import test_smoke_imports  # noqa: F401

from features.social_graph import dossier_facts, overview
from features.social_graph.ai import name_factions
from features.social_graph.analysis import GraphEdge, aggregate_edges, select_render_graph
from features.social_graph.rendering import CANVAS_HEIGHT, render_graph_png
from features.social_graph.structure import assign_roles, detect_factions
from tests.test_chronicle import build_stores


def _edge(a, b, weight):
    return GraphEdge(a, b, weight / 2, weight / 2)


TWO_CAMPS = [
    _edge(1, 2, 10), _edge(1, 3, 9), _edge(2, 3, 8),
    _edge(4, 5, 10), _edge(4, 6, 9), _edge(5, 6, 7),
    _edge(3, 4, 1),
]


# --- structure ----------------------------------------------------------------

def test_factions_split_two_camps_and_find_liaison():
    factions = detect_factions(TWO_CAMPS)

    assert sorted(sorted(f.members) for f in factions.factions) == [[1, 2, 3], [4, 5, 6]]
    assert factions.modularity > 0.3
    assert factions.liaison_id in {3, 4}
    assert all(0.8 < f.cohesion <= 1.0 for f in factions.factions)


def test_tight_chat_stays_one_faction():
    factions = detect_factions([_edge(1, 2, 5), _edge(2, 3, 5), _edge(1, 3, 5)])
    assert [sorted(f.members) for f in factions.factions] == [[1, 2, 3]]
    assert factions.liaison_id is None


def test_roles_are_assigned_from_interaction_mix():
    interactions = (
        [(1, 2, "reply", 3.0)] * 5
        + [(2, 1, "mention", 2.0)]
        + [(3, 1, "reaction", 1.0)] * 6
        + [(3, 2, "reaction", 1.0)] * 2
        + [(4, 1, "mention", 2.0)] * 4
    )
    roles = assign_roles(interactions, aggregate_edges(interactions))

    assert roles[1].role.key == "hub"
    assert roles[2].role.key == "magnet"
    assert roles[3].role.key == "reactor"
    assert roles[4].role.key == "pinger"
    assert "связ" in roles[1].reason


def test_faction_render_adds_legend_band():
    graph = select_render_graph(TWO_CAMPS, {i: f"User {i}" for i in range(1, 7)})
    plain = Image.open(BytesIO(render_graph_png(graph)))
    colored = Image.open(BytesIO(render_graph_png(
        graph,
        node_colors={1: (66, 133, 244)},
        legend=[((66, 133, 244), "Партия кондиционера"), ((234, 88, 82), "Секта реплаев")],
    )))
    assert plain.height == CANVAS_HEIGHT
    assert colored.height > CANVAS_HEIGHT


def test_faction_naming_parses_json_and_ignores_garbage():
    factions = detect_factions(TWO_CAMPS).factions
    prompts = []

    async def generator(prompt, _chat_id):
        prompts.append(prompt)
        return 'Вот: {"1": "«Клан ночного треда»", "2": "", "9": "лишнее"}'

    names = asyncio.run(name_factions(factions, {i: f"U{i}" for i in range(1, 7)}, {}, "-1", generator=generator))
    assert names == {1: "Клан ночного треда"}
    assert "не придумывай события" in prompts[0]


# --- overview -----------------------------------------------------------------

def _structure():
    names = {i: f"User {i}" for i in range(1, 7)}
    interactions = tuple((e.user_a, e.user_b, "reply", e.total_weight) for e in TWO_CAMPS)
    edges = aggregate_edges(interactions)
    return overview.ChatStructure(
        chat_id=-100,
        period_days=30,
        names=names,
        interactions=interactions,
        edges=edges,
        roles=assign_roles(interactions, edges),
        factions=detect_factions(edges),
    )


def test_faction_names_fall_back_and_cache(monkeypatch):
    overview._faction_name_cache.clear()
    structure = _structure()
    calls = []

    async def fake_names(factions, names, roles, chat_id):
        calls.append(chat_id)
        return {1: "Лига первых"}

    monkeypatch.setattr(overview, "name_factions", fake_names)
    names = asyncio.run(overview.faction_names(structure))
    assert names[1] == "Лига первых"
    assert names[2].startswith("Партия имени")

    asyncio.run(overview.faction_names(structure))
    assert len(calls) == 2  # faction 2 still has no AI name, so it is retried

    text = overview.format_factions_html(structure, names)
    assert "Лига первых" in text and "2 лагеря" in text and "связной" in text
    assert overview.format_roles_html(structure).startswith("🎭 <b>Роли в чате</b>")


def test_dossier_combines_role_faction_relationships_and_chronicle(monkeypatch):
    from tests.test_relationship_history import _view

    overview._faction_name_cache.clear()
    structure = _structure()

    async def fake_structure(_chat_id):
        return structure

    friendly = _view()
    hostile = _view().__class__(**{**{f: getattr(friendly, f) for f in friendly.__slots__}, "user_b_id": 3, "user_b_name": "User 3", "tension": 70})

    async def fake_relationships(_chat_id, *, user_id):
        return [friendly, hostile]

    async def fake_count(_chat_id, *, user_id):
        return 2

    async def fake_list(_chat_id, *, user_id, limit):
        return [SimpleNamespace(title="Война за кондиционер")]

    hours = [0] * 24
    hours[1], hours[2], hours[14] = 15, 10, 20
    facts = dossier_facts.PersonalFacts(
        activity=dossier_facts.ActivityFacts(
            total=1234, weekly=56, rank="хуятор 🔨",
            first_seen=datetime(2025, 3, 2), last_seen=datetime(2026, 10, 24),
        ),
        rhythm=dossier_facts.build_rhythm(hours, [1, 1, 1, 1, 1, 40, 1]),
        reactions=dossier_facts.ReactionFacts(
            given=(("🔥", 12),), received=(("🤣", 30),),
            best_message_id=77, best_message_reactions=9, best_message_text="кондиционер — зло",
        ),
        words=(("кондей", 40),),
        upupa=dossier_facts.UpupaFacts(requests=12, favorites=(("нарисуй", 8), ("поболтать", 4))),
    )
    hints = []

    async def fake_facts(_chat_id, _user_id):
        return facts

    async def fake_nickname(_chat_id, _user_id, _name, lines):
        hints.extend(lines)
        return "Повелитель кондея"

    monkeypatch.setattr(overview, "load_structure", fake_structure)
    monkeypatch.setattr(overview, "get_relationships", fake_relationships)
    monkeypatch.setattr(overview, "count_events", fake_count)
    monkeypatch.setattr(overview, "list_events", fake_list)
    monkeypatch.setattr(overview, "load_personal_facts", fake_facts)
    monkeypatch.setattr(overview, "dossier_nickname", fake_nickname)

    dossier = asyncio.run(overview.build_dossier(-100, 1, "Fallback"))
    today = date(2026, 10, 25)
    html = overview.format_dossier_html(dossier, "https://t.me/c/1/2", "https://t.me/c/1/77", today=today)

    assert dossier.name == "User 1"
    assert dossier.antagonist is hostile
    assert dossier.nickname == "Повелитель кондея"
    assert "любимые слова: кондей" in hints
    assert "Партия имени" in html
    assert "Оппонент: <b>User 3</b>" in html
    assert "Bob" in html  # friendly pair is listed among main relationships
    assert 'Последнее в Летописи: <a href="https://t.me/c/1/2">' in html
    assert "«Повелитель кондея»" in html and "хуятор" in html and "1 234 сообщения" in html
    assert "сова" in html.lower() and "по пятницам" in html
    assert "Ставит 🔥 · получает 🤣" in html and 'href="https://t.me/c/1/77"' in html and "«кондей»" in html
    assert "чаще всего «нарисуй», «поболтать»" in html

    caption = overview.format_dossier_caption_html(dossier, "https://t.me/c/1/2", "https://t.me/c/1/77")
    assert "Хит" in caption and 'href="https://t.me/c/1/2"' in caption
    assert "🔥" not in caption and "нарисуй" not in caption  # card and «Подробнее» cover these

    card = overview.dossier_card_data(dossier, today=today)
    assert card.nickname == "Повелитель кондея" and card.closest.name == "Bob"
    assert [label for label, _value in card.rows] == ["Активность", "Реакции", "Словечки", "Упупа", "Летопись"]
    assert dict(card.rows)["Реакции"] == "ставит огонь · получает ржач"
    assert "ранг: хуятор 🔨" in card.side_lines and "в чате с марта 2025" in card.side_lines


def test_dossier_caption_is_a_few_short_lines():
    long_text = "очень " * 400
    dossier = overview.Dossier(
        user_id=1, name="User 1", period_days=30,
        role=SimpleNamespace(role=SimpleNamespace(emoji="🌉", title="Мост", blurb="связывает"), reason=long_text),
        faction_name=None, connections=0, relationships=(), antagonist=None, asymmetry=None,
        graph_names={}, chronicle_count=1, latest_event=SimpleNamespace(title=long_text), nickname="Кличка",
        facts=dossier_facts.PersonalFacts(
            reactions=dossier_facts.ReactionFacts(
                given=(("🔥", 3),), best_message_id=7, best_message_reactions=5, best_message_text=long_text,
            ),
            upupa=dossier_facts.UpupaFacts(requests=9, favorites=(("нарисуй", 5), ("поболтать", 4))),
        ),
    )
    caption = overview.format_dossier_caption_html(dossier, "https://t.me/c/1/2", "https://t.me/c/1/7")
    lines = caption.split("\n")

    assert lines[0] == "🗂 <b>Досье: User 1</b> — «Кличка»"
    assert len(lines) == 4
    assert all(len(re.sub(r"<[^>]+>", "", line)) <= 90 for line in lines)
    assert all("…" in line for line in lines[1:])


# --- handlers -----------------------------------------------------------------

def _message(text, *, reply_user=None):
    replied = SimpleNamespace(from_user=reply_user) if reply_user is not None else None
    return SimpleNamespace(
        text=text,
        chat=SimpleNamespace(id=-100, type="supergroup"),
        from_user=SimpleNamespace(id=1, is_bot=False, full_name="Alice", first_name="Alice"),
        reply_to_message=replied,
    )


def test_dossier_command_and_target_resolution(monkeypatch):
    import handlers.social_navigation as nav

    assert nav._is_dossier_command(_message("досье"))
    assert nav._is_dossier_command(_message("Досье @bob"))
    assert not nav._is_dossier_command(_message("досье на всех"))
    assert nav._is_exact(_message("Фракции"), "фракции")

    async def resolve(_chat_id, usernames):
        return {"bob": (2, "Bob", "bob")} if "bob" in usernames else {}

    monkeypatch.setattr(nav, "resolve_relationship_usernames", resolve)
    assert asyncio.run(nav._resolve_dossier_target(_message("досье @bob"))) == (2, "Bob")
    assert asyncio.run(nav._resolve_dossier_target(_message("досье @ghost"))) is None
    carol = SimpleNamespace(id=3, is_bot=False, full_name="Carol", first_name="Carol")
    assert asyncio.run(nav._resolve_dossier_target(_message("досье", reply_user=carol))) == (3, "Carol")
    assert asyncio.run(nav._resolve_dossier_target(_message("досье"))) == (1, "Alice")


def test_social_buttons_dispatch_and_cringe_cooldown(monkeypatch):
    import handlers.social_navigation as nav

    calls = []

    def recorder(name):
        async def record(_message, chat_id, *args, **kwargs):
            calls.append((name, chat_id, args))
            return True
        return record

    monkeypatch.setattr(nav, "is_social_graph_enabled", lambda _chat_id: True)
    monkeypatch.setattr(nav, "send_factions", recorder("factions"))
    monkeypatch.setattr(nav, "send_dossier", recorder("dossier"))
    monkeypatch.setattr(nav, "send_dossier_details", recorder("details"))
    monkeypatch.setattr(nav.graph_handlers, "send_relationship_history", recorder("history"))
    monkeypatch.setattr(nav.graph_handlers, "send_cringe_graph", recorder("cringe"))
    nav._cringe_button_used_at.clear()

    answers = []

    def query(data):
        async def answer(text=None, **kwargs):
            answers.append(text)
        return SimpleNamespace(
            data=data,
            from_user=SimpleNamespace(id=1, is_bot=False),
            message=SimpleNamespace(chat=SimpleNamespace(id=-100, type="supergroup")),
            answer=answer,
        )

    async def known_name(_chat_id, user_id):
        return f"User {user_id}"

    monkeypatch.setattr(nav, "_known_name", known_name)

    for data in ("sg:fac", "sg:hist:1:2", "sg:dos:5", "sg:dosx:5", "sg:cringe", "sg:cringe", "sg:hist:x:y"):
        asyncio.run(nav.handle_social_button(query(data)))

    assert [call[0] for call in calls] == ["factions", "history", "dossier", "details", "cringe"]
    assert calls[1][2] == (1, 2)
    assert calls[3][2] == (5, "User 5")
    assert any(text and "Paint ещё не остыл" in text for text in answers)


# --- Chronicle pagination -------------------------------------------------------

def _save_events(events, count):
    now = datetime.now(timezone.utc)
    for index in range(count):
        events.save(
            chat_id=-1009, started_at=now - timedelta(days=index), ended_at=now - timedelta(days=index),
            title=f"Event {index}", summary="s", category="other", importance_score=float(index % 4),
            anchor_message_id=index + 1, reaction_count=0, unique_reactors=0, reply_count=0,
            keywords=[], entities=[], related_event_ids=[], ai_metadata={}, source="live",
            participants=[{"id": 1, "name": "P", "username": "p"}], source_message_ids=[index + 1],
        )


def test_event_store_pages_and_ranks_by_importance(tmp_path):
    _candidates, events, _backfill = build_stores(tmp_path)
    _save_events(events, 11)

    assert events.count(-1009) == 11
    assert events.count(-1009, username="p") == 11
    first = events.list(-1009, limit=8)
    second = events.list(-1009, limit=8, offset=8)
    assert [e.title for e in first][:2] == ["Event 0", "Event 1"]
    assert len(second) == 3
    best = events.list(-1009, limit=3, order="importance")
    assert [e.importance_score for e in best] == [3.0, 3.0, 2.0]
    assert [e.title for e in best[:2]] == ["Event 3", "Event 7"]  # ties: newer first


def test_chronicle_keyboard_and_hall_of_fame_command():
    import handlers.chronicle as handler

    assert handler._command_kind(SimpleNamespace(text="Зал славы")) == ("hall", None)
    markup = handler.chronicle_keyboard(handler.MODE_RECENT, 1, 3, handler._encode_filter(None, "bob"))
    data = [button.callback_data for row in markup.inline_keyboard for button in row]
    assert data == ["chr:r:0:ubob", "chr:r:2:ubob", "chr:h:0:ubob"]
    assert handler._decode_filter("ubob") == (None, "bob")
    assert handler._decode_filter("i42") == (42, None)
    assert handler._decode_filter("a") == (None, None)


def test_chronicle_page_callback_edits_message(monkeypatch):
    import handlers.chronicle as handler

    seen = {}

    async def fake_count(chat_id, **kwargs):
        return 20

    async def fake_list(chat_id, **kwargs):
        seen.update(kwargs)
        now = datetime.now(timezone.utc)
        return [SimpleNamespace(event_started_at=now, title="T", summary="S", anchor_message_id=None)]

    edits = []

    async def edit_text(text, **kwargs):
        edits.append((text, kwargs))

    answers = []

    async def answer(text=None, **_kwargs):
        answers.append(text)

    monkeypatch.setattr(handler, "count_events", fake_count)
    monkeypatch.setattr(handler, "list_events", fake_list)
    message = SimpleNamespace(chat=SimpleNamespace(id=-1001, type="supergroup", username=None), edit_text=edit_text)
    asyncio.run(handler.handle_chronicle_page(SimpleNamespace(data="chr:h:1:a", message=message, answer=answer)))

    assert seen["order"] == "importance" and seen["offset"] == handler.config.DISPLAY_EVENT_LIMIT
    assert "Зал славы" in edits[0][0] and "стр. 2/3" in edits[0][0]
    assert edits[0][1]["reply_markup"] is not None
