"""Shared test isolation."""

import sys

import pytest

from core.chronicle_sink import configure_chronicle_sink


@pytest.fixture(autouse=True)
def _detach_chronicle_sink():
    """Bootstrap tests wire the real Chronicle sink; never let it leak into other tests."""
    yield
    configure_chronicle_sink(None)


@pytest.fixture(autouse=True)
def _forget_demoted_dnd_models():
    """Simulated 503s must not reorder the DnD model queue for later tests."""
    yield
    resilience = sys.modules.get("AI.dnd_generation_resilience")
    if resilience is not None:
        resilience._model_failure_streak.clear()
        resilience._model_demoted_until.clear()
