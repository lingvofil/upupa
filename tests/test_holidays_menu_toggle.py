import asyncio
from types import SimpleNamespace

import features.interactive_settings as interactive_settings


def test_holiday_toggle_enables_setting(monkeypatch):
    chat_id = "-1001"
    monkeypatch.setattr(interactive_settings, "chat_settings", {chat_id: {}})
    monkeypatch.setattr(interactive_settings, "save_chat_settings", lambda: None)

    async def allow_settings(*args, **kwargs):
        return True

    monkeypatch.setattr(interactive_settings, "has_settings_permission", allow_settings)

    class MessageStub:
        def __init__(self):
            self.chat = SimpleNamespace(id=int(chat_id))

        async def edit_text(self, *args, **kwargs):
            return None

    class QueryStub:
        def __init__(self):
            self.message = MessageStub()
            self.from_user = SimpleNamespace(id=1)
            self.data = "settings:toggle:holidays"
            self.answers = []

        async def answer(self, text=None, **kwargs):
            self.answers.append(text)

    query = QueryStub()
    asyncio.run(interactive_settings.handle_settings_callback(query))

    assert interactive_settings.chat_settings[chat_id]["holidays_enabled"] is True
    assert query.answers == ["Настройка сохранена"]
