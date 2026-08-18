"""Local + live regression tests for AI Center dashboard queries (grouped by sim)."""
from __future__ import annotations

from typing import Any

import pytest

from tests.dashboard_regression.catalog import Check, all_checks, iter_catalogs
from tests.dashboard_regression.cx_client import (
    CxError,
    CxResult,
    promql_has_nonzero,
    query_dataprime,
    query_promql,
)


def _record_tag_map(row: dict[str, Any]) -> dict[str, Any]:
    """Collect span tags + log attributes from a cx JSON row."""
    out: dict[str, Any] = {}
    for blob_key in ("userData", "user_data"):
        blob = row.get(blob_key)
        if not isinstance(blob, dict):
            continue
        tags = blob.get("tags")
        if isinstance(tags, dict):
            out.update(tags)
        attrs = blob.get("attributes")
        if isinstance(attrs, dict):
            out.update(attrs)
        proc = blob.get("process")
        if isinstance(proc, dict) and isinstance(proc.get("tags"), dict):
            for k, v in proc["tags"].items():
                out.setdefault(k, v)
    labels = row.get("labels")
    if isinstance(labels, dict):
        for k, v in labels.items():
            out.setdefault(k, v)
    return out


def _rows_with_required_tags(result: CxResult, required: tuple[str, ...]) -> int:
    if not required:
        return result.row_count
    hits = 0
    for row in result.records:
        if not isinstance(row, dict):
            continue
        tags = _record_tag_map(row)
        ok = True
        for key in required:
            val = tags.get(key)
            if val is None or val == "" or val == "<redacted>":
                ok = False
                break
        if ok:
            hits += 1
    return hits


def _run_check(check: Check, profile: str | None) -> None:
    try:
        if check.kind == "dataprime":
            result = query_dataprime(
                check.query,
                start=check.start,
                end="now",
                limit=check.limit,
                profile=profile,
                tier=check.tier,
            )
        else:
            result = query_promql(check.query, profile=profile)
    except CxError as exc:
        pytest.fail(f"{check.id}: {exc}")

    if result.source_missing:
        pytest.fail(
            f"{check.id}: DataPrime source missing ({check.title}). "
            f"stderr={result.stderr.strip()[:400]}"
        )

    if not result.ok and not result.records:
        pytest.fail(
            f"{check.id}: query failed ({check.title}). "
            f"rc={result.returncode} stderr={result.stderr.strip()[:400]}"
        )

    if check.expect == "source_exists":
        return
    if check.expect == "has_rows":
        if result.row_count < 1:
            pytest.fail(
                f"{check.id}: expected rows for dashboard query ({check.title}); got 0. "
                f"{check.notes}"
            )
        if check.require_tags:
            hits = _rows_with_required_tags(result, check.require_tags)
            if hits < 1:
                sample = _record_tag_map(result.records[0]) if result.records else {}
                pytest.fail(
                    f"{check.id}: rows returned but missing required tags "
                    f"{list(check.require_tags)} ({check.title}). "
                    f"sample_keys={sorted(sample)[:30]}"
                )
        return
    if check.expect == "has_nonzero":
        if not promql_has_nonzero(result):
            pytest.fail(
                f"{check.id}: expected non-zero PromQL sample ({check.title}); "
                f"records={result.records!r}"
            )
        return
    pytest.fail(f"{check.id}: unknown expect {check.expect!r}")


@pytest.mark.catalog
def test_catalogs_load_and_are_grouped_by_sim() -> None:
    grouped = list(iter_catalogs())
    assert grouped, "expected at least one catalog YAML"
    sims = {sim for sim, _ in grouped}
    assert sims >= {"claude", "copilot", "gemini", "codex", "cursor", "anthropic_admin"}
    for sim, checks in grouped:
        assert checks, f"{sim} catalog empty"
        ids = [c.id for c in checks]
        assert len(ids) == len(set(ids)), f"duplicate check ids in {sim}"
        for c in checks:
            assert c.query, f"{c.id} missing query"
            assert c.kind in ("dataprime", "promql")


@pytest.mark.catalog
def test_claude_catalog_covers_session_analyze_source_drift() -> None:
    """The Aug 2026 'No messages' incident: UI source != TCO dataset."""
    claude = next(checks for sim, checks in iter_catalogs() if sim == "claude")
    ids = {c.id for c in claude}
    assert "claude.session_messages.ui_dataset" in ids
    assert "claude.session_messages.tco_dataset" in ids
    ui = next(c for c in claude if c.id == "claude.session_messages.ui_dataset")
    tco = next(c for c in claude if c.id == "claude.session_messages.tco_dataset")
    assert "ai.sessions.claude" in ui.query
    assert "ai_sessions_claude" in tco.query


@pytest.mark.live
@pytest.mark.parametrize(
    "check",
    all_checks(),
    ids=lambda c: c.id,
)
def test_dashboard_query_has_data(
    check: Check,
    cx_profile: str | None,
    selected_sims: set[str] | None,
    require_cx: None,
) -> None:
    if selected_sims and check.sim not in selected_sims:
        pytest.skip(f"sim filter excludes {check.sim}")
    if check.xfail_reason:
        pytest.xfail(check.xfail_reason)
    _run_check(check, cx_profile)
