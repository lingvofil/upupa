import asyncio
import json
import time
from pathlib import Path
from types import SimpleNamespace

from tests import test_smoke_imports  # noqa: F401

from app.bootstrap import REQUIRED_BACKGROUND_TASKS
from app.readiness import PollingHealth, ReadinessServer
import core.settings as settings
import services.weather as weather


def test_weather_api_failures_are_not_rendered_as_zero_temperatures(monkeypatch):
    async def failed_weather(_city):
        return None, "ошибка API: 401"

    monkeypatch.setattr(weather, "get_weather", failed_weather)

    report = asyncio.run(weather.format_weather_report())

    assert report == (
        "Не удалось получить погоду: OpenWeather не вернул данные ни по одному городу."
    )
    assert "Москва 0" not in report
    assert "Архангельск 0" not in report


def test_weather_report_keeps_real_zero_and_marks_only_failed_cities(monkeypatch):
    readings = {
        "Moscow": (0, "ясно"),
        "Arkhangelsk": (None, "ошибка API: 503"),
    }

    async def fake_weather(city):
        return readings.get(city, (5, "облачно"))

    monkeypatch.setattr(weather, "get_weather", fake_weather)

    report = asyncio.run(weather.format_weather_report())

    assert "Москва 0" in report
    assert "Архангельск 0" not in report
    assert "Нет данных: Архангельск" in report


def test_weather_uses_https_and_runtime_setting_only():
    weather_source = Path(weather.__file__).read_text(encoding="utf-8")
    settings_source = Path(settings.__file__).read_text(encoding="utf-8")

    assert "https://api.openweathermap.org" in weather_source
    assert "http://api.openweathermap.org" not in weather_source
    assert "from core.settings import OPENWEATHER_API_KEY" in weather_source
    assert "OPENWEATHER_API_KEY = os.getenv" in settings_source
    assert "get_mock_weather" not in weather_source


def test_crocodile_socket_recovery_makes_application_readiness_unhealthy():
    assert "crocodile-socket-server" in REQUIRED_BACKGROUND_TASKS

    supervisor = SimpleNamespace(
        task_names=tuple(REQUIRED_BACKGROUND_TASKS),
        recovering_task_names=("crocodile-socket-server",),
    )
    server = ReadinessServer(
        PollingHealth(last_success=time.monotonic()),
        supervisor,
        lambda: None,
        REQUIRED_BACKGROUND_TASKS,
    )

    response = asyncio.run(server.handle(None))
    payload = json.loads(response.text)

    assert response.status == 503
    assert payload["ok"] is False
    assert payload["checks"]["background_tasks"] is False
    assert payload["recovering_tasks"] == ["crocodile-socket-server"]
