"""Thin wrapper around the Coralogix ``cx`` CLI for dashboard regression checks."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from typing import Any


class CxError(RuntimeError):
    """Raised when ``cx`` is missing, fails auth, or returns a query error."""


@dataclass(frozen=True)
class CxResult:
    ok: bool
    records: list[Any]
    stdout: str
    stderr: str
    returncode: int

    @property
    def source_missing(self) -> bool:
        blob = f"{self.stderr}\n{self.stdout}".lower()
        return any(
            s in blob
            for s in (
                "source does not exist",
                "no matching datasets",
                "dataset not found",
            )
        )

    @property
    def row_count(self) -> int:
        return len(self.records)


def cx_available() -> bool:
    return shutil.which("cx") is not None


def _profile_args(profile: str | None) -> list[str]:
    p = profile or os.environ.get("CX_PROFILE") or os.environ.get("DASHBOARD_REGRESSION_PROFILE")
    return ["-p", p] if p else []


def run_cx(
    args: list[str],
    *,
    profile: str | None = None,
    timeout_sec: int = 120,
) -> CxResult:
    if not cx_available():
        raise CxError("`cx` CLI not found on PATH (install from https://get.coralogix.dev/cli)")

    cmd = ["cx", *_profile_args(profile), *args, "-o", "json"]
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout_sec,
        check=False,
    )
    stdout = proc.stdout or ""
    stderr = proc.stderr or ""
    records: list[Any] = []
    # cx may print progress lines before JSON; take the last JSON value.
    for candidate in _json_candidates(stdout):
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, list):
            records = parsed
            break
        if isinstance(parsed, dict):
            if "records" in parsed and isinstance(parsed["records"], list):
                records = parsed["records"]
            else:
                records = [parsed]
            break

    err_blob = f"{stderr}\n{stdout}".lower()
    hard_fail = any(
        s in err_blob
        for s in (
            "source does not exist",
            "no matching datasets",
            "dataset not found",
            "compilation errors",
            "unauthorized",
            "permission denied",
            "authentication",
            "api request failed",
        )
    )
    ok = proc.returncode == 0 and not hard_fail
    return CxResult(ok=ok, records=records, stdout=stdout, stderr=stderr, returncode=proc.returncode)


def _json_candidates(stdout: str) -> list[str]:
    text = stdout.strip()
    if not text:
        return []
    # Prefer whole stdout, then last [...] / {...} block.
    out = [text]
    for opener, closer in (("[", "]"), ("{", "}")):
        start = text.rfind(opener)
        end = text.rfind(closer)
        if start != -1 and end != -1 and end > start:
            out.append(text[start : end + 1])
    return out


def query_dataprime(
    query: str,
    *,
    start: str = "now-24h",
    end: str = "now",
    limit: int = 20,
    profile: str | None = None,
    tier: str | None = None,
) -> CxResult:
    args = [
        "dataprime",
        "query",
        "--start",
        start,
        "--end",
        end,
        "--limit",
        str(limit),
        query,
    ]
    if tier:
        args[2:2] = ["--tier", tier]
    return run_cx(args, profile=profile)


def query_promql(
    expr: str,
    *,
    profile: str | None = None,
) -> CxResult:
    return run_cx(["metrics", "query", expr], profile=profile)


def promql_has_nonzero(result: CxResult) -> bool:
    """True if any instant vector sample is a finite number != 0."""
    if result.source_missing or not result.ok and not result.records:
        return False
    for row in result.records:
        if not isinstance(row, dict):
            continue
        value = row.get("value")
        if isinstance(value, list) and len(value) >= 2:
            try:
                if float(value[1]) != 0.0:
                    return True
            except (TypeError, ValueError):
                continue
        # Some shapes nest under "data" / "result"
        data = row.get("data")
        if isinstance(data, dict):
            for sample in data.get("result") or []:
                v = sample.get("value") if isinstance(sample, dict) else None
                if isinstance(v, list) and len(v) >= 2:
                    try:
                        if float(v[1]) != 0.0:
                            return True
                    except (TypeError, ValueError):
                        continue
    return False
