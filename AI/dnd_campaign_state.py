"""Explicit composition policy for DnD campaign session state."""
from __future__ import annotations


class DndCampaignStatePolicy:
    """Compose session initialization, persistence, and restore hooks."""

    def __init__(self, downstream_ensure, downstream_state, downstream_restore=None):
        self.downstream_ensure = downstream_ensure
        self.downstream_state = downstream_state
        self.downstream_restore = downstream_restore
        self.ensure_hooks = []
        self.state_fields = {}
        self.restore_hooks = []

    def add_ensure_hook(self, hook):
        if hook not in self.ensure_hooks:
            self.ensure_hooks.append(hook)
        return self

    def add_state_field(self, name: str, provider):
        self.state_fields.setdefault(str(name), provider)
        return self

    def add_restore_hook(self, hook):
        if hook not in self.restore_hooks:
            self.restore_hooks.append(hook)
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

    def restore(self, session, data) -> None:
        if self.downstream_restore is None:
            raise RuntimeError("DnD campaign state restore dependency is not configured")
        self.downstream_restore(session, data)
        for hook in self.restore_hooks:
            hook(session, data)


def configure_dnd_campaign_state(campaign, policy: DndCampaignStatePolicy) -> DndCampaignStatePolicy:
    """Attach stable campaign state delegators and return the active policy."""
    existing = getattr(campaign, "_upupa_dnd_campaign_state_policy", None)
    if existing is not None:
        if existing.downstream_restore is None and policy.downstream_restore is not None:
            existing.downstream_restore = policy.downstream_restore
            campaign._restore_state = existing.restore
        return existing

    campaign._ensure = policy.ensure
    campaign._state = policy.state
    if policy.downstream_restore is not None:
        campaign._restore_state = policy.restore
    campaign._upupa_dnd_campaign_state_policy = policy
    return policy
