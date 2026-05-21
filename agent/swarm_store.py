"""Profile-safe JSON/JSONL store for swarm operator shadow state."""

from __future__ import annotations

import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional

from hermes_constants import get_hermes_home

from agent.swarm_state import AuditEvent, SwarmJob


class SwarmStoreError(RuntimeError):
    """Raised when swarm state cannot be loaded without risking data loss."""


class SwarmStore:
    """Persist swarm jobs to a small state snapshot plus append-only metrics.

    ``base_dir`` is primarily for tests. Production callers should omit it so
    paths resolve beneath ``get_hermes_home() / "state"`` and respect profiles.
    """

    def __init__(self, base_dir: Optional[Path | str] = None):
        self.base_dir = Path(base_dir) if base_dir is not None else get_hermes_home() / "state"
        self.state_path = self.base_dir / "swarm_operator_state.json"
        self.metrics_path = self.base_dir / "swarm_operator_metrics.jsonl"

    def _ensure_dir(self) -> None:
        self.base_dir.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def _locked(self):
        """Best-effort interprocess lock around read-modify-write store ops."""
        self._ensure_dir()
        lock_path = self.base_dir / ".swarm_operator_state.lock"
        with lock_path.open("a+", encoding="utf-8") as handle:
            if os.name == "posix":
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            else:  # pragma: no cover - Windows CI coverage is platform-specific.
                yield

    def load_jobs(self) -> Dict[str, SwarmJob]:
        if not self.state_path.exists():
            return {}
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise SwarmStoreError(f"Corrupt swarm state file: {self.state_path}: {exc}") from exc
        except OSError as exc:
            raise SwarmStoreError(f"Unable to read swarm state file: {self.state_path}: {exc}") from exc

        if not isinstance(data, dict):
            raise SwarmStoreError(f"Invalid swarm state file shape: {self.state_path}")
        raw_jobs = data.get("jobs", {})
        if not isinstance(raw_jobs, dict):
            raise SwarmStoreError(f"Invalid swarm jobs shape: {self.state_path}")
        jobs: Dict[str, SwarmJob] = {}
        for job_id, raw_job in raw_jobs.items():
            if not isinstance(raw_job, dict):
                raise SwarmStoreError(f"Invalid swarm job {job_id!r} in {self.state_path}")
            job = SwarmJob.from_dict(raw_job)
            jobs[job.job_id] = job
        return jobs

    def save_job(self, job: SwarmJob) -> None:
        with self._locked():
            jobs = self.load_jobs()
            jobs[job.job_id] = job
            payload = {
                "version": 1,
                "jobs": {job_id: item.to_dict() for job_id, item in sorted(jobs.items())},
            }
            fd, tmp_name = tempfile.mkstemp(prefix=f".{self.state_path.name}.", suffix=".tmp", dir=str(self.base_dir))
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
                    handle.write("\n")
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(tmp_name, self.state_path)
            except Exception:
                try:
                    os.unlink(tmp_name)
                except OSError:
                    pass
                raise

    def append_event(self, event: AuditEvent | Dict[str, Any]) -> None:
        with self._locked():
            if isinstance(event, AuditEvent):
                payload = event.to_dict()
            else:
                payload = dict(event)
            with self.metrics_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")


__all__ = ["SwarmStore", "SwarmStoreError"]
