import asyncio

import features.interactive_settings as interactive_settings
import services.holidays as holidays


def _button_texts(markup):
    return [button.text for row in markup.inline_keyboard for button in row]


def test_holiday_broadcast_chat_ids_only_include_explicitly_enabled(monkeypatch):
    monkeypatch.setattr(
        holidays,
        "chat_settings",
        {
            "-1001": {},
            "-1002": {"holidays_enabled": False},
            "-1003": {"holidays_enabled": True},
            "not-a-chat": {"holidays_enabled": True},
        },
    )

    assert holidays.get_holiday_broadcast_chat_ids() == [-1003]


def test_holiday_setting_is_off_by_default(monkeypatch):
    monkeypatch.setattr(interactive_settings, "chat_settings", {"-1001": {}})

    text, markup = asyncio.run(interactive_settings.get_main_settings_markup("-1001"))

    assert "📅 *Празднеки:* Выкл. ❌" in text
    assert "Вкл. празднеки" in _button_texts(markup)


def test_holiday_setting_shows_disable_action_when_enabled(monkeypatch):
    monkeypatch.setattr(
        interactive_settings,
        "chat_settings",
        {"-1001": {"holidays_enabled": True}},
    )

    text, markup = asyncio.run(interactive_settings.get_main_settings_markup("-1001"))

    assert "📅 *Празднеки:* Вкл. ✅" in text
    assert "Выкл. празднеки" in _button_texts(markup)
