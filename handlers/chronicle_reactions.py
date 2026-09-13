"""Reaction transport shared by Chronicle and the existing social graph."""

from __future__ import annotations

import logging

from aiogram import Router, types

from features.chronicle.runtime import (
    capture_reaction as capture_chronicle_reaction,
    capture_reaction_count as capture_chronicle_reaction_count,
)
from features.social_graph.service import capture_reaction as capture_social_reaction


router = Router(name="chronicle_reactions")


@router.message_reaction()
async def handle_message_reaction(update: types.MessageReactionUpdated):
    """Preserve social-graph accounting and feed the same update to Chronicle."""
    try:
        await capture_social_reaction(update)
    except Exception as exc:
        logging.error("Failed to capture social interaction: %s", exc, exc_info=True)
    try:
        await capture_chronicle_reaction(update)
    except Exception as exc:
        logging.error("[chronicle] reaction capture failed: %s", exc, exc_info=True)


@router.message_reaction_count()
async def handle_message_reaction_count(update: types.MessageReactionCountUpdated):
    """Use anonymous/aggregate reaction counts without inventing unique reactors."""
    try:
        await capture_chronicle_reaction_count(update)
    except Exception as exc:
        logging.error("[chronicle] reaction-count capture failed: %s", exc, exc_info=True)
