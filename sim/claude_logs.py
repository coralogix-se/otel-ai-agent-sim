"""Claude Code OTLP log attribute shapes (flat snake_case vs dotted keys)."""
from __future__ import annotations

from sim.common import _cx_log_record_attrs


def _cc_claude_log_attributes_flat(
    base: dict,
    *,
    event_name: str,
    event_sequence: int,
    event_timestamp_iso: str,
    cx_app: str,
    cx_sub: str,
    extra: dict,
) -> dict:
    """
    Coralogix-style log attributes for Claude Code (matches EU2 ``claude_code.api_request`` exports:
    snake_case keys; token counts, ``cost_usd``, and ``duration_ms`` as strings in JSON).
    """
    session_id = str(base.get("session.id", ""))
    user_email = str(base.get("user.email", ""))
    out: dict[str, str | int | float] = {
        **_cx_log_record_attrs(cx_app, cx_sub),
        "organization_id": str(base.get("organization.id", "")),
        # Flat snake_case (Coralogix JSON exports) plus dotted aliases for ``ai_sessions_claude``
        # / AI Center queries: ``firstNonNull(...['session.id'], ...['session_id'])`` and ``user.email``.
        "session_id": session_id,
        "session.id": session_id,
        "user_account_uuid": str(base.get("user.account_uuid", "")),
        "user_account_id": str(base.get("user.account.id", "")),
        "user_id": str(base.get("user.id", "")),
        "user_email": user_email,
        "user.email": user_email,
        "terminal_type": str(base.get("terminal.type", "")),
        "event_name": event_name,
        "event_sequence": event_sequence,
        "event_timestamp": event_timestamp_iso,
    }
    _str_int_keys = frozenset(
        {
            "input_tokens",
            "output_tokens",
            "cache_read_tokens",
            "cache_creation_tokens",
        }
    )
    for k, v in extra.items():
        if v is None:
            continue
        if k == "duration_ms" and isinstance(v, (int, float)):
            out[k] = str(int(v))
        elif k in _str_int_keys and isinstance(v, (int, float)):
            out[k] = str(int(v))
        elif k == "cost_usd":
            out[k] = str(v) if isinstance(v, str) else str(float(v))
        elif k in ("prompt_length", "body_length"):
            out[k] = str(v)
        elif k == "cost_usd_micros" and isinstance(v, (int, float)):
            out[k] = str(int(v))
        else:
            out[k] = v
    return out


def _cc_claude_log_attributes_dotted(
    base: dict,
    *,
    event_name: str,
    event_sequence: int,
    event_timestamp_iso: str,
    cx_app: str,
    cx_sub: str,
    extra: dict,
) -> dict[str, object]:
    """
    Log attributes with dotted keys for event/session/user/org/terminal/prompt/request,
    snake_case numeric counters for tokens and cost.
    """
    out: dict[str, object] = {
        **_cx_log_record_attrs(cx_app, cx_sub),
        "organization.id": str(base.get("organization.id", "")),
        "session.id": str(base.get("session.id", "")),
        "user.account_uuid": str(base.get("user.account_uuid", "")),
        "user.account_id": str(base.get("user.account.id", "")),
        "user.id": str(base.get("user.id", "")),
        "user.email": str(base.get("user.email", "")),
        "terminal.type": str(base.get("terminal.type", "")),
        "event.name": event_name,
        "event.sequence": int(event_sequence),
        "event.timestamp": event_timestamp_iso,
    }
    _rename = {"prompt_id": "prompt.id", "request_id": "request.id"}
    _numeric = frozenset(
        {
            "input_tokens",
            "output_tokens",
            "cache_read_tokens",
            "cache_creation_tokens",
            "duration_ms",
            "tool_result_size_bytes",
            "attempt",
            "cost_usd_micros",
        }
    )
    _string_keys = frozenset({"prompt_length", "body_length"})
    for k, v in extra.items():
        if v is None:
            continue
        nk = _rename.get(k, k)
        if k in _string_keys:
            out[nk] = str(v)
        elif k == "cost_usd":
            out[nk] = float(v) if not isinstance(v, str) else float(v)
        elif k in _numeric and isinstance(v, (int, float)):
            out[nk] = int(v)
        elif k in _numeric and isinstance(v, str) and k != "cost_usd":
            try:
                out[nk] = int(v)
            except ValueError:
                out[nk] = v
        else:
            out[nk] = v
    return out


def _cc_claude_log_record_attrs(
    base: dict,
    *,
    event_name: str,
    event_sequence: int,
    event_timestamp_iso: str,
    cx_app: str,
    cx_sub: str,
    extra: dict,
    profile: str,
) -> dict[str, str | int | float] | dict[str, object]:
    if profile == "dotted":
        return _cc_claude_log_attributes_dotted(
            base,
            event_name=event_name,
            event_sequence=event_sequence,
            event_timestamp_iso=event_timestamp_iso,
            cx_app=cx_app,
            cx_sub=cx_sub,
            extra=extra,
        )
    return _cc_claude_log_attributes_flat(
        base,
        event_name=event_name,
        event_sequence=event_sequence,
        event_timestamp_iso=event_timestamp_iso,
        cx_app=cx_app,
        cx_sub=cx_sub,
        extra=extra,
    )
