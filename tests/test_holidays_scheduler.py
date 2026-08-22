import asyncio

import services.holidays as holidays


def test_send_daily_holidays_uses_preloaded_holidays(monkeypatch):
    sample = [
        holidays.Holiday(
            title="Тестовый праздник",
            category="Тест",
            description="Описание",
            url="https://example.com/holiday",
        )
    ]

    async def fail_fetch():
        raise AssertionError("fetch_today_holidays must not be called when holidays are preloaded")

    async def fake_generate(items, chat_id):
        assert items is sample
        assert chat_id == -1001
        return {"Тестовый праздник": "Стилизованное описание"}

    class BotStub:
        def __init__(self):
            self.calls = []

        async def send_message(self, *args, **kwargs):
            self.calls.append((args, kwargs))

    monkeypatch.setattr(holidays, "fetch_today_holidays", fail_fetch)
    monkeypatch.setattr(holidays, "generate_holiday_descriptions", fake_generate)
    bot = BotStub()

    asyncio.run(holidays.send_daily_holidays(bot, -1001, sample))

    assert len(bot.calls) == 1
    args, kwargs = bot.calls[0]
    assert args[0] == -1001
    assert "Стилизованное описание" in args[1]
    assert kwargs["parse_mode"] == "HTML"
