"""Copilot collector sparse sample window (brief §7)."""

from __future__ import annotations

import importlib.util
from datetime import datetime, timezone
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_SW = _ROOT / "sim" / "copilot" / "sample_window.py"


def _load_sample_window():
    # Avoid importing sim.copilot (pulls OTel LogRecord via cli).
    spec = importlib.util.spec_from_file_location("copilot_sample_window", _SW)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_copilot_window(monkeypatch: pytest.MonkeyPatch) -> None:
    mod = _load_sample_window()
    monkeypatch.setenv("SIM_COPILOT_SPARSE_SAMPLES", "true")
    monkeypatch.setenv("SIM_COPILOT_SAMPLE_HOUR_UTC", "20")
    monkeypatch.setenv("SIM_COPILOT_SAMPLE_WINDOW_SEC", "90")
    monkeypatch.delenv("SIM_COPILOT_SAMPLE_ALWAYS", raising=False)
    assert mod.copilot_in_sample_window(datetime(2026, 10, 12, 20, 0, 30, tzinfo=timezone.utc))
    assert not mod.copilot_in_sample_window(datetime(2026, 10, 12, 12, 0, 0, tzinfo=timezone.utc))
