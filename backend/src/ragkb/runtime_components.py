"""Stable public facade for runtime component assembly.

The implementation lives under :mod:`ragkb.runtime_profiles` so the API layer does
not become coupled to profile-specific adapter selection.
"""

from ragkb.runtime_profiles.assembly import RuntimeComponents, build_runtime_components

__all__ = ["RuntimeComponents", "build_runtime_components"]
