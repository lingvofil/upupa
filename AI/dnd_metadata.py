"""Explicit composition policy for DnD campaign metadata processing."""
from __future__ import annotations


class DndMetadataPolicy:
    """Compose ordered around, pre-, downstream, and post-processors."""

    def __init__(self, downstream):
        self.downstream = downstream
        self.preprocessors = []
        self.downstream_processors = []
        self.postprocessors = []
        self.around_processors = []

    def add_preprocessor(self, preprocessor):
        if preprocessor not in self.preprocessors:
            self.preprocessors.append(preprocessor)
        return self

    def add_downstream_processor(self, processor):
        if processor not in self.downstream_processors:
            self.downstream_processors.append(processor)
        return self

    def add_postprocessor(self, postprocessor):
        if postprocessor not in self.postprocessors:
            self.postprocessors.append(postprocessor)
        return self

    def add_around_processor(self, around_processor):
        if around_processor not in self.around_processors:
            self.around_processors.append(around_processor)
        return self

    def _apply_downstream(self, session, text):
        def invoke(index, current_session, current_text):
            if index >= len(self.downstream_processors):
                return self.downstream(current_session, current_text)
            processor = self.downstream_processors[index]
            return processor(
                current_session,
                current_text,
                lambda next_session, next_text: invoke(index + 1, next_session, next_text),
            )

        return invoke(0, session, text)

    def _apply_core(self, session, text):
        original_text = text
        current = text
        for preprocessor in self.preprocessors:
            current = preprocessor(current)
        cleaned, notices = self._apply_downstream(session, current)
        for postprocessor in self.postprocessors:
            cleaned, notices = postprocessor(session, original_text, cleaned, notices)
        return cleaned, notices

    def apply(self, session, text):
        def invoke(index, current_session, current_text):
            if index >= len(self.around_processors):
                return self._apply_core(current_session, current_text)
            around_processor = self.around_processors[index]
            return around_processor(
                current_session,
                current_text,
                lambda next_session, next_text: invoke(index + 1, next_session, next_text),
            )

        return invoke(0, session, text)


def configure_dnd_metadata(campaign, policy: DndMetadataPolicy) -> DndMetadataPolicy:
    """Attach one stable campaign delegator and return the active policy."""
    existing = getattr(campaign, "_upupa_dnd_metadata_policy", None)
    if existing is not None:
        return existing

    campaign._apply_metadata = lambda session, text: policy.apply(session, text)
    campaign._upupa_dnd_metadata_policy = policy
    return policy
