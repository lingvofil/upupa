"""Compatibility alias for the current DnD implementation."""

import sys

from AI import dnd_game as _implementation

sys.modules[__name__] = _implementation
