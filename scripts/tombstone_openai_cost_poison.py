#!/usr/bin/env python3
"""Zero abandoned openai_administration_cost_usd series left by the stacking bug.

The API Platform UI does ``sum(last_over_time(openai_administration_cost_usd[Nd]))``.
Series created every minute with new label sets (Oct 1–6) still inflate that sum until
they age out of the lookback. Re-writing the same labels at 0 makes last_over_time
return 0 immediately.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time

import cramjam
import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from prompb.remote_write_pb2 import WriteRequest  # noqa: E402

POISON_DATES = [
    "2026-10-01",
    "2026-10-02",
    "2026-10-03",
    "2026-10-04",
    "2026-10-05",
    "2026-10-06",
]
DROP_LABELS = {"__name__"}  # keep job/instance/otel so we overwrite the same series


def cx_query(expr: str, profile: str) -> list[dict]:
    raw = subprocess.check_output(
        ["cx", "metrics", "query", expr, "--profile", profile, "-o", "json"],
        text=True,
    )
    # cx prints a status line before JSON on stderr; stdout should be JSON.
    raw = raw.strip()
    if not raw.startswith("["):
        # sometimes includes a preamble
        idx = raw.find("[")
        if idx < 0:
            return []
        raw = raw[idx:]
    return json.loads(raw)


def list_api_key_ids(date: str, profile: str) -> list[str]:
    rows = cx_query(
        f'count by (api_key_id) (last_over_time(openai_administration_cost_usd{{date="{date}"}}[7d]))',
        profile,
    )
    out: list[str] = []
    for row in rows:
        metric = row.get("metric") or {}
        kid = metric.get("api_key_id")
        if kid:
            out.append(kid)
    return sorted(set(out))


def fetch_series(date: str, api_key_id: str, profile: str) -> list[dict]:
    return cx_query(
        f'last_over_time(openai_administration_cost_usd{{date="{date}",api_key_id="{api_key_id}"}}[7d])',
        profile,
    )


def build_write_request(series_rows: list[dict], value: float = 0.0001) -> WriteRequest:
    wr = WriteRequest()
    now_ms = int(time.time() * 1000)
    for row in series_rows:
        metric = row.get("metric") or {}
        if metric.get("__name__") != "openai_administration_cost_usd":
            continue
        ts = wr.timeseries.add()
        n = ts.labels.add()
        n.name = "__name__"
        n.value = "openai_administration_cost_usd"
        for k, v in sorted(metric.items()):
            if k in DROP_LABELS or v is None:
                continue
            pair = ts.labels.add()
            pair.name = str(k)
            pair.value = str(v)
        sm = ts.samples.add()
        sm.value = float(value)
        sm.timestamp = now_ms
    return wr


def push(wr: WriteRequest, url: str, token: str) -> None:
    if not wr.timeseries:
        return
    body = bytes(cramjam.snappy.compress_raw(wr.SerializeToString()))
    r = requests.post(
        url,
        data=body,
        headers={
            "Content-Type": "application/x-protobuf",
            "Content-Encoding": "snappy",
            "X-Prometheus-Remote-Write-Version": "0.1.0",
            "Authorization": f"Bearer {token}",
        },
        timeout=120,
    )
    if r.status_code not in (200, 201, 204):
        raise RuntimeError(f"remote_write failed {r.status_code}: {(r.text or '')[:400]}")
    print(f"  pushed {len(wr.timeseries)} series → HTTP {r.status_code}")


def main() -> int:
    profile = os.environ.get("CX_PROFILE", "default")
    region = os.environ.get("CORALOGIX_REGION", "us2")
    token = os.environ.get("CORALOGIX_PRIVATE_KEY", "").strip()
    if not token:
        print("CORALOGIX_PRIVATE_KEY required", file=sys.stderr)
        return 2
    url = (
        os.environ.get("CORALOGIX_PROMETHEUS_REMOTE_WRITE_URL", "").strip()
        or f"https://ingress.{region}.coralogix.com/prometheus/v1"
        "?appLabelName=cx_application_name&subSystemLabelName=cx_subsystem_name"
    )
    dates = [d.strip() for d in os.environ.get("POISON_DATES", ",".join(POISON_DATES)).split(",") if d.strip()]
    total = 0
    for date in dates:
        keys = list_api_key_ids(date, profile)
        print(f"{date}: {len(keys)} api_key_ids")
        for kid in keys:
            rows = fetch_series(date, kid, profile)
            # Only tombstone obviously inflated keys (many series); still zero all for that day.
            # Near-zero (not 0): some backends drop exact-zero samples so last_over_time
            # would keep serving the inflated pre-tombstone value.
            wr = build_write_request(rows, value=0.0001)
            push(wr, url, token)
            total += len(wr.timeseries)
            time.sleep(0.05)
    print(f"done: tombstoned {total} series")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
