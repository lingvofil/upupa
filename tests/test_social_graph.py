import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from tests import test_smoke_imports  # noqa: F401


def user(user_id, name, username=None, *, is_bot=False):
    return SimpleNamespace(
        id=user_id,
        full_name=name,
        first_name=name,
        username=username,
        is_bot=is_bot,
    )


class FakeRepository:
    def __init__(self):
        self.bundles = []
        self.username_map = {}
        self.message_authors = {}
        self.interactions = []

    def init_schema(self):
        return None

    def resolve_usernames(self, chat_id, usernames):
        return {name: self.username_map[name] for name in usernames if name in self.username_map}

    def record_message_bundle(self, chat_id, message_id, timestamp, actor, participants, interactions):
        self.bundles.append((chat_id, message_id, actor, tuple(participants), tuple(interactions)))
        self.message_authors[(chat_id, message_id)] = actor[0]
        return len(interactions)

    def resolve_message_author(self, chat_id, message_id):
        return self.message_authors.get((chat_id, message_id))

    def record_interaction(self, chat_id, actor, target_user_id, interaction_type, message_id, timestamp, weight):
        item = (chat_id, actor[0], target_user_id, interaction_type, message_id, weight)
        if item in self.interactions:
            return False
        self.interactions.append(item)
        return True

    def load_graph(self, chat_id, since):
        return [], {}


def message(*, actor, message_id=10, text="hello", reply_user=None, entities=None):
    replied = None
    if reply_user is not None:
        replied = SimpleNamespace(from_user=reply_user)
    return SimpleNamespace(
        chat=SimpleNamespace(id=-100, type="supergroup"),
        from_user=actor,
        message_id=message_id,
        text=text,
        caption=None,
        entities=entities,
        caption_entities=None,
        reply_to_message=replied,
        date=datetime.now(timezone.utc),
    )


def test_interaction_weights_are_explicit():
    from features.social_graph.service import MENTION_WEIGHT, REACTION_WEIGHT, REPLY_WEIGHT

    assert REPLY_WEIGHT == 3.0
    assert MENTION_WEIGHT == 2.0
    assert REACTION_WEIGHT == 1.0
    assert REPLY_WEIGHT > MENTION_WEIGHT > REACTION_WEIGHT


def test_reply_interaction_is_recorded(monkeypatch):
    import features.social_graph.service as service

    repo = FakeRepository()
    monkeypatch.setattr(service, "_repository_instance", repo)
    actor = user(1, "Alice", "alice")
    target = user(2, "Bob", "bob")

    asyncio.run(service.capture_message(message(actor=actor, reply_user=target)))

    assert repo.bundles
    interactions = repo.bundles[0][4]
    assert (2, "reply", service.REPLY_WEIGHT) in interactions


def test_username_mention_interaction_is_recorded(monkeypatch):
    import features.social_graph.service as service

    repo = FakeRepository()
    repo.username_map["bob"] = (2, "Bob (@bob)", "bob")
    monkeypatch.setattr(service, "_repository_instance", repo)
    entity = SimpleNamespace(type="mention", user=None, offset=3, length=4)

    asyncio.run(service.capture_message(message(actor=user(1, "Alice"), text="hi @bob", entities=[entity])))

    interactions = repo.bundles[0][4]
    assert (2, "mention", service.MENTION_WEIGHT) in interactions


def test_text_mention_and_reply_never_create_self_edge(monkeypatch):
    import features.social_graph.service as service

    repo = FakeRepository()
    monkeypatch.setattr(service, "_repository_instance", repo)
    actor = user(1, "Alice", "alice")
    entity = SimpleNamespace(type="text_mention", user=actor, offset=0, length=5)

    asyncio.run(service.capture_message(message(actor=actor, text="Alice", reply_user=actor, entities=[entity])))

    assert repo.bundles[0][4] == ()


def test_disabled_social_graph_does_not_collect(monkeypatch):
    import features.social_graph.service as service
    from core.state import chat_settings

    repo = FakeRepository()
    monkeypatch.setattr(service, "_repository_instance", repo)
    monkeypatch.setitem(chat_settings, "-100", {"social_graph_enabled": False})

    result = asyncio.run(service.capture_message(message(actor=user(1, "Alice"), reply_user=user(2, "Bob"))))

    assert result == 0
    assert repo.bundles == []


def test_capture_uses_to_thread_for_persistence(monkeypatch):
    import features.social_graph.service as service

    repo = FakeRepository()
    monkeypatch.setattr(service, "_repository_instance", repo)
    calls = []

    async def fake_to_thread(func, *args, **kwargs):
        calls.append(func.__name__)
        return func(*args, **kwargs)

    monkeypatch.setattr(service.asyncio, "to_thread", fake_to_thread)
    asyncio.run(service.capture_message(message(actor=user(1, "Alice"), reply_user=user(2, "Bob"))))

    assert "record_message_bundle" in calls


def test_reaction_is_recorded_only_for_first_reaction(monkeypatch):
    import features.social_graph.service as service

    repo = FakeRepository()
    repo.message_authors[(-100, 77)] = 2
    monkeypatch.setattr(service, "_repository_instance", repo)
    update = SimpleNamespace(
        chat=SimpleNamespace(id=-100, type="supergroup"),
        user=user(1, "Alice", "alice"),
        message_id=77,
        old_reaction=[],
        new_reaction=[SimpleNamespace(type="emoji")],
        date=datetime.now(timezone.utc),
    )

    assert asyncio.run(service.capture_reaction(update)) is True
    assert repo.interactions[0][-1] == service.REACTION_WEIGHT

    changed = SimpleNamespace(**{**update.__dict__, "old_reaction": [SimpleNamespace(type="emoji")]})
    assert asyncio.run(service.capture_reaction(changed)) is False


def test_personal_summary_keeps_direction_and_mutuality():
    from features.social_graph.analysis import aggregate_edges, build_personal_summary

    edges = aggregate_edges([
        (1, 2, "reply", 3.0),
        (1, 2, "mention", 2.0),
        (2, 1, "reply", 3.0),
        (3, 1, "reply", 3.0),
    ])
    summary = build_personal_summary(1, edges)

    assert summary.total_outgoing == 5.0
    assert summary.total_incoming == 6.0
    assert summary.strongest_mutual[0].user_id == 2
    assert summary.strongest_mutual[0].outgoing == 5.0
    assert summary.strongest_mutual[0].incoming == 3.0


def test_centrality_is_graph_based_not_message_count():
    from features.social_graph.analysis import aggregate_edges, rank_central_participants

    interactions = []
    for target in range(2, 8):
        interactions.append((1, target, "reply", 3.0))
        interactions.append((target, 1, "reply", 3.0))
    interactions.extend([(8, 9, "reply", 30.0), (9, 8, "reply", 30.0)])
    ranking = rank_central_participants(aggregate_edges(interactions))

    assert ranking[0].user_id == 1
    assert ranking[0].unique_neighbors == 6


def test_large_graph_is_capped_for_rendering():
    from features.social_graph.analysis import aggregate_edges, select_render_graph

    interactions = [(1, target, "reply", float(40 - target)) for target in range(2, 35)]
    edges = aggregate_edges(interactions)
    names = {user_id: f"User {user_id}" for user_id in range(1, 35)}
    view = select_render_graph(edges, names)

    assert len(view.nodes) <= 18
    assert len(view.edges) <= 32
    assert view.total_node_count == 34


def test_sqlite_persistence_deduplicates_and_isolates_chats(tmp_path):
    from infrastructure.persistence.sqlite_social_graph import SQLiteSocialGraphRepository

    repo = SQLiteSocialGraphRepository(tmp_path / "statistics.db")
    repo.init_schema()
    now = datetime.now(timezone.utc)
    actor = (1, "Alice (@alice)", "alice")
    target = (2, "Bob (@bob)", "bob")

    assert repo.record_message_bundle(-100, 10, now, actor, [actor, target], [(2, "reply", 3.0)]) == 1
    assert repo.record_message_bundle(-100, 10, now, actor, [actor, target], [(2, "reply", 3.0)]) == 0
    repo.record_message_bundle(-200, 11, now, actor, [actor, target], [(2, "reply", 3.0)])

    rows, names = repo.load_graph(-100, now - timedelta(days=1))
    assert rows == [(1, 2, "reply", 3.0)]
    assert names == {1: "Alice (@alice)", 2: "Bob (@bob)"}


def test_ai_interpretation_has_fallback():
    from features.social_graph.ai import interpret_personal_summary
    from features.social_graph.analysis import PersonalSummary

    summary = PersonalSummary(1, 1.0, 1.0, 1, (), (), (), None)

    async def broken(_prompt, _chat_id):
        raise RuntimeError("provider unavailable")

    result = asyncio.run(interpret_personal_summary(summary, {}, "-100", generator=broken))
    assert result is None


def test_my_connections_handler_formats_personal_stats(monkeypatch):
    import handlers.social_graph as handler
    from features.social_graph.service import SocialGraphData

    async def fake_data(_chat_id):
        return SocialGraphData(
            interactions=((1, 2, "reply", 3.0), (2, 1, "mention", 2.0)),
            names={1: "Alice", 2: "Bob"},
            period_days=30,
        )

    async def no_ai(*_args, **_kwargs):
        return None

    replies = []

    async def reply(text):
        replies.append(text)

    fake_message = SimpleNamespace(
        chat=SimpleNamespace(id=-100, type="supergroup"),
        from_user=user(1, "Alice"),
        reply=reply,
    )
    monkeypatch.setattr(handler, "get_graph_data", fake_data)
    monkeypatch.setattr(handler, "interpret_personal_summary", no_ai)

    asyncio.run(handler.handle_my_connections(fake_message))

    assert "Bob" in replies[0]
    assert "ты → 3" in replies[0]
    assert "тебе → 2" in replies[0]


def test_social_router_is_registered_before_dialog_catch_all():
    from handlers import ROUTERS
    from handlers import dialog, social_graph

    assert ROUTERS.index(social_graph.router) < ROUTERS.index(dialog.router)
