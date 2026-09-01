"""International authority scoring for World of Upupa."""

from __future__ import annotations

from features.world.models import WorldProfile


def authority_from_counts(allies: int, wars: int) -> int:
    """Return authority without an upper cap; zero remains the natural floor."""
    return max(0, 50 + 8 * int(allies) - 5 * int(wars))


def calculate_authority(profile: WorldProfile) -> int:
    return authority_from_counts(len(profile.allies), len(profile.wars))
