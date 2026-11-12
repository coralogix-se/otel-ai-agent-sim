"""Shared helpers for OpenAI Compliance / ChatGPT Enterprise gauge sims.

Metric shapes follow the Real Query Engine maps under ``docs/query-engine-maps/``
and live ``kb`` (eu2) label vocabularies (2026-11).
"""

from __future__ import annotations

import hashlib
import os
import random
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sim.common.env import _env_int


def utc_today() -> datetime:
    return datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)


def date_str(day: datetime | None = None) -> str:
    return (day or utc_today()).date().isoformat()


def backfill_dates(days: int | None = None) -> list[str]:
    n = max(1, days if days is not None else _env_int("SIM_OPENAI_COMPLIANCE_BACKFILL_DAYS", 28))
    today = utc_today()
    return [(today - timedelta(days=i)).date().isoformat() for i in range(n - 1, -1, -1)]


def hours_for_day(day: str, *, today: str, partial_hour: int | None = None) -> range:
    """UTC hours to emit for a ledger day (today may be partial)."""
    if day == today:
        end = partial_hour if partial_hour is not None else datetime.now(timezone.utc).hour
        return range(0, max(1, end + 1))
    return range(24)


@dataclass(frozen=True)
class ComplianceSeat:
    user_id: str
    email: str
    name: str
    workspace_id: str
    role: str
    status: str
    is_scim_managed: str = "true"


@dataclass(frozen=True)
class ComplianceWorkspace:
    workspace_id: str
    label: str


_FIRST = (
    "Alex", "Sam", "Jordan", "Taylor", "Riley", "Casey", "Morgan", "Quinn",
    "Avery", "Skyler", "Reese", "Drew", "Jamie", "Cameron", "Rowan", "Eli",
)
_LAST = (
    "Nguyen", "Patel", "Garcia", "Okafor", "Silva", "Kim", "Cohen", "Hansen",
    "Iyer", "Martinez", "Lindberg", "Fischer", "Tanaka", "Bernstein", "Duvidzon",
)


def _stable_token(seed: str, n: int = 24) -> str:
    digest = hashlib.sha256(seed.encode()).hexdigest()
    alphabet = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    out = []
    for i in range(0, n):
        out.append(alphabet[int(digest[i * 2 : i * 2 + 2], 16) % len(alphabet)])
    return "".join(out)


def default_organization() -> str:
    return os.environ.get("SIM_OPENAI_COMPLIANCE_ORG", "org-SimOpenAICompliance01").strip()


def shared_roster_seed(organization: str | None = None) -> str:
    """One seed for ChatGPT + Codex compliance sims.

    Both emit ``openai_compliance_user_state`` / ``_user_identity`` into the same
    tenant. Distinct seeds produce distinct ``workspace_id`` values, and the
    ChatGPT Users grid (unfiltered by subsystem) then shows every person twice.
    """
    org = (organization or default_organization()).strip()
    return f"openai-compliance:{org}"


def build_workspaces(count: int, *, seed: str) -> list[ComplianceWorkspace]:
    n = max(1, count)
    out: list[ComplianceWorkspace] = []
    for i in range(n):
        wid = str(
            __import__("uuid").UUID(hashlib.md5(f"{seed}:ws:{i}".encode()).hexdigest())
        )
        out.append(ComplianceWorkspace(workspace_id=wid, label=f"workspace-{i + 1}"))
    return out


def build_seats(
    workspaces: list[ComplianceWorkspace],
    *,
    users_per_workspace: int,
    seed: str,
    domain: str = "example.com",
) -> list[ComplianceSeat]:
    """Deterministic roster with roles/status mixes that light idle/orphan insights."""
    seats: list[ComplianceSeat] = []
    n = max(2, users_per_workspace)
    for wi, ws in enumerate(workspaces):
        for ui in range(n):
            idx = wi * n + ui
            # Index first/last independently so small rosters (≤ len(_FIRST))
            # do not all collapse onto _LAST[0].
            first = _FIRST[idx % len(_FIRST)]
            last = _LAST[idx % len(_LAST)]
            email = f"{first.lower()}.{last.lower()}{ui}@{domain}"
            name = f"{first} {last}"
            user_id = f"user-{_stable_token(f'{seed}:{email}', 24)}"
            if ui == 0:
                role = "account-owner"
            elif ui == 1:
                role = "account-admin"
            else:
                role = "standard-user"
            # Keep ~15% inactive so ChatGPT posture orphaned-automation insight can fire.
            status = "inactive" if ui % 7 == 6 else "active"
            seats.append(
                ComplianceSeat(
                    user_id=user_id,
                    email=email,
                    name=name,
                    workspace_id=ws.workspace_id,
                    role=role,
                    status=status,
                    is_scim_managed="true" if ui % 5 else "false",
                )
            )
    return seats


def day_rng(namespace: str, day: str) -> random.Random:
    return random.Random(f"{namespace}:{day}")


def metric_base(
    *,
    organization: str,
    cx_application_name: str,
    cx_subsystem_name: str,
) -> dict[str, str]:
    return {
        "organization": organization,
        "cx_application_name": cx_application_name,
        "cx_subsystem_name": cx_subsystem_name,
    }
