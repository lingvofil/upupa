"""Shared test isolation."""

import pytest

from core.chronicle_sink import configure_chronicle_sink


@pytest.fixture(autouse=True)
def _detach_chronicle_sink():
    """Bootstrap tests wire the real Chronicle sink; never let it leak into other tests."""
    yield
    configure_chronicle_sink(None)
