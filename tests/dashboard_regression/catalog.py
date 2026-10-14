"""Load per-sim dashboard regression catalogs from YAML."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Literal

import yaml

CATALOG_DIR = Path(__file__).resolve().parent / "catalogs"

Kind = Literal["dataprime", "promql"]
Expect = Literal["has_rows", "has_nonzero", "source_exists"]


@dataclass(frozen=True)
class Check:
    sim: str
    id: str
    title: str
    kind: Kind
    query: str
    expect: Expect
    window: str = "24h"
    limit: int = 20
    notes: str = ""
    # DataPrime storage tier: frequent | archive | None (cx profile default).
    tier: str | None = None
    # For span/log rows: require these attribute/tag keys to be present & non-empty
    # on at least one returned record (inspects JSON tags / attributes client-side).
    require_tags: tuple[str, ...] = ()
    # When set, pytest.xfail until tenant/product/sim fix lands.
    xfail_reason: str | None = None

    @property
    def start(self) -> str:
        return f"now-{self.window}"


def _parse_check(sim: str, raw: dict[str, Any]) -> Check:
    kind = raw["kind"]
    expect = raw.get("expect") or ("has_nonzero" if kind == "promql" else "has_rows")
    if kind not in ("dataprime", "promql"):
        raise ValueError(f"{sim}/{raw.get('id')}: invalid kind {kind!r}")
    if expect not in ("has_rows", "has_nonzero", "source_exists"):
        raise ValueError(f"{sim}/{raw.get('id')}: invalid expect {expect!r}")
    tier = raw.get("tier")
    if tier is not None:
        tier = str(tier)
        if tier not in ("frequent", "archive"):
            raise ValueError(f"{sim}/{raw.get('id')}: invalid tier {tier!r}")
    require_tags = tuple(str(t) for t in (raw.get("require_tags") or []))
    return Check(
        sim=sim,
        id=str(raw["id"]),
        title=str(raw.get("title") or raw["id"]),
        kind=kind,  # type: ignore[arg-type]
        query=str(raw["query"]).strip(),
        expect=expect,  # type: ignore[arg-type]
        window=str(raw.get("window") or "24h"),
        limit=int(raw.get("limit") or 20),
        notes=str(raw.get("notes") or ""),
        tier=tier,
        require_tags=require_tags,
        xfail_reason=(str(raw["xfail"]) if raw.get("xfail") else None),
    )


def load_catalog(path: Path) -> list[Check]:
    data = yaml.safe_load(path.read_text()) or {}
    sim = str(data.get("sim") or path.stem)
    checks = [_parse_check(sim, c) for c in (data.get("checks") or [])]
    if not checks:
        raise ValueError(f"catalog {path} has no checks")
    return checks


def iter_catalogs(directory: Path | None = None) -> Iterator[tuple[str, list[Check]]]:
    root = directory or CATALOG_DIR
    for path in sorted(root.glob("*.yaml")):
        checks = load_catalog(path)
        yield checks[0].sim, checks


def all_checks(directory: Path | None = None) -> list[Check]:
    out: list[Check] = []
    for _sim, checks in iter_catalogs(directory):
        out.extend(checks)
    return out
