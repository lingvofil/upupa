"""Indexed history with a resumable, append-only legacy journal importer.

The journal remains a recovery/rollback source. Checkpoints and imported rows
commit together; queries never need to parse its already indexed prefix.
"""

from contextlib import closing
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import threading


HEADER = re.compile(rb"^\d{4}-\d{2}-\d{2}T[^ ]+ - ")
RECORD = re.compile(
    r"^(?P<timestamp>\S+) - Chat (?P<chat_id>-?\d+) \((?P<chat_title>.*?)\) "
    r"- User (?P<user_id>\d+) \((?P<username>.*?)\) \[(?P<full_name>.*?)\]: (?P<text>.*)$",
    re.DOTALL,
)


class HistorySourceChanged(RuntimeError):
    """The indexed journal was replaced/truncated; keep the index for recovery."""


def local_timestamp(value: datetime) -> str:
    if value.tzinfo is not None:
        value = value.astimezone().replace(tzinfo=None)
    return value.isoformat(timespec="microseconds")


class SQLiteHistoryRepository:
    def __init__(self, path: str | Path, log_path: str | Path):
        self.path, self.log_path = Path(path), Path(log_path).resolve()
        self._lock = threading.RLock()

    def _connect(self):
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.create_function("casefold", 1, str.casefold, deterministic=True)
        conn.execute("PRAGMA busy_timeout=30000")
        return conn

    def initialize(self, *, finalize_tail=True):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as conn, conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS history_messages (
                    id INTEGER PRIMARY KEY, log_offset INTEGER NOT NULL UNIQUE,
                    timestamp TEXT NOT NULL, chat_id TEXT NOT NULL, chat_title TEXT NOT NULL,
                    user_id TEXT NOT NULL, username TEXT NOT NULL, full_name TEXT NOT NULL,
                    username_fold TEXT NOT NULL, full_name_fold TEXT NOT NULL,
                    text TEXT NOT NULL, message_id INTEGER
                );
                CREATE INDEX IF NOT EXISTS history_chat_time ON history_messages(chat_id, timestamp, id);
                CREATE INDEX IF NOT EXISTS history_chat_user ON history_messages(chat_id, user_id, id);
                CREATE INDEX IF NOT EXISTS history_chat_username ON history_messages(chat_id, username_fold, id);
                CREATE INDEX IF NOT EXISTS history_chat_name ON history_messages(chat_id, full_name_fold, id);
                CREATE INDEX IF NOT EXISTS history_chat_order ON history_messages(chat_id, id);
                CREATE UNIQUE INDEX IF NOT EXISTS history_telegram_message
                    ON history_messages(chat_id, message_id) WHERE message_id IS NOT NULL;
                CREATE TABLE IF NOT EXISTS history_checkpoint (
                    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                    source TEXT NOT NULL, inode TEXT NOT NULL, offset INTEGER NOT NULL,
                    anchor_start INTEGER NOT NULL, anchor_hash TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS history_pending (
                    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                    log_offset INTEGER NOT NULL, record_json TEXT NOT NULL,
                    raw BLOB NOT NULL, message_id INTEGER
                );
                CREATE TABLE IF NOT EXISTS history_summary_cursors (
                    chat_id TEXT NOT NULL, user_id TEXT NOT NULL,
                    through_id INTEGER NOT NULL, requested_at TEXT NOT NULL,
                    PRIMARY KEY (chat_id, user_id)
                );
                CREATE TABLE IF NOT EXISTS history_rejected (
                    log_offset INTEGER PRIMARY KEY, raw BLOB NOT NULL, reason TEXT NOT NULL
                );
                CREATE VIRTUAL TABLE IF NOT EXISTS history_fts USING fts5(
                    text, content='history_messages', content_rowid='id', tokenize='unicode61'
                );
                CREATE TRIGGER IF NOT EXISTS history_insert AFTER INSERT ON history_messages BEGIN
                    INSERT INTO history_fts(rowid, text) VALUES (new.id, new.text);
                END;
            """)
        self.synchronize(finalize_tail=finalize_tail)

    @staticmethod
    def _identity(stat):
        return f"{stat.st_dev}:{stat.st_ino}"

    def _checkpoint(self, conn, file, offset):
        anchor_start = max(0, offset - 256)
        file.seek(anchor_start)
        digest = hashlib.sha256(file.read(offset - anchor_start)).hexdigest()
        conn.execute("INSERT OR REPLACE INTO history_checkpoint VALUES (1, ?, ?, ?, ?, ?)",
                     (str(self.log_path), self._identity(os.fstat(file.fileno())), offset, anchor_start, digest))

    def _validated_offset(self, conn, file):
        row = conn.execute("SELECT * FROM history_checkpoint WHERE singleton=1").fetchone()
        if row is None:
            return 0
        stat = os.fstat(file.fileno())
        # Backup restores can legitimately change path/inode; validate contents.
        if stat.st_size < row["offset"]:
            raise HistorySourceChanged("History journal was replaced or truncated; restore the original journal")
        file.seek(row["anchor_start"])
        digest = hashlib.sha256(file.read(row["offset"] - row["anchor_start"])).hexdigest()
        if digest != row["anchor_hash"]:
            raise HistorySourceChanged("Indexed history journal prefix changed; restore the original journal")
        return row["offset"]

    @staticmethod
    def _insert(conn, offset, record, message_id=None):
        conn.execute("""INSERT INTO history_messages
            (log_offset,timestamp,chat_id,chat_title,user_id,username,full_name,
             username_fold,full_name_fold,text,message_id) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (offset, record["timestamp"], str(record["chat_id"]), record["chat_title"],
             str(record["user_id"]), record["username"], record["full_name"],
             record["username"].casefold(), record["full_name"].casefold(), record["text"], message_id))

    def _import_record(self, conn, offset, raw):
        try:
            match = RECORD.match(raw.decode("utf-8").rstrip("\r\n"))
            if not match:
                raise ValueError("unrecognized record header")
            record = match.groupdict()
            record["timestamp"] = local_timestamp(datetime.fromisoformat(record["timestamp"]))
        except (UnicodeDecodeError, ValueError):
            conn.execute("INSERT INTO history_rejected VALUES (?, ?, ?)",
                         (offset, raw, "Invalid header, timestamp or UTF-8"))
            return
        self._insert(conn, offset, record)

    def synchronize(self, *, finalize_tail=True):
        """Index complete new records in bounded batches; retry safely after a crash."""
        with self._lock, closing(self._connect()) as conn:
            self._flush_pending(conn)
            if not self.log_path.exists():
                if conn.execute("SELECT 1 FROM history_checkpoint").fetchone():
                    raise HistorySourceChanged("Indexed history journal is missing")
                return
            with self.log_path.open("rb") as file:
                while True:
                    with conn:
                        conn.execute("BEGIN IMMEDIATE")
                        offset = self._validated_offset(conn, file)
                        file.seek(offset)
                        start, raw, count = offset, bytearray(), 0
                        complete_offset = offset
                        while True:
                            line_start = file.tell()
                            line = file.readline()
                            if not line:
                                if finalize_tail and raw and raw.endswith(b"\n"):
                                    self._import_record(conn, start, bytes(raw))
                                    complete_offset = file.tell()
                                break
                            if HEADER.match(line) and raw:
                                self._import_record(conn, start, bytes(raw))
                                complete_offset = line_start
                                count += 1
                                if count >= 1000:
                                    break
                                start, raw = line_start, bytearray()
                            raw.extend(line)
                        if complete_offset > offset:
                            self._checkpoint(conn, file, complete_offset)
                    if count < 1000:
                        return

    def _flush_pending(self, conn):
        with conn:
            conn.execute("BEGIN IMMEDIATE")
            pending = conn.execute("SELECT * FROM history_pending WHERE singleton=1").fetchone()
            if pending is None:
                return
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.log_path.open("a+b") as file:
                offset = self._validated_offset(conn, file)
                if offset != pending["log_offset"]:
                    raise HistorySourceChanged("Pending journal offset does not match checkpoint")
                file.seek(offset)
                raw = bytes(pending["raw"])
                existing = file.read(len(raw))
                if not raw.startswith(existing):
                    raise HistorySourceChanged("Journal tail conflicts with pending message")
                file.seek(0, os.SEEK_END)
                file.write(raw[len(existing):])
                file.flush()
                os.fsync(file.fileno())
                self._insert(conn, offset, json.loads(pending["record_json"]), pending["message_id"])
                # A live backup may include later journal entries than this
                # pending DB snapshot. Leave that tail for normal synchronization.
                self._checkpoint(conn, file, offset + len(raw))
                conn.execute("DELETE FROM history_pending WHERE singleton=1")

    def recover_pending(self):
        """Flush the last durable message before rolling back to a journal-only release."""
        if not self.path.exists():
            return
        with self._lock, closing(self._connect()) as conn:
            if conn.execute("SELECT 1 FROM sqlite_master WHERE name='history_pending'").fetchone():
                self._flush_pending(conn)

    def append(self, record: dict, message_id: int | None = None) -> None:
        """Durable pending row makes retries recover both journal and Telegram ID."""
        record = {**record, "timestamp": local_timestamp(datetime.fromisoformat(record["timestamp"]))}
        with self._lock:
            self.synchronize()
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with closing(self._connect()) as conn:
                with conn, self.log_path.open("a+b") as file:
                    conn.execute("BEGIN IMMEDIATE")
                    offset = self._validated_offset(conn, file)
                    if message_id is not None and conn.execute(
                        "SELECT 1 FROM history_messages WHERE chat_id=? AND message_id=?",
                        (str(record["chat_id"]), message_id),
                    ).fetchone():
                        return
                    file.seek(0, os.SEEK_END)
                    if offset != file.tell():
                        raise HistorySourceChanged("History journal has an incomplete or concurrently appended record")
                    line = (f"{record['timestamp']} - Chat {record['chat_id']} ({record['chat_title']}) "
                            f"- User {record['user_id']} ({record['username']}) [{record['full_name']}]: {record['text']}\n")
                    conn.execute("INSERT INTO history_pending VALUES (1, ?, ?, ?, ?)",
                                 (offset, json.dumps(record, ensure_ascii=False), line.encode("utf-8"), message_id))
                self._flush_pending(conn)

    @staticmethod
    def _where(chat_id, *, user_id=None, username=None, full_name=None, start=None, end=None,
               nonempty=False, exclude_commands=False, min_chars=None, after_id=None,
               through_id=None, exclude_texts=()):
        clauses, args = (["chat_id=?"], [str(chat_id)]) if chat_id is not None else (["1"], [])
        for column, value in (("user_id", user_id), ("username_fold", username.casefold() if username is not None else None),
                              ("full_name_fold", full_name.casefold() if full_name is not None else None)):
            if value is not None:
                clauses.append(f"{column}=?")
                args.append(str(value))
        for operator, value in ((">=", start), ("<=", end)):
            if value is not None:
                clauses.append(f"timestamp {operator} ?")
                args.append(local_timestamp(value))
        if nonempty:
            clauses.append("length(trim(text)) > 0")
        if exclude_commands:
            clauses.append("text NOT LIKE '/%'")
        if min_chars is not None:
            clauses.append("length(trim(text)) >= ?")
            args.append(min_chars)
        for operator, value in ((">", after_id), ("<=", through_id)):
            if value is not None:
                clauses.append(f"id {operator} ?")
                args.append(value)
        if exclude_texts:
            clauses.append("casefold(trim(text, ' !?.,' || char(10) || char(13))) NOT IN (" +
                           ",".join("?" for _ in exclude_texts) + ")")
            args.extend(exclude_texts)
        return " AND ".join(clauses), args

    def summary_boundary(self, chat_id, user_id):
        """Freeze the journal position before calling an external provider."""
        self.synchronize()
        with closing(self._connect()) as conn:
            conn.execute("BEGIN")
            previous = conn.execute("SELECT through_id, requested_at FROM history_summary_cursors "
                                    "WHERE chat_id=? AND user_id=?", (str(chat_id), str(user_id))).fetchone()
            latest = conn.execute("SELECT COALESCE(MAX(id), 0) FROM history_messages WHERE chat_id=?",
                                  (str(chat_id),)).fetchone()[0]
            return latest, dict(previous) if previous else None

    def acknowledge_summary(self, chat_id, user_id, through_id, requested_at):
        """A slower, older delivery must never move a reader's cursor backwards."""
        with closing(self._connect()) as conn, conn:
            conn.execute("""INSERT INTO history_summary_cursors VALUES (?, ?, ?, ?)
                ON CONFLICT(chat_id, user_id) DO UPDATE SET
                    through_id=excluded.through_id, requested_at=excluded.requested_at
                WHERE excluded.through_id > history_summary_cursors.through_id
                   OR (excluded.through_id = history_summary_cursors.through_id
                       AND excluded.requested_at > history_summary_cursors.requested_at)""",
                (str(chat_id), str(user_id), through_id, local_timestamp(requested_at)))

    def scan(self, chat_id, visitor, *, descending=False, **filters):
        """Stream only the selected chat/user to a synchronous visitor."""
        self.synchronize()
        where, args = self._where(chat_id, **filters)
        with closing(self._connect()) as conn:
            order = "DESC" if descending else "ASC"
            for row in conn.execute(f"SELECT * FROM history_messages WHERE {where} ORDER BY id {order}", args):
                if visitor(dict(row)) is False:
                    break

    def select(self, chat_id, *, limit=None, sample_size=None, recent_size=0, **filters):
        self.synchronize()
        where, args = self._where(chat_id, **filters)
        with closing(self._connect()) as conn:
            # One snapshot for the tail and older sample, even with concurrent appends.
            conn.execute("BEGIN")
            base = f"SELECT * FROM history_messages WHERE {where}"
            if sample_size is not None:
                capacity = max(0, sample_size)
                if capacity == 0:
                    return []
                recent = list(conn.execute(base + " ORDER BY id DESC LIMIT ?", [*args, min(max(0, recent_size), capacity)]))
                older_where, older_args = (" AND id < ?", [recent[-1]["id"]]) if recent else ("", [])
                older = list(conn.execute(base + older_where + " ORDER BY random() LIMIT ?",
                                          [*args, *older_args, capacity - len(recent)]))
                return [dict(row) for row in sorted(older + recent, key=lambda row: row["id"])]
            sql = base + (" ORDER BY id DESC LIMIT ?" if limit is not None else " ORDER BY id")
            rows = list(conn.execute(sql, [*args, max(0, limit)] if limit is not None else args))
            return [dict(row) for row in reversed(rows)] if limit is not None else [dict(row) for row in rows]

    def search(self, chat_id, query, *, limit=100):
        self.synchronize()
        terms = re.findall(r"\w+", query, re.UNICODE)[:20]
        if not terms:
            return []
        expression = " OR ".join('"' + term + '"' for term in terms)
        with closing(self._connect()) as conn:
            return [dict(row) for row in conn.execute("""SELECT m.* FROM history_fts
                JOIN history_messages m ON m.id=history_fts.rowid
                WHERE history_fts MATCH ? AND m.chat_id=? ORDER BY rank LIMIT ?""",
                (expression, str(chat_id), limit))]

    def count(self, chat_id, **filters):
        self.synchronize()
        where, args = self._where(chat_id, **filters)
        with closing(self._connect()) as conn:
            return conn.execute(f"SELECT COUNT(*) FROM history_messages WHERE {where}", args).fetchone()[0]

    def context(self, chat_id, row_id, radius=3):
        with closing(self._connect()) as conn:
            before = list(conn.execute("SELECT * FROM history_messages WHERE chat_id=? AND id<=? ORDER BY id DESC LIMIT ?",
                                       (str(chat_id), row_id, radius + 1)))
            after = list(conn.execute("SELECT * FROM history_messages WHERE chat_id=? AND id>? ORDER BY id LIMIT ?",
                                      (str(chat_id), row_id, radius)))
            return [dict(row) for row in list(reversed(before)) + after]

    def chat_name(self, chat_id):
        with closing(self._connect()) as conn:
            row = conn.execute("SELECT chat_title FROM history_messages WHERE chat_id=? AND length(trim(text))>0 ORDER BY id LIMIT 1",
                               (str(chat_id),)).fetchone()
            return row[0] if row else None

    def participants(self, chat_id, **filters):
        self.synchronize()
        where, args = self._where(chat_id, **filters)
        with closing(self._connect()) as conn:
            return [dict(row) for row in conn.execute(f"""SELECT m.*, counts.message_count
                FROM (SELECT user_id, COUNT(*) message_count, MAX(id) latest
                      FROM history_messages WHERE {where} GROUP BY user_id) counts
                JOIN history_messages m ON m.id=counts.latest ORDER BY counts.message_count DESC""", args)]

    def group_chat_ids(self):
        self.synchronize()
        with closing(self._connect()) as conn:
            return [int(row[0]) for row in conn.execute("SELECT DISTINCT chat_id FROM history_messages") if int(row[0]) < 0]
