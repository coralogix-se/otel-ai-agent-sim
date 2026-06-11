"""
GitHub Enterprise Copilot collector Prometheus metrics (``github_copilot_org_*`` / ``github_copilot_user_*``).

When ``SIM_COPILOT_COLLECTOR_METRICS=true``, each Copilot CLI session also increments the series queried
by AI Center with ``github-copilot-collector`` (PromQL path in cx498 HAR ``chunk-DPVBNZ5E.js``).

Label shapes follow the dashboard filter helper ``y()``: ``organization``, optional ``model`` / ``user_login``,
plus breakdown dimensions ``feature``, ``ide``, ``language``, ``sku``. User panels prefer ``user_email``;
we always set ``user_email`` + ``user_login`` + ``user_name`` on per-user series.

Field semantics mirror GitHub usage metrics API docs (``daily_active_cli_users``, ``totals_by_cli``, etc.).
No live ``github_copilot_*`` samples were available in cxai/c4c MCP tenants at implementation time.

Uses a custom ``Collector`` so metric names match GitHub's exporter exactly (no ``_total`` suffix).
"""

from __future__ import annotations

import random
import threading
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import date

from prometheus_client.core import GaugeMetricFamily
from prometheus_client.registry import Collector

from sim.env import _env_bool, _env_float

_ORG_L = ("organization",)
_USER_L = ("organization", "user_email", "user_login", "user_name")
_MODEL_FEATURE_L = _ORG_L + ("model", "feature")
_IDE_L = _ORG_L + ("ide",)
_FEATURE_L = _ORG_L + ("feature",)
_LANG_FEATURE_L = _ORG_L + ("language", "feature")
_SKU_L = _ORG_L + ("sku",)
_USER_MODEL_FEATURE_L = _USER_L + ("model", "feature")

# ``totals_by_feature`` / ``totals_by_ide`` slugs (GitHub usage metrics API + example schema).
_DEFAULT_FEATURE_MIX: tuple[tuple[str, float], ...] = (
    ("code_completion", 0.36),
    ("agent", 0.22),
    ("chat_panel_ask_mode", 0.14),
    ("chat_panel_edit_mode", 0.12),
    ("chat_panel_plan_mode", 0.08),
    ("chat_panel_agent_mode", 0.05),
    ("chat_panel_custom_mode", 0.03),
)
_DEFAULT_IDE_MIX: tuple[tuple[str, float], ...] = (
    ("vscode", 0.44),
    ("jetbrains", 0.22),
    ("visualstudio", 0.12),
    ("neovim", 0.08),
    ("xcode", 0.06),
    ("eclipse", 0.04),
    ("Copilot CLI", 0.04),
)
_LANGUAGES = ("python", "typescript", "go", "java", "rust", "ruby")
_BILLING_SKUS = ("copilot_premium_request", "copilot_chat", "copilot_agent")

_feature_mix_cache: tuple[tuple[str, float], ...] | None = None
_ide_mix_cache: tuple[tuple[str, float], ...] | None = None


def copilot_collector_enabled() -> bool:
    return _env_bool("SIM_COPILOT_COLLECTOR_METRICS", False)


def copilot_collector_org() -> str:
    import os

    return os.environ.get("SIM_COPILOT_COLLECTOR_ORG", "coralogix").strip() or "coralogix"


def _parse_weighted_mix(
    env_name: str,
    default: tuple[tuple[str, float], ...],
) -> tuple[tuple[str, float], ...]:
    """
    Parse ``name:weight,name:weight`` (weight optional, defaults to 1).

    Override defaults with ``SIM_COPILOT_COLLECTOR_FEATURE_MIX`` or
    ``SIM_COPILOT_COLLECTOR_IDE_MIX``.
    """
    import os

    raw = os.environ.get(env_name, "").strip()
    if not raw:
        return default
    out: list[tuple[str, float]] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        if ":" in part:
            name, weight_raw = part.rsplit(":", 1)
            name = name.strip()
            try:
                weight = float(weight_raw.strip())
            except ValueError:
                weight = 1.0
        else:
            name = part
            weight = 1.0
        if name:
            out.append((name, max(0.0, weight)))
    return tuple(out) if out else default


def _feature_mix() -> tuple[tuple[str, float], ...]:
    global _feature_mix_cache
    if _feature_mix_cache is None:
        _feature_mix_cache = _parse_weighted_mix(
            "SIM_COPILOT_COLLECTOR_FEATURE_MIX",
            _DEFAULT_FEATURE_MIX,
        )
    return _feature_mix_cache


def _ide_mix() -> tuple[tuple[str, float], ...]:
    global _ide_mix_cache
    if _ide_mix_cache is None:
        _ide_mix_cache = _parse_weighted_mix(
            "SIM_COPILOT_COLLECTOR_IDE_MIX",
            _DEFAULT_IDE_MIX,
        )
    return _ide_mix_cache


def _pick_weighted(mix: tuple[tuple[str, float], ...]) -> str:
    total = sum(weight for _, weight in mix)
    if total <= 0:
        return mix[0][0]
    target = random.random() * total
    acc = 0.0
    for name, weight in mix:
        acc += weight
        if target <= acc:
            return name
    return mix[-1][0]


def _user_login_from_attrs(user_attrs: dict) -> str:
    email = str(user_attrs.get("user.email", "") or "")
    if "@" in email:
        return email.split("@", 1)[0]
    return str(user_attrs.get("user.name", "unknown")).replace(" ", "-").lower() or "unknown"


def _label_key(labels: dict[str, str]) -> tuple[tuple[str, str], ...]:
    return tuple(sorted((k, str(v)) for k, v in labels.items()))


@dataclass
class _ActiveUserTracker:
    day: str = ""
    all_users: set[str] = field(default_factory=set)
    cli_users: set[str] = field(default_factory=set)
    recent_7d: deque[tuple[str, set[str]]] = field(default_factory=lambda: deque(maxlen=7))
    recent_28d: deque[tuple[str, set[str]]] = field(default_factory=lambda: deque(maxlen=28))
    lock: threading.Lock = field(default_factory=threading.Lock)

    def touch(self, user_email: str, *, cli: bool = True) -> tuple[int, int, int, int]:
        today = date.today().isoformat()
        with self.lock:
            if self.day != today:
                if self.day:
                    self.recent_7d.append((self.day, set(self.all_users)))
                    self.recent_28d.append((self.day, set(self.all_users)))
                self.day = today
                self.all_users.clear()
                self.cli_users.clear()
            self.all_users.add(user_email)
            if cli:
                self.cli_users.add(user_email)
            dau = len(self.all_users)
            cli_dau = len(self.cli_users)
            wau = len(set().union(*[s for _, s in self.recent_7d], self.all_users))
            mau = len(set().union(*[s for _, s in self.recent_28d], self.all_users))
            return dau, wau, mau, cli_dau


_tracker = _ActiveUserTracker()


class _MetricHandle:
    __slots__ = ("_parent", "_name", "_labelnames", "_label_values")

    def __init__(self, parent: GitHubCopilotCollector, name: str, labelnames: tuple[str, ...]) -> None:
        self._parent = parent
        self._name = name
        self._labelnames = labelnames
        self._label_values: dict[str, str] = {}

    def labels(self, **kwargs: str) -> _MetricHandle:
        missing = [k for k in self._labelnames if k not in kwargs]
        if missing:
            raise ValueError(f"missing labels for {self._name}: {missing}")
        handle = _MetricHandle(self._parent, self._name, self._labelnames)
        handle._label_values = dict(kwargs)
        return handle

    def inc(self, amount: float = 1.0) -> None:
        self._parent._inc_counter(self._name, self._label_values, amount)

    def set(self, value: float) -> None:
        self._parent._set_gauge(self._name, self._label_values, value)


class GitHubCopilotCollector(Collector):
    """Exact-name GitHub Copilot org/user metrics for Coralogix collector-mode dashboards."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: dict[tuple[str, tuple[tuple[str, str], ...]], float] = defaultdict(float)
        self._gauges: dict[tuple[str, tuple[tuple[str, str], ...]], float] = {}

    def _inc_counter(self, name: str, labels: dict[str, str], amount: float) -> None:
        key = (name, _label_key(labels))
        with self._lock:
            self._counters[key] += amount

    def _set_gauge(self, name: str, labels: dict[str, str], value: float) -> None:
        key = (name, _label_key(labels))
        with self._lock:
            self._gauges[key] = value

    def _handle(self, name: str, labelnames: tuple[str, ...]) -> _MetricHandle:
        return _MetricHandle(self, name, labelnames)

    def collect(self):
        with self._lock:
            counters = dict(self._counters)
            gauges = dict(self._gauges)

        by_name: dict[str, list[tuple[tuple[tuple[str, str], ...], float]]] = defaultdict(list)
        for (name, label_key), val in counters.items():
            by_name[name].append((label_key, val))
        for name, rows in by_name.items():
            if not rows:
                continue
            label_names = [k for k, _ in rows[0][0]]
            fam = GaugeMetricFamily(name, f"GitHub Copilot collector {name}", labels=label_names)
            for label_key, val in rows:
                fam.add_metric([v for _, v in label_key], val)
            yield fam

        by_gauge: dict[str, list[tuple[tuple[tuple[str, str], ...], float]]] = defaultdict(list)
        for (name, label_key), val in gauges.items():
            by_gauge[name].append((label_key, val))
        for name, rows in by_gauge.items():
            if not rows:
                continue
            label_names = [k for k, _ in rows[0][0]]
            fam = GaugeMetricFamily(name, f"GitHub Copilot collector {name}", labels=label_names)
            for label_key, val in rows:
                fam.add_metric([v for _, v in label_key], val)
            yield fam


@dataclass
class CopilotCollectorMetrics:
    org_cli_session: _MetricHandle
    org_cli_request: _MetricHandle
    org_cli_prompt_tokens: _MetricHandle
    org_cli_output_tokens: _MetricHandle
    org_code_generation: _MetricHandle
    org_code_acceptance: _MetricHandle
    org_interaction: _MetricHandle
    org_loc_suggested: _MetricHandle
    org_loc_added_lang: _MetricHandle
    org_loc_deleted_lang: _MetricHandle
    org_interaction_model: _MetricHandle
    org_interaction_ide: _MetricHandle
    org_interaction_feature: _MetricHandle
    org_billing_net: _MetricHandle
    org_billing_gross: _MetricHandle
    org_billing_discount: _MetricHandle
    org_billing_quantity: _MetricHandle
    org_pr_created: _MetricHandle
    org_pr_reviewed: _MetricHandle
    org_pr_merged: _MetricHandle
    org_pr_created_by_copilot: _MetricHandle
    org_pr_reviewed_by_copilot: _MetricHandle
    org_pr_suggestions: _MetricHandle
    org_pr_applied_suggestions: _MetricHandle
    org_daily_active: _MetricHandle
    org_weekly_active: _MetricHandle
    org_monthly_active: _MetricHandle
    org_daily_active_cli: _MetricHandle
    org_daily_active_cloud: _MetricHandle
    org_daily_active_cr: _MetricHandle
    org_daily_passive_cr: _MetricHandle
    org_pr_median_merge_min: _MetricHandle
    user_cli_session: _MetricHandle
    user_cli_prompt_tokens: _MetricHandle
    user_cli_output_tokens: _MetricHandle
    user_interaction: _MetricHandle
    user_code_generation: _MetricHandle
    user_code_acceptance: _MetricHandle
    user_code_acceptance_activity: _MetricHandle
    user_loc_added: _MetricHandle
    user_interaction_model: _MetricHandle
    collector: GitHubCopilotCollector


def register_copilot_collector_metrics(registry) -> CopilotCollectorMetrics:
    collector = GitHubCopilotCollector()
    registry.register(collector)

    def h(name: str, labels: tuple[str, ...] = _ORG_L) -> _MetricHandle:
        return collector._handle(name, labels)

    return CopilotCollectorMetrics(
        collector=collector,
        org_cli_session=h("github_copilot_org_cli_session_count"),
        org_cli_request=h("github_copilot_org_cli_request_count"),
        org_cli_prompt_tokens=h("github_copilot_org_cli_prompt_tokens_sum"),
        org_cli_output_tokens=h("github_copilot_org_cli_output_tokens_sum"),
        org_code_generation=h("github_copilot_org_code_generation_count"),
        org_code_acceptance=h("github_copilot_org_user_initiated_code_acceptance_count"),
        org_interaction=h("github_copilot_org_user_initiated_interaction_count"),
        org_loc_suggested=h("github_copilot_org_loc_suggested_to_add_sum"),
        org_loc_added_lang=h("github_copilot_org_loc_added_sum_by_language_feature", _LANG_FEATURE_L),
        org_loc_deleted_lang=h("github_copilot_org_loc_deleted_sum_by_language_feature", _LANG_FEATURE_L),
        org_interaction_model=h(
            "github_copilot_org_user_initiated_interaction_count_by_model_feature",
            _MODEL_FEATURE_L,
        ),
        org_interaction_ide=h("github_copilot_org_user_initiated_interaction_count_by_ide", _IDE_L),
        org_interaction_feature=h(
            "github_copilot_org_user_initiated_interaction_count_by_feature",
            _FEATURE_L,
        ),
        org_billing_net=h("github_copilot_billing_net_amount", _SKU_L),
        org_billing_gross=h("github_copilot_billing_gross_amount", _SKU_L),
        org_billing_discount=h("github_copilot_billing_discount_amount", _SKU_L),
        org_billing_quantity=h("github_copilot_billing_net_quantity", _SKU_L),
        org_pr_created=h("github_copilot_org_pull_requests_created"),
        org_pr_reviewed=h("github_copilot_org_pull_requests_reviewed"),
        org_pr_merged=h("github_copilot_org_pull_requests_merged"),
        org_pr_created_by_copilot=h("github_copilot_org_pull_requests_created_by_copilot"),
        org_pr_reviewed_by_copilot=h("github_copilot_org_pull_requests_reviewed_by_copilot"),
        org_pr_suggestions=h("github_copilot_org_pull_requests_copilot_suggestions"),
        org_pr_applied_suggestions=h("github_copilot_org_pull_requests_copilot_applied_suggestions"),
        org_daily_active=h("github_copilot_org_daily_active_users"),
        org_weekly_active=h("github_copilot_org_weekly_active_users"),
        org_monthly_active=h("github_copilot_org_monthly_active_users"),
        org_daily_active_cli=h("github_copilot_org_daily_active_cli_users"),
        org_daily_active_cloud=h("github_copilot_org_daily_active_copilot_cloud_agent_users"),
        org_daily_active_cr=h("github_copilot_org_daily_active_copilot_code_review_users"),
        org_daily_passive_cr=h("github_copilot_org_daily_passive_copilot_code_review_users"),
        org_pr_median_merge_min=h("github_copilot_org_pull_requests_median_minutes_to_merge"),
        user_cli_session=h("github_copilot_user_cli_session_count", _USER_L),
        user_cli_prompt_tokens=h("github_copilot_user_cli_prompt_tokens_sum", _USER_L),
        user_cli_output_tokens=h("github_copilot_user_cli_output_tokens_sum", _USER_L),
        user_interaction=h("github_copilot_user_user_initiated_interaction_count", _USER_L),
        user_code_generation=h("github_copilot_user_code_generation_count", _USER_L),
        user_code_acceptance=h("github_copilot_user_code_acceptance_count", _USER_L),
        user_code_acceptance_activity=h("github_copilot_user_code_acceptance_activity_count", _USER_L),
        user_loc_added=h("github_copilot_user_loc_added_sum", _USER_L),
        user_interaction_model=h(
            "github_copilot_user_user_initiated_interaction_count_by_model_feature",
            _USER_MODEL_FEATURE_L,
        ),
    )


def _maybe_pr_activity(metrics: CopilotCollectorMetrics, org: str) -> None:
    rate = _env_float("SIM_COPILOT_COLLECTOR_PR_RATE", 0.02)
    if random.random() >= rate:
        return
    metrics.org_pr_created.labels(organization=org).inc()
    if random.random() < 0.35:
        metrics.org_pr_created_by_copilot.labels(organization=org).inc()
    if random.random() < 0.55:
        metrics.org_pr_reviewed.labels(organization=org).inc()
    if random.random() < 0.25:
        metrics.org_pr_reviewed_by_copilot.labels(organization=org).inc()
    suggestions = random.randint(1, 8)
    metrics.org_pr_suggestions.labels(organization=org).inc(suggestions)
    metrics.org_pr_applied_suggestions.labels(organization=org).inc(
        random.randint(0, suggestions)
    )
    if random.random() < 0.4:
        metrics.org_pr_merged.labels(organization=org).inc()
        metrics.org_pr_median_merge_min.labels(organization=org).set(
            random.uniform(15.0, 720.0)
        )


def record_copilot_collector_session(
    metrics: CopilotCollectorMetrics,
    *,
    user_attrs: dict,
    model: str,
    n_turns: int,
    n_tools: int,
    total_in: int,
    total_out: int,
    cost_usd: float,
    productivity_ok: bool,
    org: str | None = None,
) -> None:
    """Increment org/user collector counters and refresh DAU gauges for one CLI session."""
    org_name = org or copilot_collector_org()
    user_email = str(user_attrs.get("user.email", "") or "unknown@coralogix.com")
    user_login = _user_login_from_attrs(user_attrs)
    user_name = str(user_attrs.get("user.name", user_login))
    language = random.choice(_LANGUAGES)
    feature = _pick_weighted(_feature_mix())
    ide = _pick_weighted(_ide_mix())
    loc_added = random.randint(4, 120) if productivity_ok else random.randint(0, 24)
    loc_deleted = random.randint(0, 18) if productivity_ok else 0
    loc_suggested = loc_added + random.randint(0, 40)
    requests = max(n_turns, n_turns + n_tools // 2)
    generations = max(1, n_turns + random.randint(0, 2))
    interactions = max(1, n_turns)
    acceptances = 1 if productivity_ok else 0
    sku = random.choice(_BILLING_SKUS)
    gross = cost_usd * random.uniform(1.05, 1.18)
    discount = max(0.0, gross - cost_usd)

    user_l = dict(
        organization=org_name,
        user_email=user_email,
        user_login=user_login,
        user_name=user_name,
    )

    metrics.org_cli_session.labels(organization=org_name).inc()
    metrics.org_cli_request.labels(organization=org_name).inc(requests)
    metrics.org_cli_prompt_tokens.labels(organization=org_name).inc(total_in)
    metrics.org_cli_output_tokens.labels(organization=org_name).inc(total_out)
    metrics.org_code_generation.labels(organization=org_name).inc(generations)
    metrics.org_interaction.labels(organization=org_name).inc(interactions)
    metrics.org_loc_suggested.labels(organization=org_name).inc(loc_suggested)
    metrics.org_loc_added_lang.labels(
        organization=org_name, language=language, feature=feature
    ).inc(loc_added)
    if loc_deleted:
        metrics.org_loc_deleted_lang.labels(
            organization=org_name, language=language, feature=feature
        ).inc(loc_deleted)
    metrics.org_interaction_model.labels(
        organization=org_name, model=model, feature=feature
    ).inc(interactions)
    metrics.org_interaction_ide.labels(organization=org_name, ide=ide).inc(interactions)
    metrics.org_interaction_feature.labels(organization=org_name, feature=feature).inc(
        interactions
    )
    if acceptances:
        metrics.org_code_acceptance.labels(organization=org_name).inc(acceptances)
    metrics.org_billing_net.labels(organization=org_name, sku=sku).inc(cost_usd)
    metrics.org_billing_gross.labels(organization=org_name, sku=sku).inc(gross)
    metrics.org_billing_discount.labels(organization=org_name, sku=sku).inc(discount)
    metrics.org_billing_quantity.labels(organization=org_name, sku=sku).inc(
        max(1, requests // 2)
    )

    metrics.user_cli_session.labels(**user_l).inc()
    metrics.user_cli_prompt_tokens.labels(**user_l).inc(total_in)
    metrics.user_cli_output_tokens.labels(**user_l).inc(total_out)
    metrics.user_interaction.labels(**user_l).inc(interactions)
    metrics.user_code_generation.labels(**user_l).inc(generations)
    metrics.user_loc_added.labels(**user_l).inc(loc_added)
    metrics.user_interaction_model.labels(**user_l, model=model, feature=feature).inc(
        interactions
    )
    if acceptances:
        metrics.user_code_acceptance.labels(**user_l).inc(acceptances)
        metrics.user_code_acceptance_activity.labels(**user_l).inc(acceptances)

    dau, wau, mau, cli_dau = _tracker.touch(user_email, cli=True)
    metrics.org_daily_active.labels(organization=org_name).set(dau)
    metrics.org_weekly_active.labels(organization=org_name).set(wau)
    metrics.org_monthly_active.labels(organization=org_name).set(mau)
    metrics.org_daily_active_cli.labels(organization=org_name).set(cli_dau)
    if random.random() < _env_float("SIM_COPILOT_COLLECTOR_CLOUD_AGENT_RATE", 0.08):
        metrics.org_daily_active_cloud.labels(organization=org_name).set(
            max(1, cli_dau // random.randint(3, 8))
        )
    if random.random() < _env_float("SIM_COPILOT_COLLECTOR_CODE_REVIEW_RATE", 0.05):
        cr_users = max(1, dau // random.randint(4, 10))
        metrics.org_daily_active_cr.labels(organization=org_name).set(cr_users)
        if random.random() < 0.5:
            metrics.org_daily_passive_cr.labels(organization=org_name).set(
                max(1, cr_users // 2)
            )

    _maybe_pr_activity(metrics, org_name)
