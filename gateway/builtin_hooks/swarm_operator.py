"""Shadow-mode gateway hook for the Jeeves swarm operator.

The hook is deliberately observe-only in Sprint 1. When disabled (the default)
it does nothing. When enabled with dry_run=true it creates a SwarmJob, attaches
a deterministic routing plan, and persists JSON/JSONL audit records without
altering gateway response flow.
"""

from __future__ import annotations

import logging
import math
from typing import Any, Dict, Optional

from agent.swarm_honcho import persist_swarm_honcho_summary
from agent.swarm_live_runner import start_live_swarm
from agent.swarm_router import route_request
from agent.swarm_state import AuditEvent, SwarmJob
from agent.swarm_store import SwarmStore

logger = logging.getLogger(__name__)

DEFAULT_CONFIG: Dict[str, Any] = {
    "enabled": False,
    "dry_run": True,
    "live_delegation_enabled": False,
    "live_delegation_timeout_seconds": 30.0,
    "max_children": 3,
    "persist_to_honcho": False,
    "honcho_summary_enabled": False,
}


def _get_value(config: Any, key: str, default: Any = None) -> Any:
    if isinstance(config, dict):
        return config.get(key, default)
    return getattr(config, key, default)


def _strict_bool(value: Any, *, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes", "on"}:
            return True
        if lowered in {"false", "0", "no", "off"}:
            return False
    if isinstance(value, (int, float)) and value in {0, 1}:
        return bool(value)
    return default


def _swarm_config(config: Any) -> Dict[str, Any]:
    raw = _get_value(config, "swarm_operator", {}) if config is not None else {}
    if not isinstance(raw, dict):
        raw = {}
    merged = dict(DEFAULT_CONFIG)
    merged.update(raw)
    merged["enabled"] = _strict_bool(merged.get("enabled", False), default=False)
    merged["dry_run"] = _strict_bool(merged.get("dry_run", True), default=True)
    merged["live_delegation_enabled"] = _strict_bool(merged.get("live_delegation_enabled", False), default=False)
    try:
        merged["live_delegation_timeout_seconds"] = float(merged.get("live_delegation_timeout_seconds", 30.0))
    except (TypeError, ValueError):
        merged["live_delegation_timeout_seconds"] = 30.0
    if not math.isfinite(merged["live_delegation_timeout_seconds"]) or not (0 < merged["live_delegation_timeout_seconds"] <= 30.0):
        merged["live_delegation_timeout_seconds"] = 30.0
    try:
        merged["max_children"] = int(merged.get("max_children", 3))
    except (TypeError, ValueError):
        merged["max_children"] = 3
    merged["max_children"] = max(1, min(10, merged["max_children"]))
    merged["persist_to_honcho"] = _strict_bool(merged.get("persist_to_honcho", False), default=False)
    merged["honcho_summary_enabled"] = _strict_bool(merged.get("honcho_summary_enabled", False), default=False)
    return merged


def handle(event_type: str, context: Optional[Dict[str, Any]] = None) -> None:
    if event_type != "agent:start":
        return
    ctx = context or {}
    cfg = _swarm_config(ctx.get("gateway_config") or ctx.get("config"))
    if not cfg["enabled"]:
        return

    try:
        preview_message = str(ctx.get("message") or "")
        full_message = ctx.get("message_full")
        message = str(full_message) if isinstance(full_message, str) else preview_message
        message_truncated = bool(ctx.get("message_truncated", False)) and not isinstance(full_message, str)
        job = SwarmJob.create(
            message,
            platform=str(ctx.get("platform") or ""),
            user_id=str(ctx.get("user_id") or ""),
            chat_id=str(ctx.get("chat_id") or ""),
            session_id=str(ctx.get("session_id") or ""),
            metadata={
                "dry_run": cfg["dry_run"],
                "live_delegation_enabled": cfg["live_delegation_enabled"],
                "max_children": cfg["max_children"],
                "persist_to_honcho": cfg["persist_to_honcho"],
                "honcho_summary_enabled": cfg["honcho_summary_enabled"],
                "message_id": ctx.get("message_id"),
                "message_truncated": message_truncated,
            },
        )
        plan = route_request(
            message,
            platform_context={
                "platform": job.platform,
                "user_id": job.user_id,
                "chat_id": job.chat_id,
                "session_id": job.session_id,
            },
            config=cfg,
        )
        job.routing_plan = plan
        job.audit.append(
            AuditEvent(
                "shadow_routed",
                "Swarm operator shadow route recorded.",
                metadata={
                    "mode": plan.mode,
                    "dry_run": cfg["dry_run"],
                    "live_delegation_enabled": cfg["live_delegation_enabled"],
                },
            )
        )
        store = ctx.get("swarm_store") or SwarmStore()

        should_dispatch_live = bool(cfg["enabled"] and not cfg["dry_run"] and cfg["live_delegation_enabled"])
        live_runner_started = False
        if should_dispatch_live:
            delegate_fn = ctx.get("swarm_delegate_fn")
            if message_truncated:
                job.metadata["live_delegation_status"] = "blocked"
                job.metadata["live_delegation_block_reason"] = "truncated_message"
                job.audit.append(
                    AuditEvent(
                        "live_delegation_blocked",
                        "Live delegation requires the full user message; only a truncated preview was available.",
                        metadata={"reason": "truncated_message"},
                    )
                )
            elif not callable(delegate_fn):
                job.metadata["live_delegation_status"] = "blocked"
                job.metadata["live_delegation_block_reason"] = "missing_delegate_fn"
                job.audit.append(
                    AuditEvent(
                        "live_delegation_blocked",
                        "Live delegation gate was enabled, but no delegate function was available.",
                        metadata={"reason": "missing_delegate_fn"},
                    )
                )
            else:
                handle = start_live_swarm(
                    job,
                    plan,
                    delegate_fn,
                    store=store,
                    max_children=cfg["max_children"],
                    timeout_seconds=cfg["live_delegation_timeout_seconds"],
                )
                live_runner_started = handle.started
                if not handle.started:
                    job.metadata["live_delegation_status"] = "blocked"
                    job.metadata["live_delegation_block_reason"] = handle.reason
                    job.audit.append(
                        AuditEvent(
                            "live_delegation_blocked",
                            "Swarm live delegation runner could not start.",
                            metadata=handle.to_dict(),
                        )
                    )
        elif not cfg["dry_run"]:
            job.metadata["live_delegation_status"] = "blocked"
            job.metadata["live_delegation_block_reason"] = "live_delegation_disabled"
            job.audit.append(
                AuditEvent(
                    "live_delegation_blocked",
                    "Swarm operator stayed shadow-only because live delegation is not explicitly enabled.",
                    metadata={"reason": "live_delegation_disabled"},
                )
            )

        if not live_runner_started:
            store.save_job(job)
        store.append_event(
            AuditEvent(
                "shadow_job_recorded",
                "Swarm operator shadow job persisted.",
                metadata={
                    "job_id": job.job_id,
                    "mode": plan.mode,
                    "platform": job.platform,
                    "session_id": job.session_id,
                    "dry_run": cfg["dry_run"],
                    "live_delegation_enabled": cfg["live_delegation_enabled"],
                    "job_status": job.status,
                    "live_delegation_status": job.metadata.get("live_delegation_status"),
                    "live_delegation_block_reason": job.metadata.get("live_delegation_block_reason"),
                },
            )
        )
        if (cfg["persist_to_honcho"] or cfg["honcho_summary_enabled"]) and not live_runner_started:
            honcho_result = persist_swarm_honcho_summary(
                job,
                enabled=True,
                writer=ctx.get("swarm_honcho_writer"),
            )
            job.metadata["honcho_summary"] = honcho_result
            store.save_job(job)
    except Exception as exc:
        logger.warning("swarm operator shadow hook failed: %s", exc)


__all__ = ["DEFAULT_CONFIG", "handle"]
