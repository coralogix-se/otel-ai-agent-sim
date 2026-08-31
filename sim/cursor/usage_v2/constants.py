"""Catalog for Cursor Usage dashboard ``cursor_*`` metrics (Admin APIs shape)."""

from __future__ import annotations

import os

# Match live cxai-dev series labels (team 3405693 / eu2).
DEFAULT_TEAM_ID = "3405693"
DEFAULT_CX_APPLICATION = "Cursor"
DEFAULT_CX_SUBSYSTEM = "Admin APIs"

CURSOR_USAGE_MODELS: tuple[str, ...] = (
    "default",
    "composer-2.5-fast",
    "cursor-grok-4.6-high-fast",
    "cursor-grok-4.6-medium-fast",
    "gpt-5.6-sol-medium",
    "gpt-5.6-terra-high",
    "claude-4.6-sonnet-medium-thinking",
    "claude-4.6-opus-high-thinking",
    "claude-sonnet-5-thinking-medium",
    "claude-opus-4-8-thinking-high",
)

CURSOR_USAGE_MODEL_WEIGHTS: tuple[float, ...] = (
    0.18,
    0.14,
    0.12,
    0.10,
    0.10,
    0.08,
    0.10,
    0.08,
    0.06,
    0.04,
)

# Requests surfaces seen on cxai-dev; keep tab/cli/non_ai for ai_code_lines overlap.
CURSOR_SURFACES: tuple[str, ...] = (
    "agent",
    "chat",
    "composer",
    "cmdk",
    "tab",
    "bugbot",
    "cli",
    "non_ai",
)
CURSOR_SURFACE_WEIGHTS: tuple[float, ...] = (0.28, 0.16, 0.14, 0.10, 0.12, 0.06, 0.06, 0.08)

CURSOR_BILLING_KINDS: tuple[str, ...] = (
    "Included in Business",
    "Included in Pro",
    "On-Demand",
    "API Key",
)
CURSOR_BILLING_KIND_WEIGHTS: tuple[float, ...] = (0.55, 0.25, 0.15, 0.05)

CURSOR_BILLING_CLASSES: tuple[str, ...] = (
    "subscription_included",
    "usage_based",
    "api_key",
)
CURSOR_BILLING_CLASS_WEIGHTS: tuple[float, ...] = (0.62, 0.28, 0.10)

CURSOR_TOKEN_TYPES: tuple[str, ...] = (
    "input",
    "output",
    "cache_read",
    "cache_write",
)

# Live cxai-dev currently only emits commit_source="ide".
CURSOR_COMMIT_SOURCES: tuple[str, ...] = ("ide",)
CURSOR_DIRECTIONS: tuple[str, ...] = ("added", "deleted")

CURSOR_FILE_EXTENSIONS: tuple[str, ...] = (
    "ts",
    "tsx",
    "py",
    "go",
    "rs",
    "java",
    "md",
)
# Live change_source values are uppercase (e.g. COMPOSER).
CURSOR_CHANGE_SOURCES: tuple[str, ...] = ("COMPOSER", "TAB", "AGENT")

CURSOR_CONVERSATION_DIMENSIONS: dict[str, tuple[str, ...]] = {
    "categories": (
        "API Integration",
        "Architecture",
        "Bug Fixing & Debugging",
        "Code Explanation",
        "Code Refactoring",
        "Code Review",
        "Configuration",
        "Data/Database",
        "DevOps/Deployment",
        "Documentation",
        "Learning",
        "New Features",
        "Performance",
        "Security",
        "Testing",
        "UI/Styling",
    ),
    "intents": ("feature", "bugfix", "refactor", "explain", "tests", "docs", "ktlo"),
    "complexity": ("low", "medium", "high", "trivial"),
    "guidanceLevels": ("low", "medium", "high"),
    "workTypes": ("feature", "bugfix", "ktlo", "chore", "spike", "new_feature", "bug"),
}

CURSOR_COMMANDS: tuple[str, ...] = (
    "edit",
    "fix",
    "explain",
    "generate-tests",
    "refactor",
    "commit",
)
CURSOR_MCP_SERVERS: tuple[str, ...] = (
    "github",
    "coralogix-server",
    "cxaidev-coralogix-server",
    "ob-coralogix-server",
    "slack",
    "notion",
    "linear",
    "playwright",
    "Atlassian",
)
CURSOR_MCP_TOOLS: tuple[str, ...] = (
    "query_dataprime",
    "query_promql_instant",
    "search_docs",
    "fetch_doc",
    "search_pull_requests",
    "list_issues",
    "get_file_contents",
    "browser_navigate",
    "search",
    "mcp_auth",
)
CURSOR_SKILLS: tuple[str, ...] = (
    "code-review",
    "pr-summary",
    "debug",
    "migrate",
    "docs",
)

CURSOR_ROLES: tuple[str, ...] = ("owner", "admin", "member", "member", "member", "member")

CURSOR_CLIENT_VERSIONS: tuple[str, ...] = (
    "0.50.5",
    "0.49.6",
    "0.48.2",
    "0.47.8",
)

CURSOR_REPOS: tuple[str, ...] = (
    "coralogix/cxai-observability-demo-playground",
    "coralogix/onlineboutique",
    "coralogix/dataprime-query-engine",
    "acme/payments-api",
    "personal/dotfiles",
)

CURSOR_GROUPS: tuple[tuple[str, str], ...] = (
    ("eng", "Engineering"),
    ("product", "Product"),
    ("cs", "Customer Success"),
    ("sec", "Security"),
    ("unassigned", "Unassigned"),
)


def cursor_usage_team_id() -> str:
    return os.environ.get("SIM_CURSOR_USAGE_TEAM_ID", DEFAULT_TEAM_ID).strip() or DEFAULT_TEAM_ID


def cursor_usage_cx_application() -> str:
    return (
        os.environ.get("SIM_CURSOR_USAGE_CX_APPLICATION_NAME", DEFAULT_CX_APPLICATION).strip()
        or DEFAULT_CX_APPLICATION
    )


def cursor_usage_cx_subsystem() -> str:
    return (
        os.environ.get("SIM_CURSOR_USAGE_CX_SUBSYSTEM_NAME", DEFAULT_CX_SUBSYSTEM).strip()
        or DEFAULT_CX_SUBSYSTEM
    )


def cursor_usage_roster_size() -> int:
    from sim.common.env import _env_int

    return max(4, min(64, _env_int("SIM_CURSOR_USAGE_ROSTER_SIZE", 24)))
