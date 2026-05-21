import json
from types import SimpleNamespace
from typing import Any, cast

from gateway.config import Platform
from gateway.run import GatewayRunner
from gateway.session import SessionSource


class _Hooks:
    def __init__(self):
        self.calls = []

    async def emit(self, event_type, context):
        self.calls.append((event_type, context))


def test_swarm_delegate_fn_bridges_to_delegate_task_with_parent_agent(monkeypatch):
    runner = GatewayRunner.__new__(GatewayRunner)
    parent_agent = object()
    captured = {}

    def fake_delegate_task(*, tasks, parent_agent):
        captured["tasks"] = tasks
        captured["parent_agent"] = parent_agent
        return json.dumps({"results": [{"status": "completed"}]})

    monkeypatch.setattr("tools.delegate_tool.delegate_task", fake_delegate_task)

    delegate_fn = runner._make_swarm_delegate_fn(parent_agent)
    result = delegate_fn(tasks=[{"goal": "Verify smoke"}])

    assert json.loads(result)["results"][0]["status"] == "completed"
    assert captured == {"tasks": [{"goal": "Verify smoke"}], "parent_agent": parent_agent}


def test_agent_start_hook_context_includes_live_swarm_delegate_fn():
    runner = cast(Any, GatewayRunner.__new__(GatewayRunner))
    runner.hooks = _Hooks()
    runner.config = SimpleNamespace(swarm_operator={"enabled": True, "dry_run": False, "live_delegation_enabled": True})
    source = SessionSource(platform=Platform.SLACK, chat_id="C123", user_id="U123")
    agent = object()

    runner._emit_agent_start_hook_sync(
        source=source,
        session_id="sess-1",
        message="review code and test the result",
        event_message_id="123.456",
        agent=agent,
    )

    assert len(runner.hooks.calls) == 1
    event_type, context = runner.hooks.calls[0]
    assert event_type == "agent:start"
    assert context["platform"] == "slack"
    assert context["session_id"] == "sess-1"
    assert context["message_full"] == "review code and test the result"
    assert context["message_truncated"] is False
    assert context["message_id"] == "123.456"
    assert context["gateway_config"] is runner.config
    assert callable(context["swarm_delegate_fn"])
