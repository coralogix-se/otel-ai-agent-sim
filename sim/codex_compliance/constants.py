"""Catalog for Codex Compliance + Codex Dashboard (Analytics) simulator.

Shapes match ``docs/query-engine-maps/codex-compliance.md`` and
``docs/query-engine-maps/codex-dashboard.md``, plus live ``kb`` vocabularies.
"""

from __future__ import annotations

import os

DEFAULT_BACKFILL_DAYS = 28
DEFAULT_WORKSPACES = 1
DEFAULT_USERS = 16

# Live kb clients / sub-events (2026-11).
CODEX_CLIENT_IDS: tuple[str, ...] = (
    "CODEX_BROWSER",
    "CODEX_CHATGPT_CCA",
    "CODEX_WEB",
    "CODEX_CLI",
)

CODEX_LOG_SUB_EVENTS: tuple[str, ...] = (
    "PROMPT_SENT",
    "PROMPT_RESPONSE_RECEIVED",
    "TOOL_CALL_SUGGESTED",
    "APP_MCP_CALL",
    "APP_MCP_RESULT",
    "TOOL_CALL_COMPLETED",
)

SECURITY_SUB_EVENTS: tuple[str, ...] = ("PROPOSED_PATCH_PR_CREATED",)

SHARE_SETTINGS: tuple[str, ...] = ("private", "workspace", "organization")
MACHINE_IDS: tuple[str, ...] = (
    "wham-public/wham-universal",
    "wham-public/wham-cuda",
)

DEVICE_KEY_CLASSES: tuple[str, ...] = (
    "os_protected_nonextractable",
    "hardware_secure_enclave",
    "software",
    "unknown",
)

GEO_COUNTRIES: tuple[str, ...] = ("US", "IL", "GB", "DE", "IN", "")
ACTION_RESULTS: tuple[str, ...] = ("SUCCESS", "FAILURE", "DENIED")

FEED_EVENT_TYPES: tuple[str, ...] = (
    "CODEX_LOG",
    "CODEX_SECURITY_LOG",
    "AUDIT_LOG",
    "AUTH_LOG",
)

# Include gpt-5-codex so "full-size models" insight can fire (>50% tokens).
CODEX_MODELS: tuple[str, ...] = (
    "gpt-5-codex",
    "gpt-5.4-codex",
    "gpt-5.3-codex",
    "gpt-5.2-codex",
    "o3",
    "gpt-4.1",
)

SERVING_SPEEDS: tuple[str, ...] = ("standard", "fast")

TOKEN_TYPES: tuple[str, ...] = (
    "input",
    "cached_input",
    "output",
    "reasoning_output",
)

REASONING_EFFORTS: tuple[str, ...] = ("low", "medium", "high")

TOOL_NAMES: tuple[str, ...] = (
    "shell",
    "apply_patch",
    "read_file",
    "grep",
    "web_search",
    "mcp.github",
)

TOOL_TYPES: tuple[str, ...] = ("builtin", "mcp")

REVIEW_PRIORITIES: tuple[str, ...] = ("p0", "p1", "p2")
REVIEW_REACTIONS: tuple[str, ...] = ("upvote", "downvote", "other")


def cx_app() -> str:
    return (
        os.environ.get("CODEX_COMPLIANCE_CX_APPLICATION_NAME", "openai-integration").strip()
        or "openai-integration"
    )


def cx_sub() -> str:
    return (
        os.environ.get("CODEX_COMPLIANCE_CX_SUBSYSTEM_NAME", "codex-compliance-sim").strip()
        or "codex-compliance-sim"
    )
