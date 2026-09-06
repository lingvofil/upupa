"""Safe process-local diagnostics exposed only through the loopback HTTP server."""

from dataclasses import asdict
import os

from infrastructure.ai.execution import get_ai_execution_snapshot


def build_runtime_diagnostics(supervisor) -> dict:
    """Return bounded, non-sensitive runtime counters for operational debugging."""
    return {
        "pid": os.getpid(),
        "ai": asdict(get_ai_execution_snapshot()),
        "background_tasks": {
            "active": list(supervisor.task_names),
            "recovering": list(getattr(supervisor, "recovering_task_names", ())),
            "restart_total": int(getattr(supervisor, "restart_count", 0)),
            "restart_counts": dict(getattr(supervisor, "restart_counts", {})),
        },
    }


__all__ = ["build_runtime_diagnostics"]
