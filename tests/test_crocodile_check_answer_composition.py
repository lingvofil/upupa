import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from tests import test_smoke_imports  # noqa: F401  (fake env + heavy-library mocks)
from games import crocodile


ROOT = Path(__file__).resolve().parents[1]


def _source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _message(user_id: int, text: str):
    return SimpleNamespace(
        chat=SimpleNamespace(id=-42),
        message_id=777,
        text=text,
        from_user=SimpleNamespace(id=user_id, full_name=f"Игрок {user_id}"),
    )


def _check_answer_assignments(path: Path) -> list[int]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    lines = []
    for node in ast.walk(tree):
        targets = []
        if isinstance(node, (ast.Assign, ast.AugAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        for target in targets:
            if (
                isinstance(target, ast.Attribute)
                and target.attr == "check_answer"
                and isinstance(target.value, ast.Name)
                and target.value.id == "crocodile"
            ):
                lines.append(node.lineno)
    return lines


def test_ui_answer_context_wraps_downstream_and_resets_afterwards():
    from games import crocodile
    from games import crocodile_ui_enhancements as ui

    chat_id = "-42"
    previous = crocodile.game_sessions.pop(chat_id, None)
    crocodile.game_sessions[chat_id] = {
        "drawer_id": 1,
        "drawer_name": "Первый + Второй",
        "drawer_ids": [1, 2],
        "drawer_names": ["Первый", "Второй"],
    }
    message = SimpleNamespace(chat=SimpleNamespace(id=-42))
    observed = []

    async def downstream(current_message):
        assert current_message is message
        observed.append(ui._final_like_context.get())
        return True

    try:
        result = asyncio.run(ui.check_answer_with_like_context(message, downstream))
    finally:
        crocodile.game_sessions.pop(chat_id, None)
        if previous is not None:
            crocodile.game_sessions[chat_id] = previous

    assert result is True
    assert observed == [
        {"chat_id": chat_id, "artists": [(1, "Первый"), (2, "Второй")]}
    ]
    assert ui._final_like_context.get() is None


def test_modes_answer_wrapper_blocks_both_duo_artists(monkeypatch):
    from games import crocodile
    from games import crocodile_modes as modes

    chat_id = "-42"
    previous = crocodile.game_sessions.pop(chat_id, None)
    crocodile.game_sessions[chat_id] = {
        "word": "барсук",
        "drawer_id": 1,
        "drawer_name": "Первый + Второй",
        "drawer_ids": [1, 2],
        "drawer_names": ["Первый", "Второй"],
        "mode": "duo",
        "last_preview_bytes": b"image",
    }
    record = AsyncMock()
    monkeypatch.setattr(modes, "record_drawing", record)

    try:
        for user_id in (1, 2):
            downstream = AsyncMock(return_value=True)
            handled = asyncio.run(
                modes.check_regular_answer_with_archive(
                    _message(user_id, "это барсук"), downstream
                )
            )
            assert handled is True
            downstream.assert_not_awaited()
        record.assert_not_awaited()
    finally:
        crocodile.game_sessions.pop(chat_id, None)
        if previous is not None:
            crocodile.game_sessions[chat_id] = previous


def test_modes_answer_wrapper_archives_only_successful_guess(monkeypatch):
    from games import crocodile
    from games import crocodile_modes as modes

    chat_id = "-42"
    previous = crocodile.game_sessions.pop(chat_id, None)
    crocodile.game_sessions[chat_id] = {
        "word": "барсук",
        "drawer_id": 1,
        "drawer_name": "Первый + Второй",
        "drawer_ids": [1, 2],
        "drawer_names": ["Первый", "Второй"],
        "mode": "duo",
        "last_preview_bytes": b"image",
    }
    record = AsyncMock()
    monkeypatch.setattr(modes, "record_drawing", record)
    message = _message(3, "это барсук")

    try:
        rejected = AsyncMock(return_value=False)
        assert asyncio.run(modes.check_regular_answer_with_archive(message, rejected)) is False
        rejected.assert_awaited_once_with(message)
        record.assert_not_awaited()

        accepted = AsyncMock(return_value=True)
        assert asyncio.run(modes.check_regular_answer_with_archive(message, accepted)) is True
        accepted.assert_awaited_once_with(message)
        record.assert_awaited_once_with(
            -42,
            b"image",
            "барсук",
            ["Первый", "Второй"],
            "duo",
        )
    finally:
        crocodile.game_sessions.pop(chat_id, None)
        if previous is not None:
            crocodile.game_sessions[chat_id] = previous


def test_check_answer_handler_configurator_drives_stable_entrypoint():
    message = _message(3, "барсук")
    original = crocodile.get_check_answer_handler()
    configured = AsyncMock(return_value=True)

    try:
        crocodile.configure_check_answer_handler(configured)
        result = asyncio.run(crocodile.check_answer(message))
    finally:
        crocodile.configure_check_answer_handler(original)

    assert result is True
    configured.assert_awaited_once_with(message)


def test_runtime_owns_check_answer_entrypoint_and_wrapper_order():
    violations = []
    for path in sorted((ROOT / "games").glob("*.py")):
        relative = path.relative_to(ROOT).as_posix()
        for line in _check_answer_assignments(path):
            violations.append(f"{relative}:{line}")

    assert not violations, (
        "crocodile.check_answer нельзя заменять прямым присваиванием; "
        "используй configure_check_answer_handler(): " + ", ".join(violations)
    )

    modes_source = _source("games/crocodile_modes.py")
    assert "_original_check_answer" not in modes_source
    assert "check_regular_answer_with_archive(message, next_handler)" in modes_source

    runtime_source = _source("games/crocodile_runtime.py")
    wiring_entrypoint = "crocodile.configure_check_answer_handler("
    assert runtime_source.count(wiring_entrypoint) == 1
    assert "crocodile.check_answer =" not in runtime_source

    crocodile_source = _source("games/crocodile.py")
    assert "def get_check_answer_handler(" in crocodile_source
    assert "def configure_check_answer_handler(" in crocodile_source

    raw_capture = runtime_source.index(
        "raw_check_answer = crocodile.get_check_answer_handler()"
    )
    modes_install = runtime_source.index("configure_crocodile_modes()")
    wiring = runtime_source.index(wiring_entrypoint)
    raw_handler = runtime_source.index("raw_check_answer,", wiring)
    modes_wrapper = runtime_source.index("check_regular_answer_with_archive,", wiring)
    ui_wrapper = runtime_source.index("check_answer_with_like_context,", wiring)
    ui_install = runtime_source.index("configure_crocodile_ui_enhancements()")
    assert raw_capture < modes_install < wiring
    assert wiring < raw_handler < modes_wrapper < ui_wrapper < ui_install
