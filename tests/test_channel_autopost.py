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
    assert _validate_post("мне похуй", []) is None
    assert _validate_post("я червяк", [{"text": "я червяк"}]) == "точный дубль недавнего поста"


def test_channel_length_distribution_strongly_prefers_two_or_three_words():
    from prompts.channel import POST_LENGTH_MODES

    weights = {mode["name"]: mode["weight"] for mode in POST_LENGTH_MODES}
    assert weights == {"micro": 70, "short": 25, "medium": 5}
    micro = next(mode for mode in POST_LENGTH_MODES if mode["name"] == "micro")
    assert micro["min_words"] == 2
    assert micro["max_words"] == 3


def test_micro_length_mode_is_enforced_not_just_prompted():
    from features.channel.service import _validate_length_mode
    from prompts.channel import POST_LENGTH_MODES

    micro = next(mode for mode in POST_LENGTH_MODES if mode["name"] == "micro")

    assert _validate_length_mode("я червяк", micro) is None
    assert _validate_length_mode("мне похуй", micro) is None
    assert "нужно 2–3 слов" in _validate_length_mode("я", micro)
    assert "нужно 2–3 слов" in _validate_length_mode("я теперь снова червяк", micro)


def test_all_normal_length_modes_are_bounded():
    from prompts.channel import POST_LENGTH_MODES

    assert max(mode["max_chars"] for mode in POST_LENGTH_MODES) == 240
    assert sum(mode["weight"] for mode in POST_LENGTH_MODES) == 100


def test_content_distribution_is_mostly_absurd_and_domestic():
    from prompts.channel import POST_CONTENT_MODES

    weights = {mode["name"]: mode["weight"] for mode in POST_CONTENT_MODES}
    assert sum(weights.values()) == 100
    assert weights["absurd"] == 70
    assert weights["domestic"] == 15
    assert weights["functionality"] == 5
    assert weights["absurd"] + weights["domestic"] == 85


def test_base_prompt_does_not_prime_batya_or_capabilities():
    from features.channel.service import _build_prompt
    from prompts.channel import CHANNEL_PERSONA, POST_CONTENT_MODES, POST_LENGTH_MODES

    assert "батя" not in CHANNEL_PERSONA.casefold()

    micro = next(mode for mode in POST_LENGTH_MODES if mode["name"] == "micro")
    absurd = next(mode for mode in POST_CONTENT_MODES if mode["name"] == "absurd")
    prompt = _build_prompt([], None, micro, absurd, False)

    assert "батя" not in prompt.casefold()
    assert "фактчекать" not in prompt.casefold()
    assert "я червяк" in prompt.casefold()
    assert "мне похуй" in prompt.casefold()


def test_capabilities_and_batya_are_injected_only_in_selected_modes():
    from features.channel.service import _build_prompt
    from prompts.channel import POST_CONTENT_MODES, POST_LENGTH_MODES

    micro = next(mode for mode in POST_LENGTH_MODES if mode["name"] == "micro")
    functionality = next(mode for mode in POST_CONTENT_MODES if mode["name"] == "functionality")
    prompt = _build_prompt([], None, micro, functionality, True)

    assert "батя" in prompt.casefold()
    assert "фактчекать" in prompt.casefold()


def test_batya_mention_is_hard_blocked_outside_rare_mode():
    from features.channel.service import _validate_batya_mention_policy

    assert _validate_batya_mention_policy("я червяк", allow_batya_mention=False) is None
    assert _validate_batya_mention_policy("батя не знает", allow_batya_mention=True) is None
    assert _validate_batya_mention_policy("батя не знает", allow_batya_mention=False) == "редкий режим бати не выбран"
    assert _validate_batya_mention_policy("папа спалит", allow_batya_mention=False) == "редкий режим бати не выбран"


def test_batya_mention_has_twenty_post_cooldown_even_if_rng_wants_it():
    from features.channel.service import BATYA_MENTION_COOLDOWN_POSTS, _should_allow_batya_mention

    class ZeroRng:
        @staticmethod
        def random():
            return 0.0

    recent = [{"text": "я червяк"} for _ in range(BATYA_MENTION_COOLDOWN_POSTS - 1)]
    recent.append({"text": "батя не знает"})
    assert _should_allow_batya_mention(recent, rng=ZeroRng()) is False

    clean_history = [{"text": "я червяк"} for _ in range(BATYA_MENTION_COOLDOWN_POSTS)]
    assert _should_allow_batya_mention(clean_history, rng=ZeroRng()) is True


def test_recent_batya_posts_are_hidden_from_normal_prompt_memory():
    from features.channel.service import _format_recent_posts

    formatted = _format_recent_posts(
        [
            {"text": "батя не знает"},
            {"text": "мне похуй"},
        ],
        allow_batya_mention=False,
    )

    assert "батя" not in formatted.casefold()
    assert "мне похуй" in formatted.casefold()


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
            "text": "второй пост",
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
    assert _validate_batya_comment("ну и хули это вообще было блять опять") is None
    assert "многословный" in _validate_batya_comment("раз два три четыре пять шесть семь восемь девять")
    assert _validate_batya_comment("") == "пустой ответ"
    assert _validate_batya_comment("https://t.me/lukeimyourmouth/5404 дебил") == "модель сама добавила Telegram-ссылку"
    assert "легенду про батю" in _validate_batya_comment("батя дебил")


def test_channel_storage_roundtrip(tmp_path, monkeypatch):
    from features.channel import storage

    posts_file = tmp_path / "posts.json"
    monkeypatch.setattr(storage, "POSTS_FILE", posts_file)

    storage.append_post({"text": "я червяк"})
    storage.append_post({"text": "уже нет"})

    assert storage.load_posts() == [{"text": "я червяк"}, {"text": "уже нет"}]
    assert storage.load_posts(limit=1) == [{"text": "уже нет"}]
