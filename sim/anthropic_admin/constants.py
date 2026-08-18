"""Fixed-cardinality catalog for the Anthropic Admin API usage/cost simulator.

Metric names and usage labels match the live cxai-dev Admin series
(``anthropic_admin_api_key_usage``, ``anthropic_admin_rate_limit_value``).
Cost + structured reports follow the Anthropic Admin usage/cost API shape.
"""

from __future__ import annotations

import hashlib
import os
import uuid

# Live Admin usage models (cxai-dev). Keep this pool small and priced.
ANTHROPIC_ADMIN_MODELS: tuple[str, ...] = (
    "claude-sonnet-5",
    "claude-sonnet-4-6",
    "claude-haiku-4-5-20251001",
    "claude-opus-5",
    "claude-opus-4-8",
    "claude-fable-5",
)

# Rate-limit gauges also appear for these non-model ids.
RATE_LIMIT_EXTRA_MODELS: tuple[str, ...] = ("batch", "web_search")

TOKEN_TYPES: tuple[str, ...] = (
    "uncached_input_tokens",
    "output_tokens",
    "cache_read_input_tokens",
    "cache_creation.ephemeral_5m_input_tokens",
    "cache_creation.ephemeral_1h_input_tokens",
    "server_tool_use.web_search_requests",
)

CONTEXT_WINDOWS: tuple[str, ...] = ("0-200k", "200k-1M")

SERVICE_TIERS: tuple[str, ...] = ("standard", "priority", "batch")

LIMIT_TYPES: tuple[str, ...] = (
    "requests_per_minute",
    "input_tokens_per_minute_cache_aware",
    "output_tokens_per_minute",
    "enqueued_batch_requests",
    "tool_uses_per_second",
    "fast_itpmca",
    "fast_otpm",
)

ORG_ROLES: tuple[tuple[str, int], ...] = (
    ("primary_owner", 1),
    ("owner", 3),
    ("membership_admin", 4),
    ("user", 40),
)

ACTIVITY_TYPES: tuple[str, ...] = (
    "claude_chat_viewed",
    "claude_chat_created",
    "claude_artifact_viewed",
    "claude_artifact_published",
    "claude_file_viewed",
    "claude_file_uploaded",
    "claude_file_exported",
    "admin_request_created",
    "claude_organization_settings_updated",
)

# Flattened field names on ``anthropic.api_keys_usage`` logs.
TOKEN_TYPE_LOG_FIELD: dict[str, str] = {
    "uncached_input_tokens": "uncached_input_tokens",
    "output_tokens": "output_tokens",
    "cache_read_input_tokens": "cache_read_input_tokens",
    "cache_creation.ephemeral_5m_input_tokens": "cache_creation_5m_input_tokens",
    "cache_creation.ephemeral_1h_input_tokens": "cache_creation_1h_input_tokens",
    "server_tool_use.web_search_requests": "web_search_requests",
}

SOURCE = "admin"
ANALYTICS_SOURCE = "analytics"

# Claude Products page (AI Center). Names/labels match live cxai analytics gauges.
# Admin/compliance series stay on the Governance dashboard; this package emits both.
ANALYTICS_PRODUCTS: tuple[str, ...] = (
    "claude_code",
    "cowork",
    "chat",
    "claude_in_chrome",
)
ANALYTICS_PRODUCT_WEIGHTS: dict[str, float] = {
    "claude_code": 0.58,
    "cowork": 0.20,
    "chat": 0.16,
    "claude_in_chrome": 0.06,
}

# Org token gauges on cxai use these ids (cache writes live on anthropic_org_cache_creation).
ANALYTICS_TOKEN_TYPES: tuple[str, ...] = (
    "uncached_input_tokens",
    "output_tokens",
    "cache_read_input_tokens",
)
CACHE_CREATION_TOKEN_TYPES: tuple[str, ...] = (
    "ephemeral_5m_input_tokens",
    "ephemeral_1h_input_tokens",
)

ACTIVE_USER_PRODUCTS: tuple[str, ...] = (
    "all",
    "chat",
    "claude_code",
    "cowork",
    "claude_design",
    "office_agent",
    "science",
)
ACTIVE_WINDOWS: tuple[str, ...] = ("daily", "weekly", "monthly")

ANALYTICS_TOOLS: tuple[str, ...] = (
    "edit_tool",
    "write_tool",
    "multi_edit_tool",
    "notebook_edit_tool",
)
ANALYTICS_SKILLS: tuple[str, ...] = (
    "cx-catalog",
    "receiving-code-review",
    "run-code-checks",
    "create-pr",
    "claude-in-chrome",
    "systematic-debugging",
    "fix-pr-comments",
    "brainstorming",
    "review",
)
ANALYTICS_CONNECTORS: tuple[str, ...] = (
    "claude-in-chrome",
    "claude-ai-atlassian",
    "claude-ai-slack",
    "plugin-linear-linear",
)

# Summary-log field prefixes (HAR ``anthropic.summary.active_users``).
SUMMARY_PRODUCT_PREFIXES: tuple[tuple[str, str], ...] = (
    ("all", ""),
    ("chat", "chat_"),
    ("claude_code", "claude_code_"),
    ("cowork", "cowork_"),
    ("office_agent", "office_agent_"),
    ("claude_design", "claude_design_"),
    ("science", "science_"),
)

# Display names used on cost_report ``description``.
_MODEL_COST_LABEL: dict[str, str] = {
    "claude-sonnet-5": "Claude Sonnet 5",
    "claude-sonnet-4-6": "Claude Sonnet 4.6",
    "claude-haiku-4-5-20251001": "Claude Haiku 4.5",
    "claude-opus-5": "Claude Opus 5",
    "claude-opus-4-8": "Claude Opus 4.8",
    "claude-fable-5": "Claude Fable 5",
}


def _stable_id(prefix: str, seed: str, n: int) -> str:
    """Anthropic-style id: ``prefix`` + 24 chars from a stable hash (base62-ish)."""
    alphabet = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
    digest = hashlib.sha256(f"otel-ai-agent-sim:anthropic-admin:{seed}".encode()).digest()
    chars: list[str] = []
    acc = int.from_bytes(digest, "big")
    while len(chars) < n:
        chars.append(alphabet[acc % len(alphabet)])
        acc //= len(alphabet)
        if acc == 0:
            digest = hashlib.sha256(digest).digest()
            acc = int.from_bytes(digest, "big")
    return prefix + "".join(chars)


def default_organization_id() -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "otel-ai-agent-sim:anthropic-admin:org"))


def default_organization_api_id() -> str:
    """Anthropic-style ``org_…`` id used on analytics log payloads."""
    return _stable_id("org_", "primary", 24)


def default_api_key_ids(n: int = 6) -> tuple[str, ...]:
    return tuple(_stable_id("apikey_", f"key:{i}", 24) for i in range(n))


def default_workspace_ids(n: int = 2) -> tuple[str, ...]:
    return tuple(_stable_id("wrkspc_", f"workspace:{i}", 24) for i in range(n))


def model_cost_label(model: str) -> str:
    return _MODEL_COST_LABEL.get(model, model)


def cost_description(*, model: str, kind: str, service_tier: str) -> str:
    """Human-readable Admin cost line (workspace attribution)."""
    label = model_cost_label(model)
    kind_map = {
        "uncached_input_tokens": "Input Tokens",
        "output_tokens": "Output Tokens",
        "cache_read_input_tokens": "Input Tokens, Cache Read",
        "cache_creation.ephemeral_5m_input_tokens": "Input Tokens, Cache Write 5 Minutes",
        "cache_creation.ephemeral_1h_input_tokens": "Input Tokens, Cache Write 1 Hour",
        "server_tool_use.web_search_requests": "Web Search Requests",
    }
    piece = kind_map.get(kind, kind)
    tier = service_tier.replace("_", " ").title()
    return f"{label} Usage - {piece} - {tier}"


# Same hash as ``sim.common.identity`` so Admin emails line up with Claude Code panels.
# Kept here so this package does not import Claude/OTEL helpers.
_ROSTER_FIRST_NAMES: tuple[str, ...] = (
    "Alex",
    "Sam",
    "Jordan",
    "Taylor",
    "Riley",
    "Casey",
    "Morgan",
    "Quinn",
    "Avery",
    "Skyler",
    "Reese",
    "Drew",
    "Jamie",
    "Cameron",
    "Rowan",
)
_ROSTER_LAST_NAMES: tuple[str, ...] = (
    "Nguyen",
    "Patel",
    "Garcia",
    "Okonkwo",
    "Silva",
    "Kim",
    "Cohen",
    "Hansen",
    "Iyer",
    "Martinez",
    "Lindberg",
    "Fischer",
    "Okafor",
    "Tanaka",
    "Bernstein",
)


def _roster_email_local(i: int, first: str, last: str, *, occurrence: int) -> str:
    fmt = os.environ.get("SIM_ROSTER_EMAIL_FORMAT", "natural").strip().lower()
    fn = first.lower()
    ln = last.lower()
    if fmt in ("legacy", "dots", "dotted", "period", "periods"):
        return f"team.{i:03d}.{fn}.{ln}"
    if fmt in ("underscore", "team_underscore", "old_underscore"):
        return f"team{i:03d}_{fn}_{ln}"
    base = f"{fn}.{ln}"
    if occurrence <= 1:
        return base
    return f"{base}{occurrence}"


def default_roster_rows(n: int) -> tuple[dict[str, str], ...]:
    """First ``n`` synthetic @coralogix.com users (same seeds as the shared agent roster)."""
    users: list[dict[str, str]] = []
    name_counts: dict[str, int] = {}
    size = max(1, n)
    for i in range(size):
        digest = hashlib.sha256(f"coralogix:sim:team:{i}".encode()).digest()
        first = _ROSTER_FIRST_NAMES[digest[0] % len(_ROSTER_FIRST_NAMES)]
        last = _ROSTER_LAST_NAMES[digest[1] % len(_ROSTER_LAST_NAMES)]
        base_key = f"{first.lower()}.{last.lower()}"
        name_counts[base_key] = name_counts.get(base_key, 0) + 1
        occ = name_counts[base_key]
        local = _roster_email_local(i, first, last, occurrence=occ)
        users.append(
            {
                "user.name": f"{first} {last}",
                "user.email": f"{local}@coralogix.com",
            }
        )
    return tuple(users)

