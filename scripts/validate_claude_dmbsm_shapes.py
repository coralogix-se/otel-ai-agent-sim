#!/usr/bin/env python3
"""
Validate Claude Code **new** telemetry shapes (repo metric + prompt text + dotted logs)
and print dmbsm MCP queries to confirm them in Coralogix.

Run from repo root: ``.venv/bin/python scripts/validate_claude_dmbsm_shapes.py``

The dmbsm tenant already has **legacy** sim traffic (``job=otel-ai-agent-sim``,
``$l.subsystemname == 'claude-code'``). After redeploying the latest sim image, use the
printed MCP queries to confirm the **new** shapes appear alongside the old series.
"""

from __future__ import annotations

import json
import os
import sys
import uuid

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from sim.claude.logs import _cc_claude_log_record_attrs
from sim.common.constants import CLAUDE_CODE_DEFAULT_MODEL, claude_api_response_body_json
from sim.common.repos import sim_session_repository_names
from sim.common.identity import _CORALOGIX_TEAM_USERS

_SAMPLE_PROMPT = "Refactor the auth middleware to use structured logging and add unit tests."


def _base() -> dict:
    sid = str(uuid.uuid4())
    return {
        "organization.id": "8867142e-84ac-5969-8215-ef67b8ea8de7",
        "session.id": sid,
        "user.account_uuid": str(uuid.uuid4()),
        "user.account.id": "user_01TEST",
        "user.id": "a" * 64,
        "user.email": "alex.silva@coralogix.com",
        "terminal.type": "vscode",
    }


def validate_local_shapes() -> list[str]:
    failures: list[str] = []
    base = _base()
    cx_app = "claude-code"
    cx_sub_flat = "claude-code"
    cx_sub_dotted = os.environ.get("SIM_CLAUDE_DOTTED_CX_SUBSYSTEM_NAME", "claude-code-sessions")

    flat = _cc_claude_log_record_attrs(
        base,
        event_name="user_prompt",
        event_sequence=1,
        event_timestamp_iso="2026-06-03T12:00:00.000Z",
        cx_app=cx_app,
        cx_sub=cx_sub_flat,
        extra={"prompt": _SAMPLE_PROMPT, "prompt_length": len(_SAMPLE_PROMPT)},
        profile="flat",
    )
    if flat.get("prompt") != _SAMPLE_PROMPT:
        failures.append("flat user_prompt log missing prompt text")
    if flat.get("prompt_length") != str(len(_SAMPLE_PROMPT)):
        failures.append("flat user_prompt log: prompt_length should be string")
    if flat.get("user.email") != "alex.silva@coralogix.com":
        failures.append("flat user_prompt log missing user.email alias for AI Center")
    if flat.get("session.id") != str(base["session.id"]):
        failures.append("flat user_prompt log missing session.id alias for AI Center")

    dotted = _cc_claude_log_record_attrs(
        base,
        event_name="user_prompt",
        event_sequence=1,
        event_timestamp_iso="2026-06-03T12:00:00.000Z",
        cx_app=cx_app,
        cx_sub=cx_sub_dotted,
        extra={"prompt": _SAMPLE_PROMPT, "prompt_length": len(_SAMPLE_PROMPT)},
        profile="dotted",
    )
    if dotted.get("prompt") != _SAMPLE_PROMPT:
        failures.append("dotted user_prompt log missing prompt text")
    if dotted.get("event.name") != "user_prompt":
        failures.append("dotted log missing event.name")

    api_flat = _cc_claude_log_record_attrs(
        base,
        event_name="api_request",
        event_sequence=2,
        event_timestamp_iso="2026-06-03T12:00:01.000Z",
        cx_app=cx_app,
        cx_sub=cx_sub_flat,
        extra={
            "cost_usd_micros": 1_220_949,
            "effort": "high",
            "query_source": "repl_main_thread",
            "input_tokens": 100,
            "output_tokens": 50,
            "cost_usd": 1.220949,
            "duration_ms": 900,
            "model": CLAUDE_CODE_DEFAULT_MODEL,
        },
        profile="flat",
    )
    for k in ("cost_usd_micros", "effort", "query_source"):
        if k not in api_flat:
            failures.append(f"flat api_request missing {k}")

    sample_body = claude_api_response_body_json("ok")
    body_flat = _cc_claude_log_record_attrs(
        base,
        event_name="api_response_body",
        event_sequence=3,
        event_timestamp_iso="2026-06-03T12:00:02.000Z",
        cx_app=cx_app,
        cx_sub=cx_sub_flat,
        extra={"body": sample_body, "body_length": len(sample_body), "model": CLAUDE_CODE_DEFAULT_MODEL},
        profile="flat",
    )
    if "body" not in body_flat or "body_length" not in body_flat:
        failures.append("flat api_response_body missing body/body_length")
    if '"content"' not in str(body_flat.get("body", "")):
        failures.append("flat api_response_body body should use Anthropic content[] JSON")

    repos = sim_session_repository_names(str(base["session.id"]), _CORALOGIX_TEAM_USERS[0])
    if not repos:
        failures.append("sim_session_repository_names returned empty")
    managed = [r for r in repos if r == "coralogix/cxai-observability-demo-playground"]
    if not managed:
        failures.append("expected at least one obdev-scan managed repo (cxai-observability-demo-playground)")

    return failures


def dmbsm_mcp_cookbook() -> dict:
    dotted_sub = os.environ.get("SIM_CLAUDE_DOTTED_CX_SUBSYSTEM_NAME", "claude-code-sessions")
    return {
        "mcp_server": "user-dmbsm-coralogix-server",
        "legacy_sim_identifiers": {
            "metrics_job": "otel-ai-agent-sim",
            "metrics_instance": "otel-ai-agent-sim:9090",
            "flat_logs_subsystem": "claude-code",
        },
        "new_vs_old": {
            "user_prompt": "NEW logs include attributes.prompt (full text), not just prompt_length",
            "api_request": "NEW adds cost_usd_micros, effort, query_source",
            "api_response_body": "NEW event claude_code.api_response_body with body + body_length",
            "dotted_profile": f"NEW dual emit: subsystemname == '{dotted_sub}' (dotted keys like event.name, session.id)",
            "repo_metric": "NEW gauge claude_code_session_repo_info{session_id,repository_name} value 1",
        },
        "metrics_search": {
            "tool": "search_metrics",
            "by_name": "claude_code_session_repo_info",
            "expect_after_deploy": "name appears in catalog (missing on legacy image)",
        },
        "metrics_range": [
            {
                "tool": "query_metrics_range",
                "query": 'max by (session_id, repository_name) (max_over_time(claude_code_session_repo_info{job="otel-ai-agent-sim"}[1h]))',
                "note": "Dashboard join key; repository_name values are org/repo (managed coralogix/* or fictional external repos)",
            },
            {
                "tool": "query_metrics_range",
                "query": 'sum by (repository_name) (max_over_time(claude_code_session_repo_info{job="otel-ai-agent-sim"}[1h]))',
                "note": "Managed vs unmanaged mix per session",
            },
        ],
        "dataprime_logs": [
            {
                "tool": "query_dataprime",
                "query": "source logs | filter $l.applicationname == 'claude-code' | limit 20",
                "note": "Inspect user_data JSON; legacy rows lack attributes.prompt on user_prompt events",
            },
            {
                "tool": "query_dataprime",
                "query": f"source logs | filter $l.applicationname == 'claude-code' | filter $l.subsystemname == '{dotted_sub}' | limit 10",
                "note": "Dotted profile (both mode); empty until latest sim image is deployed",
            },
        ],
        "deploy_note": "k8s/codeagentsim/sim-deployment.yaml uses collector scrape on :9090 — rebuild/push otel-ai-agent-sim:latest and rollout restart after merging repo metric changes.",
    }


def main() -> int:
    failures = validate_local_shapes()
    print("=== Local shape validation ===")
    if failures:
        for f in failures:
            print(f"FAIL: {f}")
    else:
        print("OK: flat/dotted user_prompt, api_request extras, api_response_body, repo names")

    print("\n=== dmbsm MCP cookbook (paste into user-dmbsm-coralogix-server) ===")
    print(json.dumps(dmbsm_mcp_cookbook(), indent=2))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
