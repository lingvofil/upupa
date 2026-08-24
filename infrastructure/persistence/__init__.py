"""Persistence adapters for durable application state."""

from infrastructure.persistence.sqlite_social_graph import SQLiteSocialGraphRepository
from infrastructure.persistence.sqlite_statistics import SQLiteStatisticsRepository
from infrastructure.persistence.sqlite_world import SQLiteWorldRepository

__all__ = [
    "SQLiteSocialGraphRepository",
    "SQLiteStatisticsRepository",
    "SQLiteWorldRepository",
]
