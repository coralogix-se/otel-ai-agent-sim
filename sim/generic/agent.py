"""Generic multi-step agent workflow (non-CLI agents: ChatGPT, Copilot, Grok, …)."""
from __future__ import annotations

import random
import time
import uuid

from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode

from sim.common.otel import _gen_ai_dashboard_llm_span_attributes, tool_version_for
from sim.common.model_pricing import estimate_llm_cost_usd
from sim.common.identity import random_coralogix_identity

# Longest generic workflow: root + planning subtree + N tools + rag + completion + validate.
_DEEP_TOOL_NAMES = (
    "tool.read_file",
    "tool.grep",
    "tool.bash",
    "tool.apply_patch",
    "tool.run_tests",
    "tool.web_fetch",
    "tool.db_query",
    "tool.git_status",
    "tool.linter",
    "tool.formatter",
    "tool.deploy_preview",
)


def emit_generic_agent_workflow(
    session_id: str,
    profile: dict,
    input_tokens: int,
    output_tokens: int,
    *,
    deep: bool,
    deep_span_target: int,
) -> None:
    """
    Generic multi-step agent trace (not Gemini/Claude/Codex single-span CLI shape).

    - Shallow (``deep=False``): **4** spans — root, planning, one tool, completion.
    - Deep (``deep=True``): **at least 10** spans by default — root, planning + 2 children,
      multiple tools, rag, completion, validate. ``deep_span_target`` sets total span count
      (clamped to a feasible range).
    """
    ver = tool_version_for(profile["agent.product"])
    # Not Gemini CLI: generic multi-vendor workflows stay on service ``ai-agent-engine`` (do not use ``gemini-cli`` name).
    tracer = trace.get_tracer("generic.agent.workflow", ver)
    user_attrs = random_coralogix_identity(session_id)
    workflow_request_id = str(uuid.uuid4())
    if not deep:
        with tracer.start_as_current_span(
            "agent.workflow.root",
            attributes={
                **user_attrs,
                "gen_ai.system": profile["gen_ai.system"],
                "gen_ai.request.model": profile["gen_ai.request.model"],
                "gen_ai.request.id": workflow_request_id,
                "gen_ai.session.id": session_id,
                "gen_ai.conversation.id": session_id,
                "sim.workflow.kind": "agent.workflow",
                "agent.product": profile["agent.product"],
                "sim.agent_tool_version": ver,
                "sim.trace.depth": "shallow",
                "sim.trace.span_count_expected": 4,
            },
        ) as root:
            with tracer.start_as_current_span("gen_ai.planning") as plan:
                plan.set_attributes(
                    {
                        "gen_ai.system": profile["gen_ai.system"],
                        "gen_ai.request.model": profile["gen_ai.request.model"],
                        "gen_ai.usage.input_tokens": input_tokens,
                    }
                )
                time.sleep(random.uniform(1.5, 4.0))

            tool_name = random.choice(["git_patch", "web_search", "db_query"])
            with tracer.start_as_current_span(f"tool.{tool_name}") as tool:
                if random.random() < 0.10:
                    tool.set_status(Status(StatusCode.ERROR, "Context window exceeded"))
                    root.add_event("agent_hallucination_detected", {"confidence": 0.42})
                else:
                    time.sleep(0.5)
                    tool.set_attribute("gen_ai.tool.status", "success")

            with tracer.start_as_current_span("gen_ai.completion") as comp:
                comp.set_attributes(
                    _gen_ai_dashboard_llm_span_attributes(
                        input_tokens,
                        output_tokens,
                        operation_name="chat",
                        model=profile["gen_ai.request.model"],
                    )
                )
        return

    # Deep tree: 1 root + 1 planning + 2 under planning + N tools + rag + completion + validate
    # total spans = 7 + N  =>  N = total - 7  (need total >= 10 => N >= 3)
    target = max(10, deep_span_target)
    n_tools = max(3, target - 7)

    with tracer.start_as_current_span(
        "agent.workflow.root",
        attributes={
            **user_attrs,
            "gen_ai.system": profile["gen_ai.system"],
            "gen_ai.request.model": profile["gen_ai.request.model"],
            "gen_ai.request.id": workflow_request_id,
            "gen_ai.session.id": session_id,
            "gen_ai.conversation.id": session_id,
            "sim.workflow.kind": "agent.workflow",
            "agent.product": profile["agent.product"],
            "sim.agent_tool_version": ver,
            "sim.trace.depth": "deep",
            "sim.trace.span_count_expected": target,
        },
    ) as root:
        with tracer.start_as_current_span("gen_ai.planning") as plan:
            plan.set_attributes(
                {
                    "gen_ai.system": profile["gen_ai.system"],
                    "gen_ai.request.model": profile["gen_ai.request.model"],
                    "gen_ai.usage.input_tokens": input_tokens,
                }
            )
            time.sleep(random.uniform(0.8, 2.2))
            with tracer.start_as_current_span("gen_ai.context.load") as ctx:
                ctx.set_attribute("gen_ai.context.chars", random.randint(4_000, 120_000))
                time.sleep(random.uniform(0.05, 0.35))
            with tracer.start_as_current_span("gen_ai.token_estimate") as est:
                est.set_attribute(
                    "gen_ai.estimated_cost_usd",
                    round(
                        estimate_llm_cost_usd(
                            profile["gen_ai.request.model"],
                            input_tokens,
                            output_tokens,
                        ),
                        4,
                    ),
                )
                time.sleep(random.uniform(0.03, 0.2))

        for i in range(n_tools):
            name = _DEEP_TOOL_NAMES[i % len(_DEEP_TOOL_NAMES)]
            with tracer.start_as_current_span(name) as sp:
                if random.random() < 0.06:
                    sp.set_status(Status(StatusCode.ERROR, "tool_timeout"))
                    root.add_event("tool_retry_scheduled", {"attempt": i + 1})
                else:
                    time.sleep(random.uniform(0.08, 0.45))
                    sp.set_attribute("gen_ai.tool.status", "success")

        with tracer.start_as_current_span("gen_ai.rag.retrieve") as rag:
            rag.set_attribute("gen_ai.rag.chunks", random.randint(1, 24))
            time.sleep(random.uniform(0.1, 0.5))

        with tracer.start_as_current_span("gen_ai.completion") as comp:
            comp.set_attributes(
                _gen_ai_dashboard_llm_span_attributes(
                    input_tokens,
                    output_tokens,
                    operation_name="chat",
                    model=profile["gen_ai.request.model"],
                )
            )
            time.sleep(random.uniform(0.2, 1.0))

        with tracer.start_as_current_span("gen_ai.output.validate") as val:
            val.set_attribute("gen_ai.validation.passed", random.random() > 0.05)
            time.sleep(random.uniform(0.05, 0.25))

