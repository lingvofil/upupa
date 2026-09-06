"""Explicitly configured history index; custom legacy files remain readable."""

from pathlib import Path


_repository = None


def configure_history_repository(repository):
    """Install the production history repository with durable deletion support."""
    global _repository

    # Keep tests/custom repositories compatible, but wrap the actual SQLite
    # history used by the application. The base index is already initialized by
    # bootstrap; the managed layer replays the append-only deletion ledger.
    from infrastructure.persistence.managed_history import ManagedHistoryRepository
    from infrastructure.persistence.sqlite_history import SQLiteHistoryRepository

    if isinstance(repository, SQLiteHistoryRepository):
        repository = ManagedHistoryRepository(repository)
    _repository = repository


def get_history_repository(log_path):
    if _repository is not None and Path(log_path).resolve() == _repository.log_path:
        return _repository
    return None
