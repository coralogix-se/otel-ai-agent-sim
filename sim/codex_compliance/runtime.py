"""Emit Codex Compliance + Codex Dashboard (Analytics) gauges.

Contracts:
- ``docs/query-engine-maps/codex-compliance.md``
- ``docs/query-engine-maps/codex-dashboard.md`` (Client usage, model/cost tiles, reviews)

Counters are cumulative running day totals per (labels, ``date``). Analytics gauges
restate each cycle for today and keep closed-day series. Feed lag is restated every
cycle (no ``date``).
"""

from __future__ import annotations

import logging
import random
from datetime import datetime, timezone

from prometheus_client import CollectorRegistry, Gauge

from sim.common.env import _env_float, _env_int
from sim.common.openai_compliance import (
    backfill_dates,
    build_seats,
    build_workspaces,
    date_str,
    day_rng,
    default_organization,
    metric_base,
    utc_today,
)
from sim.codex_compliance.constants import (
    ACTION_RESULTS,
    CODEX_CLIENT_IDS,
    CODEX_LOG_SUB_EVENTS,
    CODEX_MODELS,
    DEFAULT_BACKFILL_DAYS,
    DEFAULT_USERS,
    DEFAULT_WORKSPACES,
    DEVICE_KEY_CLASSES,
    FEED_EVENT_TYPES,
    GEO_COUNTRIES,
    MACHINE_IDS,
    REASONING_EFFORTS,
    REVIEW_PRIORITIES,
    REVIEW_REACTIONS,
    SECURITY_SUB_EVENTS,
    SERVING_SPEEDS,
    SHARE_SETTINGS,
    TOKEN_TYPES,
    TOOL_NAMES,
    cx_app,
    cx_sub,
)

log = logging.getLogger("sim.codex_compliance")

BASE_LABELS = ("organization", "cx_application_name", "cx_subsystem_name")
EVENTS_LABELS = BASE_LABELS + (
    "event_type",
    "sub_event_type",
    "client_id",
    "actor_type",
    "workspace_id",
    "date",
)
ENV_LABELS = BASE_LABELS + (
    "workspace_id",
    "share_settings",
    "machine_id",
    "is_pinned",
    "date",
)
DEVICE_LABELS = BASE_LABELS + (
    "workspace_id",
    "user_id",
    "device_key_protection_class",
    "is_deleted",
    "date",
)
SWEEP_LABELS = BASE_LABELS + ("family", "outcome", "workspace_id", "date")
SOURCE_IP_LABELS = BASE_LABELS + ("user_id", "date", "workspace_id")
GEO_LABELS = BASE_LABELS + (
    "country",
    "action_result",
    "action_privilege",
    "event_type",
    "workspace_id",
    "date",
)
USER_STATE_LABELS = BASE_LABELS + (
    "workspace_id",
    "user_id",
    "role",
    "status",
    "is_scim_managed",
    "date",
)
IDENTITY_LABELS = BASE_LABELS + ("user_id", "email", "name", "workspace_id", "date")
FEED_LAG_LABELS = BASE_LABELS + ("event_type", "workspace_id")
CREDITS_LABELS = BASE_LABELS + ("user_id", "user_email", "workspace_id", "date")
ANALYTICS_TOKEN_LABELS = BASE_LABELS + ("user_id", "user_email", "workspace_id", "date")
ANALYTICS_LINES_LABELS = BASE_LABELS + (
    "user_id",
    "user_email",
    "workspace_id",
    "date",
    "change",
)
MODEL_ANALYTICS_LABELS = BASE_LABELS + (
    "user_id",
    "user_email",
    "workspace_id",
    "date",
    "model",
    "speed",
)
CLIENT_ANALYTICS_LABELS = BASE_LABELS + (
    "user_id",
    "user_email",
    "workspace_id",
    "date",
    "client_id",
)
REVIEW_LABELS = BASE_LABELS + ("workspace_id", "date")
REVIEW_PRIORITY_LABELS = REVIEW_LABELS + ("priority",)
REVIEW_REACTION_LABELS = REVIEW_LABELS + ("reaction",)
TOKENS_TOTAL_LABELS = BASE_LABELS + (
    "user_id",
    "model",
    "token_type",
    "reasoning_effort",
    "client_id",
    "workspace_id",
    "date",
)
SESSION_TOKEN_LABELS = BASE_LABELS + (
    "session_id",
    "user_id",
    "model",
    "token_type",
    "service_tier",
    "reasoning_effort",
    "client_id",
    "workspace_id",
    "date",
)
SESSION_TURNS_LABELS = BASE_LABELS + ("session_id", "workspace_id", "date")
TOOL_CALL_LABELS = BASE_LABELS + (
    "tool_name",
    "tool_type",
    "status",
    "user_id",
    "workspace_id",
    "date",
)
REPO_LABELS = ("session_id", "repository_name")
E2E_LABELS = BASE_LABELS + ("workspace_id",)


class CodexComplianceSim:
    def __init__(self, *, registry: CollectorRegistry) -> None:
        self.registry = registry
        self.organization = default_organization()
        self.cx_app = cx_app()
        self.cx_sub = cx_sub()
        self.backfill_days = max(
            14, _env_int("SIM_CODEX_COMPLIANCE_BACKFILL_DAYS", DEFAULT_BACKFILL_DAYS)
        )
        n_ws = max(1, _env_int("SIM_CODEX_COMPLIANCE_WORKSPACES", DEFAULT_WORKSPACES))
        n_users = max(10, _env_int("SIM_CODEX_COMPLIANCE_USERS", DEFAULT_USERS))
        seed = f"codex-compliance:{self.organization}"
        self.workspaces = build_workspaces(n_ws, seed=seed)
        self.seats = build_seats(
            self.workspaces, users_per_workspace=n_users, seed=seed, domain="coralogix.com"
        )
        self.volume = max(0.05, _env_float("SIM_CODEX_COMPLIANCE_VOLUME", 1.0))
        self._closed_days: set[str] = set()
        self._g = self._build_gauges()
        self._seed_history()

    def _base(self) -> dict[str, str]:
        return metric_base(
            organization=self.organization,
            cx_application_name=self.cx_app,
            cx_subsystem_name=self.cx_sub,
        )

    def _build_gauges(self) -> dict[str, Gauge]:
        def g(name: str, doc: str, labels: tuple[str, ...]) -> Gauge:
            return Gauge(name, doc, labelnames=labels, registry=self.registry)

        gauges: dict[str, Gauge] = {
            "events": g(
                "openai_compliance_events_total",
                "Codex compliance event counters (cumulative per date)",
                EVENTS_LABELS,
            ),
            "environments": g(
                "openai_compliance_codex_environments_total",
                "Codex environment inventory snapshot",
                ENV_LABELS,
            ),
            "devices": g(
                "openai_compliance_remote_control_devices_by_user",
                "Remote control devices by user snapshot",
                DEVICE_LABELS,
            ),
            "sweep": g(
                "openai_compliance_fanout_sweep_users",
                "Fan-out sweep coverage by family",
                SWEEP_LABELS,
            ),
            "source_ips": g(
                "openai_compliance_source_ips_by_user",
                "Distinct source IPs by user per day",
                SOURCE_IP_LABELS,
            ),
            "access_geo": g(
                "openai_compliance_access_geo_total",
                "Access events by country (cumulative per date)",
                GEO_LABELS,
            ),
            "user_state": g(
                "openai_compliance_user_state",
                "Workspace seat roster snapshot",
                USER_STATE_LABELS,
            ),
            "user_identity": g(
                "openai_compliance_user_identity",
                "Directory identity join",
                IDENTITY_LABELS,
            ),
            "feed_lag": g(
                "openai_compliance_log_feed_lag_seconds",
                "Compliance log feed lag (restated each poll)",
                FEED_LAG_LABELS,
            ),
            "credits": g(
                "openai_analytics_codex_credits",
                "Codex Analytics credits",
                CREDITS_LABELS,
            ),
            "cost_usd": g(
                "openai_analytics_codex_cost_usd",
                "Codex Analytics list cost USD",
                CREDITS_LABELS,
            ),
            "uncached_in": g(
                "openai_analytics_codex_uncached_text_input_tokens",
                "Codex Analytics uncached input tokens",
                ANALYTICS_TOKEN_LABELS,
            ),
            "cached_in": g(
                "openai_analytics_codex_cached_text_input_tokens",
                "Codex Analytics cached input tokens",
                ANALYTICS_TOKEN_LABELS,
            ),
            "text_out": g(
                "openai_analytics_codex_text_output_tokens",
                "Codex Analytics output tokens",
                ANALYTICS_TOKEN_LABELS,
            ),
            "threads": g(
                "openai_analytics_codex_threads",
                "Codex Analytics threads",
                ANALYTICS_TOKEN_LABELS,
            ),
            "turns": g(
                "openai_analytics_codex_turns",
                "Codex Analytics turns",
                ANALYTICS_TOKEN_LABELS,
            ),
            "lines": g(
                "openai_analytics_codex_lines",
                "Codex Analytics lines added/removed",
                ANALYTICS_LINES_LABELS,
            ),
            "model_credits": g(
                "openai_analytics_codex_model_credits",
                "Codex Analytics model credits",
                MODEL_ANALYTICS_LABELS,
            ),
            "model_cost": g(
                "openai_analytics_codex_model_cost_usd",
                "Codex Analytics model cost USD",
                MODEL_ANALYTICS_LABELS,
            ),
            "model_uncached": g(
                "openai_analytics_codex_model_uncached_text_input_tokens",
                "Codex Analytics model uncached input",
                MODEL_ANALYTICS_LABELS,
            ),
            "model_cached": g(
                "openai_analytics_codex_model_cached_text_input_tokens",
                "Codex Analytics model cached input",
                MODEL_ANALYTICS_LABELS,
            ),
            "model_out": g(
                "openai_analytics_codex_model_text_output_tokens",
                "Codex Analytics model output",
                MODEL_ANALYTICS_LABELS,
            ),
            "client_credits": g(
                "openai_analytics_codex_client_credits",
                "Codex Analytics client credits (Client usage)",
                CLIENT_ANALYTICS_LABELS,
            ),
            "client_cost": g(
                "openai_analytics_codex_client_cost_usd",
                "Codex Analytics client cost USD",
                CLIENT_ANALYTICS_LABELS,
            ),
            "client_uncached": g(
                "openai_analytics_codex_client_uncached_text_input_tokens",
                "Codex Analytics client uncached input",
                CLIENT_ANALYTICS_LABELS,
            ),
            "client_cached": g(
                "openai_analytics_codex_client_cached_text_input_tokens",
                "Codex Analytics client cached input",
                CLIENT_ANALYTICS_LABELS,
            ),
            "client_out": g(
                "openai_analytics_codex_client_text_output_tokens",
                "Codex Analytics client output",
                CLIENT_ANALYTICS_LABELS,
            ),
            "pr_reviews": g(
                "openai_analytics_codex_pr_reviews",
                "Codex Code Review PR reviews",
                REVIEW_LABELS,
            ),
            "review_comments": g(
                "openai_analytics_codex_review_comments",
                "Codex Code Review comments",
                REVIEW_LABELS,
            ),
            "review_comments_by_priority": g(
                "openai_analytics_codex_review_comments_by_priority",
                "Codex Code Review comments by priority",
                REVIEW_PRIORITY_LABELS,
            ),
            "review_response_pr_reviews": g(
                "openai_analytics_codex_review_response_pr_reviews",
                "Codex Code Review response PR reviews",
                REVIEW_LABELS,
            ),
            "review_response_comments": g(
                "openai_analytics_codex_review_response_comments",
                "Codex Code Review response comments",
                REVIEW_LABELS,
            ),
            "review_replies": g(
                "openai_analytics_codex_review_replies",
                "Codex Code Review replies",
                REVIEW_LABELS,
            ),
            "review_reactions": g(
                "openai_analytics_codex_review_reactions",
                "Codex Code Review reactions",
                REVIEW_LABELS,
            ),
            "review_reactions_by_type": g(
                "openai_analytics_codex_review_reactions_by_type",
                "Codex Code Review reactions by type",
                REVIEW_REACTION_LABELS,
            ),
            "review_comments_engaged": g(
                "openai_analytics_codex_review_comments_engaged",
                "Codex Code Review engaged comments",
                REVIEW_LABELS,
            ),
            "review_comments_replied": g(
                "openai_analytics_codex_review_comments_replied",
                "Codex Code Review replied comments",
                REVIEW_LABELS,
            ),
            "review_comments_reacted": g(
                "openai_analytics_codex_review_comments_reacted",
                "Codex Code Review reacted comments",
                REVIEW_LABELS,
            ),
            "review_comments_reacted_by_type": g(
                "openai_analytics_codex_review_comments_reacted_by_type",
                "Codex Code Review reacted comments by type",
                REVIEW_REACTION_LABELS,
            ),
            "codex_tokens": g(
                "openai_compliance_codex_tokens_total",
                "Compliance Codex token counters",
                TOKENS_TOTAL_LABELS,
            ),
            "session_tokens": g(
                "openai_compliance_codex_session_tokens_total",
                "Compliance Codex session token counters",
                SESSION_TOKEN_LABELS,
            ),
            "session_turns": g(
                "openai_compliance_codex_session_turns_total",
                "Compliance Codex session turn counters",
                SESSION_TURNS_LABELS,
            ),
            "tool_calls": g(
                "openai_compliance_codex_tool_calls_total",
                "Compliance Codex tool call counters",
                TOOL_CALL_LABELS,
            ),
            "repo_info": g(
                "codex_session_repo_info",
                "Repo-tracker session repository info",
                REPO_LABELS,
            ),
            "repo_commits": g(
                "codex_session_commits",
                "Repo-tracker commits per session",
                REPO_LABELS,
            ),
            "repo_prs": g(
                "codex_session_prs_opened",
                "Repo-tracker PRs opened per session",
                REPO_LABELS,
            ),
            "repo_added": g(
                "codex_session_lines_added",
                "Repo-tracker lines added per session",
                REPO_LABELS,
            ),
            "repo_removed": g(
                "codex_session_lines_removed",
                "Repo-tracker lines removed per session",
                REPO_LABELS,
            ),
            "e2e_count": g(
                "codex_turn_e2e_duration_ms_count",
                "Codex CLI e2e duration count (shell detection)",
                E2E_LABELS,
            ),
        }
        return gauges

    def _seed_history(self) -> None:
        today = date_str()
        for day in backfill_dates(self.backfill_days):
            force = day == today
            self._materialize_day(day, force=force)
            if day != today:
                self._closed_days.add(day)
        self._emit_feed_lag()

    def emit_cycle(self, *, now: datetime | None = None) -> None:
        day = date_str(now.astimezone(timezone.utc) if now else None)
        self._materialize_day(day, force=True)
        self._emit_feed_lag()

    def _emit_feed_lag(self) -> None:
        base = self._base()
        ws = self.workspaces[0].workspace_id
        for event_type in FEED_EVENT_TYPES:
            self._g["feed_lag"].labels(
                **base, event_type=event_type, workspace_id=ws
            ).set(random.uniform(30.0, 180.0))

    def _day_scale(self, day: str, *, vol: float) -> float:
        """Weekend dampen + recent-week climb so Cost 'climbing' insight can fire."""
        weekend = datetime.fromisoformat(day).weekday() >= 5
        age = (utc_today().date() - datetime.fromisoformat(day).date()).days
        trend = 1.35 if 1 <= age <= 7 else (1.0 if 8 <= age <= 14 else 0.85)
        return (0.55 if weekend else 1.0) * vol * trend

    def _materialize_day(self, day: str, *, force: bool = False) -> None:
        if day in self._closed_days and not force:
            return
        rng = day_rng("codex-compliance", day)
        base = self._base()
        day_scale = self._day_scale(day, vol=self.volume)

        for seat in self.seats:
            self._g["user_state"].labels(
                **base,
                workspace_id=seat.workspace_id,
                user_id=seat.user_id,
                role=seat.role,
                status=seat.status,
                is_scim_managed=seat.is_scim_managed,
                date=day,
            ).set(1)
            self._g["user_identity"].labels(
                **base,
                user_id=seat.user_id,
                email=seat.email,
                name=seat.name,
                workspace_id=seat.workspace_id,
                date=day,
            ).set(1)

        for ws in self.workspaces:
            for share in SHARE_SETTINGS:
                for machine in MACHINE_IDS[:2]:
                    pinned = "true" if share == "private" and rng.random() < 0.3 else "false"
                    count = {
                        "private": rng.randint(2, 6),
                        "workspace": rng.randint(1, 3),
                        "organization": rng.randint(0, 2),
                    }[share]
                    if count <= 0:
                        continue
                    self._g["environments"].labels(
                        **base,
                        workspace_id=ws.workspace_id,
                        share_settings=share,
                        machine_id=machine,
                        is_pinned=pinned,
                        date=day,
                    ).set(count)

        for ws in self.workspaces:
            ws_seats = [s for s in self.seats if s.workspace_id == ws.workspace_id]
            for seat in ws_seats:
                if rng.random() > 0.55:
                    continue
                klass = rng.choice(DEVICE_KEY_CLASSES)
                deleted = "true" if rng.random() < 0.15 else "false"
                self._g["devices"].labels(
                    **base,
                    workspace_id=ws.workspace_id,
                    user_id=seat.user_id,
                    device_key_protection_class=klass,
                    is_deleted=deleted,
                    date=day,
                ).set(rng.randint(1, 3))
            self._g["sweep"].labels(
                **base,
                family="remote_control_clients",
                outcome="ok",
                workspace_id=ws.workspace_id,
                date=day,
            ).set(len(ws_seats))

        for seat in self.seats:
            if rng.random() > 0.7:
                continue
            ips = 1 if rng.random() > 0.35 else rng.randint(2, 5)
            self._g["source_ips"].labels(
                **base,
                user_id=seat.user_id,
                date=day,
                workspace_id=seat.workspace_id,
            ).set(ips)

        for country in GEO_COUNTRIES:
            for result in ACTION_RESULTS:
                if result != "SUCCESS" and rng.random() > 0.4:
                    continue
                val = int(rng.randint(2, 40) * day_scale)
                if val <= 0:
                    continue
                self._g["access_geo"].labels(
                    **base,
                    country=country,
                    action_result=result,
                    action_privilege="STANDARD_USER",
                    event_type="AUDIT_LOG",
                    workspace_id=self.workspaces[0].workspace_id,
                    date=day,
                ).set(val)

        for ws in self.workspaces:
            for client in CODEX_CLIENT_IDS:
                for sub in CODEX_LOG_SUB_EVENTS:
                    val = int(rng.randint(5, 80) * day_scale)
                    self._g["events"].labels(
                        **base,
                        event_type="CODEX_LOG",
                        sub_event_type=sub,
                        client_id=client,
                        actor_type="ACCOUNT_USER",
                        workspace_id=ws.workspace_id,
                        date=day,
                    ).set(val)
            self._g["events"].labels(
                **base,
                event_type="CODEX_SECURITY_LOG",
                sub_event_type=SECURITY_SUB_EVENTS[0],
                client_id="CODEX_WEB",
                actor_type="ACCOUNT_USER",
                workspace_id=ws.workspace_id,
                date=day,
            ).set(rng.randint(1, 4))
            self._emit_reviews(ws.workspace_id, day, base, rng, day_scale)
            self._g["e2e_count"].labels(**base, workspace_id=ws.workspace_id).set(
                int(rng.randint(50, 400) * day_scale)
            )

        self._emit_analytics_and_sessions(day, base, rng, day_scale)

        if day != date_str():
            self._closed_days.add(day)
        log.debug("codex compliance/dashboard materialized day=%s seats=%d", day, len(self.seats))

    def _emit_reviews(
        self,
        workspace_id: str,
        day: str,
        base: dict[str, str],
        rng: random.Random,
        day_scale: float,
    ) -> None:
        reviews = max(12, int(rng.randint(15, 40) * day_scale))
        comments = max(reviews, int(reviews * rng.uniform(1.2, 2.5)))
        engaged = int(comments * rng.uniform(0.55, 0.85))
        common = {**base, "workspace_id": workspace_id, "date": day}
        self._g["pr_reviews"].labels(**common).set(reviews)
        self._g["review_comments"].labels(**common).set(comments)
        self._g["review_response_pr_reviews"].labels(**common).set(int(reviews * 0.7))
        self._g["review_response_comments"].labels(**common).set(int(comments * 0.6))
        self._g["review_replies"].labels(**common).set(int(comments * 0.35))
        self._g["review_reactions"].labels(**common).set(int(comments * 0.25))
        self._g["review_comments_engaged"].labels(**common).set(engaged)
        self._g["review_comments_replied"].labels(**common).set(int(engaged * 0.7))
        self._g["review_comments_reacted"].labels(**common).set(int(engaged * 0.4))
        for priority in REVIEW_PRIORITIES:
            self._g["review_comments_by_priority"].labels(
                **common, priority=priority
            ).set(max(1, int(comments * {"p0": 0.15, "p1": 0.35, "p2": 0.5}[priority])))
        for reaction in REVIEW_REACTIONS:
            share = {"upvote": 0.55, "downvote": 0.25, "other": 0.2}[reaction]
            self._g["review_reactions_by_type"].labels(**common, reaction=reaction).set(
                max(1, int(comments * 0.25 * share))
            )
            self._g["review_comments_reacted_by_type"].labels(
                **common, reaction=reaction
            ).set(max(1, int(engaged * 0.4 * share)))

    def _emit_analytics_and_sessions(
        self,
        day: str,
        base: dict[str, str],
        rng: random.Random,
        day_scale: float,
    ) -> None:
        active = [s for s in self.seats if s.status == "active"]
        # Rank seats so top few drive credits (concentration) and one stays idle-ish.
        ranked = sorted(active, key=lambda s: s.user_id)
        for idx, seat in enumerate(ranked):
            if idx == len(ranked) - 1:
                # Unused-seat insight: < 500 credits over recent window.
                credits = rng.uniform(40.0, 180.0)
            elif idx < 3:
                credits = rng.uniform(900.0, 2200.0) * day_scale
            else:
                credits = rng.uniform(120.0, 500.0) * day_scale

            # High cache hit rate so "prompt cache barely used" stays dark by default.
            uncached = int(rng.randint(30_000, 200_000) * day_scale)
            cached = int(uncached * rng.uniform(2.5, 4.0))
            out = int(rng.randint(10_000, 160_000) * day_scale)
            cost = credits * 0.04
            threads = rng.randint(3, 18) if idx < 3 else rng.randint(1, 8)
            turns = max(threads * rng.randint(8, 20), threads + 1)

            common = {
                **base,
                "user_id": seat.user_id,
                "user_email": seat.email,
                "workspace_id": seat.workspace_id,
                "date": day,
            }
            self._g["credits"].labels(**common).set(credits)
            self._g["cost_usd"].labels(**common).set(cost)
            self._g["uncached_in"].labels(**common).set(uncached)
            self._g["cached_in"].labels(**common).set(cached)
            self._g["text_out"].labels(**common).set(out)
            self._g["threads"].labels(**common).set(threads)
            self._g["turns"].labels(**common).set(turns)
            self._g["lines"].labels(**common, change="added").set(rng.randint(40, 900))
            self._g["lines"].labels(**common, change="removed").set(rng.randint(10, 250))

            # Client usage — split per user across clients (must carry client_id).
            client_weights = [rng.uniform(0.15, 0.4) for _ in CODEX_CLIENT_IDS]
            wsum = sum(client_weights) or 1.0
            client_weights = [w / wsum for w in client_weights]
            for client_id, w in zip(CODEX_CLIENT_IDS, client_weights):
                clabels = {**common, "client_id": client_id}
                self._g["client_credits"].labels(**clabels).set(credits * w)
                self._g["client_cost"].labels(**clabels).set(cost * w)
                self._g["client_uncached"].labels(**clabels).set(uncached * w)
                self._g["client_cached"].labels(**clabels).set(cached * w)
                self._g["client_out"].labels(**clabels).set(out * w)

            # Model breakdown — bias gpt-5-codex + some fast speed for insights.
            model_picks = ["gpt-5-codex", rng.choice(CODEX_MODELS[1:])]
            for mi, model in enumerate(model_picks):
                speed = "fast" if (idx < 3 and mi == 0 and rng.random() < 0.55) else "standard"
                if speed not in SERVING_SPEEDS:
                    speed = "standard"
                share = 0.65 if model == "gpt-5-codex" else 0.35
                mlabels = {**common, "model": model, "speed": speed}
                self._g["model_credits"].labels(**mlabels).set(credits * share)
                self._g["model_cost"].labels(**mlabels).set(cost * share)
                self._g["model_uncached"].labels(**mlabels).set(uncached * share)
                self._g["model_cached"].labels(**mlabels).set(cached * share)
                self._g["model_out"].labels(**mlabels).set(out * share)

                client_id = CODEX_CLIENT_IDS[idx % len(CODEX_CLIENT_IDS)]
                effort = rng.choice(REASONING_EFFORTS)
                for token_type in TOKEN_TYPES:
                    if token_type == "input":
                        val = int(uncached * share)
                    elif token_type == "cached_input":
                        val = int(cached * share)
                    elif token_type == "output":
                        val = int(out * share)
                    else:  # reasoning_output — keep under 20% of output by default
                        val = int(out * share * rng.uniform(0.05, 0.15))
                    self._g["codex_tokens"].labels(
                        **base,
                        user_id=seat.user_id,
                        model=model,
                        token_type=token_type,
                        reasoning_effort=effort,
                        client_id=client_id,
                        workspace_id=seat.workspace_id,
                        date=day,
                    ).set(max(1, val))

                session_id = f"sess-{day}-{seat.user_id[-8:]}-{model[-6:]}-{mi}"
                for token_type in TOKEN_TYPES:
                    if token_type == "input":
                        val = int(uncached * share * 0.35)
                    elif token_type == "cached_input":
                        val = int(cached * share * 0.35)
                    elif token_type == "output":
                        val = int(out * share * 0.35)
                    else:
                        val = int(out * share * 0.05)
                    self._g["session_tokens"].labels(
                        **base,
                        session_id=session_id,
                        user_id=seat.user_id,
                        model=model,
                        token_type=token_type,
                        service_tier="default",
                        reasoning_effort=effort,
                        client_id=client_id,
                        workspace_id=seat.workspace_id,
                        date=day,
                    ).set(max(1, val))
                self._g["session_turns"].labels(
                    **base,
                    session_id=session_id,
                    workspace_id=seat.workspace_id,
                    date=day,
                ).set(rng.randint(5, 40))

                repo = rng.choice(
                    ("coralogix/otel-ai-agent-sim", "coralogix/frontend", "acme/payments")
                )
                self._g["repo_info"].labels(session_id=session_id, repository_name=repo).set(1)
                self._g["repo_commits"].labels(session_id=session_id, repository_name=repo).set(
                    rng.randint(1, 6)
                )
                self._g["repo_prs"].labels(session_id=session_id, repository_name=repo).set(
                    rng.randint(0, 2)
                )
                self._g["repo_added"].labels(session_id=session_id, repository_name=repo).set(
                    rng.randint(40, 600)
                )
                self._g["repo_removed"].labels(session_id=session_id, repository_name=repo).set(
                    rng.randint(5, 120)
                )

            for tool_name in rng.sample(list(TOOL_NAMES), k=3):
                tool_type = "mcp" if tool_name.startswith("mcp.") else "builtin"
                for status, weight in (("completed", 0.9), ("failed", 0.1)):
                    self._g["tool_calls"].labels(
                        **base,
                        tool_name=tool_name,
                        tool_type=tool_type,
                        status=status,
                        user_id=seat.user_id,
                        workspace_id=seat.workspace_id,
                        date=day,
                    ).set(max(1, int(rng.randint(2, 30) * day_scale * weight)))
