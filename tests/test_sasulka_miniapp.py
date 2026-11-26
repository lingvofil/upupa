import asyncio
import hashlib
import hmac
import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import urlencode

from aiohttp.test_utils import TestClient, TestServer

from tests import test_smoke_imports  # noqa: F401


def signed_headers(settings, username, age=0):
    user = {"id": 42, "first_name": "Имя из Telegram"}
    if username is not None:
        user["username"] = username
    fields = {
        "auth_date": str(int(time.time()) - age),
        "user": json.dumps(user),
    }
    key = hmac.digest(b"WebAppData", settings.API_TOKEN.encode(), hashlib.sha256)
    check = "\n".join(f"{name}={fields[name]}" for name in sorted(fields))
    fields["hash"] = hmac.new(key, check.encode(), hashlib.sha256).hexdigest()
    return {"X-Telegram-Init-Data": urlencode(fields)}


def test_sasulka_command_opens_the_named_app():
    from core.settings import SASULKA_APP_LINK
    from handlers.basic import open_sasulka, router

    message = SimpleNamespace(text="  СаСуЛьКа  ", from_user=SimpleNamespace(id=42),
                              reply=AsyncMock())
    handler = next(item for item in router.message.handlers if item.callback is open_sasulka)
    assert asyncio.run(handler.check(message))[0]
    asyncio.run(open_sasulka(message))
    sent = message.reply.call_args
    assert sent.args == ("сасулька - пасасеш, ок?",)
    assert sent.kwargs["reply_markup"].inline_keyboard[0][0].url == SASULKA_APP_LINK + "?startapp=drop"


def test_sasulka_launch_and_authenticated_ranking(tmp_path, monkeypatch):
    from core import settings
    from games import crocodile, sasulka_webapp
    from infrastructure.persistence.sasulka_ranking import SasulkaRanking

    monkeypatch.setattr(settings, "API_TOKEN", test_smoke_imports.FAKE_ENV["API_TOKEN"])
    db_path = tmp_path / "ranking.db"
    monkeypatch.setattr(sasulka_webapp, "STORE", SasulkaRanking(db_path))
    signed = signed_headers(settings, "actual_player", age=10)
    renamed = signed_headers(settings, "new_username", age=5)
    no_username = signed_headers(settings, None)
    game_url = "/game?view=sasulka"

    async def scenario():
        async with TestClient(TestServer(crocodile.app)) as client:
            for entry in ("drop", "drop_mouth-easy-0"):
                async with client.get("/game", params={"tgWebAppStartParam": entry}) as response:
                    assert response.status == 200
                    page = await response.text()
                    assert "<title>Сасулька</title>" in page
                    assert "пасасеш, ок?" in page
                    assert "Пошаговая" not in page
            async with client.get(game_url + "&asset=drop-engine.js") as response:
                assert response.status == 200
                assert "name: 'Род'" in await response.text()
            async with client.get("/game?view=guru") as response:
                assert response.status == 200

            async def request(body, headers, status=200):
                async with client.post(game_url, json=body, headers=headers) as response:
                    assert response.status == status
                    return await response.json()

            ranking = {"op": "ranking", "sort": "points"}
            record = {"op": "result", "id": "record", "difficulty": "normal", "seed": 0,
                      "trace": [[1, 0, 60]], "username": "fake", "name": "fake"}
            mouth = {**record, "id": "mouth", "difficulty": "easy",
                     "trace": [[1, 73, 167], [1, -36, 364], [1, 88, 121]]}

            await request(record, {}, status=401)
            await request(record, no_username, status=422)
            await request({**mouth, "trace": [[1, 0, 500]], "points": 10000}, signed, status=400)
            assert await request(record, signed) == {"gained": 42}
            assert await request(mouth, signed) == {"gained": 30}
            assert await request(mouth, signed) == {"gained": 0}
            assert (await request(ranking, signed))["rows"] == [
                {"name": "@actual_player", "record": 21, "solved": 1, "points": 72}
            ]

            monkeypatch.setattr(sasulka_webapp, "STORE", SasulkaRanking(db_path))
            assert (await request(ranking, renamed))["rows"][0]["name"] == "@new_username"
            assert (await request(ranking, signed))["rows"][0]["name"] == "@new_username"
            assert (await request(ranking, no_username))["rows"] == []

    asyncio.run(scenario())
