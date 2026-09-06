import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3
from urllib.error import HTTPError

import pytest


ROOT = Path(__file__).resolve().parents[1]


def _load_script(name):
    path = ROOT / "scripts" / name
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


backup = _load_script("backup_runtime_state.py")
health = _load_script("production_healthcheck.py")


def test_runtime_backup_uses_sqlite_online_backup_and_manifest(tmp_path):
    source = tmp_path / "app"
    destination = tmp_path / "backups"
    source.mkdir()

    database = source / "statistics.db"
    with sqlite3.connect(database) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("CREATE TABLE events(value TEXT)")
        conn.execute("INSERT INTO events VALUES ('preserved')")

    (source / "chat_settings.json").write_text(
        '{"enabled": true}',
        encoding="utf-8",
    )
    (source / "user_messages.log").write_text("message\n", encoding="utf-8")
    (source / "ignore.txt").write_text("not runtime state", encoding="utf-8")

    backup_dir = backup.create_backup(source, destination, "abc123")

    assert backup_dir.parent == destination
    assert not (backup_dir / "ignore.txt").exists()
    with sqlite3.connect(backup_dir / "statistics.db") as conn:
        assert conn.execute("SELECT value FROM events").fetchone()[0] == "preserved"

    manifest = json.loads(
        (backup_dir / "manifest.json").read_text(encoding="utf-8")
    )
    files = {item["name"]: item for item in manifest["files"]}
    assert set(files) == {
        "statistics.db",
        "chat_settings.json",
        "user_messages.log",
    }
    for name, metadata in files.items():
        digest = hashlib.sha256((backup_dir / name).read_bytes()).hexdigest()
        assert metadata["sha256"] == digest


def test_runtime_backup_rejects_unsafe_label(tmp_path):
    with pytest.raises(ValueError):
        backup.create_backup(tmp_path, tmp_path / "backups", "../../unsafe")


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


def test_production_healthcheck_calls_get_me_without_exposing_token():
    requests = []

    def opener(request, *, timeout):
        requests.append((request.full_url, timeout))
        return FakeResponse(
            {
                "ok": True,
                "result": {"id": 123, "username": "upupa_test_bot"},
            }
        )

    result = health.check_telegram(
        "secret-token",
        timeout=3,
        api_base="https://telegram.test",
        opener=opener,
    )

    assert result["id"] == 123
    assert requests == [
        ("https://telegram.test/botsecret-token/getMe", 3),
    ]


def test_production_healthcheck_rejects_non_object_json():
    with pytest.raises(health.HealthCheckError) as caught:
        health.check_telegram(
            "secret-token",
            timeout=3,
            opener=lambda request, *, timeout: FakeResponse([]),
        )

    assert "invalid response" in str(caught.value)
    assert "secret-token" not in str(caught.value)


def test_production_healthcheck_sanitizes_http_errors():
    def opener(request, *, timeout):
        raise HTTPError(request.full_url, 401, "Unauthorized", {}, None)

    with pytest.raises(health.HealthCheckError) as caught:
        health.check_telegram(
            "secret-token",
            timeout=3,
            opener=opener,
        )

    assert "secret-token" not in str(caught.value)
    assert "HTTP 401" in str(caught.value)


def test_process_healthcheck_matches_systemd_pid():
    payload = {"ok": True, "pid": 123, "checks": {
        "polling": True, "databases": True, "background_tasks": True}}
    opener = lambda request, *, timeout: FakeResponse(payload)
    assert health.check_process(timeout=3, expected_pid=123, opener=opener)["pid"] == 123
    with pytest.raises(health.HealthCheckError, match="PID"):
        health.check_process(timeout=3, expected_pid=456, opener=opener)
    payload["checks"]["polling"] = False
    with pytest.raises(health.HealthCheckError):
        health.check_process(timeout=3, opener=opener)


@pytest.mark.parametrize("payload", [[], {}, {"ok": True}, {"ok": False}])
def test_process_healthcheck_rejects_invalid_payload(payload):
    with pytest.raises(health.HealthCheckError):
        health.check_process(timeout=3, opener=lambda request, *, timeout: FakeResponse(payload))


def test_workflows_use_node24_actions_and_native_ssh_setup():
    tests_workflow = (ROOT / ".github" / "workflows" / "tests.yml").read_text(
        encoding="utf-8"
    )
    deploy_workflow = (ROOT / ".github" / "workflows" / "deploy.yml").read_text(
        encoding="utf-8"
    )

    assert "actions/checkout@v7" in tests_workflow
    assert "actions/setup-python@v7" in tests_workflow
    assert "actions/checkout@v4" not in tests_workflow
    assert "actions/setup-python@v5" not in tests_workflow

    assert "webfactory/ssh-agent" not in deploy_workflow
    assert "StrictHostKeyChecking=no" in deploy_workflow
    assert "StrictHostKeyChecking=yes" not in deploy_workflow
    assert "UserKnownHostsFile=/dev/null" in deploy_workflow
    assert "SSH_KNOWN_HOSTS" not in deploy_workflow
    assert 'chmod 600 "${DEPLOY_KEY_PATH}"' in deploy_workflow
    assert "if: always()" in deploy_workflow
