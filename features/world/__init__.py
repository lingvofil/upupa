"""World of Upupa application feature."""

from features.world.service import (
    WorldService,
    configure_world_service,
    get_world_service,
    is_world_enabled,
)

__all__ = [
    "WorldService",
    "configure_world_service",
    "get_world_service",
    "is_world_enabled",
]
