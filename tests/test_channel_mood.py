import random

from tests import test_smoke_imports  # noqa: F401  (env + heavy-library mocks)


def test_mood_catalog_has_inertia_and_activity_centered_near_ten_posts():
    from features.channel.mood import MOODS

    assert set(MOODS) == {"neutral", "irritated", "sleepy", "thoughtful", "chaotic", "social"}
    assert sum(config["weight"] for config in MOODS.values()) == 100
    assert all(config["duration_posts"][0] >= 2 for config in MOODS.values())

    expected_daily = sum(
        config["weight"] * sum(config["daily_posts"]) / 2
        for config in MOODS.values()
    ) / 100
    assert 9.5 <= expected_daily <= 10.5


def test_mood_state_persists_and_transitions_after_its_post_budget(tmp_path, monkeypatch):
    from features.channel import mood, storage

    mood_file = tmp_path / "mood.json"
    monkeypatch.setattr(storage, "MOOD_FILE", mood_file)

    rng = random.Random(42)
    first = mood.get_current_mood(rng=rng)
    second = mood.get_current_mood(rng=random.Random(999))
    assert second == first

    storage.save_mood({"name": "neutral", "posts_left": 1, "started_at": "test"})
    next_mood, changed = mood.consume_mood_post(expected_name="neutral", rng=random.Random(7))

    assert changed is True
    assert next_mood["name"] != "neutral"
    assert next_mood["posts_left"] >= 2
    assert storage.load_mood() == next_mood


def test_moods_change_content_length_and_top_level_probabilities():
    from features.channel.mood import content_weights, external_probability, image_probability, length_weights
    from prompts.channel import POST_CONTENT_MODES, POST_LENGTH_MODES

    thoughtful = {"name": "thoughtful", "posts_left": 4}
    sleepy = {"name": "sleepy", "posts_left": 3}
    chaotic = {"name": "chaotic", "posts_left": 3}
    social = {"name": "social", "posts_left": 3}

    content_names = [mode["name"] for mode in POST_CONTENT_MODES]
    thoughtful_weights = dict(zip(content_names, content_weights(POST_CONTENT_MODES, thoughtful)))
    assert thoughtful_weights["philosophy"] > 10
    assert thoughtful_weights["philosophy"] > thoughtful_weights["absurd"]

    sleepy_weights = dict(zip(content_names, content_weights(POST_CONTENT_MODES, sleepy)))
    assert sleepy_weights["mischief"] > sleepy_weights["domestic"]
    assert sleepy_weights["mischief"] > sleepy_weights["philosophy"]

    length_names = [mode["name"] for mode in POST_LENGTH_MODES]
    thoughtful_lengths = dict(zip(length_names, length_weights(POST_LENGTH_MODES, thoughtful)))
    assert thoughtful_lengths["medium"] == 30

    assert image_probability(chaotic) == 0.25
    assert external_probability(social) == 0.22


def test_mood_prompt_is_injected_without_exposing_state_name():
    from features.channel.mood_service import _build_prompt
    from prompts.channel import POST_CONTENT_MODES, POST_LENGTH_MODES

    medium = next(mode for mode in POST_LENGTH_MODES if mode["name"] == "medium")
    philosophy = next(mode for mode in POST_CONTENT_MODES if mode["name"] == "philosophy")
    prompt = _build_prompt(
        [],
        None,
        medium,
        philosophy,
        False,
        {"name": "thoughtful", "posts_left": 5},
    ).casefold()

    assert "текущее внутреннее состояние" in prompt
    assert "задумчив" in prompt
    assert "не называй это состояние" in prompt
    assert "thoughtful" not in prompt


def test_sleepy_mood_keeps_lazy_tone_without_defaulting_to_gloom():
    from features.channel.mood import mood_prompt

    prompt = mood_prompt({"name": "sleepy", "posts_left": 3}).casefold()

    assert "не унылый" in prompt
    assert "схалтурить" in prompt
    assert "бытовую победу" in prompt
    assert "сохраняя действие" in prompt


def test_production_scheduler_targets_about_ten_but_mood_can_raise_or_lower_it():
    from features.channel.scheduler import MOSCOW_TZ, NOMINAL_POSTS_PER_DAY, _new_schedule
    from datetime import datetime

    now = MOSCOW_TZ.localize(datetime(2026, 8, 24, 10, 0))
    sleepy = _new_schedule(now, mood={"name": "sleepy", "posts_left": 3}, rng=random.Random(1))
    chaotic = _new_schedule(now, mood={"name": "chaotic", "posts_left": 3}, rng=random.Random(1))

    assert NOMINAL_POSTS_PER_DAY == 10
    assert 5 <= sleepy["target_posts"] <= 7
    assert 11 <= chaotic["target_posts"] <= 15
    assert len(sleepy["slots"]) == sleepy["target_posts"]
    assert len(chaotic["slots"]) == chaotic["target_posts"]


def test_production_scheduler_has_no_minimum_gap_and_can_make_bursts():
    from datetime import date, datetime
    from features.channel.scheduler import MOSCOW_TZ, _pick_daily_slots

    now = MOSCOW_TZ.localize(datetime(2026, 8, 24, 10, 0))
    slots = _pick_daily_slots(
        date(2026, 8, 24),
        count=7,
        now=now,
        burst_chance=1.0,
        rng=random.Random(7),
    )

    assert len(slots) == 7
    gaps = [(current - previous).total_seconds() for previous, current in zip(slots, slots[1:])]
    assert min(gaps) <= 180
