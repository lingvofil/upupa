import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from tests import test_smoke_imports  # noqa: F401  (fake env + heavy-library mocks)


def _callback(data: str):
    return SimpleNamespace(
        data=data,
        from_user=SimpleNamespace(id=101, full_name="Игрок"),
        message=SimpleNamespace(chat=SimpleNamespace(id=-42)),
        answer=AsyncMock(),
    )


def test_reverse_mode_callback_configurator_drives_stable_entrypoint():
    from games import reverse_crocodile_modes as reverse_modes

    callback = _callback("configured")
    original = reverse_modes.get_callback_handler()
    stable_entrypoint = reverse_modes.handle_callback
    configured = AsyncMock(return_value="configured-result")

    try:
        reverse_modes.configure_callback_handler(configured)
        assert reverse_modes.get_callback_handler() is configured
        assert reverse_modes.handle_callback is stable_entrypoint
        result = asyncio.run(reverse_modes.handle_callback(callback))
    finally:
        reverse_modes.configure_callback_handler(original)

    assert result == "configured-result"
    configured.assert_awaited_once_with(callback)


def test_reverse_mode_callback_composes_admin_wrapper_without_mutating_entrypoint():
    from games import crocodile_admin_controls as admin
    from games import crocodile_runtime as runtime

    callback = _callback("rcrocm_stop_reveal_-42")
    callback.from_user.id = admin.ADMIN_ID + 1
    downstream = AsyncMock(return_value="delegated")
    handler = runtime._compose_callback_handler(
        downstream,
        admin.reverse_modes_callback_with_admin,
    )

    result = asyncio.run(handler(callback))

    assert result == "delegated"
    downstream.assert_awaited_once_with(callback)
