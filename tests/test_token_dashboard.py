import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from aiohttp import web

from core.settings import ADMIN_ID
from games import crocodile
from games import token_dashboard


ROOT = Path(__file__).resolve().parents[1]


def _request(*, headers=None, query=None):
    return SimpleNamespace(headers=headers or {}, query=query or {})


def test_token_dashboard_html_contains_visual_sections_and_periods():
    source = (ROOT / "tokens.html").read_text(encoding="utf-8")

    assert "<title>Упупа · Токены</title>" in source
    assert 'id="features"' in source
    assert 'id="models"' in source
    assert 'id="chats"' in source
    assert 'id="users"' in source
    assert 'data-period="24"' in source
    assert 'data-period="168"' in source
    assert 'view=tokens-api' in source
    assert "X-Telegram-Init-Data" in source


def test_crocodile_mini_app_redirects_token_start_param_to_dashboard():
    source = (ROOT / "index.html").read_text(encoding="utf-8")

    assert 'startParam === "tokens"' in source
    assert 'target.searchParams.set("view", "tokens")' in source


def test_dashboard_auth_accepts_only_admin(monkeypatch):
    monkeypatch.setattr(
        token_dashboard,
        "validate_telegram_init_data",
        lambda _data, _token: SimpleNamespace(user_id=ADMIN_ID),
    )

    assert token_dashboard._authorize_dashboard_request(
        _request(headers={"X-Telegram-Init-Data": "signed"})
    ) == int(ADMIN_ID)


def test_dashboard_auth_rejects_non_admin(monkeypatch):
    monkeypatch.setattr(
        token_dashboard,
        "validate_telegram_init_data",
        lambda _data, _token: SimpleNamespace(user_id=int(ADMIN_ID) + 1),
    )

    with pytest.raises(web.HTTPForbidden):
        token_dashboard._authorize_dashboard_request(
            _request(headers={"X-Telegram-Init-Data": "signed"})
        )


def test_dashboard_api_returns_repository_report(monkeypatch):
    calls = []

    monkeypatch.setattr(
        token_dashboard,
        "_authorize_dashboard_request",
        lambda _request: int(ADMIN_ID),
    )

    async def fake_report(period_hours, *, limit):
        calls.append((period_hours, limit))
        return {"totals": {"total_tokens": 123}, "features": []}

    monkeypatch.setattr(
        token_dashboard.bot_statistics,
        "get_model_usage_report",
        fake_report,
    )

    response = asyncio.run(
        token_dashboard.token_dashboard_api(
            _request(query={"period": "168"})
        )
    )
    payload = json.loads(response.text)

    assert calls == [(168, 20)]
    assert payload["period"] == "168"
    assert payload["report"]["totals"]["total_tokens"] == 123
    assert response.headers["Cache-Control"] == "no-store"


def test_game_route_dispatches_dashboard_views(monkeypatch):
    seen = []

    async def fake_page(_request):
        seen.append("page")
        return web.Response(text="page")

    async def fake_api(_request):
        seen.append("api")
        return web.json_response({"ok": True})

    monkeypatch.setattr(crocodile, "serve_token_dashboard", fake_page)
    monkeypatch.setattr(crocodile, "token_dashboard_api", fake_api)

    page = asyncio.run(crocodile.serve_index(_request(query={"view": "tokens"})))
    api = asyncio.run(crocodile.serve_index(_request(query={"view": "tokens-api"})))

    assert page.text == "page"
    assert json.loads(api.text) == {"ok": True}
    assert seen == ["page", "api"]
