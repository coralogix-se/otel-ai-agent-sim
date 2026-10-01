"""Emit OpenAI Administration / API Platform Prometheus gauges.

Contract:
- Cost/usage: one daily gauge bucket per ``date`` (backfill ≥28d; restamp today).
- Inventory / compliance / spend limits: no ``date``; restated each cycle.
- Models: gpt-5.5 (~65% spend), gpt-5.4, gpt-5.4-mini, gpt-5-mini, gpt-4.1-mini.
- Weekday ~$700–1200 / ~270M in / ~30M out / ~150k requests; weekend ×0.6.
- Labels match live OpenAI Administration exporter (kb): ``organization`` everywhere;
  cost carries key/user + org id/name; compliance metric names without ``project_`` prefix.
"""

from __future__ import annotations

import logging
import os
import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from prometheus_client import CollectorRegistry, Gauge

from sim.common.env import _env_bool, _env_csv_model_pool, _env_float, _env_int
from sim.common.model_pricing import model_rates
from sim.openai.constants import (
    ADMIN_KEY_LABELS,
    CACHE_HIT_FRAC,
    COMPLIANCE_HOSTED_TOOL_LABELS,
    COMPLIANCE_ORG_LABELS,
    COMPLIANCE_PROJECT_LABELS,
    COMPLIANCE_PROJECT_MODEL_LABELS,
    COMPLIANCE_RETENTION_INFO_LABELS,
    COST_QTY_LABELS,
    COST_TOOL_LINE_ITEMS,
    COST_USD_LABELS,
    DEFAULT_ADMIN_KEYS,
    DEFAULT_API_KEYS,
    DEFAULT_BACKFILL_DAYS,
    DEFAULT_PROJECTS,
    DEFAULT_SERVICE_ACCOUNTS,
    DEFAULT_USERS,
    FAMILY_LABELS,
    HOSTED_TOOLS,
    OPENAI_ADMIN_MODEL_SPEND_WEIGHTS,
    OPENAI_ADMIN_MODELS,
    ORG_INVITE_LABELS,
    ORG_USER_LABELS,
    PROJECT_KEY_LABELS,
    PROJECT_LABELS,
    PROJECT_MEMBER_LABELS,
    PROJECT_SA_LABELS,
    SERVICE_TIERS,
    SPEND_ORG_LABELS,
    SPEND_PROJECT_LABELS,
    SPEND_PROJECT_USD_LABELS,
    USAGE_COMPLETION_FIELDS,
    USAGE_FAMILY_FIELDS,
    USAGE_LABELS,
    WEEKDAY_COST_USD_HI,
    WEEKDAY_COST_USD_LO,
    WEEKDAY_INPUT_TOKENS,
    WEEKDAY_OUTPUT_TOKENS,
    WEEKDAY_REQUESTS,
    WEEKEND_COST_MULT,
    _stable_id,
    default_api_keys,
    default_organization_id,
    default_organization_name,
    default_projects,
)

log = logging.getLogger(__name__)


def _cx_app() -> str:
    return os.environ.get("OPENAI_ADMIN_CX_APPLICATION_NAME", "OpenAI").strip() or "OpenAI"


def _cx_sub() -> str:
    return (
        os.environ.get("OPENAI_ADMIN_CX_SUBSYSTEM_NAME", "API Platform").strip()
        or "API Platform"
    )


@dataclass
class _SimUser:
    email: str
    name: str
    user_id: str
    role: str
    roster_index: int = 0


@dataclass
class OpenAIAdminSim:
    registry: CollectorRegistry
    models: tuple[str, ...] = field(default_factory=lambda: OPENAI_ADMIN_MODELS)
    projects: tuple[dict[str, str], ...] = field(default_factory=tuple)
    api_keys: tuple[dict[str, str], ...] = field(default_factory=tuple)
    service_accounts: tuple[dict[str, str], ...] = field(default_factory=tuple)
    admin_keys: tuple[dict[str, str], ...] = field(default_factory=tuple)
    users: tuple[_SimUser, ...] = field(default_factory=tuple)
    organization: str = field(default_factory=default_organization_id)
    organization_name: str = field(default_factory=default_organization_name)

    _closed_days: set[str] = field(default_factory=set)
    _backfilled: bool = False
    _inventory_hour: int = -1

    _usage_gauges: dict[str, Gauge] = field(default_factory=dict)
    _family_gauges: dict[str, Gauge] = field(default_factory=dict)
    cost_usd: Gauge = field(init=False)
    cost_qty: Gauge = field(init=False)
    spend_limit_configured: Gauge = field(init=False)
    project_spend_limit_configured: Gauge = field(init=False)
    project_spend_limit_enforcing: Gauge = field(init=False)
    project_spend_limit_usd: Gauge = field(init=False)
    spend_alert_threshold_usd: Gauge = field(init=False)
    org_user_info: Gauge = field(init=False)
    project_info: Gauge = field(init=False)
    project_api_key_info: Gauge = field(init=False)
    project_service_account_info: Gauge = field(init=False)
    org_invite_info: Gauge = field(init=False)
    admin_key_info: Gauge = field(init=False)
    project_member_info: Gauge = field(init=False)
    org_mtls_enabled: Gauge = field(init=False)
    rate_limit_configured: Gauge = field(init=False)
    model_permissions_configured: Gauge = field(init=False)
    data_retention_enabled: Gauge = field(init=False)
    data_retention_info: Gauge = field(init=False)
    hosted_tool_enabled: Gauge = field(init=False)

    def __post_init__(self) -> None:
        self.models = _env_csv_model_pool("SIM_OPENAI_ADMIN_MODELS", self.models)
        self.organization = default_organization_id()
        self.organization_name = default_organization_name()
        n_users = max(4, _env_int("SIM_OPENAI_ADMIN_USERS", DEFAULT_USERS))
        n_projects = max(1, _env_int("SIM_OPENAI_ADMIN_PROJECTS", DEFAULT_PROJECTS))
        n_keys = max(1, _env_int("SIM_OPENAI_ADMIN_API_KEYS", DEFAULT_API_KEYS))
        self.projects = default_projects(n_projects)
        keys = list(default_api_keys(self.projects, n_keys))
        self.users = self._build_users(n_users)
        for i, key in enumerate(keys):
            if key["owner_type"] == "user":
                u = self.users[i % len(self.users)]
                key["owner_email"] = u.email.lower()
                key["owner_name"] = u.name
                key["owner_role"] = u.role
            else:
                key["owner_email"] = f"sa-{i}@coralogix.com"
                key["owner_name"] = f"Service Account {i + 1}"
                key["owner_role"] = "member"
            key["owner_project_access"] = "active"
        self.api_keys = tuple(keys)
        active = [p for p in self.projects if p["status"] == "active"] or list(self.projects)
        self.service_accounts = tuple(
            {
                "name": f"sim-sa-{i + 1}",
                "project_id": active[i % len(active)]["project_id"],
                "user_id": _stable_id("user-", f"sa-user:{i}", 24),
                "role": "member",
            }
            for i in range(max(1, _env_int("SIM_OPENAI_ADMIN_SERVICE_ACCOUNTS", DEFAULT_SERVICE_ACCOUNTS)))
        )
        self.admin_keys = tuple(
            {
                "api_key_id": _stable_id("key_", f"admin:{i}", 18),
                "name": f"sim-admin-{i + 1}",
            }
            for i in range(max(1, _env_int("SIM_OPENAI_ADMIN_ADMIN_KEYS", DEFAULT_ADMIN_KEYS)))
        )
        self._init_gauges()
        self._seed_directory()
        self._seed_spend_limits()
        self._seed_compliance()

    def _build_users(self, n: int) -> tuple[_SimUser, ...]:
        try:
            from sim.common.identity import products_roster_users

            roster = [dict(u) for u in products_roster_users()]
            if len(roster) < n:
                from sim.common.identity import _CORALOGIX_TEAM_USERS

                roster = [dict(u) for u in _CORALOGIX_TEAM_USERS[:n]]
        except Exception:
            from sim.common.identity import _CORALOGIX_TEAM_USERS

            roster = [dict(u) for u in _CORALOGIX_TEAM_USERS[:n]]
        while len(roster) < n:
            i = len(roster)
            roster.append(
                {
                    "user.email": f"openai.user{i}@coralogix.com",
                    "user.name": f"OpenAI User {i}",
                }
            )
        out: list[_SimUser] = []
        roles = ("owner", "owner", "member", "member", "member", "reader")
        for i, row in enumerate(roster[:n]):
            email = str(row.get("user.email", f"user{i}@coralogix.com")).lower()
            name = str(row.get("user.name", email.split("@", 1)[0].replace(".", " ").title()))
            out.append(
                _SimUser(
                    email=email,
                    name=name,
                    user_id=_stable_id("user-", f"openai-user:{i}", 24),
                    role=roles[i % len(roles)],
                    roster_index=i,
                )
            )
        return tuple(out)

    def _metric_base(self) -> dict[str, str]:
        return {
            "cx_application_name": _cx_app(),
            "cx_subsystem_name": _cx_sub(),
            "organization": self.organization,
        }

    def _cost_org_labels(self) -> dict[str, str]:
        return {
            **self._metric_base(),
            "organization_id": self.organization,
            "organization_name": self.organization_name,
        }

    def _init_gauges(self) -> None:
        for field_name in USAGE_COMPLETION_FIELDS:
            self._usage_gauges[field_name] = Gauge(
                f"openai_administration_usage_{field_name}",
                f"OpenAI Administration daily usage ({field_name})",
                labelnames=USAGE_LABELS,
                registry=self.registry,
            )
        for field_name in USAGE_FAMILY_FIELDS:
            self._family_gauges[field_name] = Gauge(
                f"openai_administration_usage_{field_name}",
                f"OpenAI Administration platform-feature usage ({field_name})",
                labelnames=FAMILY_LABELS,
                registry=self.registry,
            )
        self.cost_usd = Gauge(
            "openai_administration_cost_usd",
            "OpenAI Administration daily cost USD",
            labelnames=COST_USD_LABELS,
            registry=self.registry,
        )
        self.cost_qty = Gauge(
            "openai_administration_cost_quantity",
            "OpenAI Administration daily cost quantity",
            labelnames=COST_QTY_LABELS,
            registry=self.registry,
        )
        self.spend_limit_configured = Gauge(
            "openai_administration_spend_limit_configured",
            "Org spend limit configured flag",
            labelnames=SPEND_ORG_LABELS,
            registry=self.registry,
        )
        self.project_spend_limit_configured = Gauge(
            "openai_administration_project_spend_limit_configured",
            "Project spend limit configured flag",
            labelnames=SPEND_PROJECT_LABELS,
            registry=self.registry,
        )
        self.project_spend_limit_enforcing = Gauge(
            "openai_administration_project_spend_limit_enforcing",
            "Project spend limit enforcing flag",
            labelnames=SPEND_PROJECT_LABELS,
            registry=self.registry,
        )
        self.project_spend_limit_usd = Gauge(
            "openai_administration_project_spend_limit_usd",
            "Project spend limit USD",
            labelnames=SPEND_PROJECT_USD_LABELS,
            registry=self.registry,
        )
        self.spend_alert_threshold_usd = Gauge(
            "openai_administration_spend_alert_threshold_usd",
            "Org spend alert threshold USD",
            labelnames=SPEND_ORG_LABELS,
            registry=self.registry,
        )
        self.org_user_info = Gauge(
            "openai_administration_org_user_info",
            "OpenAI org user directory row",
            labelnames=ORG_USER_LABELS,
            registry=self.registry,
        )
        self.project_info = Gauge(
            "openai_administration_project_info",
            "OpenAI project directory row",
            labelnames=PROJECT_LABELS,
            registry=self.registry,
        )
        self.project_api_key_info = Gauge(
            "openai_administration_project_api_key_info",
            "OpenAI project API key directory row",
            labelnames=PROJECT_KEY_LABELS,
            registry=self.registry,
        )
        self.project_service_account_info = Gauge(
            "openai_administration_project_service_account_info",
            "OpenAI project service account directory row",
            labelnames=PROJECT_SA_LABELS,
            registry=self.registry,
        )
        self.org_invite_info = Gauge(
            "openai_administration_org_invite_info",
            "OpenAI org invite directory row",
            labelnames=ORG_INVITE_LABELS,
            registry=self.registry,
        )
        self.admin_key_info = Gauge(
            "openai_administration_admin_key_info",
            "OpenAI admin API key directory row",
            labelnames=ADMIN_KEY_LABELS,
            registry=self.registry,
        )
        self.project_member_info = Gauge(
            "openai_administration_project_member_info",
            "OpenAI project member directory row",
            labelnames=PROJECT_MEMBER_LABELS,
            registry=self.registry,
        )
        self.org_mtls_enabled = Gauge(
            "openai_administration_org_mtls_enabled",
            "Org mutual TLS enforced (0/1)",
            labelnames=COMPLIANCE_ORG_LABELS,
            registry=self.registry,
        )
        # kb names (no project_ prefix).
        self.rate_limit_configured = Gauge(
            "openai_administration_rate_limit_configured",
            "Project×model rate limit configured (0/1)",
            labelnames=COMPLIANCE_PROJECT_MODEL_LABELS,
            registry=self.registry,
        )
        self.model_permissions_configured = Gauge(
            "openai_administration_model_permissions_configured",
            "Project model allow-list configured (0/1)",
            labelnames=COMPLIANCE_PROJECT_LABELS,
            registry=self.registry,
        )
        self.data_retention_enabled = Gauge(
            "openai_administration_data_retention_enabled",
            "Org data retention enabled (0/1)",
            labelnames=COMPLIANCE_ORG_LABELS,
            registry=self.registry,
        )
        self.data_retention_info = Gauge(
            "openai_administration_data_retention_info",
            "Per-project data retention posture",
            labelnames=COMPLIANCE_RETENTION_INFO_LABELS,
            registry=self.registry,
        )
        self.hosted_tool_enabled = Gauge(
            "openai_administration_hosted_tool_enabled",
            "Hosted tool enabled for project (0/1)",
            labelnames=COMPLIANCE_HOSTED_TOOL_LABELS,
            registry=self.registry,
        )

    def _seed_directory(self) -> None:
        base = self._metric_base()
        for u in self.users:
            self.org_user_info.labels(
                **base, user_id=u.user_id, email=u.email, name=u.name, role=u.role
            ).set(1)
        for p in self.projects:
            self.project_info.labels(
                **base, project_id=p["project_id"], name=p["name"], status=p["status"]
            ).set(1)
        for k in self.api_keys:
            self.project_api_key_info.labels(
                **base,
                project_id=k["project_id"],
                api_key_id=k["api_key_id"],
                name=k["name"],
                owner_type=k["owner_type"],
                owner_email=k["owner_email"],
                owner_name=k["owner_name"],
                owner_role=k["owner_role"],
                owner_project_access=k["owner_project_access"],
            ).set(1)
        for sa in self.service_accounts:
            self.project_service_account_info.labels(
                **base,
                project_id=sa["project_id"],
                name=sa["name"],
                user_id=sa["user_id"],
                role=sa["role"],
            ).set(1)
        for ak in self.admin_keys:
            self.admin_key_info.labels(**base, api_key_id=ak["api_key_id"], name=ak["name"]).set(1)
        for i, u in enumerate(self.users):
            p = self.projects[i % len(self.projects)]
            self.project_member_info.labels(
                **base,
                project_id=p["project_id"],
                user_id=u.user_id,
                email=u.email,
                name=u.name,
                role=u.role,
            ).set(1)
        invite_statuses = ("pending", "accepted", "accepted")
        for i in range(3):
            self.org_invite_info.labels(
                **base,
                invite_id=_stable_id("invite-", f"invite:{i}", 16),
                email=f"invitee{i + 1}@example.com",
                role="member",
                status=invite_statuses[i],
            ).set(1)

    def _seed_spend_limits(self) -> None:
        base = self._metric_base()
        self.spend_limit_configured.labels(**base, scope="org").set(1)
        self.spend_alert_threshold_usd.labels(**base, scope="org").set(
            _env_float("SIM_OPENAI_ADMIN_SPEND_ALERT_USD", 20_000.0)
        )
        active = [p for p in self.projects if p["status"] == "active"]
        configured_n = min(5, max(0, len(active) - 3)) if len(active) >= 5 else max(1, len(active) // 2)
        limits = (2500.0, 4000.0, 5500.0, 7000.0, 8000.0)
        for i, p in enumerate(self.projects):
            pid = p["project_id"]
            pl = {**base, "project_id": pid, "scope": "project"}
            if p["status"] != "active":
                self.project_spend_limit_configured.labels(**pl).set(0)
                self.project_spend_limit_enforcing.labels(**pl).set(0)
                self.project_spend_limit_usd.labels(
                    **pl, currency="USD", interval="month"
                ).set(0)
                continue
            active_idx = active.index(p)
            if active_idx < configured_n:
                limit = limits[active_idx % len(limits)]
                self.project_spend_limit_configured.labels(**pl).set(1)
                self.project_spend_limit_enforcing.labels(**pl).set(
                    1 if active_idx % 2 == 0 else 0
                )
                self.project_spend_limit_usd.labels(
                    **pl, currency="USD", interval="month"
                ).set(limit)
            else:
                self.project_spend_limit_configured.labels(**pl).set(0)
                self.project_spend_limit_enforcing.labels(**pl).set(0)
                self.project_spend_limit_usd.labels(
                    **pl, currency="USD", interval="month"
                ).set(0)

    def _seed_compliance(self) -> None:
        """Posture gauges for Compliance insights (mtls / rate limits / allow-lists)."""
        base = self._metric_base()
        self.org_mtls_enabled.labels(**base, scope="org").set(0)
        # Org-level retention flag (kb: scope=org).
        self.data_retention_enabled.labels(**base, scope="org").set(1)
        active = [p for p in self.projects if p["status"] == "active"]
        for i, p in enumerate(active):
            pid = p["project_id"]
            self.model_permissions_configured.labels(**base, project_id=pid).set(
                0 if i % 3 == 0 else 1
            )
            self.data_retention_info.labels(
                **base,
                project_id=pid,
                scope="project",
                type="organization_default",
            ).set(1 if i % 5 != 0 else 0)
            for tool in HOSTED_TOOLS:
                # Leave some tools off so Compliance can surface gaps.
                enabled = 0 if (i + HOSTED_TOOLS.index(tool)) % 4 == 3 else 1
                self.hosted_tool_enabled.labels(
                    **base, project_id=pid, tool=tool
                ).set(enabled)
            for mi, model in enumerate(self.models):
                configured = 0 if (i + mi) % 3 == 2 else 1
                self.rate_limit_configured.labels(
                    **base, project_id=pid, model=model
                ).set(configured)

    def _day_scale(self, day: datetime, *, today: datetime) -> float:
        """Weekend ×0.6; recent 7d ~1.35× prior 7d for WoW insight."""
        scale = WEEKEND_COST_MULT if day.weekday() >= 5 else 1.0
        age = (today.date() - day.date()).days
        if age == 0:
            pass
        elif 1 <= age <= 7:
            scale *= 1.35
        elif 8 <= age <= 14:
            scale *= 1.0
        else:
            scale *= 0.85
        return scale

    def _target_day_usd(self, day: datetime, *, today: datetime) -> float:
        lo = _env_float("SIM_OPENAI_ADMIN_WEEKDAY_COST_LO", WEEKDAY_COST_USD_LO)
        hi = _env_float("SIM_OPENAI_ADMIN_WEEKDAY_COST_HI", WEEKDAY_COST_USD_HI)
        base = random.uniform(lo, hi)
        return max(50.0, base * self._day_scale(day, today=today))

    def _set_usage(
        self,
        *,
        field_name: str,
        model: str,
        api_key_id: str,
        day: str,
        project_id: str,
        user_id: str,
        service_tier: str,
        value: float,
    ) -> None:
        if value <= 0:
            return
        labels = {
            **self._metric_base(),
            "model": model,
            "api_key_id": api_key_id,
            "date": day,
            "project_id": project_id,
            "user_id": user_id,
            "service_tier": service_tier,
        }
        self._usage_gauges[field_name].labels(**labels).set(value)

    def _set_cost(
        self,
        *,
        line_item: str,
        project: dict[str, str],
        day: str,
        usd: float,
        qty: float,
        unit: str,
        api_key_id: str,
        user_id: str,
        user_email: str,
    ) -> None:
        if usd <= 0 and qty <= 0:
            return
        labels = {
            **self._cost_org_labels(),
            "line_item": line_item,
            "project_id": project["project_id"],
            "project_name": project["name"],
            "api_key_id": api_key_id,
            "user_id": user_id,
            "user_email": user_email,
            "date": day,
        }
        self.cost_usd.labels(**labels).set(usd)
        self.cost_qty.labels(**labels, unit=unit).set(qty)

    def _materialize_day(self, day_dt: datetime, *, today: datetime, force: bool = False) -> None:
        day = day_dt.date().isoformat()
        if day in self._closed_days and not force:
            return

        target_usd = self._target_day_usd(day_dt, today=today)
        tok_scale = self._day_scale(day_dt, today=today)
        day_input = WEEKDAY_INPUT_TOKENS * tok_scale
        day_output = WEEKDAY_OUTPUT_TOKENS * tok_scale
        day_requests = WEEKDAY_REQUESTS * tok_scale

        weights = {
            m: OPENAI_ADMIN_MODEL_SPEND_WEIGHTS.get(m, 0.05)
            for m in self.models
        }
        w_sum = sum(weights.values()) or 1.0
        weights = {m: w / w_sum for m, w in weights.items()}

        parts: list[tuple[str, float, float, float, float, float]] = []
        for model, w in weights.items():
            rates = model_rates(model)
            in_tok = day_input * w
            out_tok = day_output * w
            cached = in_tok * CACHE_HIT_FRAC
            uncached = max(0.0, in_tok - cached)
            req = day_requests * w
            usd = (
                uncached * rates.input / 1_000_000.0
                + cached * rates.cache_read_rate() / 1_000_000.0
                + out_tok * rates.output / 1_000_000.0
            )
            parts.append((model, uncached, cached, out_tok, req, usd))

        raw_total = sum(p[-1] for p in parts) or 1.0
        usd_scale = target_usd / raw_total

        keys = list(self.api_keys)
        users = list(self.users)
        project_by_id = {p["project_id"]: p for p in self.projects}

        for model, uncached, cached, out_tok, req, usd in parts:
            n_slices = min(8, max(3, len(keys) // 3))
            slice_keys = random.sample(keys, k=min(n_slices, len(keys)))
            for sk in slice_keys:
                share = 1.0 / len(slice_keys)
                user = random.choice(users)
                project = project_by_id.get(sk["project_id"], self.projects[0])
                tier = random.choices(SERVICE_TIERS, weights=(0.8, 0.12, 0.08), k=1)[0]
                u_unc = uncached * share
                u_c = cached * share
                u_in = u_unc + u_c
                u_out = out_tok * share
                u_req = req * share

                self._set_usage(
                    field_name="input_tokens",
                    model=model,
                    api_key_id=sk["api_key_id"],
                    day=day,
                    project_id=project["project_id"],
                    user_id=user.user_id,
                    service_tier=tier,
                    value=u_in,
                )
                self._set_usage(
                    field_name="input_uncached_tokens",
                    model=model,
                    api_key_id=sk["api_key_id"],
                    day=day,
                    project_id=project["project_id"],
                    user_id=user.user_id,
                    service_tier=tier,
                    value=u_unc,
                )
                self._set_usage(
                    field_name="input_cached_tokens",
                    model=model,
                    api_key_id=sk["api_key_id"],
                    day=day,
                    project_id=project["project_id"],
                    user_id=user.user_id,
                    service_tier=tier,
                    value=u_c,
                )
                self._set_usage(
                    field_name="input_text_tokens",
                    model=model,
                    api_key_id=sk["api_key_id"],
                    day=day,
                    project_id=project["project_id"],
                    user_id=user.user_id,
                    service_tier=tier,
                    value=u_in * 0.97,
                )
                self._set_usage(
                    field_name="input_cached_text_tokens",
                    model=model,
                    api_key_id=sk["api_key_id"],
                    day=day,
                    project_id=project["project_id"],
                    user_id=user.user_id,
                    service_tier=tier,
                    value=u_c * 0.95,
                )
                self._set_usage(
                    field_name="input_cache_write_tokens",
                    model=model,
                    api_key_id=sk["api_key_id"],
                    day=day,
                    project_id=project["project_id"],
                    user_id=user.user_id,
                    service_tier=tier,
                    value=u_c * 0.08,
                )
                self._set_usage(
                    field_name="output_tokens",
                    model=model,
                    api_key_id=sk["api_key_id"],
                    day=day,
                    project_id=project["project_id"],
                    user_id=user.user_id,
                    service_tier=tier,
                    value=u_out,
                )
                self._set_usage(
                    field_name="output_text_tokens",
                    model=model,
                    api_key_id=sk["api_key_id"],
                    day=day,
                    project_id=project["project_id"],
                    user_id=user.user_id,
                    service_tier=tier,
                    value=u_out * 0.98,
                )
                self._set_usage(
                    field_name="requests",
                    model=model,
                    api_key_id=sk["api_key_id"],
                    day=day,
                    project_id=project["project_id"],
                    user_id=user.user_id,
                    service_tier=tier,
                    value=u_req,
                )
                for field_name, frac in (
                    ("input_audio_tokens", 0.005),
                    ("input_image_tokens", 0.01),
                    ("input_cached_audio_tokens", 0.002),
                    ("input_cached_image_tokens", 0.008),
                    ("output_audio_tokens", 0.004),
                    ("output_image_tokens", 0.006),
                ):
                    base_tok = u_in if "input" in field_name else u_out
                    if "cached" in field_name:
                        base_tok = u_c
                    self._set_usage(
                        field_name=field_name,
                        model=model,
                        api_key_id=sk["api_key_id"],
                        day=day,
                        project_id=project["project_id"],
                        user_id=user.user_id,
                        service_tier=tier,
                        value=base_tok * frac,
                    )

                rates = model_rates(model)
                usd_in = u_unc * rates.input / 1_000_000.0
                usd_cached = u_c * rates.cache_read_rate() / 1_000_000.0
                usd_out = u_out * rates.output / 1_000_000.0
                part_sum = (usd_in + usd_cached + usd_out) or 1.0
                model_usd = usd * usd_scale * share
                usd_in = usd_in * model_usd / part_sum
                usd_cached = usd_cached * model_usd / part_sum
                usd_out = usd_out * model_usd / part_sum

                cost_kw = dict(
                    project=project,
                    day=day,
                    api_key_id=sk["api_key_id"],
                    user_id=user.user_id,
                    user_email=user.email,
                )
                self._set_cost(
                    line_item=f"{model}, input",
                    usd=usd_in,
                    qty=u_unc,
                    unit="tokens",
                    **cost_kw,
                )
                self._set_cost(
                    line_item=f"{model}, cached input",
                    usd=usd_cached,
                    qty=u_c,
                    unit="tokens",
                    **cost_kw,
                )
                self._set_cost(
                    line_item=f"{model}, output",
                    usd=usd_out,
                    qty=u_out,
                    unit="tokens",
                    **cost_kw,
                )

                if random.random() < 0.4:
                    fam_labels = {
                        **self._metric_base(),
                        "model": model,
                        "api_key_id": sk["api_key_id"],
                        "date": day,
                        "project_id": project["project_id"],
                        "user_id": user.user_id,
                        "service_tier": tier,
                    }
                    self._family_gauges["embeddings_tokens"].labels(**fam_labels).set(
                        random.uniform(50_000, 400_000) * tok_scale * share
                    )
                    self._family_gauges["embeddings_model_requests"].labels(**fam_labels).set(
                        random.uniform(20, 200) * tok_scale * share
                    )
                    self._family_gauges["web_search_requests"].labels(**fam_labels).set(
                        random.uniform(10, 80) * tok_scale * share
                    )
                    self._family_gauges["file_search_requests"].labels(**fam_labels).set(
                        random.uniform(5, 40) * tok_scale * share
                    )

        for tool in COST_TOOL_LINE_ITEMS:
            proj = random.choice([p for p in self.projects if p["status"] == "active"] or list(self.projects))
            u = random.choice(users)
            k = random.choice(keys)
            self._set_cost(
                line_item=tool,
                project=proj,
                day=day,
                usd=random.uniform(2.0, 12.0) * tok_scale,
                qty=random.uniform(20, 120) * tok_scale,
                unit="calls",
                api_key_id=k["api_key_id"],
                user_id=u.user_id,
                user_email=u.email,
            )

        self._closed_days.add(day)

    def _ensure_backfill(self, now: datetime) -> None:
        if self._backfilled:
            return
        days = max(14, _env_int("SIM_OPENAI_ADMIN_BACKFILL_DAYS", DEFAULT_BACKFILL_DAYS))
        for age in range(days, 0, -1):
            day_dt = now - timedelta(days=age)
            self._materialize_day(day_dt, today=now, force=True)
        self._backfilled = True
        log.info(
            "OpenAI Admin backfilled %d daily buckets (models=%s; weekday~$%.0f–%.0f; org=%s)",
            days,
            ",".join(self.models),
            WEEKDAY_COST_USD_LO,
            WEEKDAY_COST_USD_HI,
            self.organization,
        )

    def emit_cycle(self, now: datetime | None = None) -> None:
        now = now or datetime.now(timezone.utc)
        self._ensure_backfill(now)

        today = now.replace(hour=0, minute=0, second=0, microsecond=0)
        today_key = today.date().isoformat()
        if today_key in self._closed_days:
            self._closed_days.discard(today_key)
        self._materialize_day(today, today=now, force=True)

        if _env_bool("SIM_OPENAI_ADMIN_REFRESH_DIRECTORY", True):
            self._seed_directory()
            self._seed_spend_limits()
            self._seed_compliance()
            self._inventory_hour = now.hour
