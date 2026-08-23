import random
from datetime import date, datetime

from tests import test_smoke_imports  # noqa: F401  (env + heavy-library mocks)


def test_daily_scheduler_picks_two_random_slots_with_preferred_gap():
    from features.channel.scheduler import DAY_END, DAY_START, MOSCOW_TZ, _pick_daily_slots

    now = MOSCOW_TZ.localize(datetime(2026, 8, 23, 0, 0))
    slots = _pick_daily_slots(date(2026, 8, 23), now=now, rng=random.Random(42))

    assert len(slots) == 2
    assert slots[0].time().replace(tzinfo=None) >= DAY_START
    assert slots[1].time().replace(tzinfo=None) <= DAY_END
    assert (slots[1] - slots[0]).total_seconds() >= 3 * 60 * 60


def test_chat_fragment_is_anonymized_before_ai_prompt():
    from features.channel.service import _anonymize_fragment

    fragment = _anonymize_fragment([
        {"name": "Алиса", "text": "пиши @detector https://example.com 123456789"},
        {"name": "Боб", "text": "ок"},
        {"name": "Алиса", "text": "понял"},
    ])

    assert "Алиса" not in fragment
    assert "Боб" not in fragment
    assert "@detector" not in fragment
    assert "https://example.com" not in fragment
    assert "123456789" not in fragment
    assert "Участник 1" in fragment
    assert "Участник 2" in fragment


def test_nonsense_is_a_valid_channel_post_but_exact_duplicate_is_not():
    from features.channel.service import _validate_post

    assert _validate_post("я червяк", []) is None
    assert _validate_post("щас какаю", []) is None
    assert _validate_post("я червяк", [{"text": "я червяк"}]) == "точный дубль недавнего поста"


def test_batya_public_feed_parser_extracts_real_text_post_links():
    from features.channel.batya_source import _parse_public_feed

    html = """
    <div class="tgme_widget_message" data-post="lukeimyourmouth/5403">
      <div class="tgme_widget_message_text">первый <b>пост</b></div>
    </div>
    <div class="tgme_widget_message" data-post="lukeimyourmouth/5404">
      <div class="tgme_widget_message_text">второй<br>пост</div>
    </div>
    <div class="tgme_widget_message" data-post="lukeimyourmouth/5405"></div>
    <div class="tgme_widget_message" data-post="other/1">
      <div class="tgme_widget_message_text">чужое</div>
    </div>
    """

    posts = _parse_public_feed(html, channel="lukeimyourmouth")

    assert posts == [
        {
            "message_id": 5403,
            "url": "https://t.me/lukeimyourmouth/5403",
            "text": "первый пост",
        },
        {
            "message_id": 5404,
            "url": "https://t.me/lukeimyourmouth/5404",
            "text": "второй\nпост",
        },
    ]


def test_batya_comment_can_be_one_word_and_does_not_repeat_source_link():
    from features.channel.service import (
        _pick_uncommented_batya_post,
        _validate_batya_comment,
    )

    source_posts = [
        {"url": "https://t.me/lukeimyourmouth/5403", "text": "старое"},
        {"url": "https://t.me/lukeimyourmouth/5404", "text": "новое"},
    ]
    published = [
        {"external_source_url": "https://t.me/lukeimyourmouth/5403", "text": "что-то"},
    ]

    picked = _pick_uncommented_batya_post(source_posts, published)
    assert picked["url"] == "https://t.me/lukeimyourmouth/5404"
    assert _validate_batya_comment("дебил") is None
    assert _validate_batya_comment("") == "пустой ответ"
    assert _validate_batya_comment("https://t.me/lukeimyourmouth/5404 дебил") == "модель сама добавила Telegram-ссылку"


def test_channel_storage_roundtrip(tmp_path, monkeypatch):
    from features.channel import storage

    posts_file = tmp_path / "posts.json"
    monkeypatch.setattr(storage, "POSTS_FILE", posts_file)

    storage.append_post({"text": "я червяк"})
    storage.append_post({"text": "уже нет"})

    assert storage.load_posts() == [{"text": "я червяк"}, {"text": "уже нет"}]
    assert storage.load_posts(limit=1) == [{"text": "уже нет"}]
