"""Pytest fixtures for live Coralogix dashboard regression."""
from __future__ import annotations

import os

import pytest

from tests.dashboard_regression.cx_client import cx_available


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--sim",
        action="append",
        default=[],
        help="Only run checks for this sim (repeatable): claude, copilot, gemini, codex, cursor, anthropic_admin",
    )
    parser.addoption(
        "--cx-profile",
        action="store",
        default=None,
        help="Coralogix cx profile (default: CX_PROFILE / DASHBOARD_REGRESSION_PROFILE / cx default)",
    )


@pytest.fixture(scope="session")
def cx_profile(pytestconfig: pytest.Config) -> str | None:
    return (
        pytestconfig.getoption("--cx-profile")
        or os.environ.get("DASHBOARD_REGRESSION_PROFILE")
        or os.environ.get("CX_PROFILE")
    )


@pytest.fixture(scope="session")
def selected_sims(pytestconfig: pytest.Config) -> set[str] | None:
    sims = pytestconfig.getoption("--sim") or []
    return {s.lower() for s in sims} if sims else None


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "live: queries a live Coralogix tenant via cx CLI")
    config.addinivalue_line("markers", "catalog: validates local catalog YAML only")


@pytest.fixture(scope="session")
def require_cx() -> None:
    if os.environ.get("DASHBOARD_REGRESSION_SKIP_LIVE") == "1":
        pytest.skip("DASHBOARD_REGRESSION_SKIP_LIVE=1")
    if not cx_available():
        pytest.skip("`cx` CLI not on PATH")
