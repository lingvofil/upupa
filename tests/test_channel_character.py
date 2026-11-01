import asyncio
import random
from datetime import datetime, timedelta, timezone

from tests import test_smoke_imports  # noqa: F401  (env + heavy-library mocks)


def test_clock_prompt_names_the_real_weekday_and_date():
    from features.channel.mood import MOSCOW_TZ, clock_prompt, mood_prompt

    now = MOSCOW_TZ.localize(datetime(2026, 10, 31, 18, 5))
    prompt = clock_prompt(now)

    assert "суббота" in prompt
    assert "31 октября" in prompt
    assert "18:05" in prompt
    assert "суббота" in mood_prompt({"name": "neutral", "posts_left": 3}, now=now)


def test_night_slots_are_rarer_but_not_forbidden():
    from features.channel.scheduler import MOSCOW_TZ, _pick_daily_slots

    rng = random.Random(11)
    slots = []
    for _ in range(100):
        slots.extend(_pick_daily_slots(datetime(2026, 11, 2).date(), count=5, rng=rng))
    hours = [slot.astimezone(MOSCOW_TZ).hour for slot in slots]
    night_share = sum(1 <= hour < 8 for hour in hours) / len(hours)

    # Без прореживания на эти 7 часов пришлось бы около 29% слотов.
    assert 0 < night_share < 0.15


def test_persona_does_not_prime_recurring_threads():
    from prompts.channel import CHANNEL_PERSONA, CHARACTER_THREADS

    persona = CHANNEL_PERSONA.casefold()
    for word in ("голуб", "мухтар", "удод", "батя", "народ", "главн"):
        assert word not in persona
    assert "признани" in persona
    assert "характер, а не тема" in persona
    assert {thread["name"] for thread in CHARACTER_THREADS} >= {"people", "ambition", "rival", "pigeons"}


def test_character_thread_rests_after_recent_use_or_organic_mention():
    from features.channel.mood_service import _pick_character_thread

    used = {"character_thread": "people", "text": "пост без ключевых слов"}
    organic = {"text": "Мухтар опять что-то затеял"}
    for _ in range(30):
        thread = _pick_character_thread([used, organic], rng=random)
        assert thread["name"] not in {"people", "rival"}

    everything_used = [
        {"text": "народ, признание, Мухтар, голуби, удод"},
    ]
    assert _pick_character_thread(everything_used) is None


def test_character_mode_falls_back_when_every_thread_is_resting():
    from features.channel.mood_service import _character_mode

    mode = _character_mode(
        [{"text": "народ, признание, Мухтар, голуби, удод"}],
        {"name": "neutral", "posts_left": 3},
    )

    assert mode["name"] not in {"character", "chat"}
    assert "character_thread" not in mode


def _capture_generation(monkeypatch, *, posts, reply="Читатели опять меня не ценят, ну и сидите без указов."):
    from AI import summarize
    from features.channel import mood_service

    captured = {}

    async def fake_generate(prompt, _chat_id):
        captured["prompt"] = prompt
        return reply

    monkeypatch.setattr(summarize, "_generate_with_active_model", fake_generate)
    monkeypatch.setattr(mood_service.base, "load_posts", lambda: posts)
    monkeypatch.setattr(mood_service.random, "random", lambda: 0.99)
    return captured


def test_generated_character_post_records_thread_and_skips_micro(monkeypatch):
    from features.channel import mood_service

    captured = _capture_generation(monkeypatch, posts=[])
    character = next(mode for mode in mood_service.POST_CONTENT_MODES if mode["name"] == "character")
    monkeypatch.setattr(mood_service, "_choose_content_mode", lambda _mood: character)
    monkeypatch.setattr(mood_service.reposts, "pick_reaction_event", lambda _posts: None)

    def fake_length(mood, *, allow_micro=True):
        captured["allow_micro"] = allow_micro
        return next(mode for mode in mood_service.POST_LENGTH_MODES if mode["name"] == "short")

    monkeypatch.setattr(mood_service, "_choose_length_mode", fake_length)

    _text, metadata = asyncio.run(mood_service.generate_channel_post({"name": "neutral", "posts_left": 3}))

    assert metadata["content_mode"] == "character"
    assert metadata["character_thread"]
    assert captured["allow_micro"] is False
    assert "Тема этого поста" in captured["prompt"]


def test_second_decree_in_a_row_is_rejected_but_a_lone_one_passes():
    from features.channel.service import _validate_post

    assert _validate_post("Объявляю вторник выходным.", []) is None
    assert _validate_post("Сегодня я официально отменяю вторник.", []) is None

    reason = _validate_post("Конфискую у заката красный цвет.", [{"text": "Отменяю понедельник."}])
    assert "указ" in reason


def test_pigeon_cannot_star_in_consecutive_images():
    from features.channel.service import _validate_image_plan

    prompt = "Огромный голубь в костюме паркует автомобиль на перекрёстке."
    assert _validate_image_plan(prompt, "Опять он", []) is None

    recent = [{"post_kind": "image", "text": "Менеджер", "image_prompt": "Голубь в костюме чистит фару экскаватора."}]
    assert "голубь" in _validate_image_plan(prompt, "Опять он", recent)


def test_names_are_hidden_inside_message_text_in_any_case():
    from features.channel.chat_context import HIDDEN_NAME, anonymize_fragment

    fragment = anonymize_fragment(
        [
            {"name": "Виктор Петров", "text": "виталию не наливать"},
            {"name": "Боб", "text": "Виктору скажи, что боб против"},
        ],
        known_names={"Виталий"},
    )

    assert "Виктор" not in fragment
    assert "итали" not in fragment
    assert "боб" not in fragment.casefold().split("участник 2:")[1]
    assert fragment.count(HIDDEN_NAME) == 3
    assert "Участник 1" in fragment
    assert "Участник 2" in fragment


def test_post_naming_a_real_chat_is_rejected(monkeypatch):
    from features.channel import service

    monkeypatch.setattr(service, "chat_list", [{"id": -1, "title": "Обнимашкинный движ"}, {"id": -2, "title": "Коты"}])

    assert "реального чата" in service._validate_post("Обнимашкинный движ опять воюет.", [])
    # Короткие названия слишком легко совпадают с обычными словами.
    assert service._validate_post("Коты опять воюют.", []) is None


_FEED_HTML = """
<div class="tgme_widget_message" data-post="muhtarboodka/70">
  <div class="tgme_widget_message_forwarded_from">Forwarded from
    <a class="tgme_widget_message_forwarded_from_name" href="https://t.me/upupa_channel/656">Упупа</a>
  </div>
  <div class="tgme_widget_message_text">ЫЫАУУААЭЭЭЭЭ!!!</div>
  <a class="tgme_widget_message_date"><time datetime="2026-10-29T23:30:00+00:00"></time></a>
</div>
<div class="tgme_widget_message" data-post="muhtarboodka/71">
  <div class="tgme_widget_message_text">Просто пост</div>
</div>
"""


def test_public_feed_parser_exposes_forward_source_and_date():
    from features.channel.batya_source import _parse_public_feed
    from features.channel.service import _is_own_repost

    repost, plain = _parse_public_feed(_FEED_HTML, channel="muhtarboodka")

    assert repost["forwarded_from_channel"] == "upupa_channel"
    assert repost["forwarded_from_message_id"] == 656
    assert repost["posted_at"] == "2026-10-29T23:30:00+00:00"
    assert _is_own_repost(repost) is True
    assert _is_own_repost(plain) is False
    assert _is_own_repost({"forwarded_from_channel": "someone_else"}) is False


def test_own_repost_is_not_taken_for_someone_elses_post():
    from features.channel.cringedep_service import _pick_unanswered_image_post
    from features.channel.service import _pick_uncommented_external_post

    own = {
        "url": "https://t.me/lukeimyourmouth/5447",
        "text": "Мне похуй.",
        "image_url": "https://example.com/a.jpg",
        "forwarded_from_channel": "upupa_channel",
    }

    assert _pick_uncommented_external_post([own], []) is None
    assert _pick_unanswered_image_post([own], []) is None


def test_own_repost_mentioning_upupa_does_not_queue_a_reply(tmp_path, monkeypatch):
    from features.channel import external_mentions

    state_file = tmp_path / "mentions.json"
    source = {"channel": "muhtarboodka", "description": "@muhtarboodka", "owner": None}
    state_file.write_text(
        '{"sources": {"muhtarboodka": {"last_seen_message_id": 1}}, "pending": []}',
        encoding="utf-8",
    )

    async def fake_fetch(_channel, *, limit):
        return [
            {
                "message_id": 2,
                "url": "https://t.me/muhtarboodka/2",
                "text": "Я, Упупа, снова прав",
                "forwarded_from_channel": "upupa_channel",
            },
        ]

    monkeypatch.setattr(external_mentions, "MENTION_STATE_FILE", state_file)
    monkeypatch.setattr(external_mentions.base, "EXTERNAL_COMMENT_SOURCES", (source,))
    monkeypatch.setattr(external_mentions.base, "fetch_public_posts", fake_fetch)
    monkeypatch.setattr(external_mentions.base, "load_posts", lambda: [])

    assert asyncio.run(external_mentions.scan_for_mentions()) == 0
    assert asyncio.run(external_mentions.peek_pending()) is None


def test_scan_for_reposts_stores_each_repost_once(tmp_path, monkeypatch):
    from features.channel import reposts

    async def fake_fetch(channel, *, limit):
        if channel != "muhtarboodka":
            return [{"url": f"https://t.me/{channel}/1", "text": "чужой пост"}]
        return [
            {
                "url": "https://t.me/muhtarboodka/70",
                "text": "ЫЫА",
                "forwarded_from_channel": "upupa_channel",
                "forwarded_from_message_id": 656,
                "posted_at": "2026-10-29T23:30:00+00:00",
            },
            {
                "url": "https://t.me/muhtarboodka/71",
                "text": "чужой репост",
                "forwarded_from_channel": "someone_else",
                "forwarded_from_message_id": 5,
            },
        ]

    monkeypatch.setattr(reposts, "REPOSTS_FILE", tmp_path / "reposts.json")
    monkeypatch.setattr(reposts.base, "fetch_public_posts", fake_fetch)

    assert asyncio.run(reposts.scan_for_reposts()) == 1
    assert asyncio.run(reposts.scan_for_reposts()) == 0

    stored = reposts.load_reposts()
    assert [item["url"] for item in stored] == ["https://t.me/muhtarboodka/70"]
    assert stored[0]["source_message_id"] == 656
    assert stored[0]["channel"] == "muhtarboodka"


class _AlwaysReact:
    @staticmethod
    def random():
        return 0.0


def _repost_record(now, *, hours_ago=1):
    return {
        "channel": "muhtarboodka",
        "url": "https://t.me/muhtarboodka/70",
        "source_message_id": 656,
        "posted_at": (now - timedelta(hours=hours_ago)).isoformat(),
    }


def test_reaction_event_requires_fresh_unused_repost_and_rests_afterwards(tmp_path, monkeypatch):
    from features.channel import reposts

    now = datetime(2026, 10, 30, 12, 0, tzinfo=timezone.utc)
    own_post = {"message_id": 656, "text": "ЫЫАУУААЭЭЭЭЭ!!!"}
    monkeypatch.setattr(reposts, "REPOSTS_FILE", tmp_path / "reposts.json")
    reposts._save_reposts([_repost_record(now)])

    event = reposts.pick_reaction_event([own_post], now=now, rng=_AlwaysReact)
    assert event["post_text"] == "ЫЫАУУААЭЭЭЭЭ!!!"
    assert "Мухтар" in reposts.reaction_instruction(event)

    answered = {"content_mode": "repost_reaction", "repost_reaction_url": event["url"]}
    assert reposts.pick_reaction_event([own_post, answered], now=now, rng=_AlwaysReact) is None

    long_ago = [{"text": "пост"} for _ in range(reposts.REACTION_COOLDOWN_POSTS)]
    assert reposts.pick_reaction_event([own_post, answered, *long_ago], now=now, rng=_AlwaysReact) is None

    reposts._save_reposts([_repost_record(now, hours_ago=reposts.REACTION_MAX_AGE_HOURS + 1)])
    assert reposts.pick_reaction_event([own_post], now=now, rng=_AlwaysReact) is None


def test_repost_reaction_reaches_prompt_and_is_recorded(monkeypatch):
    from features.channel import mood_service

    event = {
        "channel": "muhtarboodka",
        "url": "https://t.me/muhtarboodka/70",
        "post_text": "ЫЫАУУААЭЭЭЭЭ!!!",
    }
    captured = _capture_generation(monkeypatch, posts=[], reply="Мухтар опять спёр мой вопль, вор.")
    monkeypatch.setattr(mood_service.reposts, "pick_reaction_event", lambda _posts: event)

    def fail_if_called(_mood):
        raise AssertionError("repost reaction must replace the random content mode")

    monkeypatch.setattr(mood_service, "_choose_content_mode", fail_if_called)

    text, metadata = asyncio.run(mood_service.generate_channel_post({"name": "neutral", "posts_left": 3}))

    assert text == "Мухтар опять спёр мой вопль, вор."
    assert metadata["content_mode"] == "repost_reaction"
    assert metadata["repost_reaction_url"] == event["url"]
    assert "репостнул" in captured["prompt"]
    assert "ЫЫАУУААЭЭЭЭЭ!!!" in captured["prompt"]


def test_batya_repost_reaction_may_mention_batya(monkeypatch):
    from features.channel import mood_service

    event = {
        "channel": mood_service.base.BATYA_CHANNEL,
        "url": "https://t.me/lukeimyourmouth/5447",
        "post_text": "Мне похуй.",
    }
    _capture_generation(monkeypatch, posts=[], reply="Батя опять спёр мою мысль и радуется.")
    monkeypatch.setattr(mood_service.reposts, "pick_reaction_event", lambda _posts: event)
    monkeypatch.setattr(mood_service.base, "_should_allow_batya_mention", lambda _posts: False)

    text, metadata = asyncio.run(mood_service.generate_channel_post({"name": "neutral", "posts_left": 3}))

    assert "Батя" in text
    assert metadata["batya_mention_allowed"] is True


def test_past_replies_reach_external_comment_and_mention_prompts():
    from features.channel import external_mentions, mood_service
    from features.channel.service import _source_history_block

    published = [
        {"external_source_channel": "@muhtarboodka", "text": f"https://t.me/muhtarboodka/{index}\n\nРеплика {index}"}
        for index in range(7)
    ] + [{"external_source_channel": "@neuralmeduza", "text": "https://t.me/neuralmeduza/1\n\nЧужая реплика"}]

    history = _source_history_block("muhtarboodka", published)
    assert "Реплика 6" in history
    assert "Реплика 1" not in history
    assert "Чужая реплика" not in history
    assert "https://" not in history
    assert _source_history_block("kapibara_fen", published) == ""

    source = {"channel": "muhtarboodka", "description": "@muhtarboodka; Мухтар"}
    mood = {"name": "neutral", "posts_left": 3}
    comment_prompt = mood_service._build_external_prompt(
        source,
        {"text": "пост"},
        mood,
        source_history=history,
    )
    mention_prompt = external_mentions._build_prompt(
        source,
        {"text": "пост"},
        mood,
        source_history=history,
    )

    assert "Реплика 6" in comment_prompt
    assert comment_prompt.index("Реплика 6") < comment_prompt.index("ТВОЙ КОММЕНТАРИЙ")
    assert "Реплика 6" in mention_prompt


def test_rival_and_batya_descriptions_replace_the_old_legend():
    from features.channel.service import EXTERNAL_COMMENT_SOURCES
    from prompts.channel import BATYA_MENTION_PROMPT

    by_channel = {source["channel"]: source["description"] for source in EXTERNAL_COMMENT_SOURCES}

    assert "соперник" in by_channel["muhtarboodka"]
    assert "подростковом бунте" in by_channel["lukeimyourmouth"]
    assert "не знает" not in BATYA_MENTION_PROMPT


def test_world_context_shows_state_numbers_instead_of_chat_titles(monkeypatch):
    from types import SimpleNamespace

    from features.channel import continuity
    from features.world import news, service as world_service

    states = [SimpleNamespace(world_id=8, title="Обнимашкинный движ")]

    class FakeWorld:
        async def list_all_states(self):
            return states

    async def fake_feed(_service, *, days, event_limit):
        return "01.11 10:00 — №8 — Обнимашкинный движ объявило войну. Обнимашкинный движ доволен."

    monkeypatch.setattr(news, "build_world_fact_feed", fake_feed)
    monkeypatch.setattr(world_service, "get_world_service", lambda: FakeWorld())

    facts = asyncio.run(continuity._world_context())

    assert "Обнимашкинный" not in facts
    assert "№8 объявило войну" in facts
