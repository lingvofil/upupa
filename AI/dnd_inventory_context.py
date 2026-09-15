"""Explicit composition policy for DnD campaign inventory prompt context."""
from __future__ import annotations


class DndInventoryContextPolicy:
    """Render campaign inventory context through a runtime-selected dependency."""

    def __init__(self, renderer):
        self.renderer = renderer

    def render(self, campaign, session) -> str:
        return str(self.renderer(campaign, session) or "")


def configure_dnd_inventory_context(campaign, policy: DndInventoryContextPolicy) -> DndInventoryContextPolicy:
    """Attach one stable campaign delegator while keeping renderer selection explicit."""
    existing = getattr(campaign, "_upupa_dnd_inventory_context_policy", None)
    if existing is not None:
        return existing

    campaign._inventory_context = lambda session: policy.render(campaign, session)
    campaign._upupa_dnd_inventory_context_policy = policy
    return policy
