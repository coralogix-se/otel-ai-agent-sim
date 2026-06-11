"""Push Prometheus ``CollectorRegistry`` to Coralogix via remote_write (Snappy + protobuf)."""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
import time
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse
import cramjam
import requests
from prometheus_client import CollectorRegistry

from prompb.remote_write_pb2 import WriteRequest

log = logging.getLogger(__name__)

_configured = False


def _ensure_logging_configured() -> None:
    """Ensure INFO logs from this module reach stderr (root logger is often WARNING-only)."""
    global _configured
    if _configured:
        return
    _configured = True
    level_name = os.environ.get("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    log.setLevel(level)
    if log.handlers:
        return
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(levelname)s %(name)s %(message)s"))
    log.addHandler(handler)
    log.propagate = False


def _timeseries_name_and_value(ts) -> tuple[str | None, float | None]:
    """First ``__name__`` label and first sample value from a remote-write time series."""
    name: str | None = None
    for lb in ts.labels:
        if lb.name == "__name__":
            name = lb.value
            break
    val: float | None = None
    if ts.samples:
        val = ts.samples[0].value
    return name, val


def _log_write_request_payload(wr: WriteRequest, url: str) -> None:
    """Log metric names and values about to be sent (volume capped)."""
    n = len(wr.timeseries)
    try:
        max_lines = max(0, int(os.environ.get("PROMETHEUS_RW_LOG_MAX_SERIES", "100")))
    except ValueError:
        max_lines = 100
    log.info("prometheus remote_write: posting to %s, time_series_count=%d", url, n)
    if max_lines == 0:
        return
    for i, ts in enumerate(wr.timeseries):
        if i >= max_lines:
            log.info(
                "prometheus remote_write: ... truncated (%d more series not logged)",
                n - max_lines,
            )
            break
        name, val = _timeseries_name_and_value(ts)
        log.info("prometheus remote_write: metric=%s value=%s", name, val)


def _external_labels_for_remote_write() -> dict[str, str]:
    """
    Optional labels merged into every pushed series so they match scraped-target identity in PromQL/UI.

    Scraped metrics get ``job`` and ``instance`` from Prometheus; pure remote_write does not, so queries like
    ``{job=\"otel-ai-agent-sim\"}`` return nothing unless we add the same labels here.

    - ``PROMETHEUS_RW_JOB`` / ``PROMETHEUS_RW_INSTANCE`` — set e.g. instance to ``<pod_ip>:9090`` (Downward API).
    - ``PROMETHEUS_RW_EXTRA_LABELS_JSON`` — optional JSON object of extra labels (does not override existing names).
    """
    extra: dict[str, str] = {}
    job = os.environ.get("PROMETHEUS_RW_JOB", "").strip()
    inst = os.environ.get("PROMETHEUS_RW_INSTANCE", "").strip()
    if job:
        extra["job"] = job
    if inst:
        extra["instance"] = inst
    raw = os.environ.get("PROMETHEUS_RW_EXTRA_LABELS_JSON", "").strip()
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                for k, v in parsed.items():
                    if isinstance(k, str) and v is not None:
                        extra[k] = str(v)
        except json.JSONDecodeError:
            log.warning("PROMETHEUS_RW_EXTRA_LABELS_JSON is not valid JSON; ignoring")
    return extra


def registry_to_write_request(registry: CollectorRegistry) -> WriteRequest:
    wr = WriteRequest()
    now_ms = int(time.time() * 1000)
    external = _external_labels_for_remote_write()
    for metric in registry.collect():
        for sample in metric.samples:
            if sample.name.endswith("_created"):
                continue
            ts = wr.timeseries.add()
            n = ts.labels.add()
            n.name = "__name__"
            n.value = sample.name
            label_keys = {"__name__"}
            for lk, lv in sample.labels.items():
                pair = ts.labels.add()
                pair.name = lk
                pair.value = str(lv)
                label_keys.add(lk)
            for lk, lv in external.items():
                if lk in label_keys:
                    continue
                pair = ts.labels.add()
                pair.name = lk
                pair.value = lv
            sm = ts.samples.add()
            sm.value = float(sample.value)
            sm.timestamp = now_ms if sample.timestamp is None else int(sample.timestamp)
    return wr


def push_remote_write(
    registry: CollectorRegistry,
    url: str,
    bearer_token: str,
    timeout: float = 60.0,
) -> None:
    _ensure_logging_configured()
    wr = registry_to_write_request(registry)
    if len(wr.timeseries) == 0:
        log.info("prometheus remote_write: skip empty write to %s", url)
        return
    _log_write_request_payload(wr, url)
    raw = wr.SerializeToString()
    body = bytes(cramjam.snappy.compress(raw))
    headers = {
        "Content-Type": "application/x-protobuf",
        "Content-Encoding": "snappy",
        "X-Prometheus-Remote-Write-Version": "0.1.0",
        "Authorization": f"Bearer {bearer_token}",
    }
    r = requests.post(url, data=body, headers=headers, timeout=timeout)
    log.info("prometheus remote_write: http_status=%s", r.status_code)
    if r.status_code not in (200, 201, 204):
        log.warning(
            "prometheus remote_write failed http_status=%s body_prefix=%s",
            r.status_code,
            (r.text or "")[:800],
        )


def resolve_prometheus_remote_write_url() -> str:
    """
    Remote_write URL, with optional Coralogix query params that map Prometheus label **keys**
    to Application / Subsystem (see Coralogix Prometheus docs: ``appLabelName``,
    ``subSystemLabelName``).

    Defaults add ``cx_application_name`` / ``cx_subsystem_name`` when the URL does not
    already set them. Set ``CORALOGIX_PROMETHEUS_RW_APP_LABEL_NAME`` (or subsystem) to
    ``""`` to omit that key from the query string.
    """
    u = os.environ.get("CORALOGIX_PROMETHEUS_REMOTE_WRITE_URL", "").strip()
    if not u:
        region = os.environ.get("CORALOGIX_REGION", "us2").strip().lower()
        u = f"https://ingress.{region}.coralogix.com/prometheus/v1"

    app_l = os.environ.get("CORALOGIX_PROMETHEUS_RW_APP_LABEL_NAME", "cx_application_name")
    sub_l = os.environ.get("CORALOGIX_PROMETHEUS_RW_SUBSYSTEM_LABEL_NAME", "cx_subsystem_name")
    app_l = app_l.strip() if isinstance(app_l, str) else ""
    sub_l = sub_l.strip() if isinstance(sub_l, str) else ""

    parsed = urlparse(u)
    q = dict(parse_qsl(parsed.query, keep_blank_values=True))
    if "appLabelName" not in q and app_l:
        q["appLabelName"] = app_l
    if "subSystemLabelName" not in q and sub_l:
        q["subSystemLabelName"] = sub_l
    new_query = urlencode(q)
    return urlunparse(
        (parsed.scheme, parsed.netloc, parsed.path, parsed.params, new_query, parsed.fragment)
    )


def start_push_thread(
    registry: CollectorRegistry,
    interval_sec: float,
    url: str,
    bearer_token: str,
) -> tuple[threading.Event, threading.Thread]:
    stop = threading.Event()

    def _loop() -> None:
        while not stop.wait(interval_sec):
            try:
                push_remote_write(registry, url, bearer_token)
            except Exception:
                log.exception("prometheus remote_write push failed")

    t = threading.Thread(target=_loop, name="prom-rw-push", daemon=True)
    t.start()
    return stop, t


def push_once_safe(registry: CollectorRegistry, url: str, bearer_token: str) -> None:
    try:
        push_remote_write(registry, url, bearer_token)
    except Exception:
        log.exception("prometheus remote_write final push failed")
