"""Agent marketing descriptions, sample prompts, and model pools (shared)."""
from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence

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

# Model pools below (Claude, Codex, Cursor, Copilot, Gemini) should be reviewed regularly against
# each vendor's current model list. ``claude-fable-5`` restored globally Jul 2026 (was briefly suspended
# Jun 2026). Do not simulate ``claude-mythos-5`` in general pools — Glasswing / limited release only.
# Last reviewed 2026-09-02 against Claude Code / Codex / Cursor / Copilot / Gemini CLI docs + vendor pricing.
# Dump pools + assert rates: ``python3 scripts/print_model_pools.py --check``.

# Default when ``SIM_CLAUDE_MODEL`` unset and profile has no model.
CLAUDE_CODE_DEFAULT_MODEL = "claude-sonnet-5"

# Claude Code API model ids (https://code.claude.com/docs/en/model-config).
# ``model`` on ``claude_code.api_request`` logs, Prometheus counters, and spans uses full ids
# (e.g. ``claude-sonnet-5``), not ``/model`` aliases like ``sonnet``. ``SIM_CLAUDE_MODEL`` overrides.
# Weighted toward Sonnet/Haiku for realistic spend; Opus 5 + Fable remain but are rare.
_CLAUDE_CODE_MODELS = (
    "claude-sonnet-5",
    "claude-sonnet-5",
    "claude-sonnet-5",
    "claude-sonnet-5",
    "claude-sonnet-4-6",
    "claude-sonnet-4-5-20250929",
    "claude-haiku-4-5",
    "claude-haiku-4-5",
    "claude-haiku-4-5-20251001",
    "claude-opus-5",
    "claude-opus-4-8",
    "claude-fable-5",
)


def claude_code_gen_ai_system_for_model(model: str) -> str:
    """``gen_ai.system`` for Claude Code spans (Anthropic API ids and legacy aliases)."""
    key = (model or "").strip().lower()
    if key in ("sonnet", "opus", "haiku", "fable", "default", "opusplan"):
        return "anthropic"
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

# Codex CLI default (https://developers.openai.com/codex/models — Power uses Sol).
CODEX_DEFAULT_MODEL = "gpt-5.6-sol"

# Codex CLI pool: GPT-5.6 Sol / Terra / Luna (gpt-5.5 / gpt-5.3-codex deprecated for ChatGPT sign-in).
CODEX_CLI_MODELS = (
    "gpt-5.6-sol",
    "gpt-5.6-sol",
    "gpt-5.6-sol",
    "gpt-5.6-terra",
    "gpt-5.6-terra",
    "gpt-5.6-luna",
    "gpt-5.6-luna",
)

# Cursor Composer: Cursor-native + API-pool models (https://cursor.com/docs/models).
# ``composer-2.5-fast`` is the default interactive tier; ``composer-2.5`` is the standard tier.
CURSOR_DEFAULT_MODEL = "composer-2.5-fast"

CURSOR_COMPOSER_MODELS = (
    "composer-2.5",
    "composer-2.5-fast",
    "composer-2.5-fast",
    "grok-4.6",
    "grok-4.5",
    "claude-sonnet-5",
    "claude-sonnet-4-6",
    "claude-opus-5",
    "claude-opus-4-8",
    "claude-haiku-4-5",
    "claude-fable-5",
    "gemini-3.7-flash",
    "gemini-3.6-flash",
    "gemini-3.5-flash",
    "gemini-3.1-pro",
    "gpt-5.6-sol",
    "gpt-5.6-terra",
    "gpt-5.6-luna",
    "gpt-5.5",
    "gpt-5.4",
    "gpt-5.4-mini",
    "kimi-k2.7-code",
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
    "upgrade dependencies with known CVEs in packages/shared",
    "add snapshot tests for the invoice PDF renderer",
    "harden SQL queries in the admin search endpoint against injection",
    "set up CI caching to speed up the monorepo pipeline",
    "document the event schema for the customer.updated Kafka topic",
    "fix the timezone bug in scheduled report generation for EU customers",
    "add a health check that validates downstream dependency connectivity",
    "implement graceful shutdown for the worker pool on SIGTERM",
    "review N+1 queries in the catalog list endpoint and batch them",
    "create a canary deployment config for the checkout microservice",
    "fix flaky e2e test that fails on the first of the month",
    "add structured audit logs for admin impersonation events",
    "optimize the aggregation used on the usage dashboard",
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
    "implement bulk export for audit logs with streaming CSV download",
    "add feature parity tests between legacy and new checkout flows",
    "fix incorrect metric labels after the service rename last sprint",
    "draft ADR for moving session storage from Redis to Postgres",
    "add retry-safe consumers for the dead-letter queue processor",
    "align protobuf definitions between services and regenerate stubs",
    "summarize OpenTelemetry gen_ai semantic conventions in five bullets",
    "how do I safely rotate database credentials in a CI/CD pipeline",
    "add structured logging around the API client and redact tokens",
    "refactor the React hook to avoid stale closures in useEffect",
    "add a guardrail check before LLM responses are shown to the user",
    "optimize this database query and add an index if needed",
)

# Short assistant replies for ``gen_ai.output.messages`` on Copilot ``invoke_agent`` spans (AI Analysis UI).
COPILOT_CLI_SAMPLE_ASSISTANT_REPLIES = (
    "I'll scan the repo for failing tests and propose a patch.",
    "Here's a concise fix for the handler plus an updated unit test.",
    "I found the root cause in the telemetry hook — applying a small refactor.",
    "Summarizing the diff and suggested next steps for this Copilot CLI session.",
    "Running the targeted grep and read_file steps, then I'll suggest edits.",
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
    "Added unit tests for the retry helper covering timeout and cancellation edge cases.",
    "The patch introduces structured logging with request IDs on every outbound HTTP call.",
    "I'll update the README with migration steps and add a changelog entry for the API break.",
    "Tool results show the failing assertion is in the mock setup; I'll fix the fixture next.",
    "Proposed changes are limited to three files; no public API surface changes required.",
    "I'll run the linter and formatter on the touched packages before suggesting the final diff.",
)

# Copilot CLI GA models (https://docs.github.com/en/copilot/reference/ai-models/supported-models).
# Exclude ``gpt-5.4-nano`` (Codex VS Code extension only). ``claude-mythos-5`` is Glasswing-only.
# Weighted toward mini/haiku/flash for realistic API-equivalent spend; Opus/Fable rare.
COPILOT_DEFAULT_MODEL = "gpt-5.4-mini"

COPILOT_CLI_MODELS = (
    "gpt-5.4-mini",
    "gpt-5.4-mini",
    "gpt-5.6-terra",
    "gpt-5.6-terra",
    "gpt-5-mini",
    "gpt-5.4",
    "gpt-5.5",
    "gpt-5.6-luna",
    "gpt-5.6-sol",
    "gpt-5.3-codex",
    "claude-haiku-4-5",
    "claude-haiku-4-5",
    "claude-sonnet-5",
    "claude-sonnet-5",
    "claude-sonnet-4-6",
    "claude-opus-5",
    "claude-opus-4-8",
    "claude-fable-5",
    "gemini-3.7-flash",
    "gemini-3.6-flash",
    "gemini-3.5-flash",
    "gemini-3.1-pro",
    "gemini-3-flash",
    "mai-code-1.1-flash",
    "mai-code-1-flash",
    "raptor-mini",
    "kimi-k2.7-code",
    "kimi-k3",
    "grok-4.6",
    "grok-4.5",
)

# Gemini CLI model ids (https://geminicli.com/docs/reference/configuration/ ``model`` aliases).
# ``gemini-3.1-flash-lite`` is the stable id; ``gemini-3.1-flash-lite-preview`` is an alias only.
GEMINI_DEFAULT_MODEL = "gemini-3.5-flash"

GEMINI_CLI_MODELS = (
    "gemini-2.5-pro",
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
    "gemini-3-pro-preview",
    "gemini-3-flash-preview",
    "gemini-3.1-pro-preview",
    "gemini-3.5-flash",
    "gemini-3.5-flash",
    "gemini-3.6-flash",
    "gemini-3.7-flash",
    "gemini-3.1-flash-lite",
    "gemma-4-31b-it",
    "gemma-4-26b-a4b-it",
)


def _generic_assistant_reply(prompt: str) -> str:
    """Fallback assistant text when no curated reply exists for a prompt."""
    p = prompt.strip().rstrip(".")
    lower = p.lower()
    if lower.startswith(("how ", "why ", "what ", "explain ", "summarize ", "document ")):
        return f"Here's a concise answer on that: {p[:120]}."
    if lower.startswith(
        (
            "add ",
            "fix ",
            "implement ",
            "migrate ",
            "refactor ",
            "debug ",
            "review ",
            "run ",
            "wire ",
            "convert ",
            "split ",
            "reduce ",
            "upgrade ",
            "harden ",
            "optimize ",
            "create ",
            "investigate ",
            "align ",
            "write ",
            "generate ",
            "clean ",
            "set ",
            "trace ",
            "draft ",
            "find ",
        ),
    ):
        return f"I'll work on that next: {p[:120]}."
    return f"Working on your request — {p[:120]}."


def _build_prompt_reply_pairs(
    prompts: Sequence[str],
    replies: Sequence[str],
) -> tuple[tuple[str, tuple[str, ...]], ...]:
    """Pair each prompt with a semantically aligned reply (index-aligned when possible)."""
    pairs: list[tuple[str, tuple[str, ...]]] = []
    n_replies = len(replies)
    for i, prompt in enumerate(prompts):
        if i < n_replies:
            pairs.append((prompt, (replies[i],)))
        else:
            pairs.append((prompt, (_generic_assistant_reply(prompt),)))
    return tuple(pairs)


CLAUDE_CODE_PROMPT_REPLY_PAIRS = _build_prompt_reply_pairs(
    CLAUDE_CODE_SAMPLE_PROMPTS,
    CLAUDE_CODE_SAMPLE_ASSISTANT_REPLIES,
)
COPILOT_CLI_PROMPT_REPLY_PAIRS = _build_prompt_reply_pairs(
    COPILOT_CLI_SAMPLE_PROMPTS,
    COPILOT_CLI_SAMPLE_ASSISTANT_REPLIES,
)


def _session_turn_pair_index(session_id: str, turn: int, n_pairs: int) -> int:
    key = f"{session_id.strip() or 'unknown-session'}:{turn}"
    return int(hashlib.sha256(key.encode()).hexdigest(), 16) % n_pairs


def prompt_reply_for_turn(
    session_id: str,
    turn: int,
    pairs: Sequence[tuple[str, Sequence[str]]],
) -> tuple[str, str]:
    """Stable prompt/reply for a session turn; reply is chosen from the aligned pair."""
    idx = _session_turn_pair_index(session_id, turn, len(pairs))
    prompt, replies = pairs[idx]
    if len(replies) == 1:
        return prompt, replies[0]
    ridx = int(hashlib.sha256(f"reply:{session_id}:{turn}".encode()).hexdigest(), 16) % len(replies)
    return prompt, replies[ridx]


def claude_prompt_for_session(session_id: str, *, turn: int = 0) -> str:
    """Stable user_prompt text for a ``session.id`` (one plausible reason per session)."""
    prompt, _reply = prompt_reply_for_turn(session_id, turn, CLAUDE_CODE_PROMPT_REPLY_PAIRS)
    return prompt


def claude_assistant_reply_for_session(session_id: str, *, turn: int = 0) -> str:
    """Stable assistant reply aligned with ``claude_prompt_for_session``."""
    _prompt, reply = prompt_reply_for_turn(session_id, turn, CLAUDE_CODE_PROMPT_REPLY_PAIRS)
    return reply


def copilot_prompt_reply_for_turn(conversation_id: str, turn: int) -> tuple[str, str]:
    """Stable Copilot CLI user/assistant message pair for a conversation turn."""
    return prompt_reply_for_turn(conversation_id, turn, COPILOT_CLI_PROMPT_REPLY_PAIRS)


def claude_api_response_body_json(text: str) -> str:
    """Anthropic ``claude_code.api_response_body`` JSON (AI Center ``sessionMessages`` regexp)."""
    return json.dumps(
        {"content": [{"type": "text", "text": text}]},
        ensure_ascii=False,
    )
