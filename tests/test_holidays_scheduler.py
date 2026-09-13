import asyncio
import logging

import services.holidays as holidays


def _holiday(title="Тестовый праздник", description="Описание"):
    return holidays.Holiday(
        title=title,
        category="Тест",
        description=description,
        url="https://example.com/holiday",
    )


def test_send_daily_holidays_uses_preloaded_holidays(monkeypatch):
    sample = [_holiday()]

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


def test_extract_json_list_accepts_wrapped_json_object():
    response = (
        "Вот результат:\n"
        '```json\n{"holidays": [{"id": 1, "description": "Стилизовано"}]}\n```\n'
        "Конец."
    )

    assert holidays._extract_json_list(response) == [
        {"id": 1, "description": "Стилизовано"}
    ]


def test_generate_holiday_descriptions_uses_current_chat_prompt_and_ids(monkeypatch):
    sample = [_holiday("День тестировщика")]
    chat_id = -1001
    monkeypatch.setitem(
        holidays.chat_settings,
        str(chat_id),
        {
            "dialog_enabled": True,
            "reactions_enabled": True,
            "prompt": "ГОВОРИ КАК ПИРАТ И ДОБАВЛЯЙ МОРСКУЮ ЛЕКСИКУ.",
            "prompt_name": "пират",
            "prompt_source": "user",
            "active_model": "gemini",
        },
    )

    async def fake_generate(prompt, generated_chat_id):
        assert generated_chat_id == str(chat_id)
        assert "ГОВОРИ КАК ПИРАТ" in prompt
        assert "от лица 'пират'" in prompt
        assert '"id": 1' in prompt
        return '[{"id": 1, "description": "Йо-хо-хо, сегодня тестируем всё, что шевелится."}]'

    monkeypatch.setattr(holidays, "generate_simple_response", fake_generate)

    result = asyncio.run(holidays.generate_holiday_descriptions(sample, chat_id))

    assert result == {
        "День тестировщика": "Йо-хо-хо, сегодня тестируем всё, что шевелится."
    }


def test_generate_holiday_descriptions_matches_normalized_titles(monkeypatch):
    sample = [_holiday("День ёжика — праздник!")]

    async def fake_generate(_prompt, _chat_id):
        return '[{"title": "день ежика праздник", "description": "Колючий, но стилизованный текст."}]'

    monkeypatch.setattr(holidays, "generate_simple_response", fake_generate)

    result = asyncio.run(holidays.generate_holiday_descriptions(sample, -1002))

    assert result == {
        "День ёжика — праздник!": "Колючий, но стилизованный текст."
    }


def test_generate_holiday_descriptions_falls_back_by_position(monkeypatch):
    sample = [
        _holiday("Первый праздник"),
        _holiday("Второй праздник"),
    ]

    async def fake_generate(_prompt, _chat_id):
        return (
            '[{"title": "Первый праздник", "description": "Первый стиль"}, '
            '{"title": "Название слегка переписали", "description": "Второй стиль"}]'
        )

    monkeypatch.setattr(holidays, "generate_simple_response", fake_generate)

    result = asyncio.run(holidays.generate_holiday_descriptions(sample, -1003))

    assert result == {
        "Первый праздник": "Первый стиль",
        "Второй праздник": "Второй стиль",
    }


def test_generate_holiday_descriptions_logs_calend_fallback(monkeypatch, caplog):
    sample = [_holiday()]

    async def fake_generate(_prompt, _chat_id):
        return "Я решил ответить обычным текстом вместо JSON."

    monkeypatch.setattr(holidays, "generate_simple_response", fake_generate)

    with caplog.at_level(logging.WARNING):
        result = asyncio.run(holidays.generate_holiday_descriptions(sample, -1004))

    assert result == {}
    assert "using calend.ru descriptions" in caplog.text
    assert "does not contain a JSON holiday list" in caplog.text
