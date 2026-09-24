import asyncio
from types import SimpleNamespace

from tests import test_smoke_imports  # noqa: F401  (env + heavy-library mocks)


def test_exact_upupa_mention_matches_only_standalone_word():
    from features.channel.external_mentions import _mentions_upupa

    assert _mentions_upupa("упупа") is True
    assert _mentions_upupa("Эй, УПУПА!") is True
    assert _mentions_upupa("что там, упупа?") is True
    assert _mentions_upupa("упупами") is False
    assert _mentions_upupa("супупа") is False


def test_initial_baseline_does_not_resurrect_old_mentions(tmp_path, monkeypatch):
    from features.channel import external_mentions

    monkeypatch.setattr(external_mentions, "MENTION_STATE_FILE", tmp_path / "mentions.json")
    monkeypatch.setattr(
        external_mentions.base,
        "EXTERNAL_COMMENT_SOURCES",
        ({"channel": "known", "description": "@known", "owner": None, "allow_batya_reference": False},),
    )
    monkeypatch.setattr(external_mentions.base, "load_posts", lambda: [])

    async def fake_fetch(channel: str, *, limit: int):
        assert channel == "known"
        return [
            {
                "message_id": 50,
                "url": "https://t.me/known/50",
                "text": "Упупа, ты где?",
            }
        ]

    monkeypatch.setattr(external_mentions.base, "fetch_public_posts", fake_fetch)

    asyncio.run(external_mentions.initialize_tracking())
    queued = asyncio.run(external_mentions.scan_for_mentions())

    assert queued == 0
    state = external_mentions._read_state()
    assert state["sources"]["known"]["last_seen_message_id"] == 50
    assert state["pending"] == []


def test_new_exact_mentions_are_queued_once_and_non_mentions_advance_cursor(tmp_path, monkeypatch):
    from features.channel import external_mentions

    monkeypatch.setattr(external_mentions, "MENTION_STATE_FILE", tmp_path / "mentions.json")
    monkeypatch.setattr(
        external_mentions.base,
        "EXTERNAL_COMMENT_SOURCES",
        ({"channel": "known", "description": "@known", "owner": None, "allow_batya_reference": False},),
    )
    monkeypatch.setattr(external_mentions.base, "load_posts", lambda: [])
    external_mentions._write_state({
        "sources": {"known": {"last_seen_message_id": 100}},
        "pending": [],
    })

    async def fake_fetch(_channel: str, *, limit: int):
        return [
            {"message_id": 101, "url": "https://t.me/known/101", "text": "тут много упупами"},
            {"message_id": 102, "url": "https://t.me/known/102", "text": "эй, Упупа! ответь"},
            {"message_id": 103, "url": "https://t.me/known/103", "text": "обычный пост"},
        ]

    monkeypatch.setattr(external_mentions.base, "fetch_public_posts", fake_fetch)

    assert asyncio.run(external_mentions.scan_for_mentions()) == 1
    state = external_mentions._read_state()
    assert state["sources"]["known"]["last_seen_message_id"] == 103
    assert [item["url"] for item in state["pending"]] == ["https://t.me/known/102"]

    assert asyncio.run(external_mentions.scan_for_mentions()) == 0
    state = external_mentions._read_state()
    assert [item["url"] for item in state["pending"]] == ["https://t.me/known/102"]


def test_already_published_external_url_is_not_queued(tmp_path, monkeypatch):
    from features.channel import external_mentions

    monkeypatch.setattr(external_mentions, "MENTION_STATE_FILE", tmp_path / "mentions.json")
    monkeypatch.setattr(
        external_mentions.base,
        "EXTERNAL_COMMENT_SOURCES",
        ({"channel": "known", "description": "@known", "owner": None, "allow_batya_reference": False},),
    )
    external_mentions._write_state({
        "sources": {"known": {"last_seen_message_id": 10}},
        "pending": [],
    })
    monkeypatch.setattr(
        external_mentions.base,
        "load_posts",
        lambda: [{"external_source_url": "https://t.me/known/11"}],
    )

    async def fake_fetch(_channel: str, *, limit: int):
        return [{"message_id": 11, "url": "https://t.me/known/11", "text": "упупа, алло"}]

    monkeypatch.setattr(external_mentions.base, "fetch_public_posts", fake_fetch)

    assert asyncio.run(external_mentions.scan_for_mentions()) == 0
    assert external_mentions._read_state()["pending"] == []


def test_prepare_reply_is_grounded_in_mention_and_keeps_source_metadata(monkeypatch):
    from AI import summarize
    from features.channel import external_mentions

    source = {
        "channel": "known",
        "description": "@known; этот канал ведёт Вася",
        "owner": "Вася",
        "allow_batya_reference": False,
    }
    monkeypatch.setattr(external_mentions.base, "EXTERNAL_COMMENT_SOURCES", (source,))
    prompts = []

    async def fake_generate(prompt: str, _chat_id: str):
        prompts.append(prompt)
        return "Сам позвал — теперь не жалуйся."

    monkeypatch.setattr(summarize, "_generate_with_active_model", fake_generate)

    mention = {
        "channel": "known",
        "message_id": 77,
        "url": "https://t.me/known/77",
        "text": "Упупа, иди сюда, разговор есть.",
    }
    prepared = asyncio.run(
        external_mentions.prepare_reply(
            mention,
            [],
            {"name": "neutral", "posts_left": 4},
        )
    )

    assert prepared is not None
    text, metadata = prepared
    assert text == "https://t.me/known/77\n\nСам позвал — теперь не жалуйся."
    assert metadata["post_kind"] == "external_mention_reply"
    assert metadata["external_mention"] is True
    assert metadata["external_source_owner"] == "Вася"
    assert "Упупа, иди сюда" in prompts[0]
    assert "ответ именно на это обращение" in prompts[0]


def test_direct_mention_consumes_next_channel_slot_before_other_modes(tmp_path, monkeypatch):
    from features.channel import cringedep_service, external_mentions

    monkeypatch.setattr(external_mentions, "MENTION_STATE_FILE", tmp_path / "mentions.json")
    external_mentions._write_state({
        "sources": {},
        "pending": [
            {
                "channel": "known",
                "message_id": 88,
                "url": "https://t.me/known/88",
                "text": "упупа, ответь",
                "attempts": 0,
            }
        ],
    })

    async def no_scan():
        return 0

    async def fake_prepare(mention, published_posts, mood):
        assert mention["url"] == "https://t.me/known/88"
        return (
            "https://t.me/known/88\n\nСам начал.",
            {
                "post_kind": "external_mention_reply",
                "external_source_url": "https://t.me/known/88",
                "external_mention": True,
            },
        )

    async def fail_continuity(*_args, **_kwargs):
        raise AssertionError("continuity must not run before a direct mention")

    async def fail_normal(*_args, **_kwargs):
        raise AssertionError("normal publisher must not run before a direct mention")

    stored = []

    async def fake_store(sent, **kwargs):
        stored.append((sent, kwargs))

    async def fake_consume(_mood, _message_id):
        return None

    monkeypatch.setattr(external_mentions, "scan_for_mentions", no_scan)
    monkeypatch.setattr(external_mentions, "prepare_reply", fake_prepare)
    monkeypatch.setattr(cringedep_service.base, "load_posts", lambda: [])
    monkeypatch.setattr(cringedep_service, "get_current_mood", lambda: {"name": "neutral", "posts_left": 3})
    monkeypatch.setattr(cringedep_service.base, "_store_published_post", fake_store)
    monkeypatch.setattr(cringedep_service.mood_service, "_consume_after_publish", fake_consume)
    monkeypatch.setattr(cringedep_service, "_try_publish_continuity", fail_continuity)
    monkeypatch.setattr(cringedep_service.mood_service, "publish_channel_post", fail_normal)

    class FakeBot:
        async def send_message(self, chat_id, text):
            assert chat_id == cringedep_service.CHANNEL_TARGET
            return SimpleNamespace(message_id=999, text=text)

    sent, text = asyncio.run(cringedep_service.publish_channel_post(FakeBot(), source="scheduled"))

    assert sent.message_id == 999
    assert text.endswith("Сам начал.")
    assert stored[0][1]["metadata"]["external_mention"] is True
    assert asyncio.run(external_mentions.peek_pending()) is None
