"""Agent marketing descriptions, sample prompts, and model pools (shared)."""
from __future__ import annotations

import hashlib
import json

GEMINI_AGENT_DESCRIPTION = (
    "Gemini CLI is an open-source AI agent that brings the power of Gemini directly "
    "into your terminal. It is designed to be a terminal-first, extensible, and "
    "powerful tool for developers, engineers, SREs, and beyond."
)

GEMINI_SAMPLE_PROMPTS = (
    "tell me something interesting about deep sea diving",
    "explain how vector databases differ from traditional RDBMS for embeddings",
    "what are pragmatic ways to cut cloud egress cost on Kubernetes",
    "summarize OpenTelemetry gen_ai semantic conventions in five bullets",
    "how do I safely rotate database credentials in a CI/CD pipeline",
)

CLAUDE_CODE_AGENT_DESCRIPTION = (
    "Claude Code is Anthropic's agentic coding tool: it runs in your terminal, reads your repo, "
    "edits files, runs commands, and uses tools under your control—aligned with Coralogix "
    "claude_code.* telemetry and Code Agents dashboards."
)

CLAUDE_CODE_SAMPLE_PROMPTS = (
    "refactor this module to use async/await consistently",
    "add error handling and structured logging to the API client",
    "find flaky tests in packages/core and suggest fixes",
    "explain what this git diff does in two short paragraphs",
    "run the test suite and summarize failures by root cause",
    "add unit tests for the checkout service retry logic",
    "why is the OTLP exporter dropping spans under high load?",
    "migrate this Dockerfile to a multi-stage build and shrink the image",
    "review the PR for security issues in the auth middleware",
    "generate a mermaid diagram of the payment flow",
    "fix the race condition when two workers update inventory",
    "add Prometheus metrics for queue depth and consumer lag",
    "debug why p99 latency spiked after yesterday's deploy",
    "wire up feature flags for the new billing UI behind a kill switch",
    "convert this class hierarchy to composition and update call sites",
    "add integration tests for the webhook signature verification path",
    "trace this 500 error through the order service and propose a fix",
    "implement idempotency keys on the payment capture endpoint",
    "clean up deprecated API routes and add a migration guide in the README",
    "reduce memory churn in the batch processor hot path",
    "add OpenTelemetry baggage propagation across our gRPC services",
    "fix the TypeScript strict-null errors in the dashboard package",
    "write a runbook section for when the Redis cluster fails over",
    "split this 800-line handler into smaller testable functions",
    "add rate limiting to the public API without breaking existing clients",
    "investigate duplicate charges in the reconciliation job logs",
    "upgrade the Go module to 1.22 and fix any breaking stdlib changes",
    "add snapshot tests for the invoice PDF renderer",
    "harden SQL queries in the admin search endpoint against injection",
    "set up GitHub Actions caching to speed up the monorepo CI pipeline",
    "document the event schema for the customer.updated Kafka topic",
    "fix the timezone bug in scheduled report generation for EU customers",
    "add a health check that validates downstream dependency connectivity",
    "implement graceful shutdown for the worker pool on SIGTERM",
    "review N+1 queries in the catalog list endpoint and batch them",
    "add dark-mode tokens to the design system and migrate Button usages",
    "create a canary deployment config for the checkout microservice",
    "fix flaky e2e test that fails on the first of the month",
    "add structured audit logs for admin impersonation events",
    "optimize the Elasticsearch aggregation used on the usage dashboard",
    "implement retry with exponential backoff for the Stripe webhook handler",
    "add validation for env vars at startup and fail fast with clear errors",
    "refactor the auth token refresh flow to avoid blocking the main thread",
    "generate example curl commands for the new internal REST endpoints",
    "fix CORS preflight failures on the staging API gateway",
    "add circuit breaker around the third-party address validation API",
    "write property-based tests for the pricing calculator edge cases",
    "migrate secrets from flat env files to the team vault pattern",
    "add pagination cursors to the activity feed API and update the client",
    "investigate why canary pods are not receiving traffic after rollout",
    "implement soft-delete on user records and update all list queries",
    "add SLO burn-rate alerts for the authentication service",
    "fix the memory leak in the websocket connection manager",
    "create a minimal repro for the deadlock in the job scheduler",
    "add input sanitization on the feedback form before persisting to DB",
    "update dependency versions with known CVEs in packages/shared",
    "implement bulk export for audit logs with streaming CSV download",
    "add feature parity tests between legacy and new checkout flows",
    "fix incorrect metric labels after the service rename last sprint",
    "draft ADR for moving session storage from Redis to Postgres",
    "add retry-safe consumers for the dead-letter queue processor",
    "reduce cold-start time on the Lambda authorizer function",
    "align protobuf definitions between services and regenerate stubs",
)

# Short assistant replies for ``claude_code.api_response_body`` log events (prompt analysis UI).
CLAUDE_CODE_SAMPLE_ASSISTANT_REPLIES = (
    "I'll refactor the module to use async/await and add tests for the error paths.",
    "The flaky tests point to shared global state in the fixture setup; I'll isolate them.",
    "Here's a summary of the failures: 3 assertion errors in checkout, 1 timeout in shipping.",
    "The diff adds a circuit breaker around the payment client and bumps the retry budget.",
    "I found two N+1 queries in the catalog service; I'll batch the lookups next.",
    "Latency spikes correlate with connection pool exhaustion; I'll add pool metrics and tune limits.",
    "I'll gate the billing UI behind the feature flag and add a fallback for disabled tenants.",
    "The webhook handler needs idempotency keys—I'll use the event ID header and a short TTL store.",
    "I'll split the handler into three modules and keep the public API unchanged for callers.",
    "Redis failover runbook steps look good; I'll add a synthetic check that validates write/read after promotion.",
    "Strict-null fixes are localized to the dashboard package; I'll run tsc and update two call sites.",
    "I'll add rate limiting via a token bucket middleware with per-API-key quotas.",
    "The reconciliation duplicates come from at-least-once delivery; I'll dedupe on payment intent ID.",
    "Graceful shutdown will drain the worker queue for up to 30s before exit.",
    "I'll batch catalog queries with a DataLoader-style helper and add a regression test.",
    "Canary analysis shows 502s from upstream timeouts; I'll increase connect timeout and add retries.",
    "Circuit breaker config: 5 failures in 30s opens for 60s, half-open probe on next request.",
    "I'll migrate secrets references to vault paths and document the rotation procedure.",
    "Pagination cursors will use opaque base64 offsets; I'll update the mobile client parser too.",
    "Memory leak is in unreleased websocket ping timers; I'll clear intervals on disconnect.",
)

# Default when ``SIM_CLAUDE_MODEL`` unset and profile has no model (Anthropic API alias).
CLAUDE_CODE_DEFAULT_MODEL = "claude-sonnet-4-6"

# Claude Code ``/model`` picker + third-party routes (June 2026). ``SIM_CLAUDE_MODEL`` overrides.
# Excludes suspended Anthropic preview routes (e.g. fable/mythos) until re-enabled upstream.
_CLAUDE_CODE_MODELS = (
    "claude-opus-4-8",
    "claude-sonnet-4-6",
    "claude-haiku-4-5",
    "gpt-5.5",
    "gemini-3.1-pro-preview",
    "gemini-3.5-flash",
    "grok-4.3",
    "qwen3.7-max",
)


def claude_code_gen_ai_system_for_model(model: str) -> str:
    """``gen_ai.system`` for Claude Code spans when routing Anthropic or third-party models."""
    key = (model or "").strip().lower()
    if key.startswith("claude-"):
        return "anthropic"
    if key.startswith("gpt-"):
        return "openai"
    if key.startswith("gemini-"):
        return "gcp.gemini"
    if key.startswith("grok-"):
        return "xai"
    if key.startswith("qwen"):
        return "qwen"
    return "anthropic"

CODEX_AGENT_DESCRIPTION = (
    "OpenAI Codex is an AI coding agent for your terminal and IDE: it reasons over your codebase, "
    "proposes edits, runs commands, and integrates with your workflow—similar export shape to other "
    "CLI agents (user_prompt span, gen_ai.*, cx.*)."
)

CODEX_SAMPLE_PROMPTS = (
    "implement a small LRU cache with TTL in Python",
    "fix the race condition in the connection pool shutdown path",
    "add OpenAPI request validation middleware",
    "write a unit test for edge cases in parse_config",
    "optimize this hot loop for fewer allocations",
)

# Codex CLI default (OpenAI recommends ``gpt-5.5`` — https://developers.openai.com/codex/models).
CODEX_DEFAULT_MODEL = "gpt-5.5"

# Codex CLI pool; excludes retired ``o4-mini`` / ``gpt-5-codex`` and deprecated ``gpt-5.*-codex`` tiers.
CODEX_CLI_MODELS = (
    "gpt-5.5",
    "gpt-5.4",
    "gpt-5.4-mini",
    "gpt-5.3-codex",
    "gpt-5.3-codex-spark",
)

# Cursor Composer: third-party + Cursor-native models (see https://cursor.com/docs/models).
CURSOR_DEFAULT_MODEL = "claude-sonnet-4-6"

CURSOR_COMPOSER_MODELS = (
    "composer-2",
    "claude-sonnet-4-6",
    "claude-sonnet-4-5",
    "claude-opus-4-8",
    "claude-opus-4-7",
    "claude-opus-4-6",
    "claude-haiku-4-5-20251001",
    "gemini-2.5-flash",
    "gemini-2.5-pro",
    "gemini-3.5-flash",
    "gpt-5.5",
    "gpt-5.4",
    "gpt-5.4-mini",
)

CURSOR_SAMPLE_PROMPTS = (
    "add structured logging around the API client and redact tokens",
    "refactor the React hook to avoid stale closures in useEffect",
    "fix the failing unit test in packages/core for DST edge cases",
    "add a guardrail check before LLM responses are shown to the user",
    "optimize this database query and add an index if needed",
)

COPILOT_CLI_AGENT_DESCRIPTION = (
    "GitHub Copilot CLI brings Copilot agent workflows to the terminal; VS Code OTel exports "
    "invoke_agent/chat/execute_tool spans (gen_ai.*) and copilot_chat.* metrics per OpenTelemetry GenAI conventions."
)

COPILOT_CLI_SAMPLE_PROMPTS = (
    "refactor this module for readability without changing behavior",
    "add unit tests for the retry helper and edge cases",
    "find where this API call is made and suggest error handling",
    "explain this stack trace and propose a minimal fix",
    "run the linter fixes for packages/api only",
)

# Copilot CLI GA models (https://docs.github.com/en/copilot/reference/ai-models/supported-models).
COPILOT_DEFAULT_MODEL = "gpt-5.5"

COPILOT_CLI_MODELS = (
    "gpt-5.5",
    "gpt-5.4",
    "gpt-5.4-mini",
    "gpt-5.3-codex",
    "gpt-5-mini",
    "claude-sonnet-4-6",
    "claude-opus-4-8",
    "claude-opus-4-7",
    "claude-opus-4-6",
    "claude-haiku-4-5",
    "gemini-2.5-pro",
    "gemini-3.5-flash",
    "gemini-3-flash-preview",
    "mai-code-1-flash",
)

# Gemini CLI model ids (https://geminicli.com/docs/reference/configuration/).
GEMINI_DEFAULT_MODEL = "gemini-2.5-pro"

GEMINI_CLI_MODELS = (
    "gemini-2.5-pro",
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
    "gemini-3-pro-preview",
    "gemini-3-flash-preview",
    "gemini-3.1-pro-preview",
    "gemini-3.5-flash",
)


def claude_prompt_for_session(session_id: str) -> str:
    """Stable user_prompt text for a ``session.id`` (one plausible reason per session)."""
    key = session_id.strip() or "unknown-session"
    idx = int(hashlib.sha256(key.encode()).hexdigest(), 16) % len(CLAUDE_CODE_SAMPLE_PROMPTS)
    return CLAUDE_CODE_SAMPLE_PROMPTS[idx]


def claude_assistant_reply_for_session(session_id: str) -> str:
    """Stable assistant reply aligned with ``claude_prompt_for_session``."""
    key = session_id.strip() or "unknown-session"
    idx = int(hashlib.sha256(b"reply:" + key.encode()).hexdigest(), 16) % len(CLAUDE_CODE_SAMPLE_ASSISTANT_REPLIES)
    return CLAUDE_CODE_SAMPLE_ASSISTANT_REPLIES[idx]


def claude_api_response_body_json(text: str) -> str:
    """Anthropic ``claude_code.api_response_body`` JSON (AI Center ``sessionMessages`` regexp)."""
    return json.dumps(
        {"content": [{"type": "text", "text": text}]},
        ensure_ascii=False,
    )
