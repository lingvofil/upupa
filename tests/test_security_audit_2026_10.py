"""Регрессии аудита безопасности 2026-10-20.

1. Токен бота и ключи API не попадают в bot_log.txt.
2. «чотам <url>» не ходит во внутреннюю сеть (SSRF).
3. Рассылка не пишет в журнал истории и не ломает его индекс.
"""
import asyncio
import io
import logging
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

# Настраивает fake env и тяжёлые моки до импорта прикладных модулей.
from tests import test_smoke_imports  # noqa: F401
from tests.test_smoke_imports import FAKE_ENV

import AI.whatisthere as whatisthere
import core.logging_setup as logging_setup
import features.broadcast as broadcast
import services.search as search
from infrastructure.persistence.sqlite_history import SQLiteHistoryRepository


TOKEN = FAKE_ENV["API_TOKEN"]
TOKEN_SECRET_PART = TOKEN.split(":", 1)[1]


# --------------------------------------------------------------------------
# 1. Секреты в логе
# --------------------------------------------------------------------------


@pytest.fixture
def bot_log():
    """Перенаправляет настоящий файловый handler бота в память.

    Под pytest basicConfig в logging_setup может не сработать (у root уже есть
    handlers pytest), поэтому handler подключается к root явно, а уровень root
    выставляется как в проде.
    """
    handler = logging_setup._file_handler
    root = logging.getLogger()
    stream = io.StringIO()
    previous_stream = handler.setStream(stream)
    attached = handler not in root.handlers
    if attached:
        root.addHandler(handler)
    previous_level = root.level
    root.setLevel(logging.INFO)
    try:
        yield stream
    finally:
        root.setLevel(previous_level)
        if attached:
            root.removeHandler(handler)
        handler.setStream(previous_stream)


def test_httpx_request_with_token_does_not_leak_into_bot_log(bot_log):
    url = f"https://api.telegram.org/file/bot{TOKEN}/photos/file_1.jpg"

    async def scenario():
        transport = httpx.MockTransport(lambda request: httpx.Response(404))
        async with httpx.AsyncClient(transport=transport) as client:
            response = await client.get(url)
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                # Текст HTTPStatusError содержит полный URL с токеном.
                logging.error("Ошибка загрузки файла: %s", exc)
                logging.error("С traceback", exc_info=True)

    asyncio.run(scenario())

    output = bot_log.getvalue()
    assert "Ошибка загрузки файла" in output
    assert "HTTPStatusError" in output
    assert TOKEN_SECRET_PART not in output
    assert TOKEN not in output
    # INFO-строка httpx "HTTP Request: GET <url>" приглушена.
    assert "HTTP Request" not in output


def test_bot_log_redacts_api_keys_in_query(bot_log):
    logging.error(
        "Ошибка: https://api.giphy.com/v1/gifs/search?api_key=GIPHYSECRET&q=cat "
        "и https://www.googleapis.com/customsearch/v1?q=cat&key=AIzaGOOGLESECRET"
    )

    output = bot_log.getvalue()
    assert "q=cat" in output
    assert "GIPHYSECRET" not in output
    assert "AIzaGOOGLESECRET" not in output


def test_giphy_http_error_log_has_no_api_key(monkeypatch, bot_log):
    monkeypatch.setattr(search, "giphy_api_key", "GIPHYSECRET")

    async def scenario():
        transport = httpx.MockTransport(lambda request: httpx.Response(403))
        async with httpx.AsyncClient(transport=transport) as client:
            return await search.search_gifs("cat", client=client)

    assert asyncio.run(scenario()) == []
    output = bot_log.getvalue()
    assert "Giphy API вернул HTTP 403" in output
    assert "GIPHYSECRET" not in output


class FakeTelegramBot:
    def __init__(self, data=b"telegram-bytes"):
        self.data = data
        self.downloaded = []

    async def get_file(self, file_id):
        return SimpleNamespace(file_path=f"photos/{file_id}.jpg", file_size=None)

    async def download_file(self, file_path, destination):
        self.downloaded.append(file_path)
        destination.write(self.data)
        return destination


def test_whatisthere_downloads_telegram_files_via_bot_api(monkeypatch):
    fake_bot = FakeTelegramBot()
    monkeypatch.setattr(whatisthere, "bot", fake_bot)

    assert asyncio.run(whatisthere._download_telegram_bytes("file-1")) == b"telegram-bytes"
    assert fake_bot.downloaded == ["photos/file-1.jpg"]


def test_search_downloads_telegram_files_via_bot_api():
    fake_bot = FakeTelegramBot(b"image-bytes")

    assert asyncio.run(search.download_image(fake_bot, "photo-1")) == b"image-bytes"
    photo = SimpleNamespace(file_id="photo-2")
    assert asyncio.run(search.download_telegram_image(fake_bot, photo)) == b"image-bytes"
    assert fake_bot.downloaded == ["photos/photo-1.jpg", "photos/photo-2.jpg"]


@pytest.mark.parametrize("module", [whatisthere, search])
def test_no_manual_tokenized_telegram_urls(module):
    source = Path(module.__file__).read_text(encoding="utf-8")
    assert "api.telegram.org/file/bot" not in source
    assert "API_TOKEN" not in source


# --------------------------------------------------------------------------
# 2. SSRF в «чотам <url>»
# --------------------------------------------------------------------------


PUBLIC_IP = "93.184.215.14"


@pytest.fixture
def public_dns(monkeypatch):
    """DNS без сети: имена резолвятся по словарю, по умолчанию в публичный IP."""
    table = {}

    async def fake_resolve(host, port):
        return table.get(host, [PUBLIC_IP])

    monkeypatch.setattr(whatisthere, "_resolve_host_addresses", fake_resolve)
    return table


def _recording_client(handler=None):
    requests = []

    def record(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if handler is not None:
            return handler(request)
        return httpx.Response(200, headers={"Content-Type": "text/plain"}, content=b"ok")

    return httpx.AsyncClient(transport=httpx.MockTransport(record)), requests


def _download(url, client):
    async def scenario():
        async with client:
            return await whatisthere._download_url_bytes(url, client=client)

    return asyncio.run(scenario())


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:8766/ready",
        "http://127.0.0.1:8766/diagnostics",
        "http://127.0.0.1:8080/",
        "http://169.254.169.254/latest/meta-data/",
        "http://[::1]:8766/ready",
        "http://[::ffff:127.0.0.1]:8766/ready",
        "http://0.0.0.0:8766/ready",
        "http://10.0.0.5/",
        "http://192.168.1.1/",
        "http://100.64.0.1/",
        "http://[fe80::1]/",
        "http://[fd00::1]/",
        "http://224.0.0.1/",
        "file:///etc/passwd",
        "ftp://example.com/file",
    ],
)
def test_download_url_rejects_internal_and_non_http_targets(url, public_dns):
    client, requests = _recording_client()

    with pytest.raises(whatisthere.UnsafeURLError):
        _download(url, client)

    assert requests == []


@pytest.mark.parametrize("address", ["127.0.0.1", "::1", "169.254.169.254", "10.1.2.3"])
def test_download_url_rejects_hostname_resolving_to_internal_address(address, public_dns):
    public_dns["internal.example"] = [PUBLIC_IP, address]
    client, requests = _recording_client()

    with pytest.raises(whatisthere.UnsafeURLError):
        _download("http://internal.example:8766/ready", client)

    assert requests == []


@pytest.mark.parametrize(
    "location",
    [
        "http://127.0.0.1:8766/ready",
        "http://169.254.169.254/latest/meta-data/",
        "http://[::1]:8080/",
        "http://localhost.example/",
    ],
)
def test_download_url_rechecks_redirect_target(location, public_dns):
    public_dns["localhost.example"] = ["127.0.0.1"]

    def handler(request):
        if request.url.host == "public.example":
            return httpx.Response(302, headers={"Location": location})
        return httpx.Response(200, content=b"internal secret")

    client, requests = _recording_client(handler)

    with pytest.raises(whatisthere.UnsafeURLError):
        _download("https://public.example/start", client)

    assert [str(request.url) for request in requests] == ["https://public.example/start"]


def test_download_url_follows_public_redirects_manually(public_dns):
    def handler(request):
        if request.url.path == "/start":
            return httpx.Response(301, headers={"Location": "/next"})
        if request.url.path == "/next":
            return httpx.Response(302, headers={"Location": "https://cdn.example/final"})
        return httpx.Response(200, headers={"Content-Type": "text/html; charset=utf-8"}, content=b"<p>hi</p>")

    client, requests = _recording_client(handler)

    data, content_type = _download("https://public.example/start", client)

    assert data == b"<p>hi</p>"
    assert content_type == "text/html"
    assert [str(request.url) for request in requests] == [
        "https://public.example/start",
        "https://public.example/next",
        "https://cdn.example/final",
    ]


def test_download_url_limits_redirect_chain(public_dns):
    def handler(request):
        step = int(request.url.path.strip("/") or 0)
        return httpx.Response(302, headers={"Location": f"/{step + 1}"})

    client, requests = _recording_client(handler)

    with pytest.raises(whatisthere.UnsafeURLError):
        _download("https://public.example/0", client)

    assert len(requests) == whatisthere.MAX_URL_REDIRECTS + 1


def test_download_url_ignores_client_redirect_setting(public_dns):
    """Даже клиент с follow_redirects=True не должен уходить по редиректу без проверки."""
    requests = []

    def handler(request):
        requests.append(request)
        if request.url.host == "public.example":
            return httpx.Response(302, headers={"Location": "http://127.0.0.1:8766/ready"})
        return httpx.Response(200, content=b"internal secret")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True)

    with pytest.raises(whatisthere.UnsafeURLError):
        _download("https://public.example/start", client)

    assert len(requests) == 1


def test_whatisthere_url_command_refuses_internal_url_without_llm(monkeypatch, public_dns):
    async def forbidden_llm(*args, **kwargs):
        raise AssertionError("internal content must not reach the LLM")

    monkeypatch.setattr(whatisthere, "_generate_text_with_active_model", forbidden_llm)
    monkeypatch.setattr(whatisthere, "analyze_media_bytes", forbidden_llm)
    message = SimpleNamespace(text="чотам", caption=None, chat=SimpleNamespace(id=1))

    success, reply = asyncio.run(
        whatisthere.process_url_whatisthere(message, "http://127.0.0.1:8766/diagnostics")
    )

    assert success is False
    assert reply == "По этой ссылке не полезу."


# --------------------------------------------------------------------------
# 3. Рассылка и журнал истории
# --------------------------------------------------------------------------


class FakeBroadcastBot:
    def __init__(self, fail_for=()):
        self.fail_for = set(fail_for)
        self.sent = []

    async def send_message(self, chat_id, text):
        if chat_id in self.fail_for:
            raise RuntimeError("boom")
        self.sent.append((chat_id, text))


def _no_sleep(monkeypatch):
    async def instant(_seconds):
        return None

    monkeypatch.setattr(broadcast.asyncio, "sleep", instant)


def test_send_broadcast_message_does_not_touch_history_journal(tmp_path, monkeypatch, caplog):
    journal = tmp_path / "user_messages.log"
    journal.write_text(
        "2026-10-20T12:00:00.000000 - Chat -100 (Чат) - User 1 (alice) [Алиса]: привет\n"
        "2026-10-20T12:00:01.000000 - Chat -200 (Чат) - User 1 (alice) [Алиса]: пока\n",
        encoding="utf-8",
    )
    before = journal.read_bytes()
    monkeypatch.setattr(broadcast, "USER_MESSAGES_LOG_PATH", journal)
    monkeypatch.setattr(broadcast, "get_history_repository", lambda path: None)
    _no_sleep(monkeypatch)
    bot = FakeBroadcastBot(fail_for={-200})
    caplog.set_level(logging.INFO, logger=broadcast.__name__)

    result = asyncio.run(broadcast.send_broadcast_message(bot, "объявление"))

    assert result == (1, 1)
    assert bot.sent == [(-100, "объявление")]
    assert journal.read_bytes() == before
    assert "BROADCAST - Начинаю рассылку в 2 групп" in caplog.text


def test_broadcast_between_history_appends_keeps_index_working(tmp_path, monkeypatch, caplog):
    repo = SQLiteHistoryRepository(tmp_path / "history.db", tmp_path / "user_messages.log")
    repo.initialize()

    def record(chat_id, text):
        return dict(
            timestamp="2026-10-20T12:00:00", chat_id=str(chat_id), chat_title="Чат",
            user_id="1", username="alice", full_name="Алиса", text=text,
        )

    repo.append(record(-100, "первое"))
    repo.append(record(-200, "второе"))
    monkeypatch.setattr(broadcast, "USER_MESSAGES_LOG_PATH", repo.log_path)
    monkeypatch.setattr(broadcast, "get_history_repository", lambda path: repo)
    _no_sleep(monkeypatch)
    bot = FakeBroadcastBot(fail_for={-200})
    caplog.set_level(logging.INFO, logger=broadcast.__name__)
    journal_before = repo.log_path.read_bytes()

    successful, failed = asyncio.run(broadcast.send_broadcast_message(bot, "объявление"))

    assert (successful, failed) == (1, 1)
    assert bot.sent == [(-100, "объявление")]
    assert repo.log_path.read_bytes() == journal_before
    assert "Рассылка завершена. Успешно: 1, Неудачно: 1" in caplog.text

    repo.append(record(-100, "третье"))
    repo.initialize()
    assert [row["text"] for row in repo.select(-100)] == ["первое", "третье"]
    assert b"BROADCAST" not in repo.log_path.read_bytes()
