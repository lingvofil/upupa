import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from tests import test_smoke_imports  # noqa: F401  (fake env + heavy-library mocks)


def _callback(data: str):
    return SimpleNamespace(data=data)


def test_duel_callback_configurator_drives_stable_entrypoint():
    from games import crocodile_modes

    callback = _callback("configured")
    original = crocodile_modes.get_duel_callback_handler()
    configured = AsyncMock(return_value="configured-result")

    try:
        crocodile_modes.configure_duel_callback_handler(configured)
        result = asyncio.run(crocodile_modes.handle_duel_callback(callback))
    finally:
        crocodile_modes.configure_duel_callback_handler(original)

    assert result == "configured-result"
    configured.assert_awaited_once_with(callback)


def test_duel_callback_composes_admin_wrapper_without_mutating_entrypoint():
    from games import crocodile_admin_controls as admin
    from games import crocodile_runtime as runtime

    callback = _callback("cduel_join_-42")
    callback.from_user = SimpleNamespace(id=admin.ADMIN_ID + 1)
    downstream = AsyncMock(return_value="delegated")
    handler = runtime._compose_callback_handler(
        downstream,
        admin.handle_duel_callback_with_admin,
    )

    result = asyncio.run(handler(callback))

    assert result == "delegated"
    downstream.assert_awaited_once_with(callback)
