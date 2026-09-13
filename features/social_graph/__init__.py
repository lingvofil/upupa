"""Social graph feature."""

from features.social_graph.service import (
    DEFAULT_PERIOD_DAYS,
    MENTION_WEIGHT,
    REACTION_WEIGHT,
    REPLY_WEIGHT,
    configure_social_graph_repository,
    get_graph_data,
    init_db,
    is_social_graph_enabled,
)
from features.chronicle.integration import ChronicleSocialInteractionMiddleware

# Keep bootstrap's existing import contract while extending the already-installed
# social accounting middleware with cheap Chronicle candidate capture.
SocialInteractionMiddleware = ChronicleSocialInteractionMiddleware

__all__ = [
    "DEFAULT_PERIOD_DAYS",
    "MENTION_WEIGHT",
    "REACTION_WEIGHT",
    "REPLY_WEIGHT",
    "SocialInteractionMiddleware",
    "configure_social_graph_repository",
    "get_graph_data",
    "init_db",
    "is_social_graph_enabled",
]
