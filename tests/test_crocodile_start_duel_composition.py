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


def test_start_duel_configurator_drives_stable_entrypoint():
    from games import crocodile_modes as modes

    message = _message()
    original = modes.get_start_duel_handler()
    stable_entrypoint = modes.start_duel
    configured = AsyncMock(return_value="configured-result")

    try:
        modes.configure_start_duel_handler(configured)
        assert modes.get_start_duel_handler() is configured
        assert modes.get_default_start_duel_handler() is not configured
        assert modes.start_duel is stable_entrypoint
        result = asyncio.run(modes.start_duel(message))
    finally:
        modes.configure_start_duel_handler(original)

    assert result == "configured-result"
    configured.assert_awaited_once_with(message)


def test_start_duel_party_guard_delegates_to_explicit_next_handler(monkeypatch):
    from games import crocodile_party_controls as party_controls

    message = _message()
    downstream = AsyncMock(return_value="started")
    monkeypatch.setattr(party_controls, "_reverse_active", lambda _chat_id: False)

    result = asyncio.run(
        party_controls.start_duel_with_party_controls(message, downstream)
    )

    assert result is None
    downstream.assert_awaited_once_with(message)


def test_start_duel_party_guard_blocks_when_reverse_is_active(monkeypatch):
    from games import crocodile_party_controls as party_controls

    message = _message()
    downstream = AsyncMock()
    monkeypatch.setattr(party_controls, "_reverse_active", lambda _chat_id: True)

    result = asyncio.run(
        party_controls.start_duel_with_party_controls(message, downstream)
    )

    assert result is None
    downstream.assert_not_awaited()
    message.answer.assert_awaited_once_with(
        "Сначала закончите текущего кракадила наоборот."
    )


def test_start_duel_composition_is_owned_by_runtime():
    runtime_source = (ROOT / "games" / "crocodile_runtime.py").read_text(
        encoding="utf-8"
    )
    modes_source = (ROOT / "games" / "crocodile_modes.py").read_text(
        encoding="utf-8"
    )
    party_source = (ROOT / "games" / "crocodile_party_controls.py").read_text(
        encoding="utf-8"
    )

    assert "def get_default_start_duel_handler(" in modes_source
    assert "def get_start_duel_handler(" in modes_source
    assert "def configure_start_duel_handler(" in modes_source
    assert "_original_start_duel" not in party_source
    assert "crocodile_modes.start_duel =" not in party_source
    assert "start_duel_with_party_controls(message, next_handler)" in party_source

    composition = runtime_source.index(
        "duel_start_handler = _compose_start_duel("
    )
    base = runtime_source.index(
        "crocodile_modes.get_default_start_duel_handler()",
        composition,
    )
    party = runtime_source.index(
        "party_controls.start_duel_with_party_controls,",
        base,
    )
    wiring = runtime_source.index(
        "crocodile_modes.configure_start_duel_handler(",
        party,
    )

    assert runtime_source.count(
        "crocodile_modes.configure_start_duel_handler("
    ) == 1
    assert composition < base < party < wiring
