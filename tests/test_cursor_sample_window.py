"""Cursor Usage sparse sample windows (onlineboutique brief §8)."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from sim.cursor.usage_v2.sample_window import cursor_in_sample_window


def test_in_window_at_sample_hour(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SIM_CURSOR_USAGE_SPARSE_SAMPLES", "true")
    monkeypatch.setenv("SIM_CURSOR_USAGE_SAMPLE_HOURS_UTC", "11,23")
    monkeypatch.setenv("SIM_CURSOR_USAGE_SAMPLE_WINDOW_SEC", "90")
    monkeypatch.delenv("SIM_CURSOR_USAGE_SAMPLE_ALWAYS", raising=False)
    assert cursor_in_sample_window(datetime(2026, 10, 12, 11, 0, 30, tzinfo=timezone.utc))
    assert cursor_in_sample_window(datetime(2026, 10, 12, 23, 1, 0, tzinfo=timezone.utc))
    assert not cursor_in_sample_window(datetime(2026, 10, 12, 12, 0, 0, tzinfo=timezone.utc))


def test_always_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SIM_CURSOR_USAGE_SPARSE_SAMPLES", "true")
    monkeypatch.setenv("SIM_CURSOR_USAGE_SAMPLE_ALWAYS", "true")
    assert cursor_in_sample_window(datetime(2026, 10, 12, 3, 0, 0, tzinfo=timezone.utc))
