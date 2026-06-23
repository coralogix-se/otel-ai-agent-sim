"""Back-compat re-exports — shared repo logic lives in ``sim.common.repos``."""

from sim.common.repos import (  # noqa: F401
    claude_org_repos,
    claude_rogue_user_emails,
    claude_rogue_user_roster_indices,
    claude_rogue_user_token_multiplier,
    claude_session_repository_names,
    is_claude_rogue_user,
    is_claude_unmanaged_power_user,
    is_sim_personal_repo_violator,
    sim_org_repos,
    sim_personal_repo_violator_emails,
    sim_personal_violation_repository,
    sim_rogue_user_emails,
    sim_rogue_user_roster_indices,
    sim_rogue_user_token_multiplier,
    sim_session_repository_names,
)
from sim.copilot.repos import (  # noqa: F401
    copilot_git_otel_attrs_from_repo_short,
    copilot_primary_session_git_attrs,
    copilot_session_git_repo_segments,
)
