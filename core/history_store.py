"""Explicitly configured history index; custom legacy files remain readable."""

from pathlib import Path


_repository = None


def configure_history_repository(repository):
    global _repository
    _repository = repository


def get_history_repository(log_path):
    if _repository is not None and Path(log_path).resolve() == _repository.log_path:
        return _repository
    return None
