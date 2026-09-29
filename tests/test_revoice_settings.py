import asyncio

import pytest

from services.elevenlabs import ElevenLabsVoice
import features.revoice_settings as revoice_settings


CHAT_ID = "-100555"


@pytest.fixture(autouse=True)
def reset_chat_settings():
    revoice_settings.chat_settings.pop(CHAT_ID, None)
    yield
    revoice_settings.chat_settings.pop(CHAT_ID, None)


def _callbacks(markup):
    return [
        button.callback_data
        for row in markup.inline_keyboard
        for button in row
        if button.callback_data
    ]


def _texts(markup):
    return [
        button.text
        for row in markup.inline_keyboard
        for button in row
    ]


def test_default_revoice_selection_is_random():
    assert revoice_settings.get_revoice_selection_label(CHAT_ID) == "🎲 Рандом"


def test_revoice_menu_lists_all_voices_with_pagination(monkeypatch):
    voices = [
        ElevenLabsVoice(f"voice-{i:02d}", f"Voice {i:02d}")
        for i in range(12)
    ]

    async def fake_get_voices(*, force_refresh=False):
        assert force_refresh is False
        return voices

    monkeypatch.setattr(revoice_settings, "get_revoice_voices", fake_get_voices)

    text, first_page = asyncio.run(
        revoice_settings.get_revoice_settings_markup(CHAT_ID, page=0)
    )
    first_callbacks = _callbacks(first_page)

    assert "Доступно голосов: *12*" in text
    assert "Страница *1/2*" in text
    assert "settings:revoice:set:random" in first_callbacks
    assert "settings:revoice:set:voice-00" in first_callbacks
    assert "settings:revoice:set:voice-09" in first_callbacks
    assert "settings:revoice:set:voice-10" not in first_callbacks
    assert "settings:revoice:page:1" in first_callbacks

    _, second_page = asyncio.run(
        revoice_settings.get_revoice_settings_markup(CHAT_ID, page=1)
    )
    second_callbacks = _callbacks(second_page)

    assert "settings:revoice:set:voice-10" in second_callbacks
    assert "settings:revoice:set:voice-11" in second_callbacks
    assert "settings:revoice:page:0" in second_callbacks


def test_fixed_voice_selection_is_persisted_and_marked(monkeypatch):
    voices = [
        ElevenLabsVoice("voice-a", "Alpha"),
        ElevenLabsVoice("voice-b", "Bella"),
    ]
    saves = []

    async def fake_get_voices(*, force_refresh=False):
        return voices

    monkeypatch.setattr(revoice_settings, "get_revoice_voices", fake_get_voices)
    monkeypatch.setattr(
        revoice_settings,
        "save_chat_settings",
        lambda: saves.append(True),
    )

    selected = asyncio.run(
        revoice_settings.set_revoice_voice(CHAT_ID, "voice-b")
    )

    assert selected == "Bella"
    assert revoice_settings.chat_settings[CHAT_ID]["revoice_voice_id"] == "voice-b"
    assert revoice_settings.chat_settings[CHAT_ID]["revoice_voice_name"] == "Bella"
    assert saves == [True]

    _, markup = asyncio.run(
        revoice_settings.get_revoice_settings_markup(CHAT_ID)
    )
    assert "✅ Bella" in _texts(markup)


def test_random_selection_clears_fixed_voice_name(monkeypatch):
    revoice_settings.chat_settings[CHAT_ID] = {
        "revoice_voice_id": "voice-b",
        "revoice_voice_name": "Bella",
    }
    saves = []
    monkeypatch.setattr(
        revoice_settings,
        "save_chat_settings",
        lambda: saves.append(True),
    )

    selected = asyncio.run(
        revoice_settings.set_revoice_voice(CHAT_ID, "random")
    )

    assert selected == "🎲 Рандом"
    assert revoice_settings.chat_settings[CHAT_ID]["revoice_voice_id"] == "random"
    assert "revoice_voice_name" not in revoice_settings.chat_settings[CHAT_ID]
    assert saves == [True]


def test_unavailable_voice_is_not_saved(monkeypatch):
    async def fake_get_voices(*, force_refresh=False):
        return [ElevenLabsVoice("voice-a", "Alpha")]

    monkeypatch.setattr(revoice_settings, "get_revoice_voices", fake_get_voices)

    with pytest.raises(ValueError):
        asyncio.run(
            revoice_settings.set_revoice_voice(CHAT_ID, "missing-voice")
        )

    assert revoice_settings.chat_settings[CHAT_ID] == {}
