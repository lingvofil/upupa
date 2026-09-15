"""Explicit composition policy for DnD campaign session state."""
from __future__ import annotations


class DndCampaignStatePolicy:
    """Compose session initialization hooks and persisted state fields."""

    def __init__(self, downstream_ensure, downstream_state):
        self.downstream_ensure = downstream_ensure
        self.downstream_state = downstream_state
        self.ensure_hooks = []
        self.state_fields = {}

    def add_ensure_hook(self, hook):
        if hook not in self.ensure_hooks:
            self.ensure_hooks.append(hook)
        return self

    def add_state_field(self, name: str, provider):
        self.state_fields.setdefault(str(name), provider)
        return self

    def ensure(self, session) -> None:
        self.downstream_ensure(session)
        for hook in self.ensure_hooks:
            hook(session)

    def state(self, session) -> dict:
        row = self.downstream_state(session)
        for name, provider in self.state_fields.items():
            row[name] = provider(session)
        return row


def configure_dnd_campaign_state(campaign, policy: DndCampaignStatePolicy) -> DndCampaignStatePolicy:
    """Attach stable campaign state delegators and return the active policy."""
    existing = getattr(campaign, "_upupa_dnd_campaign_state_policy", None)
    if existing is not None:
        return existing

    campaign._ensure = policy.ensure
    campaign._state = policy.state
    campaign._upupa_dnd_campaign_state_policy = policy
    return policy
