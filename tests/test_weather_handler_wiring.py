"""Regression tests for weather command handler wiring."""

import ast
from pathlib import Path


MEDIA_SEARCH = Path(__file__).resolve().parents[1] / "handlers" / "media_search.py"


def test_weekly_weather_handler_delegates_to_service_without_self_recursion():
    tree = ast.parse(MEDIA_SEARCH.read_text(encoding="utf-8"))

    weather_import = next(
        node
        for node in tree.body
        if isinstance(node, ast.ImportFrom) and node.module == "services.weather"
    )
    weekly_import = next(
        alias for alias in weather_import.names if alias.name == "handle_weekly_forecast_command"
    )

    assert weekly_import.asname == "handle_weekly_forecast_service"

    handler = next(
        node
        for node in tree.body
        if isinstance(node, ast.AsyncFunctionDef)
        and node.name == "handle_weekly_forecast_command"
    )
    awaited_call = handler.body[0].value

    assert isinstance(awaited_call, ast.Await)
    assert isinstance(awaited_call.value, ast.Call)
    assert isinstance(awaited_call.value.func, ast.Name)
    assert awaited_call.value.func.id == "handle_weekly_forecast_service"
