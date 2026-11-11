"""Игровая лаборатория: флаги экспериментов по чатам, журнал событий, команда «лаба»."""

from tests import test_smoke_imports  # noqa: F401  (env + моки до импорта handlers)

import asyncio
import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from core.json_repository import JsonFileRepository
from core.settings import ADMIN_ID
from features.lab import events as lab_events
from features.lab import flags as lab_flags
from features.lab.commands import LabCommand, apply_lab_command, parse_lab_command
from features.lab.events import EventLog, build_event, log_event, read_events
from features.lab.filters import ExperimentEnabled
from features.lab.flags import ExperimentFlags
from features.lab.registry import EXPERIMENTS, find_experiment, get_experiment

CHAT = -100500


class MemoryRepository:
    def __init__(self, value=None, error=None):
        self.value = value
        self.error = error
        self.saved = []

    def load(self):
        if self.error is not None:
            raise self.error
        return self.value

    def save(self, value):
        self.saved.append(value)


@pytest.fixture
def flags(monkeypatch):
    instance = ExperimentFlags(MemoryRepository(error=FileNotFoundError()))
    instance.load()
    monkeypatch.setattr(lab_flags, "_flags", instance)
    return instance


@pytest.fixture
def event_log(monkeypatch, tmp_path):
    log = EventLog(tmp_path / "lab_events.jsonl")
    monkeypatch.setattr(lab_events, "_event_log", log)
    return log


# --- реестр -----------------------------------------------------------------

def test_registry_resolves_keys_and_russian_aliases():
    assert find_experiment("oracle").key == "oracle"
    assert find_experiment("Оракул").key == "oracle"
    assert find_experiment("  МОЗГ ").key == "brain_chat"
    assert find_experiment("крокодил").key == "crocodile_lab"
    assert find_experiment("шахматы") is None


def test_registry_keys_are_unique_and_known():
    keys = [experiment.key for experiment in EXPERIMENTS]
    assert len(keys) == len(set(keys))
    with pytest.raises(KeyError):
        get_experiment("nope")


# --- флаги ------------------------------------------------------------------

def test_everything_is_disabled_by_default(flags):
    assert not flags.is_enabled(CHAT, "oracle")
    assert flags.enabled_in(CHAT) == []


def test_enable_disable_are_per_chat_and_report_changes(flags):
    assert flags.enable(CHAT, "oracle") is True
    assert flags.enable(CHAT, "oracle") is False
    assert flags.is_enabled(CHAT, "oracle")
    assert flags.is_enabled(str(CHAT), "oracle")
    assert not flags.is_enabled(-1, "oracle")

    assert flags.disable(CHAT, "oracle") is True
    assert flags.disable(CHAT, "oracle") is False
    assert flags.snapshot() == {}


def test_disable_all_returns_what_was_enabled(flags):
    flags.enable(CHAT, "guru")
    flags.enable(CHAT, "oracle")
    flags.enable(-1, "oracle")

    assert flags.disable_all(CHAT) == ["guru", "oracle"]
    assert flags.snapshot() == {"-1": ["oracle"]}


def test_load_tolerates_corrupt_state():
    for repository in (
        MemoryRepository(value=["not", "a", "dict"]),
        MemoryRepository(error=ValueError("broken json")),
    ):
        instance = ExperimentFlags(repository)
        instance.load()
        assert instance.snapshot() == {}

    instance = ExperimentFlags(MemoryRepository(value={"-1": ["oracle"], "-2": "garbage", "-3": []}))
    instance.load()
    assert instance.snapshot() == {"-1": ["oracle"]}


def test_persist_round_trips_through_json_file(tmp_path):
    path = tmp_path / "lab_experiments.json"
    instance = ExperimentFlags(JsonFileRepository(path))
    instance.enable(CHAT, "oracle")
    instance.enable(CHAT, "guru")

    asyncio.run(instance.persist())

    assert json.loads(path.read_text(encoding="utf-8")) == {str(CHAT): ["guru", "oracle"]}
    reloaded = ExperimentFlags(JsonFileRepository(path))
    reloaded.load()
    assert reloaded.is_enabled(CHAT, "guru")


# --- команда ----------------------------------------------------------------

@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("лаба", LabCommand("status")),
        ("  Лаба  ", LabCommand("status")),
        ("лаба вкл", LabCommand("status")),
        ("лаба вкл оракул", LabCommand("enable", "оракул")),
        ("лаба   выкл   oracle", LabCommand("disable", "oracle")),
        ("лаба выкл все", LabCommand("disable_all")),
        ("лаба выкл всё", LabCommand("disable_all")),
    ],
)
def test_parse_lab_command(text, expected):
    assert parse_lab_command(text) == expected


@pytest.mark.parametrize("text", [None, "", "лабадуда", "лаба привет", "скажи лаба", "лаба вкл а б"])
def test_parse_ignores_other_text(text):
    assert parse_lab_command(text) is None


def test_status_lists_every_experiment_with_its_state(flags):
    flags.enable(CHAT, "guru")

    result = apply_lab_command(flags, CHAT, LabCommand("status"))

    assert not result.changed
    assert "🌐 guru" in result.text
    assert "❌ oracle" in result.text
    assert all(experiment.key in result.text for experiment in EXPERIMENTS)


def test_enable_by_alias_and_repeat(flags):
    first = apply_lab_command(flags, CHAT, LabCommand("enable", "оракул"))
    second = apply_lab_command(flags, CHAT, LabCommand("enable", "oracle"))

    assert first.toggled == (("oracle", True),)
    assert not second.changed
    assert flags.is_enabled(CHAT, "oracle")


def test_unknown_experiment_is_rejected_with_list(flags):
    result = apply_lab_command(flags, CHAT, LabCommand("enable", "шахматы"))

    assert not result.changed
    assert "шахматы" in result.text and "oracle" in result.text
    assert flags.snapshot() == {}


def test_disable_and_disable_all(flags):
    flags.enable(CHAT, "oracle")
    flags.enable(CHAT, "brain_chat")

    single = apply_lab_command(flags, CHAT, LabCommand("disable", "мозг"))
    everything = apply_lab_command(flags, CHAT, LabCommand("disable_all"))
    nothing = apply_lab_command(flags, CHAT, LabCommand("disable_all"))

    assert single.toggled == (("brain_chat", False),)
    assert everything.toggled == (("oracle", False),)
    assert not nothing.changed


def test_public_experiment_needs_no_flag(flags):
    for action in ("enable", "disable"):
        result = apply_lab_command(flags, CHAT, LabCommand(action, "гуру"))
        assert not result.changed
        assert "всех чатах" in result.text
    assert flags.snapshot() == {}

    guru = ExperimentEnabled("guru")
    assert asyncio.run(guru(SimpleNamespace(chat=SimpleNamespace(id=-1)))) is True
    assert asyncio.run(guru(SimpleNamespace())) is False


# --- журнал событий ---------------------------------------------------------

def test_build_event_shape():
    moment = datetime(2026, 11, 7, 18, 4, 5, 123456, tzinfo=timezone.utc)

    record = build_event(
        "oracle", "finish", chat_id=CHAT, user_id=42, session_id="abc", data={"hits": 14}, now=moment
    )

    assert record == {
        "v": 1,
        "ts": "2026-11-07T18:04:05.123+00:00",
        "exp": "oracle",
        "event": "finish",
        "chat_id": CHAT,
        "user_id": 42,
        "session": "abc",
        "data": {"hits": 14},
    }
    with pytest.raises(ValueError):
        build_event("", "start", chat_id=CHAT)


def test_event_log_appends_lines_and_reader_skips_broken_tail(tmp_path):
    path = tmp_path / "lab_events.jsonl"
    log = EventLog(path)
    log.append(build_event("oracle", "start", chat_id=CHAT, data={"word": "ёж"}))
    log.append(build_event("oracle", "finish", chat_id=CHAT, data={"at": datetime(2026, 1, 1)}))
    with path.open("a", encoding="utf-8") as file:
        file.write('{"v":1,"exp":"oracle","ev')  # оборванная запись при аварии

    records = list(read_events(path))

    assert [record["event"] for record in records] == ["start", "finish"]
    assert "ёж" in path.read_text(encoding="utf-8")  # без \u-экранирования
    assert records[1]["data"]["at"] == "2026-01-01 00:00:00"


def test_log_event_writes_record(event_log):
    asyncio.run(log_event("guru", "action", chat_id=CHAT, user_id=7, session_id="s1", move="barnum"))

    (record,) = read_events(event_log.path)
    assert record["exp"] == "guru"
    assert record["user_id"] == 7
    assert record["session"] == "s1"
    assert record["data"] == {"move": "barnum"}


def test_log_event_never_raises(monkeypatch):
    class BrokenLog:
        def append(self, record):
            raise OSError("disk full")

    monkeypatch.setattr(lab_events, "_event_log", BrokenLog())

    asyncio.run(log_event("oracle", "start", chat_id=CHAT))
    asyncio.run(log_event("", "start", chat_id=CHAT))


# --- фильтр -----------------------------------------------------------------

def test_experiment_filter_follows_chat_flags(flags):
    message = SimpleNamespace(chat=SimpleNamespace(id=CHAT))
    callback = SimpleNamespace(message=message)
    no_chat = SimpleNamespace()
    oracle = ExperimentEnabled("oracle")

    assert asyncio.run(oracle(message)) is False
    flags.enable(CHAT, "oracle")
    assert asyncio.run(oracle(message)) is True
    assert asyncio.run(oracle(callback)) is True
    assert asyncio.run(oracle(no_chat)) is False


def test_experiment_filter_rejects_typo_at_import():
    with pytest.raises(KeyError):
        ExperimentEnabled("oracel")


# --- хэндлер ----------------------------------------------------------------

def _message(text, user_id=ADMIN_ID):
    replies = []

    async def reply(answer, **kwargs):
        replies.append(answer)

    return SimpleNamespace(
        text=text,
        chat=SimpleNamespace(id=CHAT),
        from_user=SimpleNamespace(id=user_id),
        reply=reply,
        replies=replies,
    )


def _handler_accepts(message):
    from handlers.lab import router

    (handler,) = router.message.handlers
    passed, _ = asyncio.run(handler.check(message))
    return passed


def test_router_accepts_only_admin_lab_commands():
    assert _handler_accepts(_message("лаба"))
    assert not _handler_accepts(_message("лаба", user_id=ADMIN_ID + 1))
    assert not _handler_accepts(_message("привет"))


def test_handler_enables_persists_and_logs(monkeypatch, tmp_path, event_log):
    from handlers.lab import handle_lab_command

    path = tmp_path / "lab_experiments.json"
    instance = ExperimentFlags(JsonFileRepository(path))
    monkeypatch.setattr(lab_flags, "_flags", instance)
    message = _message("лаба вкл оракул")

    asyncio.run(handle_lab_command(message))

    assert message.replies == ["Оракул: включено в этом чате."]
    assert json.loads(path.read_text(encoding="utf-8")) == {str(CHAT): ["oracle"]}
    (record,) = read_events(event_log.path)
    assert (record["exp"], record["event"], record["user_id"]) == ("oracle", "lab_enabled", ADMIN_ID)


def test_handler_status_does_not_write(monkeypatch, event_log):
    from handlers.lab import handle_lab_command

    repository = MemoryRepository(error=FileNotFoundError())
    monkeypatch.setattr(lab_flags, "_flags", ExperimentFlags(repository))
    message = _message("лаба")

    asyncio.run(handle_lab_command(message))

    assert message.replies[0].startswith("Лаба в этом чате:")
    assert repository.saved == []
    assert not event_log.path.exists()


def test_handler_reports_failed_save(monkeypatch, event_log):
    from handlers.lab import handle_lab_command

    class FailingRepository(MemoryRepository):
        def save(self, value):
            raise OSError("read-only fs")

    monkeypatch.setattr(lab_flags, "_flags", ExperimentFlags(FailingRepository()))
    message = _message("лаба вкл мозг")

    asyncio.run(handle_lab_command(message))

    assert "не сохранилось" in message.replies[0]
    assert not event_log.path.exists()


# --- backup -----------------------------------------------------------------

def test_runtime_backup_includes_lab_state(tmp_path):
    from scripts.backup_runtime_state import create_backup

    source = tmp_path / "app"
    source.mkdir()
    (source / "lab_events.jsonl").write_text('{"exp":"oracle"}\n', encoding="utf-8")
    (source / "lab_experiments.json").write_text('{"-1": ["oracle"]}', encoding="utf-8")

    backup = create_backup(source, tmp_path / "backups", "lab-test")

    manifest = json.loads((backup / "manifest.json").read_text(encoding="utf-8"))
    names = {item["name"] for item in manifest["files"]}
    assert {"lab_events.jsonl", "lab_experiments.json"} <= names
