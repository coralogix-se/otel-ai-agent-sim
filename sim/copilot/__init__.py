"""GitHub Copilot CLI simulator (OTLP spans/logs and enterprise collector metrics)."""

from sim.copilot.cli import emit_copilot_cli_session
from sim.copilot.collector_metrics import (
    copilot_collector_enabled,
    record_copilot_collector_session,
    register_copilot_collector_metrics,
)

__all__ = [
    "copilot_collector_enabled",
    "emit_copilot_cli_session",
    "record_copilot_collector_session",
    "register_copilot_collector_metrics",
]
