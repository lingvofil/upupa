import random
from datetime import date, datetime

from tests import test_smoke_imports  # noqa: F401  (env + heavy-library mocks)


def test_daily_scheduler_picks_five_random_slots_with_preferred_gap():
    from features.channel.scheduler import DAY_END, DAY_START, MOSCOW_TZ, POSTS_PER_DAY, _pick_daily_slots

    now = MOSCOW_TZ.localize(datetime(2026, 8, 23, 0, 0))
    slots = _pick_daily_slots(date(2026, 8, 23), now=now, rng=random.Random(42))

    assert POSTS_PER_DAY == 5
    assert len(slots) == 5
    assert slots[0].time().replace(tzinfo=None) >= DAY_START
    assert slots[-1].time().replace(tzinfo=None) <= DAY_END
    for previous, current in zip(slots, slots[1:]):
        assert (current - previous).total_seconds() >= 2 * 60 * 60


def test_late_start_reduces_slot_count_instead_of_bursting_five_posts():
    from features.channel.scheduler import MIN_GAP_MINUTES, MOSCOW_TZ, _pick_daily_slots

    now = MOSCOW_TZ.localize(datetime(2026, 8, 23, 22, 0))
    slots = _pick_daily_slots(date(2026, 8, 23), now=now, rng=random.Random(7))

    assert 1 <= len(slots) < 5
    for previous, current in zip(slots, slots[1:]):
        assert (current - previous).total_seconds() >= MIN_GAP_MINUTES * 60


def test_old_two_slot_schedule_is_marked_for_five_post_target():
    from features.channel.scheduler import MOSCOW_TZ, POSTS_PER_DAY, _top_up_schedule_slots

    now = MOSCOW_TZ.localize(datetime(2026, 8, 23, 17, 0))
    state = {
        "date": "2026-08-23",
        "slots": [
            {"at": MOSCOW_TZ.localize(datetime(2026, 8, 23, 12, 0)).isoformat(), "done": True, "missed": False},
            {"at": MOSCOW_TZ.localize(datetime(2026, 8, 23, 20, 0)).isoformat(), "done": False, "missed": False},
        ],
    }

    changed = _top_up_schedule_slots(state, now, rng=random.Random(42))

    assert changed is True
    assert state["target_posts"] == POSTS_PER_DAY == 5
    assert 2 < len(state["slots"]) <= 5


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


def test_channel_length_distribution_is_50_40_10():
    from prompts.channel import POST_LENGTH_MODES

    weights = {mode["name"]: mode["weight"] for mode in POST_LENGTH_MODES}
    assert weights == {"micro": 50, "short": 40, "medium": 10}
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


def test_content_distribution_includes_philosophy_mode():
    from prompts.channel import POST_CONTENT_MODES

    weights = {mode["name"]: mode["weight"] for mode in POST_CONTENT_MODES}
    assert sum(weights.values()) == 100
    assert weights["absurd"] == 8
    assert weights["philosophy"] == 10
    assert weights["domestic"] == 45
    assert weights["chat"] == 17
    assert weights["functionality"] == 15
    assert weights["imperfect"] == 5


def test_philosophy_mode_is_ironic_sarcastic_and_can_swear():
    from prompts.channel import POST_CONTENT_MODES

    philosophy = next(mode for mode in POST_CONTENT_MODES if mode["name"] == "philosophy")
    instruction = philosophy["instruction"].casefold()

    assert "философ" in instruction
    assert "ирони" in instruction
    assert "сарказ" in instruction
    assert "мат" in instruction
    assert "без пафоса" in instruction
    assert philosophy["include_capabilities"] is False
    assert philosophy["use_chat_context"] is False


def test_functionality_mode_knows_upupa_is_new_to_running_a_channel():
    from prompts.channel import POST_CONTENT_MODES

    functionality = next(mode for mode in POST_CONTENT_MODES if mode["name"] == "functionality")
    instruction = functionality["instruction"].casefold()

    assert "впервые ведёшь" in instruction
    assert "канал" in instruction
    assert "выругаться" in instruction


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
    assert "впервые ведёшь" in prompt.casefold()


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


def test_external_sources_include_batya_muhtar_and_kapibara_channels():
    from features.channel.service import EXTERNAL_COMMENT_SOURCES

    by_channel = {source["channel"]: source for source in EXTERNAL_COMMENT_SOURCES}
    assert "lukeimyourmouth" in by_channel
    assert by_channel["lukeimyourmouth"]["allow_batya_reference"] is True
    assert "батя" in by_channel["lukeimyourmouth"]["description"]
    assert by_channel["muhtarboodka"]["owner"] == "Мухтар"
    assert "Мухтар" in by_channel["muhtarboodka"]["description"]
    assert "kapibara_fen" in by_channel


def test_public_feed_parser_keeps_text_and_photo_posts():
    from features.channel.batya_source import _parse_public_feed

    html = """
    <div class="tgme_widget_message" data-post="muhtarboodka/101">
      <div class="tgme_widget_message_text">первый <b>пост</b></div>
    </div>
    <div class="tgme_widget_message" data-post="muhtarboodka/102">
      <a class="tgme_widget_message_photo_wrap" style="background-image:url('https://cdn.example/photo.jpg')"></a>
      <div class="tgme_widget_message_text">второй<br>пост</div>
    </div>
    <div class="tgme_widget_message" data-post="muhtarboodka/103">
      <a class="tgme_widget_message_photo_wrap" style="background-image:url('https://cdn.example/only-photo.jpg')"></a>
    </div>
    <div class="tgme_widget_message" data-post="muhtarboodka/104"></div>
    <div class="tgme_widget_message" data-post="other/1">
      <div class="tgme_widget_message_text">чужое</div>
    </div>
    """

    posts = _parse_public_feed(html, channel="muhtarboodka")

    assert posts == [
        {
            "message_id": 101,
            "url": "https://t.me/muhtarboodka/101",
            "text": "первый пост",
        },
        {
            "message_id": 102,
            "url": "https://t.me/muhtarboodka/102",
            "text": "второй пост",
            "image_url": "https://cdn.example/photo.jpg",
        },
        {
            "message_id": 103,
            "url": "https://t.me/muhtarboodka/103",
            "text": "",
            "image_url": "https://cdn.example/only-photo.jpg",
        },
    ]


def test_external_comment_prefers_longer_reactions_and_keeps_source_policy():
    from features.channel.service import (
        _pick_uncommented_external_post,
        _validate_external_comment,
    )

    source_posts = [
        {"url": "https://t.me/muhtarboodka/101", "text": "старое"},
        {"url": "https://t.me/muhtarboodka/102", "text": "новое"},
        {"url": "https://t.me/muhtarboodka/103", "text": "", "image_url": "https://cdn.example/photo.jpg"},
    ]
    published = [
        {"external_source_url": "https://t.me/muhtarboodka/101", "text": "что-то"},
        {"external_source_url": "https://t.me/muhtarboodka/102", "text": "что-то ещё"},
    ]

    picked = _pick_uncommented_external_post(source_posts, published)
    assert picked["url"] == "https://t.me/muhtarboodka/103"
    assert _validate_external_comment("дебил") is None
    assert _validate_external_comment("а б в г д е ё ж з и") is None
    assert _validate_external_comment("а б в г д е ё ж з и й к л м") is None
    assert "многословный" in _validate_external_comment("а б в г д е ё ж з и й к л м н")
    assert _validate_external_comment("") == "пустой ответ"
    assert _validate_external_comment("https://t.me/muhtarboodka/102 дебил") == "модель сама добавила Telegram-ссылку"
    assert "легенду про батю" in _validate_external_comment("батя ну ты чего")
    assert _validate_external_comment("батя ну ты чего", allow_batya_mention=True) is None


def test_external_prompt_knows_muhtar_owns_muhtarboodka():
    from features.channel.service import EXTERNAL_COMMENT_SOURCES, _build_external_prompt

    source = next(source for source in EXTERNAL_COMMENT_SOURCES if source["channel"] == "muhtarboodka")
    prompt = _build_external_prompt(source, {"text": "тест", "url": "https://t.me/muhtarboodka/1"})

    assert "@muhtarboodka" in prompt
    assert "Мухтар" in prompt
    assert "тест" in prompt
    assert "5–10 слов" in prompt


def test_batya_external_prompt_can_address_him_and_include_photo_description():
    from features.channel.service import EXTERNAL_COMMENT_SOURCES, _build_external_prompt

    source = next(source for source in EXTERNAL_COMMENT_SOURCES if source["channel"] == "lukeimyourmouth")
    prompt = _build_external_prompt(
        source,
        {"text": "", "url": "https://t.me/lukeimyourmouth/1", "image_url": "https://cdn.example/photo.jpg"},
        image_description="На фото мужчина держит огромную кружку и смотрит в окно.",
    )

    assert "батя" in prompt.casefold()
    assert "На фото мужчина держит огромную кружку" in prompt


def test_external_prompt_does_not_call_unrelated_source_batya():
    from features.channel.service import EXTERNAL_COMMENT_SOURCES, _build_external_prompt

    source = next(source for source in EXTERNAL_COMMENT_SOURCES if source["channel"] == "kapibara_fen")
    prompt = _build_external_prompt(source, {"text": "капибара", "url": "https://t.me/kapibara_fen/1"})

    assert "этот канал ведёт твой батя" not in prompt.casefold()


def test_external_comment_text_is_prefixed_with_source_url(monkeypatch):
    import asyncio
    from features.channel import service

    source = {"channel": "muhtarboodka", "description": "@muhtarboodka; этот канал ведёт Мухтар", "owner": "Мухтар", "allow_batya_reference": False}
    source_post = {"url": "https://t.me/muhtarboodka/1", "text": "исходник"}

    async def fake_fetch_public_posts(channel, limit):
        return [source_post]

    async def fake_generate(prompt, chat_id):
        return "ну допустим"

    monkeypatch.setattr(service, "EXTERNAL_COMMENT_SOURCES", (source,))
    monkeypatch.setattr(service, "fetch_public_posts", fake_fetch_public_posts)
    monkeypatch.setattr("AI.summarize._generate_with_active_model", fake_generate)

    result = asyncio.run(service._try_generate_external_comment([], []))

    assert result is not None
    text, metadata = result
    assert text == "https://t.me/muhtarboodka/1\n\nну допустим"
    assert metadata["external_source_channel"] == "@muhtarboodka"


def test_image_plan_parser_requires_two_tagged_lines():
    from features.channel.service import _parse_image_plan

    assert _parse_image_plan("КАРТИНКА: удод в тазу\nПОДПИСЬ: я дома") == ("удод в тазу", "я дома")
    assert _parse_image_plan("удод в тазу") is None


def test_image_plan_validation_rejects_long_or_batya_caption():
    from features.channel.service import _validate_image_plan

    assert _validate_image_plan("удод сидит в эмалированном тазу", "я дома", []) is None
    assert "батю" in _validate_image_plan("удод сидит рядом с батей на кухне", "я дома", [])
    assert "батю" in _validate_image_plan("удод сидит в эмалированном тазу", "батя привет", [])


def test_image_post_has_cooldown_even_if_rng_wants_it():
    from features.channel.service import IMAGE_POST_COOLDOWN_POSTS, _should_try_image_post

    class ZeroRng:
        @staticmethod
        def random():
            return 0.0

    recent = [{"post_kind": "normal"} for _ in range(IMAGE_POST_COOLDOWN_POSTS - 1)]
    recent.append({"post_kind": "image"})
    assert _should_try_image_post(recent, rng=ZeroRng()) is False

    clean_history = [{"post_kind": "normal"} for _ in range(IMAGE_POST_COOLDOWN_POSTS)]
    assert _should_try_image_post(clean_history, rng=ZeroRng()) is True


def test_image_prompt_requires_one_visual_idea_and_short_caption():
    from prompts.channel import CHANNEL_IMAGE_POST_PROMPT

    lowered = CHANNEL_IMAGE_POST_PROMPT.casefold()
    assert "одну визуально понятную картинку" in lowered
    assert "предпочтительно 1–6 слов" in lowered
    assert "жёсткий максимум — 8 слов" in lowered


def test_image_generation_uses_gigachat_then_pollinations(monkeypatch):
    import asyncio
    from features.channel import image_generation

    calls = []

    async def fake_gigachat(prompt):
        calls.append("gigachat")
        return None

    async def fake_pollinations(prompt):
        calls.append("pollinations")
        return b"png"

    monkeypatch.setattr(image_generation, "_generate_with_gigachat", fake_gigachat)
    monkeypatch.setattr(image_generation, "_generate_with_pollinations", fake_pollinations)

    image, provider = asyncio.run(image_generation.generate_channel_image("удод в тазу"))

    assert image == b"png"
    assert provider == "pollinations"
    assert calls == ["gigachat", "pollinations"]


def test_image_generation_stops_after_gigachat_success(monkeypatch):
    import asyncio
    from features.channel import image_generation

    calls = []

    async def fake_gigachat(prompt):
        calls.append("gigachat")
        return b"png"

    async def fake_pollinations(prompt):
        calls.append("pollinations")
        return b"other"

    monkeypatch.setattr(image_generation, "_generate_with_gigachat", fake_gigachat)
    monkeypatch.setattr(image_generation, "_generate_with_pollinations", fake_pollinations)

    image, provider = asyncio.run(image_generation.generate_channel_image("удод в тазу"))

    assert image == b"png"
    assert provider == "gigachat"
    assert calls == ["gigachat"]
