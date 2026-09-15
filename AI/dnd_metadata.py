"""Explicit composition policy for DnD campaign metadata processing."""
from __future__ import annotations


class DndMetadataPolicy:
    """Apply ordered text preprocessors before the captured metadata parser."""

    def __init__(self, downstream):
        self.downstream = downstream
        self.preprocessors = []

    def add_preprocessor(self, preprocessor):
        if preprocessor not in self.preprocessors:
            self.preprocessors.append(preprocessor)
        return self

    def apply(self, session, text):
        current = text
        for preprocessor in self.preprocessors:
            current = preprocessor(current)
        return self.downstream(session, current)


def configure_dnd_metadata(campaign, policy: DndMetadataPolicy) -> DndMetadataPolicy:
    """Attach one stable campaign delegator and return the active policy."""
    existing = getattr(campaign, "_upupa_dnd_metadata_policy", None)
    if existing is not None:
        return existing

    campaign._apply_metadata = lambda session, text: policy.apply(session, text)
    campaign._upupa_dnd_metadata_policy = policy
    return policy
