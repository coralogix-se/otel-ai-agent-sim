"""Fixed-cardinality catalog for the OpenAI Administration / API Platform simulator.

Contract (team, 2026-09-30): daily cost/usage gauges with ``date``, inventory
restated without ``date``, models gpt-5.5-led, ~$700–1200/weekday.

Label / metric names match live OpenAI Administration exporter shapes (kb/eu2):
``organization`` on all series; cost includes key/user + org id/name; compliance
uses ``rate_limit_configured`` / ``model_permissions_configured`` /
``data_retention_*`` / ``hosted_tool_enabled`` (no ``project_`` prefix).
"""

from __future__ import annotations

import hashlib
import os

# FE-priced / tiered model ids (spend-weighted; gpt-5.5 ~65%).
OPENAI_ADMIN_MODELS: tuple[str, ...] = (
    "gpt-5.5",
    "gpt-5.4",
    "gpt-5.4-mini",
    "gpt-5-mini",
    "gpt-4.1-mini",
)

# Spend share → expensive-model insight (≥60% on gpt-5.5).
OPENAI_ADMIN_MODEL_SPEND_WEIGHTS: dict[str, float] = {
    "gpt-5.5": 0.65,
    "gpt-5.4": 0.15,
    "gpt-5.4-mini": 0.10,
    "gpt-5-mini": 0.06,
    "gpt-4.1-mini": 0.04,
}

USAGE_COMPLETION_FIELDS: tuple[str, ...] = (
    "input_tokens",
    "input_uncached_tokens",
    "input_cached_tokens",
    "input_cache_write_tokens",
    "input_text_tokens",
    "input_audio_tokens",
    "input_image_tokens",
    "input_cached_text_tokens",
    "input_cached_audio_tokens",
    "input_cached_image_tokens",
    "output_tokens",
    "output_text_tokens",
    "output_audio_tokens",
    "output_image_tokens",
    "requests",
)

USAGE_FAMILY_FIELDS: tuple[str, ...] = (
    "embeddings_tokens",
    "embeddings_model_requests",
    "audio_speeches_characters",
    "audio_speeches_model_requests",
    "audio_transcriptions_seconds",
    "audio_transcriptions_model_requests",
    "moderations_tokens",
    "moderations_model_requests",
    "web_search_requests",
    "web_search_model_requests",
    "file_search_requests",
)

SERVICE_TIERS: tuple[str, ...] = ("default", "flex", "priority")

COST_LINE_KINDS: tuple[str, ...] = (
    "input",
    "cached input",
    "output",
)

COST_TOOL_LINE_ITEMS: tuple[str, ...] = (
    "file search tool calls",
    "web search tool calls",
)

HOSTED_TOOLS: tuple[str, ...] = (
    "code_interpreter",
    "file_search",
    "image_generation",
    "mcp",
    "web_search",
)

# Weekday targets (weekend ≈ 0.6×). Cost derived from tokens × list price, then scaled.
WEEKDAY_INPUT_TOKENS = 270_000_000
WEEKDAY_OUTPUT_TOKENS = 30_000_000
WEEKDAY_REQUESTS = 150_000
WEEKDAY_COST_USD_LO = 700.0
WEEKDAY_COST_USD_HI = 1200.0
WEEKEND_COST_MULT = 0.6
# Cache hit below 70% so "Context is re-sent, not cached" can fire.
CACHE_HIT_FRAC = 0.58

DEFAULT_USERS = 40
DEFAULT_PROJECTS = 10  # 8 active + 2 archived
DEFAULT_API_KEYS = 22
DEFAULT_SERVICE_ACCOUNTS = 5
DEFAULT_ADMIN_KEYS = 2
DEFAULT_BACKFILL_DAYS = 28
DEFAULT_ORG_NAME = "Coralogix Demo OpenAI"

# Shared base + organization (kb exporter puts organization on every series).
_BASE = (
    "cx_application_name",
    "cx_subsystem_name",
    "organization",
)

USAGE_LABELS = _BASE + (
    "model",
    "api_key_id",
    "date",
    "project_id",
    "user_id",
    "service_tier",
)

# kb cost_usd: org id/name + key + user; cost_quantity adds unit.
COST_USD_LABELS = _BASE + (
    "organization_id",
    "organization_name",
    "line_item",
    "project_id",
    "project_name",
    "api_key_id",
    "user_id",
    "user_email",
    "date",
)

COST_QTY_LABELS = COST_USD_LABELS + ("unit",)

ORG_USER_LABELS = _BASE + (
    "user_id",
    "email",
    "name",
    "role",
)

PROJECT_LABELS = _BASE + (
    "project_id",
    "name",
    "status",
)

PROJECT_KEY_LABELS = _BASE + (
    "project_id",
    "api_key_id",
    "name",
    "owner_type",
    "owner_email",
    "owner_name",
    "owner_role",
    "owner_project_access",
)

# kb: name/organization/project_id/role/user_id (no service_account_id).
PROJECT_SA_LABELS = _BASE + (
    "project_id",
    "name",
    "user_id",
    "role",
)

PROJECT_MEMBER_LABELS = _BASE + (
    "project_id",
    "user_id",
    "email",
    "name",
    "role",
)

ORG_INVITE_LABELS = _BASE + (
    "invite_id",
    "email",
    "role",
    "status",
)

ADMIN_KEY_LABELS = _BASE + (
    "api_key_id",
    "name",
)

SPEND_ORG_LABELS = _BASE + ("scope",)

SPEND_PROJECT_LABELS = _BASE + (
    "project_id",
    "scope",
)

SPEND_PROJECT_USD_LABELS = SPEND_PROJECT_LABELS + (
    "currency",
    "interval",
)

FAMILY_LABELS = USAGE_LABELS

# Compliance posture (no date label; restated each cycle).
COMPLIANCE_ORG_LABELS = _BASE + ("scope",)
COMPLIANCE_PROJECT_LABELS = _BASE + ("project_id",)
COMPLIANCE_PROJECT_MODEL_LABELS = COMPLIANCE_PROJECT_LABELS + ("model",)
COMPLIANCE_RETENTION_INFO_LABELS = _BASE + (
    "project_id",
    "scope",
    "type",
)
COMPLIANCE_HOSTED_TOOL_LABELS = _BASE + (
    "project_id",
    "tool",
)


def _stable_id(prefix: str, seed: str, n: int = 24) -> str:
    digest = hashlib.sha256(f"otel-ai-agent-sim:openai-admin-v3:{seed}".encode()).hexdigest()
    return f"{prefix}{digest[:n]}"


def _slug(name: str) -> str:
    return "".join(ch.lower() if ch.isalnum() else "-" for ch in name).strip("-")


def default_organization_id() -> str:
    raw = os.environ.get("SIM_OPENAI_ADMIN_ORGANIZATION", "").strip()
    if raw:
        return raw
    # kb-shaped org-… id (not a bare uuid).
    return _stable_id("org-", "organization", 24)


def default_organization_name() -> str:
    raw = os.environ.get("SIM_OPENAI_ADMIN_ORGANIZATION_NAME", "").strip()
    return raw or DEFAULT_ORG_NAME


def default_projects(n: int = DEFAULT_PROJECTS) -> tuple[dict[str, str], ...]:
    names = (
        "Observability Demo",
        "AI Platform",
        "Customer Success",
        "Payments API",
        "Mobile Backend",
        "Data Science",
        "Security Research",
        "Partner Integrations",
        "Legacy Billing",  # archived
        "Sandbox Archive",  # archived
    )
    out: list[dict[str, str]] = []
    n = max(1, n)
    for i in range(n):
        name = names[i] if i < len(names) else f"Project {i + 1}"
        # Last two are archived when n>=10; if fewer projects, last one archived when n>2.
        if n >= 10:
            status = "archived" if i >= n - 2 else "active"
        else:
            status = "archived" if i == n - 1 and n > 2 else "active"
        out.append(
            {
                "project_id": _stable_id("proj_", f"project:{i}", 20),
                "name": name,
                "status": status,
            }
        )
    return tuple(out)


def default_api_keys(projects: tuple[dict[str, str], ...], n: int = DEFAULT_API_KEYS) -> tuple[dict[str, str], ...]:
    """Mostly user-owned keys; a few service-account-owned."""
    out: list[dict[str, str]] = []
    roles = ("prod", "staging", "ci", "readonly", "batch", "debug", "canary", "edge")
    active = [p for p in projects if p["status"] == "active"] or list(projects)
    sa_owned = max(2, n // 10)
    for i in range(max(1, n)):
        proj = active[i % len(active)]
        kid = _stable_id("key_", f"apikey:{i}", 20)
        role = roles[i % len(roles)]
        name = f"{_slug(proj['name'])}-{role}"
        if any(k["name"] == name for k in out):
            name = f"{name}-{i + 1}"
        owner_type = "service_account" if i < sa_owned else "user"
        out.append(
            {
                "api_key_id": kid,
                "name": name,
                "project_id": proj["project_id"],
                "owner_type": owner_type,
                "owner_email": "",
                "owner_name": "",
                "owner_role": "member",
                "owner_project_access": "active",
            }
        )
    return tuple(out)
