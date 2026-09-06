import asyncio
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
from types import SimpleNamespace

import pytest

from infrastructure.persistence.sqlite_history import HistorySourceChanged, SQLiteHistoryRepository


def line(index, *, chat=-1, user=2, text=None):
    return (f"2026-09-06T12:00:00.000000 - Chat {chat} (Чат) "
            f"- User {user} (alice) [Алиса]: {text if text is not None else f'message-{index}'}\n")


def record(text="Привет\nвторая строка", chat=-1, user=2):
    return dict(timestamp="2026-09-06T12:00:00", chat_id=str(chat), chat_title="Чат",
                user_id=str(user), username="alice", full_name="Алиса", text=text)


@pytest.fixture
def repo(tmp_path):
    repository = SQLiteHistoryRepository(tmp_path / "history.db", tmp_path / "user_messages.log")
    repository.initialize()
    return repository


def test_import_multiline_rejections_and_repeated_startup(repo):
    repo.log_path.write_bytes((line(1, text="первая\nвторая: строка") +
                              "2026-09-06T12:00:00 - BROADCAST - diagnostic\n" +
                              line(2, chat=-10, user=22)).encode("utf-8"))
    repo.initialize()
    repo.initialize()
    rows = repo.select(-1)
    assert len(rows) == 1 and rows[0]["text"] == "первая\nвторая: строка"
    assert repo.count(-10, user_id=22) == 1
    assert repo.count(-1, user_id=22) == 0
    with closing(sqlite3.connect(repo.path)) as conn:
        assert conn.execute("SELECT COUNT(*) FROM history_rejected").fetchone()[0] == 1


def test_interrupted_import_resumes_at_committed_batch(repo, monkeypatch):
    repo.log_path.write_text("".join(line(i) for i in range(2200)), encoding="utf-8")
    original = repo._import_record
    calls = 0

    def fail(conn, offset, raw):
        nonlocal calls
        calls += 1
        if calls == 1100:
            raise OSError("simulated crash")
        original(conn, offset, raw)

    monkeypatch.setattr(repo, "_import_record", fail)
    with pytest.raises(OSError):
        repo.synchronize()
    with closing(sqlite3.connect(repo.path)) as conn:
        assert conn.execute("SELECT COUNT(*) FROM history_messages").fetchone()[0] == 1000
    reopened = SQLiteHistoryRepository(repo.path, repo.log_path)
    reopened.initialize()
    assert reopened.count(-1) == 2200


def test_incomplete_tail_waits_for_newline(repo):
    repo.log_path.write_bytes((line(1) + line(2).rstrip("\n")).encode())
    repo.synchronize()
    assert repo.count(-1) == 1
    with repo.log_path.open("ab") as file:
        file.write(b"\n")
    assert repo.count(-1) == 2


def test_deploy_preflight_leaves_live_multiline_tail_for_startup(tmp_path):
    log = tmp_path / "user_messages.log"
    log.write_bytes(("".join(line(i) for i in range(1001)) + line(1001, text="начало")).encode())
    root = Path(__file__).resolve().parents[1]
    command = [sys.executable, str(root / "scripts/index_history.py"),
               "--app-dir", str(tmp_path), "--repository-module",
               str(root / "infrastructure/persistence/sqlite_history.py")]
    for _ in range(2):
        subprocess.run(command, check=True, capture_output=True)
    with closing(sqlite3.connect(tmp_path / "history.db")) as conn:
        assert conn.execute("SELECT COUNT(*) FROM history_messages").fetchone()[0] == 1001
    with log.open("ab") as file:
        file.write(("продолжение\n" + line(1002)).encode())
    repository = SQLiteHistoryRepository(tmp_path / "history.db", log)
    repository.initialize()
    rows = repository.select(-1)
    assert len(rows) == 1003
    assert rows[-2]["text"] == "начало\nпродолжение"
    repository.initialize()
    assert repository.count(-1) == 1003


def test_append_is_idempotent_and_preserves_multiline_message(repo):
    repo.append(record(), 123)
    first_size = repo.log_path.stat().st_size
    repo.append(record(), 123)
    assert repo.log_path.stat().st_size == first_size
    assert repo.select(-1)[0]["text"] == record()["text"]
    assert repo.select(-1)[0]["message_id"] == 123


@pytest.mark.parametrize("write_prefix", [False, True])
def test_pending_message_recovers_after_failure_and_keeps_telegram_id(repo, monkeypatch, write_prefix):
    original = repo._flush_pending

    def fail(conn):
        pending = conn.execute("SELECT * FROM history_pending").fetchone()
        if pending:
            if write_prefix:
                with repo.log_path.open("ab") as file:
                    file.write(bytes(pending["raw"])[:19])
            raise OSError("power loss")
        original(conn)

    monkeypatch.setattr(repo, "_flush_pending", fail)
    with pytest.raises(OSError):
        repo.append(record(), 456)
    reopened = SQLiteHistoryRepository(repo.path, repo.log_path)
    reopened.initialize()
    reopened.append(record(), 456)
    rows = reopened.select(-1)
    assert len(rows) == 1 and rows[0]["message_id"] == 456
    assert rows[0]["text"] == record()["text"]
    assert repo.log_path.read_text(encoding="utf-8").count(" - Chat ") == 1


def test_index_failure_after_complete_journal_write_recovers_without_duplicate(repo, monkeypatch):
    def fail(*args):
        raise OSError("index unavailable")

    monkeypatch.setattr(repo, "_insert", fail)
    with pytest.raises(OSError):
        repo.append(record(), 123)
    size = repo.log_path.stat().st_size
    reopened = SQLiteHistoryRepository(repo.path, repo.log_path)
    reopened.initialize()
    reopened.append(record(), 123)
    assert repo.log_path.stat().st_size == size
    assert reopened.count(-1) == 1


def test_pending_snapshot_with_a_newer_journal_keeps_later_messages(repo, monkeypatch):
    def fail(*args):
        raise OSError("index unavailable")

    monkeypatch.setattr(repo, "_insert", fail)
    with pytest.raises(OSError):
        repo.append(record(), 123)
    with repo.log_path.open("a", encoding="utf-8") as file:
        file.write(line(2))
    reopened = SQLiteHistoryRepository(repo.path, repo.log_path)
    reopened.initialize()
    assert len(reopened.select(-1)) == 2
    assert reopened.select(-1)[0]["message_id"] == 123


def test_truncation_does_not_erase_index(repo):
    repo.append(record(), 1)
    repo.log_path.write_bytes(b"broken")
    with pytest.raises(HistorySourceChanged):
        repo.synchronize()
    with closing(sqlite3.connect(repo.path)) as conn:
        assert conn.execute("SELECT COUNT(*) FROM history_messages").fetchone()[0] == 1


def test_backup_copy_and_legacy_append_resume_without_duplicates(repo, tmp_path):
    repo.append(record(), 1)
    restored = tmp_path / "restored"
    restored.mkdir()
    with closing(sqlite3.connect(repo.path)) as source, closing(sqlite3.connect(restored / "history.db")) as target:
        source.backup(target)
    shutil.copyfile(repo.log_path, restored / "user_messages.log")
    with (restored / "user_messages.log").open("a", encoding="utf-8") as file:
        file.write(line(2))  # a journal-only release ran after rollback
    reopened = SQLiteHistoryRepository(restored / "history.db", restored / "user_messages.log")
    reopened.initialize()
    reopened.initialize()
    assert reopened.count(-1) == 2


def test_query_uses_index_and_reads_only_checkpoint_anchor(repo, monkeypatch):
    repo.log_path.write_text("".join(line(i, chat=-1 if i % 2 else -10) for i in range(5000)), encoding="utf-8")
    repo.synchronize()
    original = Path.open
    bytes_read = 0

    class CountedFile:
        def __init__(self, file):
            self.file = file

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.file.close()

        def __getattr__(self, name):
            return getattr(self.file, name)

        def read(self, *args):
            nonlocal bytes_read
            value = self.file.read(*args)
            bytes_read += len(value)
            return value

        def readline(self, *args):
            nonlocal bytes_read
            value = self.file.readline(*args)
            bytes_read += len(value)
            return value

    def counted(path, *args, **kwargs):
        file = original(path, *args, **kwargs)
        return CountedFile(file) if path == repo.log_path else file

    monkeypatch.setattr(Path, "open", counted)
    assert len(repo.select(-1, start=datetime(2026, 9, 6), limit=10)) == 10
    assert bytes_read <= 512
    with closing(sqlite3.connect(repo.path)) as conn:
        plan = str(conn.execute("EXPLAIN QUERY PLAN SELECT * FROM history_messages WHERE chat_id=? AND timestamp>=?",
                                ("-1", "2026-09-06")).fetchall())
        assert "history_chat_time" in plan
        plan = str(conn.execute("EXPLAIN QUERY PLAN SELECT * FROM history_messages WHERE chat_id=? AND user_id=? ORDER BY id",
                                ("-1", "2")).fetchall())
        assert "history_chat_user" in plan


def test_sampling_and_fts_stay_inside_chat(repo):
    repo.log_path.write_text("".join(line(i, text=f"крокодил обсуждение {i}") for i in range(200)) +
                             line(201, chat=-10, text="крокодил секрет чужого чата"), encoding="utf-8")
    sample = repo.select(-1, sample_size=30, recent_size=10)
    assert len(sample) == 30
    assert [row["text"] for row in sample[-10:]] == [f"крокодил обсуждение {i}" for i in range(190, 200)]
    assert len(repo.search(-1, '"крокодил" OR', limit=5)) == 5
    assert all(row["chat_id"] == "-1" for row in repo.search(-1, "крокодил"))
    assert all(row["chat_id"] == "-1" for row in repo.context(-1, sample[-1]["id"]))
    assert repo.select(-1, username="ALICE", limit=1)[0]["username"] == "alice"
    assert repo.select(-1, full_name="АЛИСА", limit=1)[0]["full_name"] == "Алиса"


@pytest.fixture
def configured(repo, monkeypatch):
    from tests import test_smoke_imports
    del test_smoke_imports
    import core.history_store as store

    monkeypatch.setattr(store, "_repository", repo)
    return repo


def test_summary_radio_lexicon_and_participant_use_index(configured, monkeypatch):
    import AI.summarize as summary
    import features.lexicon_settings as lexicon
    import features.radio.service as radio
    import AI.dialog.participant_imitation as imitation

    repo = configured
    repo.log_path.write_text("".join(line(i, text=f"Достаточно длинный рассказ про крокодила номер {i}") for i in range(20)), encoding="utf-8")
    repo.synchronize()
    monkeypatch.setattr(lexicon, "LOG_FILE", repo.log_path)
    monkeypatch.setattr(imitation, "LOG_FILE", repo.log_path)
    messages, users, title = summary._get_chat_messages(repo.log_path, "-1", datetime(2026, 9, 5), 10, 3)
    assert len(messages) == 10 and title == "Чат" and users["2"]["display_name"] == "Алиса"

    async def scenario():
        messages, title, hours = await radio.collect_radio_history("-1", log_file_path=repo.log_path,
                                                                 now=datetime(2026, 9, 6, 13))
        assert len(messages) == 20 and hours == 24
        assert len(await lexicon.extract_user_messages(2, -1, sample_size=5)) == 5
        assert dict(await lexicon.get_chat_frequent_words(-1))["крокодила"] == 20
        assert (await imitation.resolve_participant_identity("АЛИСА", -1))["user_id"] == 2

    asyncio.run(scenario())
    entry = imitation._scan_participant_history_sync(2, -1, 6, 3)
    assert entry.message_count == 20 and len(entry.snapshot()[0]) == 6


def test_live_writer_uses_index_and_journal(configured, monkeypatch):
    import features.lexicon_settings as lexicon

    monkeypatch.setattr(lexicon, "LOG_FILE", configured.log_path)
    message = SimpleNamespace(message_id=10, text="text with\nnew line", chat=SimpleNamespace(id=-1, title="Чат"),
                              from_user=SimpleNamespace(id=2, username="alice", full_name="Алиса"))
    asyncio.run(lexicon.save_user_message(message))
    assert configured.select(-1)[0]["text"] == message.text
    assert configured.log_path.exists()


def test_indexed_recall_finds_old_topic_and_surrounding_context(configured):
    from AI.chat_recall import _indexed_recall

    configured.log_path.write_text("".join(line(i, text="крокодил" if i == 10 else f"текст {i}") for i in range(100)), encoding="utf-8")
    count, episodes = _indexed_recall(configured, "-1", "крокодил")
    assert count == 100 and len(episodes) == 1
    assert len(episodes[0]) == 7
    assert episodes[0][3]["text"] == "крокодил"


def test_concurrent_writers_share_journal_without_lost_messages(repo):
    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(lambda i: repo.append(record(text=f"message-{i}"), i), range(40)))
    assert repo.count(-1) == 40
    reopened = SQLiteHistoryRepository(repo.path, repo.log_path)
    reopened.initialize()
    assert reopened.count(-1) == 40


def test_sms_and_world_activity_use_index(configured, monkeypatch):
    from unittest.mock import AsyncMock
    import features.sms_settings as sms
    from features.world.activity import get_top_active_citizen

    configured.log_path.write_text("".join(line(i) for i in range(20)) + line(21, chat=-10), encoding="utf-8")
    monkeypatch.setattr(sms, "LOG_FILE", str(configured.log_path))
    monkeypatch.setattr(sms, "sms_disabled_chats", set())
    message = SimpleNamespace(text="чоговорят 1", reply=AsyncMock(), chat=SimpleNamespace(id=-1, title="Чат"))

    async def scenario():
        await sms.process_what_they_say(message, [{"id": -1, "title": "Чат"}])
        reply = message.reply.await_args.args[0]
        assert "message-10" in reply and "message-19" in reply
        assert "message-9" not in reply and "message-21" not in reply
        citizen = await get_top_active_citizen(-1, log_file_path=configured.log_path, now=datetime(2026, 9, 6, 13))
        assert citizen == ("Алиса", 20)

    asyncio.run(scenario())
