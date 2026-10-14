"""Standalone Anthropic Admin + Claude Products analytics simulator (not Claude Code CLI)."""

from __future__ import annotations

from typing import Any

__all__ = ["AnthropicAdminSim"]


def __getattr__(name: str) -> Any:
    if name == "AnthropicAdminSim":
        from sim.anthropic_admin.runtime import AnthropicAdminSim

        return AnthropicAdminSim
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
