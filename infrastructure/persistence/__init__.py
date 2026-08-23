"""Persistence adapters for durable application state."""

from infrastructure.persistence.sqlite_statistics import SQLiteStatisticsRepository

__all__ = ["SQLiteStatisticsRepository"]
