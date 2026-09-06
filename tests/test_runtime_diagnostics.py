from types import SimpleNamespace

import app.diagnostics as diagnostics
from infrastructure.ai.execution import AIExecutionSnapshot


def test_runtime_diagnostics_combines_resources_ai_and_supervisor_metrics(monkeypatch):
    snapshot = AIExecutionSnapshot(
        waiting=2,
        waiting_background=1,
        in_flight=1,
        in_flight_background=0,
        completed=12,
        errors=2,
        queue_timeouts=3,
        request_timeouts=4,
        queue_samples=10,
        queue_wait_avg_ms=25,
        queue_wait_p95_ms=80,
        request_samples=11,
        request_latency_avg_ms=450,
        request_latency_p95_ms=1200,
        provider_samples=9,
        provider_latency_avg_ms=400,
        provider_latency_p95_ms=1000,
    )
    monkeypatch.setattr(diagnostics, "get_ai_execution_snapshot", lambda: snapshot)
    monkeypatch.setattr(
        diagnostics,
        "collect_resource_snapshot",
        lambda: {"process": {"rss_bytes": 123}},
    )
    supervisor = SimpleNamespace(
        task_names=("alpha", "beta"),
        recovering_task_names=("beta",),
        restart_count=5,
        restart_counts={"beta": 5},
    )

    payload = diagnostics.build_runtime_diagnostics(supervisor)

    assert payload["resources"]["process"]["rss_bytes"] == 123
    assert payload["ai"]["waiting"] == 2
    assert payload["ai"]["request_latency_p95_ms"] == 1200
    assert payload["background_tasks"] == {
        "active": ["alpha", "beta"],
        "recovering": ["beta"],
        "restart_total": 5,
        "restart_counts": {"beta": 5},
    }


def test_collect_resource_snapshot_reports_proc_memory_disk_and_state_sizes(
    monkeypatch,
    tmp_path,
):
    status_path = tmp_path / "status"
    meminfo_path = tmp_path / "meminfo"
    fd_path = tmp_path / "fd"
    fd_path.mkdir()
    (fd_path / "1").write_text("", encoding="utf-8")
    (fd_path / "2").write_text("", encoding="utf-8")

    journal = tmp_path / "user_messages.log"
    history = tmp_path / "history.db"
    statistics = tmp_path / "statistics.db"
    world = tmp_path / "world.db"
    ledger = tmp_path / "history_deletions.jsonl"
    for path, payload in (
        (journal, b"journal"),
        (history, b"history-db"),
        (statistics, b"stats"),
        (world, b"world"),
        (ledger, b"ledger"),
    ):
        path.write_bytes(payload)

    backups = tmp_path / "upupa-backups"
    first = backups / "first"
    second = backups / "second"
    first.mkdir(parents=True)
    second.mkdir()
    (first / "manifest.json").write_text("{}", encoding="utf-8")
    (second / "manifest.json").write_text("{}", encoding="utf-8")
    first_chunk = first / "chunk"
    second_chunk = second / "chunk"
    first_chunk.write_bytes(b"same")
    second_chunk.hardlink_to(first_chunk)

    monkeypatch.setattr(diagnostics, "_PROC_STATUS_PATH", status_path)
    monkeypatch.setattr(diagnostics, "_PROC_MEMINFO_PATH", meminfo_path)
    monkeypatch.setattr(diagnostics, "_PROC_FD_PATH", fd_path)
    monkeypatch.setattr(diagnostics, "USER_MESSAGES_LOG_PATH", journal)
    monkeypatch.setattr(diagnostics, "HISTORY_DB_PATH", history)
    monkeypatch.setattr(diagnostics, "STATISTICS_DB_PATH", statistics)
    monkeypatch.setattr(diagnostics, "WORLD_DB_PATH", world)
    monkeypatch.setattr(diagnostics, "DELETION_LEDGER_PATH", ledger)
    monkeypatch.setattr(diagnostics, "DEPLOY_BACKUPS_PATH", backups)
    monkeypatch.setattr(diagnostics, "_process_uptime_seconds", lambda: 123.4)
    monkeypatch.setattr(
        diagnostics,
        "_parse_kib_file",
        lambda path: (
            {"VmRSS": 111, "VmSwap": 22, "Threads": 7}
            if path == status_path
            else {
                "MemTotal": 1000,
                "MemAvailable": 400,
                "SwapTotal": 500,
                "SwapFree": 300,
            }
        ),
    )
    monkeypatch.setattr(
        diagnostics.shutil,
        "disk_usage",
        lambda path: SimpleNamespace(total=10_000, used=7_000, free=3_000),
    )
    monkeypatch.setattr(diagnostics, "_allocated_bytes", lambda stat_result: stat_result.st_size)

    payload = diagnostics.collect_resource_snapshot()

    assert payload["process"] == {
        "rss_bytes": 111,
        "vm_swap_bytes": 22,
        "threads": 7,
        "open_fds": 2,
        "uptime_seconds": 123.4,
    }
    assert payload["system_memory"] == {
        "total_bytes": 1000,
        "available_bytes": 400,
        "used_bytes": 600,
    }
    assert payload["system_swap"]["used_bytes"] == 200
    assert payload["filesystem"]["free_bytes"] == 3_000
    assert payload["state_files"] == {
        "user_messages_log_bytes": len(b"journal"),
        "history_db_bytes": len(b"history-db"),
        "statistics_db_bytes": len(b"stats"),
        "world_db_bytes": len(b"world"),
        "deletion_ledger_bytes": len(b"ledger"),
    }
    assert payload["deploy_backups"]["snapshot_count"] == 2
    assert payload["deploy_backups"]["file_count"] == 4
    # Logical size counts both hard-link directory entries; physical counts the inode once.
    assert payload["deploy_backups"]["logical_bytes"] == 8
    assert payload["deploy_backups"]["physical_bytes"] == 4
