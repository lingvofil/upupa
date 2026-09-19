import asyncio
from unittest.mock import AsyncMock

from tests import test_smoke_imports  # noqa: F401  (fake env + heavy-library mocks)


def test_party_stop_configurator_drives_stable_entrypoint():
    from games import crocodile_party_controls as party_controls

    original = party_controls.get_stop_active_party_handler()
    configured = AsyncMock(return_value=(True, "configured"))

    try:
        party_controls.configure_stop_active_party_handler(configured)
        result = asyncio.run(party_controls._stop_active_party("-42", 123))
    finally:
        party_controls.configure_stop_active_party_handler(original)

    assert result == (True, "configured")
    configured.assert_awaited_once_with("-42", 123)


def test_party_stop_composes_admin_wrapper_without_mutating_entrypoint():
    from games import crocodile_admin_controls as admin
    from games import crocodile_runtime as runtime

    downstream = AsyncMock(return_value=(True, "delegated"))
    handler = runtime._compose_party_stop_handler(
        downstream,
        admin.stop_active_party_with_admin,
    )

    result = asyncio.run(handler("-42", admin.ADMIN_ID + 1))

    assert result == (True, "delegated")
    downstream.assert_awaited_once_with("-42", admin.ADMIN_ID + 1)
