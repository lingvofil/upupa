import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from tests import test_smoke_imports  # noqa: F401  (fake env + heavy-library mocks)
from games import crocodile


def _callback(data: str):
    return SimpleNamespace(data=data)


def test_callback_handler_configurator_drives_stable_entrypoint():
    callback = _callback("configured")
    original = crocodile.get_callback_handler()
    configured = AsyncMock(return_value="configured-result")

    try:
        crocodile.configure_callback_handler(configured)
        result = asyncio.run(crocodile.handle_callback(callback))
    finally:
        crocodile.configure_callback_handler(original)

    assert result == "configured-result"
    configured.assert_awaited_once_with(callback)


def test_ui_callback_delegates_non_ui_actions_once():
    from games import crocodile_runtime as runtime
    from games import crocodile_ui_enhancements as ui

    callback = _callback("cr_n_-42")
    downstream = AsyncMock(return_value="handled-downstream")
    handler = runtime._compose_callback_handler(
        downstream,
        ui.handle_crocodile_callback_with_ui,
    )

    result = asyncio.run(handler(callback))

    assert result == "handled-downstream"
    downstream.assert_awaited_once_with(callback)


def test_ui_like_callback_is_intercepted_before_downstream(monkeypatch):
    from games import crocodile_runtime as runtime
    from games import crocodile_ui_enhancements as ui

    callback = _callback("cr_like_token-123")
    downstream = AsyncMock()
    attributed_like = AsyncMock(return_value="liked")
    monkeypatch.setattr(ui, "_handle_attributed_like", attributed_like)
    handler = runtime._compose_callback_handler(
        downstream,
        ui.handle_crocodile_callback_with_ui,
    )

    result = asyncio.run(handler(callback))

    assert result == "liked"
    attributed_like.assert_awaited_once_with(callback, "token-123")
    downstream.assert_not_awaited()
