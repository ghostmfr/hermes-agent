import json

import pytest

from agent.swarm_state import AuditEvent, SwarmJob
from agent.swarm_store import SwarmStore, SwarmStoreError


def test_store_writes_job_snapshot(tmp_path):
    store = SwarmStore(base_dir=tmp_path)
    job = SwarmJob.create("hello", created_at="2026-05-21T00:00:00+00:00")

    store.save_job(job)

    state_path = tmp_path / "swarm_operator_state.json"
    assert state_path.exists()
    data = json.loads(state_path.read_text())
    assert data["jobs"][job.job_id]["original_request"] == "hello"


def test_store_appends_jsonl_metrics(tmp_path):
    store = SwarmStore(base_dir=tmp_path)
    store.append_event(AuditEvent("shadow", "recorded", metadata={"job_id": "j1"}))
    store.append_event({"event_type": "shadow2", "metadata": {"job_id": "j2"}})

    lines = (tmp_path / "swarm_operator_metrics.jsonl").read_text().splitlines()
    assert [json.loads(line)["event_type"] for line in lines] == ["shadow", "shadow2"]


def test_store_loads_existing_jobs_on_restart(tmp_path):
    store = SwarmStore(base_dir=tmp_path)
    job = SwarmJob.create("persist me")
    job.add_task("task")
    store.save_job(job)

    reloaded = SwarmStore(base_dir=tmp_path).load_jobs()

    assert list(reloaded) == [job.job_id]
    assert reloaded[job.job_id].to_dict() == job.to_dict()


def test_store_handles_missing_file_as_empty(tmp_path):
    assert SwarmStore(base_dir=tmp_path).load_jobs() == {}


def test_store_raises_explicit_error_on_corrupt_state(tmp_path):
    state_path = tmp_path / "swarm_operator_state.json"
    state_path.write_text("{not json", encoding="utf-8")

    with pytest.raises(SwarmStoreError, match="Corrupt swarm state file"):
        SwarmStore(base_dir=tmp_path).load_jobs()


def test_store_raises_explicit_error_on_invalid_shape(tmp_path):
    state_path = tmp_path / "swarm_operator_state.json"
    state_path.write_text(json.dumps({"jobs": []}), encoding="utf-8")

    with pytest.raises(SwarmStoreError, match="Invalid swarm jobs shape"):
        SwarmStore(base_dir=tmp_path).load_jobs()
