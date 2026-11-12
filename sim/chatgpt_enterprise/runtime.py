"""Emit ChatGPT Dashboard + Compliance posture gauges.

Covers both Real Query Engine maps under ``docs/query-engine-maps/chatgpt-*.md``.
Ledger rows are hourly (``date``/``hour``); inventory is daily snapshots; counters
are cumulative running day totals. Detection helpers emit admin-key gauges so the
AISPM shell can mark API Platform Compliance.
"""

from __future__ import annotations

import logging
import random
from datetime import datetime, timezone

from prometheus_client import CollectorRegistry, Gauge

from sim.chatgpt_enterprise.constants import (
    APPROVAL_MODES,
    AUTH_POLICIES,
    CLIENTS,
    CONNECTOR_ACTIONS,
    CONNECTORS,
    DEFAULT_BACKFILL_DAYS,
    DEFAULT_USERS,
    DEFAULT_WORKSPACES,
    DEVICE_KEY_CLASSES,
    DISCOVERABILITIES,
    DURATION_BUCKETS,
    FEED_EVENT_TYPES,
    INSTALL_POLICIES,
    LAST_SEEN_BUCKETS,
    MEMORY_BUCKETS,
    MESSAGE_CLIENT_IDS,
    MODELS,
    PLATFORMS,
    PLUGIN_STATUSES,
    PRODUCTS,
    REASONING,
    SCHEDULE_FREQS,
    SERVICE_TIERS,
    SURFACES,
    SWEEP_FAMILIES,
    TOKEN_TYPES,
    VISIBILITIES,
    cx_app,
    cx_sub,
)
from sim.common.env import _env_float, _env_int
from sim.common.openai_compliance import (
    backfill_dates,
    build_seats,
    build_workspaces,
    date_str,
    day_rng,
    default_organization,
    hours_for_day,
    metric_base,
    utc_today,
)

log = logging.getLogger("sim.chatgpt_enterprise")

BASE = ("organization", "cx_application_name", "cx_subsystem_name")
COST_COMMON = BASE + ("date", "hour", "product", "model")
COST_CREDITS = COST_COMMON + ("surface", "client", "reasoning", "sku", "service_tier")
COST_USD = COST_COMMON + ("surface", "client", "reasoning", "sku", "service_tier")
COST_TOKENS = COST_COMMON + ("service_tier", "token_type")
EVENTS = BASE + ("event_type", "client_id", "actor_type", "workspace_id", "date")
CONNECTOR_CALLS = BASE + (
    "workspace_id",
    "connector_name",
    "action_name",
    "call_phase",
    "status",
    "requires_elicitation",
    "date",
)
USER_STATE = BASE + (
    "workspace_id",
    "user_id",
    "role",
    "status",
    "is_scim_managed",
    "date",
)
INVITES = BASE + ("workspace_id", "role", "date")
IDENTITY = BASE + ("user_id", "email", "name", "workspace_id", "date")
CREDITS_FALLBACK = BASE + ("user_id", "user_email", "workspace_id", "date")
GPTS = BASE + (
    "workspace_id",
    "visibility",
    "can_read",
    "can_view_config",
    "can_write",
    "date",
)
SHARED_USERS = BASE + ("workspace_id", "role", "status", "can_write", "date")
GPT_TOOLS = BASE + ("workspace_id", "tool_type", "auth_type", "date")
PLUGINS = BASE + (
    "workspace_id",
    "status",
    "installation_policy",
    "discoverability",
    "authentication_policy",
    "date",
)
AGENTS = BASE + ("workspace_id", "status", "date")
SKILLS = BASE + ("workspace_id", "original_product_surface", "date")
MCP = BASE + ("workspace_id", "plugin_id", "url_host", "date")
AGENT_CONN = BASE + ("workspace_id", "connector_id", "user_approvals_mode", "date")
USERS_TOTAL = BASE + ("workspace_id", "date")
FEED_LAG = BASE + ("event_type", "workspace_id")
SWEEP = BASE + ("family", "outcome", "workspace_id", "date")
DEVICES = BASE + (
    "workspace_id",
    "user_id",
    "device_key_protection_class",
    "is_deleted",
    "date",
)
CLIENTS_TOTAL = BASE + (
    "workspace_id",
    "device_type",
    "platform",
    "os_version",
    "last_seen_country",
    "last_seen_bucket",
    "device_key_algorithm",
    "device_key_protection_class",
    "is_deleted",
    "date",
)
ENVIRONMENTS = BASE + (
    "workspace_id",
    "kind",
    "os",
    "arch",
    "client_name",
    "originator",
    "terminal",
    "connection_status",
    "busy",
    "last_seen_bucket",
    "date",
)
AUTOMATIONS = BASE + (
    "workspace_id",
    "user_id",
    "is_enabled",
    "schedule_freq",
    "date",
)
LIBRARY_FILES = BASE + ("workspace_id", "user_id", "is_trashed", "date")
LIBRARY_BYTES = BASE + ("workspace_id", "user_id", "date")
RECORDINGS = BASE + ("workspace_id", "user_id", "duration_bucket", "date")
MEMORIES = BASE + ("workspace_id", "user_id", "context_count_bucket", "date")
ADMIN_KEY = BASE + ("key_id", "name", "created_at", "last_used_at")
PROJECT_KEY = BASE + (
    "project_id",
    "key_id",
    "name",
    "created_at",
    "last_used_at",
    "owner",
)


class ChatGPTEnterpriseSim:
    def __init__(self, *, registry: CollectorRegistry) -> None:
        self.registry = registry
        self.organization = default_organization()
        self.cx_app = cx_app()
        self.cx_sub = cx_sub()
        self.backfill_days = max(
            14, _env_int("SIM_CHATGPT_ENTERPRISE_BACKFILL_DAYS", DEFAULT_BACKFILL_DAYS)
        )
        n_ws = max(1, _env_int("SIM_CHATGPT_ENTERPRISE_WORKSPACES", DEFAULT_WORKSPACES))
        n_users = max(8, _env_int("SIM_CHATGPT_ENTERPRISE_USERS", DEFAULT_USERS))
        seed = f"chatgpt-enterprise:{self.organization}"
        self.workspaces = build_workspaces(n_ws, seed=seed)
        self.seats = build_seats(
            self.workspaces, users_per_workspace=n_users, seed=seed, domain="coralogix.com"
        )
        self.volume = max(0.05, _env_float("SIM_CHATGPT_ENTERPRISE_VOLUME", 1.0))
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

        return {
            "costs_credits": g(
                "openai_compliance_costs_credits",
                "ChatGPT Enterprise costs ledger credits (hourly)",
                COST_CREDITS,
            ),
            "costs_usd": g(
                "openai_compliance_costs_usd",
                "ChatGPT Enterprise costs ledger USD (hourly)",
                COST_USD,
            ),
            "costs_tokens": g(
                "openai_compliance_costs_tokens",
                "ChatGPT Enterprise costs ledger tokens (hourly)",
                COST_TOKENS,
            ),
            "events": g(
                "openai_compliance_events_total",
                "Compliance event counters (CONVERSATION_MESSAGE)",
                EVENTS,
            ),
            "connector_calls": g(
                "openai_compliance_connector_calls_total",
                "Connector call counters",
                CONNECTOR_CALLS,
            ),
            "user_state": g(
                "openai_compliance_user_state",
                "Seat roster snapshot",
                USER_STATE,
            ),
            "invites": g(
                "openai_compliance_invites_total",
                "Pending invites by role",
                INVITES,
            ),
            "user_identity": g(
                "openai_compliance_user_identity",
                "Directory identity join",
                IDENTITY,
            ),
            "analytics_credits": g(
                "openai_analytics_codex_credits",
                "Identity email fallback (Codex Analytics)",
                CREDITS_FALLBACK,
            ),
            "gpts": g("openai_compliance_gpts_total", "Shared GPTs snapshot", GPTS),
            "projects": g(
                "openai_compliance_projects_total", "Projects snapshot", GPTS
            ),
            "gpt_shared": g(
                "openai_compliance_gpt_shared_users_total",
                "GPT shared users",
                SHARED_USERS,
            ),
            "project_shared": g(
                "openai_compliance_project_shared_users_total",
                "Project shared users",
                SHARED_USERS,
            ),
            "gpt_tools": g(
                "openai_compliance_gpt_tools_total", "GPT tools snapshot", GPT_TOOLS
            ),
            "plugins": g("openai_compliance_plugins_total", "Plugins snapshot", PLUGINS),
            "agents": g("openai_compliance_agents_total", "Custom agents snapshot", AGENTS),
            "skills": g("openai_compliance_skills_total", "Skills snapshot", SKILLS),
            "mcp": g(
                "openai_compliance_plugin_mcp_servers_total",
                "Plugin MCP server hosts",
                MCP,
            ),
            "agent_connectors": g(
                "openai_compliance_agent_connectors_total",
                "Agent connector grants",
                AGENT_CONN,
            ),
            "users_total": g(
                "openai_compliance_users_total",
                "Has-data probe users gauge",
                USERS_TOTAL,
            ),
            "feed_lag": g(
                "openai_compliance_log_feed_lag_seconds",
                "Compliance feed lag",
                FEED_LAG,
            ),
            "sweep": g(
                "openai_compliance_fanout_sweep_users",
                "Fan-out sweep by family",
                SWEEP,
            ),
            "devices": g(
                "openai_compliance_remote_control_devices_by_user",
                "Remote control devices by user",
                DEVICES,
            ),
            "rc_clients": g(
                "openai_compliance_remote_control_clients_total",
                "Remote control fleet clients",
                CLIENTS_TOTAL,
            ),
            "rc_envs": g(
                "openai_compliance_remote_control_environments_total",
                "Remote control environments",
                ENVIRONMENTS,
            ),
            "automations": g(
                "openai_compliance_automations_total",
                "Automations by user",
                AUTOMATIONS,
            ),
            "library_files": g(
                "openai_compliance_library_files_by_user",
                "Library files by user",
                LIBRARY_FILES,
            ),
            "library_bytes": g(
                "openai_compliance_library_bytes_by_user",
                "Library bytes by user",
                LIBRARY_BYTES,
            ),
            "recordings": g(
                "openai_compliance_recordings_total",
                "Recordings by user/duration",
                RECORDINGS,
            ),
            "memories": g(
                "openai_compliance_memories_users_total",
                "Memories users join series",
                MEMORIES,
            ),
            "admin_key": g(
                "openai_administration_admin_key_info",
                "AISPM detection: admin API keys",
                ADMIN_KEY,
            ),
            "project_key": g(
                "openai_administration_project_api_key_info",
                "AISPM detection: project API keys",
                PROJECT_KEY,
            ),
        }

    def _seed_history(self) -> None:
        today = date_str()
        for day in backfill_dates(self.backfill_days):
            self._materialize_day(day, force=(day == today))
            if day != today:
                self._closed_days.add(day)
        self._emit_feed_lag()
        self._emit_detection_keys()

    def emit_cycle(self, *, now: datetime | None = None) -> None:
        day = date_str(now.astimezone(timezone.utc) if now else None)
        self._materialize_day(day, force=True)
        self._emit_feed_lag()
        self._emit_detection_keys()

    def _emit_feed_lag(self) -> None:
        base = self._base()
        ws = self.workspaces[0].workspace_id
        for event_type in FEED_EVENT_TYPES:
            self._g["feed_lag"].labels(
                **base, event_type=event_type, workspace_id=ws
            ).set(random.uniform(20.0, 120.0))

    def _emit_detection_keys(self) -> None:
        """Light AISPM shell detection for ChatGPT Compliance route."""
        base = self._base()
        self._g["admin_key"].labels(
            **base,
            key_id="key-sim-admin-01",
            name="sim-admin",
            created_at="2026-01-01T00:00:00Z",
            last_used_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        ).set(1)
        self._g["project_key"].labels(
            **base,
            project_id="proj-sim-01",
            key_id="key-sim-proj-01",
            name="sim-project",
            created_at="2026-01-01T00:00:00Z",
            last_used_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            owner=self.seats[0].email,
        ).set(1)

    def _materialize_day(self, day: str, *, force: bool = False) -> None:
        if day in self._closed_days and not force:
            return
        rng = day_rng("chatgpt-enterprise", day)
        base = self._base()
        today = date_str()
        weekend = datetime.fromisoformat(day).weekday() >= 5
        # Ramp recent week ~1.35× prior week so "Credit use is climbing" can fire.
        age = (utc_today().date() - datetime.fromisoformat(day).date()).days
        trend = 1.35 if 1 <= age <= 7 else (1.0 if 8 <= age <= 14 else 0.85)
        day_scale = (0.6 if weekend else 1.0) * self.volume * trend

        self._emit_roster(day, base)
        self._emit_ledger(day, today, base, rng, day_scale)
        self._emit_messages_and_connectors(day, base, rng, day_scale)
        self._emit_inventory(day, base, rng)
        self._emit_posture(day, base, rng)

        if day != today:
            self._closed_days.add(day)

    def _emit_roster(self, day: str, base: dict[str, str]) -> None:
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
            # Email fallback used by both ChatGPT pages.
            self._g["analytics_credits"].labels(
                **base,
                user_id=seat.user_id,
                user_email=seat.email,
                workspace_id=seat.workspace_id,
                date=day,
            ).set(1.0)
        for ws in self.workspaces:
            for role in ("account-owner", "account-admin", "standard-user"):
                self._g["invites"].labels(
                    **base, workspace_id=ws.workspace_id, role=role, date=day
                ).set(2 if role == "standard-user" else 1)
            self._g["users_total"].labels(
                **base, workspace_id=ws.workspace_id, date=day
            ).set(len([s for s in self.seats if s.workspace_id == ws.workspace_id]))

    def _emit_ledger(
        self,
        day: str,
        today: str,
        base: dict[str, str],
        rng: random.Random,
        day_scale: float,
    ) -> None:
        for hour in hours_for_day(day, today=today):
            for product in PRODUCTS:
                for model in MODELS:
                    if rng.random() > 0.55:
                        continue
                    surface = rng.choice(SURFACES)
                    client = rng.choice(CLIENTS)
                    reasoning = rng.choice(REASONING)
                    tier = rng.choice(SERVICE_TIERS)
                    credits = rng.uniform(0.5, 18.0) * day_scale
                    usd = credits * rng.uniform(0.008, 0.03)
                    sku = f"{model} - Input"
                    common = {
                        **base,
                        "date": day,
                        "hour": str(hour),
                        "product": product,
                        "model": model,
                        "surface": surface,
                        "client": client,
                        "reasoning": reasoning,
                        "sku": sku,
                        "service_tier": tier,
                    }
                    self._g["costs_credits"].labels(**common).set(credits)
                    self._g["costs_usd"].labels(**common).set(usd)
                    for token_type in TOKEN_TYPES:
                        tokens = int(rng.randint(1_000, 80_000) * day_scale)
                        self._g["costs_tokens"].labels(
                            **base,
                            date=day,
                            hour=str(hour),
                            product=product,
                            model=model,
                            service_tier=tier,
                            token_type=token_type,
                        ).set(tokens)

    def _emit_messages_and_connectors(
        self,
        day: str,
        base: dict[str, str],
        rng: random.Random,
        day_scale: float,
    ) -> None:
        for ws in self.workspaces:
            for client_id in MESSAGE_CLIENT_IDS:
                # Heavy concentration: leave room for one power user in raw logs;
                # metric side is workspace-wide.
                val = int(rng.randint(40, 400) * day_scale)
                if client_id == "":
                    val = int(val * 0.2)
                self._g["events"].labels(
                    **base,
                    event_type="CONVERSATION_MESSAGE",
                    client_id=client_id,
                    actor_type="ACCOUNT_USER",
                    workspace_id=ws.workspace_id,
                    date=day,
                ).set(max(1, val))

            for connector in CONNECTORS:
                for action in CONNECTOR_ACTIONS[:2]:
                    requested = int(rng.randint(5, 60) * day_scale)
                    self._g["connector_calls"].labels(
                        **base,
                        workspace_id=ws.workspace_id,
                        connector_name=connector,
                        action_name=action,
                        call_phase="requested",
                        status="",
                        requires_elicitation="false" if rng.random() < 0.35 else "true",
                        date=day,
                    ).set(requested)
                    failed = max(1, int(requested * 0.08))
                    completed_ok = max(1, requested - failed)
                    self._g["connector_calls"].labels(
                        **base,
                        workspace_id=ws.workspace_id,
                        connector_name=connector,
                        action_name=action,
                        call_phase="completed",
                        status="completed",
                        requires_elicitation="",
                        date=day,
                    ).set(completed_ok)
                    self._g["connector_calls"].labels(
                        **base,
                        workspace_id=ws.workspace_id,
                        connector_name=connector,
                        action_name=action,
                        call_phase="completed",
                        status="failed",
                        requires_elicitation="",
                        date=day,
                    ).set(failed)

            # One stale connector for "went quiet" insight (old date kept via history).
            if day == backfill_dates(self.backfill_days)[0]:
                self._g["connector_calls"].labels(
                    **base,
                    workspace_id=ws.workspace_id,
                    connector_name="legacy_dropbox",
                    action_name="search",
                    call_phase="requested",
                    status="",
                    requires_elicitation="true",
                    date=day,
                ).set(3)

    def _emit_inventory(self, day: str, base: dict[str, str], rng: random.Random) -> None:
        for ws in self.workspaces:
            wid = ws.workspace_id
            for vis in VISIBILITIES[:3]:
                self._g["gpts"].labels(
                    **base,
                    workspace_id=wid,
                    visibility=vis,
                    can_read="true",
                    can_view_config="false" if vis == "invite-only" else "true",
                    can_write="false",
                    date=day,
                ).set(rng.randint(1, 8))
                self._g["projects"].labels(
                    **base,
                    workspace_id=wid,
                    visibility=vis,
                    can_read="true",
                    can_view_config="true",
                    can_write="false",
                    date=day,
                ).set(rng.randint(1, 5))
            for role in ("standard-user", "account-admin"):
                self._g["gpt_shared"].labels(
                    **base,
                    workspace_id=wid,
                    role=role,
                    status="active",
                    can_write="false",
                    date=day,
                ).set(rng.randint(2, 12))
                self._g["project_shared"].labels(
                    **base,
                    workspace_id=wid,
                    role=role,
                    status="active",
                    can_write="true",
                    date=day,
                ).set(rng.randint(1, 6))
            for tool_type, auth_type in (("browser", "oauth"), ("code_interpreter", "none"), ("actions", "api_key")):
                self._g["gpt_tools"].labels(
                    **base,
                    workspace_id=wid,
                    tool_type=tool_type,
                    auth_type=auth_type,
                    date=day,
                ).set(rng.randint(1, 4))
            for status in PLUGIN_STATUSES:
                self._g["plugins"].labels(
                    **base,
                    workspace_id=wid,
                    status=status,
                    installation_policy=rng.choice(INSTALL_POLICIES),
                    discoverability=rng.choice(DISCOVERABILITIES),
                    authentication_policy=rng.choice(AUTH_POLICIES),
                    date=day,
                ).set(rng.randint(1, 5) if status == "ENABLED" else rng.randint(0, 2))
            for status in ("active", "draft", "archived"):
                self._g["agents"].labels(
                    **base, workspace_id=wid, status=status, date=day
                ).set(rng.randint(1, 6))
            for surface in ("chatgpt", "api", "codex"):
                self._g["skills"].labels(
                    **base,
                    workspace_id=wid,
                    original_product_surface=surface,
                    date=day,
                ).set(rng.randint(1, 4))
            for i, host in enumerate(("mcp.github.com", "mcp.slack.com", "mcp.notion.so")):
                self._g["mcp"].labels(
                    **base,
                    workspace_id=wid,
                    plugin_id=f"plugin-{i+1}",
                    url_host=host,
                    date=day,
                ).set(1)
            for connector_id, mode in zip(CONNECTORS[:4], APPROVAL_MODES + ("always",)):
                self._g["agent_connectors"].labels(
                    **base,
                    workspace_id=wid,
                    connector_id=connector_id,
                    user_approvals_mode=mode,
                    date=day,
                ).set(rng.randint(1, 4))

    def _emit_posture(self, day: str, base: dict[str, str], rng: random.Random) -> None:
        for ws in self.workspaces:
            wid = ws.workspace_id
            ws_seats = [s for s in self.seats if s.workspace_id == wid]
            for family in SWEEP_FAMILIES:
                self._g["sweep"].labels(
                    **base,
                    family=family,
                    outcome="ok",
                    workspace_id=wid,
                    date=day,
                ).set(len(ws_seats))

            for seat in ws_seats:
                if rng.random() < 0.5:
                    klass = rng.choice(DEVICE_KEY_CLASSES)
                    # Ensure software/unknown exist for posture insight.
                    if seat.role == "account-owner":
                        klass = "software"
                    self._g["devices"].labels(
                        **base,
                        workspace_id=wid,
                        user_id=seat.user_id,
                        device_key_protection_class=klass,
                        is_deleted="false",
                        date=day,
                    ).set(rng.randint(1, 3))
                # Automations — enable on some inactive seats for orphan insight.
                enabled = "true" if seat.status == "inactive" or rng.random() < 0.4 else "false"
                freq = "daily" if seat.status == "inactive" else rng.choice(SCHEDULE_FREQS)
                self._g["automations"].labels(
                    **base,
                    workspace_id=wid,
                    user_id=seat.user_id,
                    is_enabled=enabled,
                    schedule_freq=freq,
                    date=day,
                ).set(rng.randint(1, 4))
                self._g["library_files"].labels(
                    **base,
                    workspace_id=wid,
                    user_id=seat.user_id,
                    is_trashed="false",
                    date=day,
                ).set(rng.randint(2, 40))
                self._g["library_files"].labels(
                    **base,
                    workspace_id=wid,
                    user_id=seat.user_id,
                    is_trashed="true",
                    date=day,
                ).set(rng.randint(0, 8))
                self._g["library_bytes"].labels(
                    **base,
                    workspace_id=wid,
                    user_id=seat.user_id,
                    date=day,
                ).set(rng.randint(50_000, 5_000_000))
                if rng.random() < 0.6:
                    self._g["recordings"].labels(
                        **base,
                        workspace_id=wid,
                        user_id=seat.user_id,
                        duration_bucket=rng.choice(DURATION_BUCKETS),
                        date=day,
                    ).set(rng.randint(1, 5))
                if rng.random() < 0.45:
                    self._g["memories"].labels(
                        **base,
                        workspace_id=wid,
                        user_id=seat.user_id,
                        context_count_bucket=rng.choice(MEMORY_BUCKETS),
                        date=day,
                    ).set(1)

            for platform in PLATFORMS[:4]:
                self._g["rc_clients"].labels(
                    **base,
                    workspace_id=wid,
                    device_type="desktop" if platform in ("macos", "windows", "linux") else "mobile",
                    platform=platform,
                    os_version=rng.choice(("14.0", "15.1", "11")),
                    last_seen_country=rng.choice(("US", "IL", "GB")),
                    last_seen_bucket=rng.choice(LAST_SEEN_BUCKETS[:3]),
                    device_key_algorithm="ecdsa_p256_sha256",
                    device_key_protection_class=rng.choice(DEVICE_KEY_CLASSES[:2]),
                    is_deleted="false",
                    date=day,
                ).set(rng.randint(1, 8))
            self._g["rc_envs"].labels(
                **base,
                workspace_id=wid,
                kind="host",
                os="macos",
                arch="arm64",
                client_name="chatgpt",
                originator="desktop",
                terminal="false",
                connection_status="connected",
                busy="false",
                last_seen_bucket="24h",
                date=day,
            ).set(rng.randint(1, 4))
