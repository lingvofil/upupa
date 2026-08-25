import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from core.settings import ADMIN_ID
from features.interactive_settings import has_settings_permission
from features.world.permissions import is_chat_admin


def _run(coro):
    return asyncio.run(coro)


class NeverCalledBot:
    async def get_chat_member(self, chat_id, user_id):
        raise AssertionError("Telegram admin lookup must not be required for bot owner")


def test_bot_owner_has_access_to_all_interactive_settings(monkeypatch):
    import features.interactive_settings as interactive_settings

    monkeypatch.setattr(interactive_settings, "bot", NeverCalledBot())

    assert _run(has_settings_permission(-1001, ADMIN_ID)) is True


def test_bot_owner_has_world_admin_permission_without_chat_admin_status():
    assert _run(is_chat_admin(NeverCalledBot(), -1001, ADMIN_ID)) is True


def test_regular_world_member_still_has_no_admin_permission():
    bot = SimpleNamespace(
        get_chat_member=AsyncMock(return_value=SimpleNamespace(status="member"))
    )

    assert _run(is_chat_admin(bot, -1001, ADMIN_ID + 1)) is False
