from agent.swarm_evidence import (
    EvidencePacket,
    validate_evidence_packet,
)
from agent.swarm_synthesis import synthesize_swarm_result
from agent.swarm_state import EvidenceRequirement, PermissionGrant, RoutingPlan, SwarmJob


def test_valid_evidence_packet_satisfies_required_test_and_artifact():
    packet = EvidencePacket.from_result(
        {
            "summary": "implemented",
            "claims": ["Tests pass", "Artifact written"],
            "evidence": [
                {"kind": "test", "command": "python -m pytest tests/swarm -q", "output": "64 passed"},
                {"kind": "artifact", "path": "agent/swarm_evidence.py"},
            ],
            "confidence": "high",
        }
    )

    result = validate_evidence_packet(
        packet,
        [
            EvidenceRequirement("test", "Run focused tests"),
            EvidenceRequirement("artifact", "Provide changed file path"),
        ],
    )

    assert result.passed is True
    assert result.missing_required_kinds == []


def test_citation_does_not_satisfy_command_output_requirement():
    packet = EvidencePacket.from_result(
        {
            "claims": ["Command ran"],
            "evidence": [{"kind": "citation", "url": "https://example.com"}],
        }
    )

    result = validate_evidence_packet(packet, [EvidenceRequirement("command_output", "Show command output")])

    assert result.passed is False
    assert result.missing_required_kinds == ["command_output"]
    assert "missing_command_output" in result.reasons


def test_human_approval_requirement_cannot_be_faked_by_child_summary():
    packet = EvidencePacket.from_result(
        {
            "summary": "User approved it, pinky swear",
            "claims": ["Approval exists"],
            "evidence": [{"kind": "command_output", "command": "echo approved", "output": "approved"}],
        }
    )

    result = validate_evidence_packet(packet, [EvidenceRequirement("human_approval", "Garrett must approve")])

    assert result.passed is False
    assert "missing_human_approval" in result.reasons


def test_human_approval_requirement_cannot_be_faked_with_parent_verified_metadata():
    packet = EvidencePacket.from_result(
        {
            "claims": ["Approval exists"],
            "evidence": [{"kind": "human_approval", "approval_id": "fake", "parent_verified": True}],
        }
    )

    result = validate_evidence_packet(packet, [EvidenceRequirement("human_approval", "Garrett must approve")])

    assert result.passed is False
    assert "missing_human_approval" in result.reasons


def test_approved_permission_grant_satisfies_human_approval_requirement():
    packet = EvidencePacket.from_result({"claims": ["Approval exists"], "evidence": []})
    grant = PermissionGrant("perm_send", "Send report", status="approved")

    result = validate_evidence_packet(
        packet,
        [EvidenceRequirement("human_approval", "Garrett must approve", metadata={"permission_id": "perm_send"})],
        approval_grants=[grant],
    )

    assert result.passed is True
    assert "human_approval" in result.satisfied_kinds
    assert result.missing_required_kinds == []


def test_requested_denied_or_wrong_permission_grants_do_not_satisfy_human_approval():
    packet = EvidencePacket.from_result({"claims": ["Approval exists"], "evidence": []})
    requirement = EvidenceRequirement("human_approval", "Garrett must approve", metadata={"permission_id": "perm_send"})

    for grant in (
        PermissionGrant("perm_send", "Send report", status="requested"),
        PermissionGrant("perm_send", "Send report", status="denied"),
        PermissionGrant("perm_other", "Different action", status="approved"),
    ):
        result = validate_evidence_packet(packet, [requirement], approval_grants=[grant])
        assert result.passed is False
        assert result.missing_required_kinds == ["human_approval"]
        assert "missing_human_approval" in result.reasons


def test_dict_requirement_top_level_permission_id_must_match_approved_grant():
    packet = EvidencePacket.from_result({"claims": ["Approval exists"], "evidence": []})
    requirement = {"kind": "human_approval", "description": "Garrett must approve", "permission_id": "perm_send"}

    wrong = validate_evidence_packet(
        packet,
        [requirement],
        approval_grants=[{"permission_id": "perm_other", "status": "approved"}],
    )
    right = validate_evidence_packet(
        packet,
        [requirement],
        approval_grants=[{"permission_id": "perm_send", "status": "approved"}],
    )

    assert wrong.passed is False
    assert wrong.missing_required_kinds == ["human_approval"]
    assert right.passed is True


def test_multi_permission_human_approval_requirement_requires_all_grants_approved():
    packet = EvidencePacket.from_result({"claims": ["Approval exists"], "evidence": []})
    requirement = {
        "kind": "human_approval",
        "description": "Garrett must approve both side effects",
        "permission_ids": ["perm_send", "perm_pipe_execution"],
    }

    partial = validate_evidence_packet(
        packet,
        [requirement],
        approval_grants=[{"permission_id": "perm_send", "status": "approved"}],
    )
    complete = validate_evidence_packet(
        packet,
        [requirement],
        approval_grants=[
            {"permission_id": "perm_send", "status": "approved"},
            {"permission_id": "perm_pipe_execution", "status": "approved"},
        ],
    )

    assert partial.passed is False
    assert partial.missing_required_kinds == ["human_approval"]
    assert complete.passed is True


def test_parent_synthesis_uses_job_permissions_for_human_approval():
    job = SwarmJob.create("send report", created_at="2026-01-01T00:00:00+00:00")
    job.routing_plan = RoutingPlan(
        mode="swarm",
        reason="approval needed",
        evidence_requirements=[
            EvidenceRequirement("human_approval", "Garrett approves send", metadata={"permission_id": "perm_send"})
        ],
        verification_required=True,
    )
    job.permissions.append(PermissionGrant("perm_send", "Send report", status="approved"))
    task = job.add_task("send", status="completed")
    task.result = {
        "summary": "sent",
        "claims": ["Report sent"],
        "evidence": [{"kind": "human_approval", "approval_id": "fake", "parent_verified": True}],
    }

    synthesis = synthesize_swarm_result(job)

    assert synthesis.safe_to_present_complete is True
    assert synthesis.verified_claims == ["Report sent"]
    assert synthesis.missing_evidence == {}


def test_parent_synthesis_marks_unsupported_claims_unverified():
    job = SwarmJob.create("research with proof", created_at="2026-01-01T00:00:00+00:00")
    job.routing_plan = RoutingPlan(
        mode="swarm",
        reason="needs proof",
        evidence_requirements=[EvidenceRequirement("citation", "Cite sources")],
        verification_required=True,
    )
    task = job.add_task("research", status="completed")
    task.result = {
        "summary": "done",
        "claims": ["Apple paper says models collapse"],
        "evidence": [],
    }

    synthesis = synthesize_swarm_result(job)

    assert synthesis.safe_to_present_complete is False
    assert "Apple paper says models collapse" in synthesis.unverified_claims
    assert synthesis.missing_evidence["citation"] == 1
    assert "citation" in synthesis.user_disclosure
