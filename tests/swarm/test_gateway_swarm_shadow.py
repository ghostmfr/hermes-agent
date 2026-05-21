import json
import logging
import time

import pytest

from gateway.builtin_hooks import swarm_operator
from gateway.hooks import HookRegistry


class FailingStore:
    def save_job(self, job):
        raise RuntimeError("disk full")

    def append_event(self, event):  # pragma: no cover - save fails first
        raise AssertionError("should not append")


def live_delegate_success(**kwargs):
    return {
        "results": [
            {
                "summary": "researched",
                "claims": ["docs reviewed"],
                "evidence": [{"kind": "artifact", "path": "/tmp/research.md"}],
            },
            {
                "summary": "reviewed",
                "claims": ["code reviewed"],
                "evidence": [{"kind": "artifact", "path": "/tmp/review.md"}],
            },
        ]
    }


def live_delegate_slow(**kwargs):
    time.sleep(1.0)
    return {"results": [{"summary": "late"}]}


def _context(tmp_path, enabled=True):
    from agent.swarm_store import SwarmStore

    return {
        "platform": "slack",
        "user_id": "U123",
        "chat_id": "C123",
        "session_id": "S123",
        "message": "Research docs and review code",
        "message_id": "M123",
        "gateway_config": {"swarm_operator": {"enabled": enabled, "dry_run": True, "max_children": 3}},
        "swarm_store": SwarmStore(base_dir=tmp_path),
    }


def _read_job(tmp_path):
    data = json.loads((tmp_path / "swarm_operator_state.json").read_text())
    return next(iter(data["jobs"].values()))


def _wait_for_live_status(tmp_path, status, timeout=2.0):
    deadline = time.monotonic() + timeout
    last_job = None
    while time.monotonic() < deadline:
        last_job = _read_job(tmp_path)
        if last_job["metadata"].get("live_delegation_status") == status:
            return last_job
        time.sleep(0.02)
    assert last_job is not None
    assert last_job["metadata"].get("live_delegation_status") == status
    return last_job


def test_hook_receives_inbound_metadata_and_message_text(tmp_path):
    swarm_operator.handle("agent:start", _context(tmp_path, enabled=True))

    data = json.loads((tmp_path / "swarm_operator_state.json").read_text())
    job = next(iter(data["jobs"].values()))
    assert job["original_request"] == "Research docs and review code"
    assert job["platform"] == "slack"
    assert job["user_id"] == "U123"
    assert job["chat_id"] == "C123"
    assert job["session_id"] == "S123"
    assert job["metadata"]["message_id"] == "M123"


def test_hook_disabled_does_nothing(tmp_path):
    swarm_operator.handle("agent:start", _context(tmp_path, enabled=False))

    assert not (tmp_path / "swarm_operator_state.json").exists()
    assert not (tmp_path / "swarm_operator_metrics.jsonl").exists()


def test_hook_enabled_dry_run_writes_job_and_metrics_event(tmp_path):
    swarm_operator.handle("agent:start", _context(tmp_path, enabled=True))

    state = json.loads((tmp_path / "swarm_operator_state.json").read_text())
    job = next(iter(state["jobs"].values()))
    assert job["metadata"]["dry_run"] is True
    assert job["routing_plan"]["mode"] == "swarm"

    metrics = [json.loads(line) for line in (tmp_path / "swarm_operator_metrics.jsonl").read_text().splitlines()]
    assert metrics[-1]["event_type"] == "shadow_job_recorded"
    assert metrics[-1]["metadata"]["job_id"] == job["job_id"]
    assert metrics[-1]["metadata"]["live_delegation_enabled"] is False


def test_hook_non_dry_run_without_live_gate_still_does_not_dispatch(tmp_path):
    calls = []
    context = _context(tmp_path, enabled=True)
    context["gateway_config"]["swarm_operator"]["dry_run"] = False
    context["swarm_delegate_fn"] = lambda **kwargs: calls.append(kwargs)

    swarm_operator.handle("agent:start", context)

    assert calls == []
    data = json.loads((tmp_path / "swarm_operator_state.json").read_text())
    job = next(iter(data["jobs"].values()))
    assert job["metadata"]["dry_run"] is False
    assert job["metadata"]["live_delegation_enabled"] is False
    assert any(event["event_type"] == "live_delegation_blocked" for event in job["audit"])
    assert job["metadata"]["live_delegation_status"] == "blocked"
    assert job["metadata"]["live_delegation_block_reason"] == "live_delegation_disabled"


def test_hook_live_delegation_requires_explicit_double_opt_in_and_safe_runner(tmp_path):
    context = _context(tmp_path, enabled=True)
    context["gateway_config"]["swarm_operator"].update({"dry_run": False, "live_delegation_enabled": True, "max_children": 2})
    context["swarm_delegate_fn"] = live_delegate_success

    started = time.monotonic()
    swarm_operator.handle("agent:start", context)
    elapsed = time.monotonic() - started

    assert elapsed < 0.5
    job = _wait_for_live_status(tmp_path, "executed")
    assert job["metadata"]["live_delegation_enabled"] is True
    assert job["metadata"]["swarm_execution"]["status"] in {"completed", "partially_completed"}
    assert any(event["event_type"] == "live_delegation_executed" for event in job["audit"])


def test_hook_live_delegation_enabled_without_delegate_fn_blocks_instead_of_crashing(tmp_path):
    context = _context(tmp_path, enabled=True)
    context["gateway_config"]["swarm_operator"].update({"dry_run": False, "live_delegation_enabled": True})

    swarm_operator.handle("agent:start", context)

    data = json.loads((tmp_path / "swarm_operator_state.json").read_text())
    job = next(iter(data["jobs"].values()))
    assert job["metadata"]["live_delegation_enabled"] is True
    assert job["metadata"]["live_delegation_status"] == "blocked"
    assert job["metadata"]["live_delegation_block_reason"] == "missing_delegate_fn"
    assert any(
        event["event_type"] == "live_delegation_blocked"
        and event["metadata"].get("reason") == "missing_delegate_fn"
        for event in job["audit"]
    )


@pytest.mark.live_system_guard_bypass
def test_hook_live_delegation_timeout_kills_child_process(tmp_path):
    context = _context(tmp_path, enabled=True)
    context["gateway_config"]["swarm_operator"].update(
        {"dry_run": False, "live_delegation_enabled": True, "live_delegation_timeout_seconds": 0.05}
    )
    context["swarm_delegate_fn"] = live_delegate_slow

    started = time.monotonic()
    swarm_operator.handle("agent:start", context)
    elapsed = time.monotonic() - started

    assert elapsed < 0.5
    job = _wait_for_live_status(tmp_path, "timeout", timeout=3.0)
    assert job["metadata"]["live_delegation_timeout_seconds"] == 0.05
    assert any(event["event_type"] == "live_delegation_timeout" for event in job["audit"])


def test_hook_live_delegation_blocks_when_only_truncated_message_preview_is_available(tmp_path):
    calls = []
    context = _context(tmp_path, enabled=True)
    context["message"] = "Research docs and " + ("x" * 600)
    context["message_truncated"] = True
    context["gateway_config"]["swarm_operator"].update({"dry_run": False, "live_delegation_enabled": True})
    context["swarm_delegate_fn"] = lambda **kwargs: calls.append(kwargs)

    swarm_operator.handle("agent:start", context)

    assert calls == []
    data = json.loads((tmp_path / "swarm_operator_state.json").read_text())
    job = next(iter(data["jobs"].values()))
    assert job["metadata"]["message_truncated"] is True
    assert job["metadata"]["live_delegation_status"] == "blocked"
    assert job["metadata"]["live_delegation_block_reason"] == "truncated_message"


def test_hook_live_delegation_uses_full_message_when_gateway_supplies_it(tmp_path):
    full_message = "Research docs and review code " + ("full-context " * 80)
    context = _context(tmp_path, enabled=True)
    context["message"] = full_message[:500]
    context["message_full"] = full_message
    context["message_truncated"] = True
    context["gateway_config"]["swarm_operator"].update({"dry_run": False, "live_delegation_enabled": True})
    context["swarm_delegate_fn"] = live_delegate_success

    swarm_operator.handle("agent:start", context)

    job = _wait_for_live_status(tmp_path, "executed")
    assert job["original_request"] == full_message
    assert job["metadata"]["message_truncated"] is False


def test_hook_honcho_summary_uses_injected_writer_only_when_enabled(tmp_path):
    calls = []
    context = _context(tmp_path, enabled=True)
    context["gateway_config"]["swarm_operator"]["persist_to_honcho"] = True
    context["swarm_honcho_writer"] = lambda payload: calls.append(payload)

    swarm_operator.handle("agent:start", context)

    assert len(calls) == 1
    assert calls[0]["metadata"]["job_id"]
    state = json.loads((tmp_path / "swarm_operator_state.json").read_text())
    job = next(iter(state["jobs"].values()))
    assert job["metadata"]["honcho_summary"]["persisted"] is True


def test_hook_honcho_summary_not_called_when_disabled(tmp_path):
    calls = []
    context = _context(tmp_path, enabled=True)
    context["swarm_honcho_writer"] = lambda payload: calls.append(payload)

    swarm_operator.handle("agent:start", context)

    assert calls == []


def test_hook_never_blocks_normal_flow_on_store_failure(caplog):
    context = _context(tmp_path="/tmp/unused", enabled=True)
    context["swarm_store"] = FailingStore()

    with caplog.at_level(logging.WARNING):
        result = swarm_operator.handle("agent:start", context)

    assert result is None
    assert "swarm operator shadow hook failed" in caplog.text


def test_builtin_hook_registry_registers_swarm_operator_default_disabled(tmp_path):
    registry = HookRegistry(include_builtins=True)
    registry.discover_and_load()

    assert any(hook["name"] == "swarm_operator" for hook in registry.loaded_hooks)
    # Default config omitted means disabled; emitting must not create runtime state.
    import asyncio

    asyncio.run(registry.emit("agent:start", {"message": "hello", "swarm_store": _context(tmp_path)["swarm_store"]}))
    assert not (tmp_path / "swarm_operator_state.json").exists()
