"""Explicit composition policy for DnD campaign metadata processing."""
from __future__ import annotations


class DndMetadataPolicy:
    """Apply ordered text preprocessors and result postprocessors around metadata parsing."""

    def __init__(self, downstream):
        self.downstream = downstream
        self.preprocessors = []
        self.postprocessors = []

    def add_preprocessor(self, preprocessor):
        if preprocessor not in self.preprocessors:
            self.preprocessors.append(preprocessor)
        return self

    def add_postprocessor(self, postprocessor):
        if postprocessor not in self.postprocessors:
            self.postprocessors.append(postprocessor)
        return self

    def apply(self, session, text):
        original_text = text
        current = text
        for preprocessor in self.preprocessors:
            current = preprocessor(current)
        cleaned, notices = self.downstream(session, current)
        for postprocessor in self.postprocessors:
            cleaned, notices = postprocessor(session, original_text, cleaned, notices)
        return cleaned, notices


def configure_dnd_metadata(campaign, policy: DndMetadataPolicy) -> DndMetadataPolicy:
    """Attach one stable campaign delegator and return the active policy."""
    existing = getattr(campaign, "_upupa_dnd_metadata_policy", None)
    if existing is not None:
        return existing

    campaign._apply_metadata = lambda session, text: policy.apply(session, text)
    campaign._upupa_dnd_metadata_policy = policy
    return policy
