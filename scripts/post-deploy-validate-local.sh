#!/usr/bin/env bash
# Tier-0 local checks — run immediately after deploy (no Coralogix tenant required).
#
# Usage:
#   bash scripts/post-deploy-validate-local.sh
#   WITH_K8S=1 KUBECTL_CONTEXT=arn:aws:eks:... K8S_NAMESPACE=codeagentsim bash scripts/post-deploy-validate-local.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
PY="${ROOT}/.venv/bin/python"
FAIL=0

if [[ ! -x "$PY" ]]; then
  PY=python3
fi

echo "=== post-deploy-validate-local $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="

echo "--- compile / import ---"
"$PY" -m compileall -q sim app.py
"$PY" <<'PY'
from sim.claude.dashboard import emit_claude_code_dashboard
from sim.copilot.cli import emit_copilot_cli_session
from sim.common.constants import (
    claude_prompt_for_session,
    claude_assistant_reply_for_session,
    copilot_prompt_reply_for_turn,
    CLAUDE_CODE_PROMPT_REPLY_PAIRS,
)
from sim.claude.meta import _claude_resolved_telemetry_profile
import hashlib
import os

os.environ["SIM_CLAUDE_TELEMETRY_PROFILE"] = "both"
sid = "post-deploy-check-session"
p = claude_prompt_for_session(sid)
r = claude_assistant_reply_for_session(sid)
idx = int(hashlib.sha256(f"{sid}:0".encode()).hexdigest(), 16) % len(CLAUDE_CODE_PROMPT_REPLY_PAIRS)
pair_p, pair_rs = CLAUDE_CODE_PROMPT_REPLY_PAIRS[idx]
assert p == pair_p and r == pair_rs[0], "Claude prompt/reply pair mismatch"
cp, cr = copilot_prompt_reply_for_turn(sid, 0)
assert cp and cr, "empty Copilot prompt/reply"
f1 = _claude_resolved_telemetry_profile(sid)
f2 = _claude_resolved_telemetry_profile(sid)
assert f1 == f2 and f1 in ("flat", "dotted"), "unstable subsystem routing"
print("imports + alignment + subsystem routing: ok")
PY

echo "--- validate_claude_dmbsm_shapes ---"
if ! "$PY" scripts/validate_claude_dmbsm_shapes.py; then
  FAIL=1
fi

echo "--- repo pool (no unknown) ---"
ROOT="$ROOT" "$PY" <<PY
import importlib.util
import os
import sys
import types
from pathlib import Path

ROOT = Path(os.environ["ROOT"])
sys.path.insert(0, str(ROOT))
identity_stub = types.ModuleType("sim.common.identity")
identity_stub._CORALOGIX_TEAM_USERS = [
    {"user.email": f"user{i}@coralogix.com"} for i in range(100)
]
sys.modules["sim.common.identity"] = identity_stub
spec = importlib.util.spec_from_file_location(
    "sim.common.repos", ROOT / "sim/common/repos.py"
)
repos = importlib.util.module_from_spec(spec)
sys.modules["sim.common.repos"] = repos
spec.loader.exec_module(repos)

unknown = 0
managed_playground = 0
for i in range(2000):
    names = repos.sim_session_repository_names(
        f"sid-{i}",
        {"user.email": f"user{i}@coralogix.com"},
        agent_product="claude_code",
    )
    for n in names:
        if n == "unknown":
            unknown += 1
        if n == "coralogix/cxai-observability-demo-playground":
            managed_playground += 1
assert unknown == 0, f"found {unknown} unknown repository_name values"
assert managed_playground > 0, "expected managed playground repo in sample"
print(f"repo pool: ok (unknown=0, playground hits={managed_playground})")
PY

if [[ -n "${WITH_K8S:-}" ]]; then
  echo "--- verify-rollout (K8s) ---"
  if ! bash "${ROOT}/scripts/verify-rollout.sh"; then
    FAIL=1
  fi
fi

echo ""
echo "Next steps:"
echo "  1. Record deploy_start UTC in docs/post-deploy-validation.md"
echo "  2. Wait soak (30m quick / 120m full) — scripts/phase4-validate-after-soak.sh"
echo "  3. Run Tier 2–3 queries on user-ob-coralogix-server (obdev)"
echo "  4. Fill results log in docs/post-deploy-validation.md"

if (( FAIL )); then
  echo "FAIL: one or more local checks failed" >&2
  exit 1
fi
echo "OK: Tier 0 local validation passed"
