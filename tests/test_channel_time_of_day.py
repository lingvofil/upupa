from datetime import datetime, time

from tests import test_smoke_imports  # noqa: F401  (env + heavy-library mocks)


def test_channel_scheduler_has_no_quiet_hours():
    from features.channel.scheduler import DAY_END, DAY_START

    assert DAY_START == time(0, 0)
    assert DAY_END == time(23, 59, 59)


def test_production_slots_can_land_at_night():
    from features.channel.scheduler import MOSCOW_TZ, _pick_daily_slots

    class NightRng:
        @staticmethod
        def randint(low, high):
            return low

        @staticmethod
        def random():
            return 1.0

        @staticmethod
        def choice(items):
            return items[0]

    day = datetime(2026, 8, 25).date()
    slots = _pick_daily_slots(day, count=1, rng=NightRng())

    assert slots[0].astimezone(MOSCOW_TZ).hour == 0


def test_daypart_prompt_changes_with_moscow_time_without_forcing_time_mentions():
    from features.channel.mood import MOSCOW_TZ, daypart_prompt

    night = daypart_prompt(MOSCOW_TZ.localize(datetime(2026, 8, 25, 2, 0))).casefold()
    morning = daypart_prompt(MOSCOW_TZ.localize(datetime(2026, 8, 25, 8, 0))).casefold()
    day = daypart_prompt(MOSCOW_TZ.localize(datetime(2026, 8, 25, 14, 0))).casefold()
    evening = daypart_prompt(MOSCOW_TZ.localize(datetime(2026, 8, 25, 21, 0))).casefold()

    assert "ночь" in night
    assert "утро" in morning
    assert "день" in day
    assert "вечер" in evening
    assert "не обязан упоминать" in night


def test_mood_prompt_includes_daypart_context():
    from features.channel.mood import MOSCOW_TZ, mood_prompt

    prompt = mood_prompt(
        {"name": "neutral", "posts_left": 3},
        now=MOSCOW_TZ.localize(datetime(2026, 8, 25, 3, 0)),
    ).casefold()

    assert "ровное настроение" in prompt
    assert "сейчас ночь" in prompt
