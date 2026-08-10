#!/usr/bin/env bash
# Run AI Center dashboard regression checks against a live Coralogix tenant via ``cx``.
#
# Usage:
#   bash scripts/run-dashboard-regression.sh
#   bash scripts/run-dashboard-regression.sh --sim claude
#   CX_PROFILE=default bash scripts/run-dashboard-regression.sh --sim copilot --sim gemini
#
# Catalog-only (no tenant):
#   bash scripts/run-dashboard-regression.sh --catalog-only
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

CATALOG_ONLY=0
PYTEST_ARGS=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --catalog-only)
      CATALOG_ONLY=1
      shift
      ;;
    *)
      PYTEST_ARGS+=("$1")
      shift
      ;;
  esac
done

if ! command -v python3 >/dev/null; then
  echo "python3 required" >&2
  exit 1
fi

python3 - <<'PY'
import importlib.util, sys
missing = [m for m in ("pytest", "yaml") if importlib.util.find_spec(m) is None]
if missing:
    print("Missing packages:", ", ".join(missing), file=sys.stderr)
    print("Install with: python3 -m pip install -r requirements-dev.txt", file=sys.stderr)
    sys.exit(1)
PY

if [[ "$CATALOG_ONLY" -eq 1 ]]; then
  exec python3 -m pytest -m catalog tests/dashboard_regression "${PYTEST_ARGS[@]}"
fi

if ! command -v cx >/dev/null; then
  echo "cx CLI not found on PATH. Install: curl -fsSL https://get.coralogix.dev/cli | sh" >&2
  exit 1
fi

echo "=== dashboard regression $(date -u +%Y-%m-%dT%H:%M:%SZ) profile=${CX_PROFILE:-<cx-default>} ==="
exec python3 -m pytest -m "catalog or live" tests/dashboard_regression -ra "${PYTEST_ARGS[@]}"
