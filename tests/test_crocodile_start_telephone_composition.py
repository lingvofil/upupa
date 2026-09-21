import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from tests import test_smoke_imports  # noqa: F401  (fake env + heavy-library mocks)


ROOT = Path(__file__).resolve().parents[1]


def _message(chat_id: int = -42):
    return SimpleNamespace(
        chat=SimpleNamespace(id=chat_id),
        from_user=SimpleNamespace(id=101, full_name="Игрок"),
        answer=AsyncMock(),
    )


def test_start_telephone_configurator_drives_stable_entrypoint():
    from games import crocodile_modes as modes

    message = _message()
    original = modes.get_start_telephone_handler()
    stable_entrypoint = modes.start_telephone
    configured = AsyncMock(return_value="configured-result")

    try:
        modes.configure_start_telephone_handler(configured)
        assert modes.get_start_telephone_handler() is configured
        assert modes.get_default_start_telephone_handler() is not configured
        assert modes.start_telephone is stable_entrypoint
        result = asyncio.run(modes.start_telephone(message))
    finally:
        modes.configure_start_telephone_handler(original)

    assert result == "configured-result"
    configured.assert_awaited_once_with(message)


def test_start_telephone_pipeline_preserves_wrapper_order():
    from games import crocodile_runtime as runtime

    calls = []

    async def base(message):
        calls.append(("base", message))
        return "started"

    def wrapper(name):
        async def wrapped(message, next_handler):
            calls.append((f"{name}-before", message))
            result = await next_handler(message)
            calls.append((f"{name}-after", message))
            return result

        return wrapped

    message = _message()
    handler = runtime._compose_start_telephone(
        base,
        wrapper("party"),
        wrapper("roles"),
        wrapper("announcements"),
    )

    result = asyncio.run(handler(message))

    assert result == "started"
    assert [name for name, _message_obj in calls] == [
        "announcements-before",
        "roles-before",
        "party-before",
        "base",
        "party-after",
        "roles-after",
        "announcements-after",
    ]


def test_start_telephone_composition_is_owned_by_runtime():
    runtime_source = (ROOT / "games" / "crocodile_runtime.py").read_text(
        encoding="utf-8"
    )
    modes_source = (ROOT / "games" / "crocodile_modes.py").read_text(
        encoding="utf-8"
    )
    party_source = (ROOT / "games" / "crocodile_party_controls.py").read_text(
        encoding="utf-8"
    )
    roles_source = (ROOT / "games" / "crocodile_telephone_roles.py").read_text(
        encoding="utf-8"
    )
    announcements_source = (
        ROOT / "games" / "crocodile_telephone_role_announcements.py"
    ).read_text(encoding="utf-8")

    assert "def get_default_start_telephone_handler(" in modes_source
    assert "def get_start_telephone_handler(" in modes_source
    assert "def configure_start_telephone_handler(" in modes_source

    assert "_original_start_telephone" not in party_source
    assert "_original_start_telephone" not in roles_source
    assert "_original_start_telephone" not in announcements_source
    for source in (party_source, roles_source, announcements_source):
        assert "crocodile_modes.start_telephone =" not in source

    assert "start_telephone_with_party_controls(message, next_handler)" in party_source
    assert "start_telephone_with_roles(message, next_handler)" in roles_source
    assert (
        "start_telephone_with_role_announcement(message, next_handler)"
        in announcements_source
    )

    composition = runtime_source.index(
        "telephone_start_handler = _compose_start_telephone("
    )
    base = runtime_source.index(
        "crocodile_modes.get_default_start_telephone_handler()",
        composition,
    )
    party = runtime_source.index(
        "party_controls.start_telephone_with_party_controls,",
        base,
    )
    roles = runtime_source.index(
        "start_telephone_with_roles,",
        party,
    )
    announcements = runtime_source.index(
        "start_telephone_with_role_announcement,",
        roles,
    )
    wiring = runtime_source.index(
        "crocodile_modes.configure_start_telephone_handler(",
        announcements,
    )

    assert runtime_source.count(
        "crocodile_modes.configure_start_telephone_handler("
    ) == 1
    assert composition < base < party < roles < announcements < wiring
