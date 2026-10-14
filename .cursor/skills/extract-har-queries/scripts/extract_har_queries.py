#!/usr/bin/env python3
"""Extract PromQL (and related) metrics queries from Chrome HAR files.

Designed for large Coralogix AI Center / Usage HARs (100MB+) without loading
the full JSON DOM. Scans bytes for /metrics/api/v1/query(_range) URLs and
pairs each hit with nearby response status + error text when present.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import OrderedDict
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable
from urllib.parse import parse_qs, unquote, urlparse

URL_RE = re.compile(
    rb'https?://[^"\\]+/metrics/api/v1/(query_range|query)\?[^"\\]+'
)
STATUS_RE = re.compile(rb'"status"\s*:\s*(\d{3})')
STARTED_RE = re.compile(rb'"startedDateTime"\s*:\s*"([^"]+)"')
ERROR_TEXT_RE = re.compile(
    rb'"text"\s*:\s*"(\{(?:\\.|[^"\\])*ViolationType[^"\\]*(?:\\.|[^"\\])*)"'
)
DATE_FILTER_RE = re.compile(r'date=~"[^"]+"')
CLIENT_VER_RE = re.compile(r'client_version=~"[^"]+"')
LOOKBACK_PAIR_RE = re.compile(r"\[(\d{5,})s\]")


@dataclass
class QueryHit:
    promql: str
    endpoint: str  # query | query_range
    status: int | None
    eval_time: str | None
    start: str | None
    end: str | None
    step: str | None
    url_host: str
    started_datetime: str | None
    error_snippet: str | None
    byte_offset: int


def _unix_to_iso(value: str | None) -> str | None:
    if not value:
        return None
    try:
        ts = float(value)
        return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (TypeError, ValueError, OSError, OverflowError):
        return value


def _decode_promql(url: str) -> tuple[str, str, dict[str, str]]:
    parsed = urlparse(url)
    qs = parse_qs(parsed.query, keep_blank_values=True)
    flat = {k: (v[0] if v else "") for k, v in qs.items()}
    promql = " ".join(unquote(flat.get("query", "")).split())
    endpoint = "query_range" if parsed.path.endswith("query_range") else "query"
    return promql, endpoint, flat


def _nearby(data: bytes, pos: int, back: int = 8000, forward: int = 12000) -> bytes:
    return data[max(0, pos - back) : min(len(data), pos + forward)]


def _first_status_after(window: bytes, url_rel: int) -> int | None:
    for m in STATUS_RE.finditer(window):
        if m.start() >= url_rel:
            return int(m.group(1))
    matches = list(STATUS_RE.finditer(window))
    return int(matches[-1].group(1)) if matches else None


def _started_before(window: bytes, url_rel: int) -> str | None:
    last = None
    for m in STARTED_RE.finditer(window):
        if m.start() <= url_rel:
            last = m.group(1).decode("utf-8", "replace")
        else:
            break
    return last


def _error_near(window: bytes) -> str | None:
    m = ERROR_TEXT_RE.search(window)
    if not m:
        m2 = re.search(rb"Limit violation error[^\"\\]{0,240}", window)
        if not m2:
            return None
        return m2.group(0).decode("utf-8", "replace")
    raw = m.group(1).decode("utf-8", "replace")
    try:
        unescaped = bytes(raw, "utf-8").decode("unicode_escape")
        return unescaped[:300]
    except Exception:
        return raw[:300]


def extract_hits(path: Path) -> list[QueryHit]:
    data = path.read_bytes()
    hits: list[QueryHit] = []
    for m in URL_RE.finditer(data):
        url = m.group(0).decode("utf-8", "replace").rstrip("\\")
        try:
            promql, endpoint, flat = _decode_promql(url)
        except Exception:
            continue
        if not promql:
            continue
        window = _nearby(data, m.start())
        url_rel = m.start() - max(0, m.start() - 8000)
        host = urlparse(url).hostname or ""
        hits.append(
            QueryHit(
                promql=promql,
                endpoint=endpoint,
                status=_first_status_after(window, url_rel),
                eval_time=_unix_to_iso(flat.get("time")),
                start=_unix_to_iso(flat.get("start")),
                end=_unix_to_iso(flat.get("end")),
                step=flat.get("step") or None,
                url_host=host,
                started_datetime=_started_before(window, url_rel),
                error_snippet=_error_near(window),
                byte_offset=m.start(),
            )
        )
    return hits


def collapse_key(promql: str) -> str:
    q = DATE_FILTER_RE.sub('date=~"…"', promql)
    q = CLIENT_VER_RE.sub('client_version=~"…"', q)

    def _lookback(m: re.Match[str]) -> str:
        n = int(m.group(1))
        if n in {86400, 93600, 2592000}:
            return m.group(0)
        return "[…s]"

    return LOOKBACK_PAIR_RE.sub(_lookback, q)


def dedupe(hits: Iterable[QueryHit], *, by: str) -> list[QueryHit]:
    out: OrderedDict[str, QueryHit] = OrderedDict()
    for h in hits:
        if by == "collapsed":
            key = collapse_key(h.promql)
        elif by == "promql+status":
            key = f"{h.promql}||{h.status}"
        else:
            key = h.promql
        if key not in out:
            out[key] = h
        else:
            prev = out[key]
            if (h.status == 422) and (prev.status != 422):
                out[key] = h
    return list(out.values())


def filter_hits(
    hits: list[QueryHit],
    *,
    host_substr: str | None,
    status: int | None,
    contains: str | None,
    metric_prefix: str | None,
) -> list[QueryHit]:
    out = hits
    if host_substr:
        out = [h for h in out if host_substr in h.url_host]
    if status is not None:
        out = [h for h in out if h.status == status]
    if contains:
        out = [h for h in out if contains in h.promql]
    if metric_prefix:
        out = [h for h in out if metric_prefix in h.promql]
    return out


def format_markdown(hits: list[QueryHit], *, title: str, total_raw: int) -> str:
    lines = [
        f"# {title}",
        "",
        f"Unique rows: **{len(hits)}** (from {total_raw} metrics URL hits).",
        "",
        "| Status | Eval / window | Endpoint | PromQL |",
        "|---|---|---|---|",
    ]
    for h in hits:
        st = h.status if h.status is not None else "—"
        if h.endpoint == "query_range":
            window = f"{h.start or '?'} → {h.end or '?'}"
            if h.step:
                window += f" step={h.step}"
        else:
            window = h.eval_time or "—"
        q = h.promql.replace("|", "\\|")
        if len(q) > 180:
            q = q[:177] + "…"
        lines.append(f"| {st} | `{window}` | {h.endpoint} | `{q}` |")
    failing = [h for h in hits if h.status and h.status >= 400]
    if failing:
        lines += ["", "## Non-2xx samples", ""]
        for h in failing[:10]:
            lines.append(f"- **{h.status}** `{h.promql[:120]}`")
            if h.error_snippet:
                lines.append(f"  - {h.error_snippet[:200]}")
    return "\n".join(lines) + "\n"


def format_list(hits: list[QueryHit]) -> str:
    lines = []
    for i, h in enumerate(hits, 1):
        st = h.status if h.status is not None else "—"
        lines.append(f"{i:03d}. [{st}] {h.promql}")
    return "\n".join(lines) + "\n"


def raw_snippet(path: Path, needle: bytes, *, context: int = 900) -> str | None:
    data = path.read_bytes()
    pos = data.find(needle)
    if pos < 0:
        return None
    start = data.rfind(b'"_priority"', max(0, pos - 5000), pos)
    if start < 0:
        start = max(0, pos - context)
    end = min(len(data), pos + context)
    chunk = data[start:end].decode("utf-8", "replace")
    chunk = re.sub(
        r'"cookies"\s*:\s*\[[^\]]*\]',
        '"cookies": [ … ]',
        chunk,
        count=1,
        flags=re.S,
    )
    return chunk


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("har", type=Path, help="Path to .har file")
    p.add_argument(
        "--dedupe",
        choices=("promql", "promql+status", "collapsed", "none"),
        default="promql+status",
        help="Deduping strategy (default: promql+status keeps 422 and 200 twins)",
    )
    p.add_argument("--host", help="Substring filter on URL host (e.g. cx498)")
    p.add_argument("--status", type=int, help="Only this HTTP status")
    p.add_argument("--contains", help="PromQL must contain this substring")
    p.add_argument(
        "--metric",
        help="PromQL must contain this metric/token (e.g. cursor_event_tokens_total)",
    )
    p.add_argument(
        "--format",
        choices=("markdown", "list", "json"),
        default="markdown",
    )
    p.add_argument(
        "--snippet-422",
        action="store_true",
        help="Print a literal HAR byte snippet around the first 422 Limit violation",
    )
    p.add_argument("-o", "--output", type=Path, help="Write output to file")
    args = p.parse_args(argv)

    if not args.har.is_file():
        print(f"error: HAR not found: {args.har}", file=sys.stderr)
        return 2

    hits = extract_hits(args.har)
    total_raw = len(hits)
    hits = filter_hits(
        hits,
        host_substr=args.host,
        status=args.status,
        contains=args.contains,
        metric_prefix=args.metric,
    )
    if args.dedupe != "none":
        hits = dedupe(hits, by=args.dedupe)

    if args.format == "json":
        text = json.dumps([asdict(h) for h in hits], indent=2)
    elif args.format == "list":
        text = format_list(hits)
    else:
        text = format_markdown(
            hits, title=f"Extracted queries — {args.har.name}", total_raw=total_raw
        )

    if args.snippet_422:
        snippet = raw_snippet(args.har, b"ViolationTypeTotalSeriesAnalyzed")
        if not snippet:
            snippet = raw_snippet(args.har, b'"status": 422')
        if snippet:
            text += "\n## Literal HAR snippet (422)\n\n```json\n" + snippet[:2500] + "\n```\n"

    if args.output:
        args.output.write_text(text)
        print(
            f"wrote {args.output} ({len(hits)} rows, {total_raw} raw hits)",
            file=sys.stderr,
        )
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
