"""Dependency-free adapter for Partitura's Chew/Wu voice separator."""

from .voice_separation import estimate_voices

__all__ = ["estimate_voices"]
