import os
import subprocess
import time

import pytest

from agent.swarm_live_runner import _monitor_process, _terminate_process_tree, start_live_swarm
from agent.swarm_state import RoutingPlan, SwarmJob
from agent.swarm_store import SwarmStore


def dummy_delegate(**kwargs):
    return {"results": [{"summary": "ok"}]}


def crash_delegate(**kwargs):
    os._exit(7)


def spawn_sleep_delegate(**kwargs):
    pid_path = str(kwargs["tasks"][0]["context"].splitlines()[0])
    child = subprocess.Popen(["/bin/sleep", "30"], start_new_session=False)
    with open(pid_path, "w", encoding="utf-8") as handle:
        handle.write(str(child.pid))
    time.sleep(30)
    return {"results": [{"summary": "late"}]}


def _plan():
    return RoutingPlan(mode="swarm", reason="test", suggested_tasks=[{"title": "research"}])


def _wait_for_status(store, job_id, status, *, timeout=3.0):
    deadline = time.monotonic() + timeout
    latest = None
    while time.monotonic() < deadline:
        latest = store.load_jobs().get(job_id)
        if latest and latest.metadata.get("live_delegation_status") == status:
            return latest
        time.sleep(0.02)
    assert latest is not None
    assert latest.metadata.get("live_delegation_status") == status
    return latest


def _process_exists(pid: int) -> bool:
    result = subprocess.run(["/bin/ps", "-p", str(pid)], capture_output=True, text=True, check=False)
    return result.returncode == 0


class StartFailProcess:
    pid = None

    def __init__(self, *args, **kwargs):
        pass

    def start(self):
        raise RuntimeError("cannot fork today")


class StartFailContext:
    def Process(self, *args, **kwargs):
        return StartFailProcess()


class FakeProcess:
    pid = 12345

    def __init__(self):
        self.terminated = False

    def terminate(self):
        self.terminated = True


class ExitedZeroProcess:
    pid = None
    exitcode = 0

    def join(self, timeout=None):
        return None

    def is_alive(self):
        return False


def test_process_tree_termination_does_not_signal_non_isolated_process_group(monkeypatch):
    from agent import swarm_live_runner

    calls = []
    proc = FakeProcess()
    monkeypatch.setattr(swarm_live_runner.os, "getpgid", lambda pid: os.getpgrp())
    monkeypatch.setattr(swarm_live_runner.os, "killpg", lambda pgid, sig: calls.append((pgid, sig)))

    _terminate_process_tree(proc)

    assert calls == []
    assert proc.terminated is True


def test_monitor_marks_zero_exit_without_terminal_save_as_failed(tmp_path):
    store = SwarmStore(base_dir=tmp_path)
    job = SwarmJob.create("research docs", created_at="2026-01-01T00:00:00+00:00")
    job.metadata["live_delegation_status"] = "running"
    store.save_job(job)

    _monitor_process(ExitedZeroProcess(), store_base_dir=str(tmp_path), job=job, timeout_seconds=1)

    saved = store.load_jobs()[job.job_id]
    assert saved.metadata["live_delegation_status"] == "failed"
    assert any(event.event_type == "live_delegation_crashed" for event in saved.audit)


def test_live_runner_falls_back_to_fork_for_non_pickleable_delegate(tmp_path):
    store = SwarmStore(base_dir=tmp_path)
    job = SwarmJob.create("research docs", created_at="2026-01-01T00:00:00+00:00")

    def local_delegate(**kwargs):
        return {"results": [{"summary": "ok"}]}

    handle = start_live_swarm(job, _plan(), local_delegate, store=store, max_children=1, timeout_seconds=2)

    assert handle.started is True
    saved = _wait_for_status(store, job.job_id, "executed", timeout=4.0)
    assert saved.metadata["live_delegation_context"] == "fork"


def test_live_runner_process_start_failure_persists_blocked_status(tmp_path, monkeypatch):
    from agent import swarm_live_runner

    monkeypatch.setattr(swarm_live_runner.multiprocessing, "get_context", lambda name: StartFailContext())
    store = SwarmStore(base_dir=tmp_path)
    job = SwarmJob.create("research docs", created_at="2026-01-01T00:00:00+00:00")
    plan = _plan()

    handle = start_live_swarm(job, plan, dummy_delegate, store=store, max_children=2, timeout_seconds=1)

    assert handle.started is False
    assert handle.reason == "process_start_failed"
    saved = store.load_jobs()[job.job_id]
    assert saved.metadata["live_delegation_status"] == "blocked"
    assert saved.metadata["live_delegation_block_reason"] == "process_start_failed"
    assert any(event.event_type == "live_delegation_blocked" for event in saved.audit)


def test_live_runner_missing_process_context_blocks_without_persisting_running(tmp_path, monkeypatch):
    from agent import swarm_live_runner

    def fail_context(name):
        raise ValueError("fork unavailable")

    monkeypatch.setattr(swarm_live_runner.multiprocessing, "get_context", fail_context)
    store = SwarmStore(base_dir=tmp_path)
    job = SwarmJob.create("research docs", created_at="2026-01-01T00:00:00+00:00")
    plan = _plan()

    handle = start_live_swarm(job, plan, dummy_delegate, store=store, max_children=2, timeout_seconds=1)

    assert handle.started is False
    assert handle.reason == "process_context_unavailable"
    assert store.load_jobs() == {}


@pytest.mark.live_system_guard_bypass
def test_live_runner_marks_crashed_child_failed_instead_of_stuck_running(tmp_path):
    store = SwarmStore(base_dir=tmp_path)
    job = SwarmJob.create("research docs", created_at="2026-01-01T00:00:00+00:00")

    handle = start_live_swarm(job, _plan(), crash_delegate, store=store, max_children=1, timeout_seconds=1)

    assert handle.started is True
    saved = _wait_for_status(store, job.job_id, "failed")
    assert any(event.event_type == "live_delegation_crashed" for event in saved.audit)


@pytest.mark.live_system_guard_bypass
def test_live_runner_timeout_kills_delegate_subprocess_tree(tmp_path):
    pid_path = tmp_path / "grandchild.pid"
    store = SwarmStore(base_dir=tmp_path)
    job = SwarmJob.create("research docs", created_at="2026-01-01T00:00:00+00:00")
    plan = RoutingPlan(mode="swarm", reason="test", suggested_tasks=[{"title": "research", "description": str(pid_path)}])

    handle = start_live_swarm(job, plan, spawn_sleep_delegate, store=store, max_children=1, timeout_seconds=0.5)

    assert handle.started is True
    saved = _wait_for_status(store, job.job_id, "timeout", timeout=4.0)
    assert any(event.event_type == "live_delegation_timeout" for event in saved.audit)
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline and not pid_path.exists():
        time.sleep(0.02)
    assert pid_path.exists()
    grandchild_pid = int(pid_path.read_text(encoding="utf-8"))
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline and _process_exists(grandchild_pid):
        time.sleep(0.05)
    assert not _process_exists(grandchild_pid)
