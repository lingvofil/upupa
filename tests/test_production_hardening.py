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
restore = _load_script("restore_history_journal.py")


def _manifest_file(backup_dir, name):
    manifest = json.loads(
        (backup_dir / "manifest.json").read_text(encoding="utf-8")
    )
    return next(item for item in manifest["files"] if item["name"] == name)


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
    journal_bytes = b"message\n"
    (source / "user_messages.log").write_bytes(journal_bytes)
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
    assert files["user_messages.log"]["kind"] == "chunked_file"
    assert not (backup_dir / "user_messages.log").exists()
    assert (backup_dir / "user_messages.log.parts").is_dir()

    for name in ("statistics.db", "chat_settings.json"):
        metadata = files[name]
        digest = hashlib.sha256((backup_dir / name).read_bytes()).hexdigest()
        assert metadata["sha256"] == digest

    restored = tmp_path / "restored-user_messages.log"
    restore.restore_history_journal(backup_dir, restored)
    assert restored.read_bytes() == journal_bytes


def test_runtime_backup_reuses_unchanged_journal_chunks(tmp_path):
    source = tmp_path / "app"
    destination = tmp_path / "backups"
    source.mkdir()
    journal = source / "user_messages.log"
    first_bytes = b"abcdefghTAIL"
    journal.write_bytes(first_bytes)

    first = backup.create_backup(
        source,
        destination,
        "first",
        journal_chunk_size=8,
    )
    journal.write_bytes(first_bytes + b"MORE")
    second = backup.create_backup(
        source,
        destination,
        "second",
        journal_chunk_size=8,
    )

    first_meta = _manifest_file(first, "user_messages.log")
    second_meta = _manifest_file(second, "user_messages.log")
    first_head = first / first_meta["chunks"][0]["path"]
    second_head = second / second_meta["chunks"][0]["path"]
    first_tail = first / first_meta["chunks"][1]["path"]
    second_tail = second / second_meta["chunks"][1]["path"]

    assert first_head.stat().st_ino == second_head.stat().st_ino
    assert first_head.stat().st_dev == second_head.stat().st_dev
    assert first_head.stat().st_nlink >= 2
    assert first_tail.stat().st_ino != second_tail.stat().st_ino

    first_restored = tmp_path / "first.log"
    second_restored = tmp_path / "second.log"
    restore.restore_history_journal(first, first_restored)
    restore.restore_history_journal(second, second_restored)
    assert first_restored.read_bytes() == first_bytes
    assert second_restored.read_bytes() == first_bytes + b"MORE"


def test_chunked_backup_remains_restorable_after_old_snapshot_pruned(tmp_path):
    source = tmp_path / "app"
    destination = tmp_path / "backups"
    source.mkdir()
    journal = source / "user_messages.log"
    journal.write_bytes(b"abcdefghTAIL")

    backups = []
    for index in range(4):
        journal.write_bytes(journal.read_bytes() + bytes([65 + index]))
        backups.append(
            backup.create_backup(
                source,
                destination,
                f"run-{index}",
                keep=3,
                journal_chunk_size=8,
            )
        )

    assert not backups[0].exists()
    newest = backups[-1]
    restored = tmp_path / "newest.log"
    restore.restore_history_journal(newest, restored)
    assert restored.read_bytes() == journal.read_bytes()


def test_restore_history_journal_supports_legacy_full_file_backup(tmp_path):
    backup_dir = tmp_path / "legacy"
    backup_dir.mkdir()
    payload = b"legacy journal\n"
    (backup_dir / "user_messages.log").write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()
    (backup_dir / "manifest.json").write_text(
        json.dumps(
            {
                "files": [
                    {
                        "name": "user_messages.log",
                        "kind": "file",
                        "size": len(payload),
                        "sha256": digest,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    output = tmp_path / "legacy-restored.log"
    restore.restore_history_journal(backup_dir, output)
    assert output.read_bytes() == payload


def test_restore_history_journal_rejects_corrupt_chunk(tmp_path):
    source = tmp_path / "app"
    destination = tmp_path / "backups"
    source.mkdir()
    (source / "user_messages.log").write_bytes(b"abcdefghTAIL")
    backup_dir = backup.create_backup(
        source,
        destination,
        "corrupt",
        journal_chunk_size=8,
    )
    metadata = _manifest_file(backup_dir, "user_messages.log")
    chunk = backup_dir / metadata["chunks"][0]["path"]
    chunk.chmod(0o644)
    chunk.write_bytes(b"broken!!")

    with pytest.raises(restore.BackupRestoreError, match="verification"):
        restore.restore_history_journal(backup_dir, tmp_path / "broken.log")


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
    # User explicitly chose to keep host-key verification disabled so deploy
    # remains zero-maintenance; ensure no SSH_KNOWN_HOSTS dependency returns.
    assert "StrictHostKeyChecking=no" in deploy_workflow
    assert "StrictHostKeyChecking=yes" not in deploy_workflow
    assert "UserKnownHostsFile=/dev/null" in deploy_workflow
    assert "SSH_KNOWN_HOSTS" not in deploy_workflow
    assert 'chmod 600 "${DEPLOY_KEY_PATH}"' in deploy_workflow
    assert "if: always()" in deploy_workflow

    # `runner` is unavailable in jobs.<job_id>.env and would make the workflow
    # invalid before any job starts. Use a path based only on allowed `github`
    # context values at job-env evaluation time.
    assert "runner.temp" not in deploy_workflow
    assert (
        "DEPLOY_KEY_PATH: /tmp/upupa_deploy_key_${{ github.run_id }}_"
        "${{ github.run_attempt }}" in deploy_workflow
    )

    # A production disk can be too full even to create .git/config.lock. The
    # deploy must reclaim stale backup space before the first write-heavy Git
    # operation and must not mutate .git/config just to fetch main.
    assert "bootstrap_prune_backups" in deploy_workflow
    assert "MIN_BOOTSTRAP_FREE_KB=$((512 * 1024))" in deploy_workflow
    assert "git remote set-url origin" not in deploy_workflow
    direct_fetch = "git fetch --prune git@github.com:lingvofil/upupa.git main"
    assert direct_fetch in deploy_workflow
    assert (
        'PREVIOUS_SHA="$(git rev-parse HEAD)"\n'
        "          bootstrap_prune_backups\n"
        "          # Do not mutate .git/config" in deploy_workflow
    )
    assert "preserving the last known-good backup" in deploy_workflow
