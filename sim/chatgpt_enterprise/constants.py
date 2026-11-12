"""Catalog for ChatGPT Dashboard + ChatGPT Compliance posture metrics.

Shapes match ``docs/query-engine-maps/chatgpt-dashboard.md`` and
``chatgpt-compliance.md``, with live ``kb`` label vocabularies where observed.
"""

from __future__ import annotations

import os

DEFAULT_BACKFILL_DAYS = 28
DEFAULT_WORKSPACES = 1
DEFAULT_USERS = 16

PRODUCTS: tuple[str, ...] = ("ChatGPT", "Work")
# Dashboard drops product="codex"; never emit that on this sim's ledger.

MODELS: tuple[str, ...] = (
    "gpt-5.4",
    "gpt-5.3",
    "gpt-5.2",
    "gpt-4.1",
    "o3",
    "o4-mini",
)

SURFACES: tuple[str, ...] = ("chatgpt", "api", "unknown")
CLIENTS: tuple[str, ...] = ("web", "ios", "android", "desktop", "jetbrains", "unknown")
REASONING: tuple[str, ...] = ("", "low", "medium", "high")
SERVICE_TIERS: tuple[str, ...] = ("default", "priority")
TOKEN_TYPES: tuple[str, ...] = (
    "text_input_tokens",
    "text_cached_input_tokens",
    "text_output_tokens",
    "image_input_tokens",
    "image_output_tokens",
)

MESSAGE_CLIENT_IDS: tuple[str, ...] = ("", "web", "ios", "android", "desktop")

CONNECTORS: tuple[str, ...] = (
    "google_drive",
    "slack",
    "github",
    "notion",
    "jira",
    "sharepoint",
)
CONNECTOR_ACTIONS: tuple[str, ...] = ("search", "read", "write", "list")

VISIBILITIES: tuple[str, ...] = (
    "invite-only",
    "workspace-with-link",
    "workspace",
    "anyone-with-link",
    "gpt-store",
)

PLUGIN_STATUSES: tuple[str, ...] = ("ENABLED", "DISABLED", "PENDING_REVIEW")
INSTALL_POLICIES: tuple[str, ...] = ("AVAILABLE", "ADMIN_ONLY", "DISABLED")
DISCOVERABILITIES: tuple[str, ...] = ("PRIVATE", "WORKSPACE", "STORE")
AUTH_POLICIES: tuple[str, ...] = ("ON_INSTALL", "ON_USE", "NONE")

APPROVAL_MODES: tuple[str, ...] = ("never", "configured", "always")
SCHEDULE_FREQS: tuple[str, ...] = ("daily", "weekly", "monthly", "once", "unparsed")
DURATION_BUCKETS: tuple[str, ...] = ("<15m", "15-30m", "30-60m", "1-2h", "2h+")
MEMORY_BUCKETS: tuple[str, ...] = ("1-5", "6-20", "21-50", "51+")
LAST_SEEN_BUCKETS: tuple[str, ...] = ("24h", "7d", "30d", "90d", "older")
PLATFORMS: tuple[str, ...] = ("macos", "windows", "linux", "ios", "android", "ipados")
DEVICE_KEY_CLASSES: tuple[str, ...] = (
    "os_protected_nonextractable",
    "hardware_secure_enclave",
    "software",
    "unknown",
)

FEED_EVENT_TYPES: tuple[str, ...] = (
    "CONVERSATION_MESSAGE",
    "AUDIT_LOG",
    "AUTH_LOG",
    "APP_LOG",
    "APP_AUTH_LOG",
    "CUSTOM_AGENTS_LOG",
    "COSTS",
)

SWEEP_FAMILIES: tuple[str, ...] = (
    "remote_control_clients",
    "remote_control_environments",
    "automations",
    "library_files",
    "recordings",
)


def cx_app() -> str:
    return (
        os.environ.get("CHATGPT_ENTERPRISE_CX_APPLICATION_NAME", "openai-integration").strip()
        or "openai-integration"
    )


def cx_sub() -> str:
    return (
        os.environ.get(
            "CHATGPT_ENTERPRISE_CX_SUBSYSTEM_NAME", "chatgpt-enterprise-sim"
        ).strip()
        or "chatgpt-enterprise-sim"
    )
