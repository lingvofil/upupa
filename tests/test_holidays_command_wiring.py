"""Regression tests for the plain-text holiday command wiring."""

import ast
from pathlib import Path


BASIC = Path(__file__).resolve().parents[1] / "handlers" / "basic.py"


def _holiday_handler(tree):
    return next(
        node
        for node in tree.body
        if isinstance(node, ast.AsyncFunctionDef)
        and node.name == "holidays_command_handler"
    )


def test_holidays_command_handler_delegates_to_service():
    tree = ast.parse(BASIC.read_text(encoding="utf-8"))

    holidays_import = next(
        node
        for node in tree.body
        if isinstance(node, ast.ImportFrom) and node.module == "services.holidays"
    )
    command_import = next(
        alias
        for alias in holidays_import.names
        if alias.name == "process_holidays_command"
    )

    assert command_import.asname == "process_holidays_service"

    handler = _holiday_handler(tree)
    awaited_call = handler.body[0].value

    assert isinstance(awaited_call, ast.Await)
    assert isinstance(awaited_call.value, ast.Call)
    assert isinstance(awaited_call.value.func, ast.Name)
    assert awaited_call.value.func.id == "process_holidays_service"


def test_holidays_command_handler_matches_plain_text_command():
    tree = ast.parse(BASIC.read_text(encoding="utf-8"))
    handler = _holiday_handler(tree)

    constants = [
        node.value
        for decorator in handler.decorator_list
        for node in ast.walk(decorator)
        if isinstance(node, ast.Constant)
    ]

    assert "праздники" in constants
