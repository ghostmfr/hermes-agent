from gateway.builtin_hooks.swarm_operator import _swarm_config
from gateway.config import GatewayConfig, load_gateway_config
from hermes_cli.config import DEFAULT_CONFIG
from hermes_constants import get_hermes_home


def test_default_config_contains_disabled_swarm_operator():
    cfg = DEFAULT_CONFIG["swarm_operator"]

    assert cfg["enabled"] is False
    assert cfg["dry_run"] is True
    assert cfg["live_delegation_enabled"] is False
    assert cfg["live_delegation_timeout_seconds"] == 30.0
    assert cfg["max_children"] == 3
    assert cfg["persist_to_honcho"] is False
    assert cfg["honcho_summary_enabled"] is False


def test_gateway_config_preserves_swarm_operator_settings_for_hooks():
    gateway_config = GatewayConfig.from_dict({"swarm_operator": {"enabled": True, "max_children": 2}})

    assert gateway_config.swarm_operator["enabled"] is True
    assert gateway_config.to_dict()["swarm_operator"]["max_children"] == 2
    assert _swarm_config(gateway_config)["enabled"] is True
    assert _swarm_config(gateway_config)["max_children"] == 2
    assert _swarm_config(gateway_config)["live_delegation_enabled"] is False


def test_swarm_config_parses_string_booleans_fail_closed_for_live_gate():
    cfg = _swarm_config(
        {
            "swarm_operator": {
                "enabled": "true",
                "dry_run": "false",
                "live_delegation_enabled": "false",
                "persist_to_honcho": "no",
                "honcho_summary_enabled": "off",
            }
        }
    )

    assert cfg["enabled"] is True
    assert cfg["dry_run"] is False
    assert cfg["live_delegation_enabled"] is False
    assert cfg["persist_to_honcho"] is False
    assert cfg["honcho_summary_enabled"] is False


def test_swarm_config_invalid_live_gate_values_fail_closed():
    cfg = _swarm_config({"swarm_operator": {"enabled": "sure", "dry_run": "nope", "live_delegation_enabled": "absolutely"}})

    assert cfg["enabled"] is False
    assert cfg["dry_run"] is True
    assert cfg["live_delegation_enabled"] is False


def test_swarm_config_rejects_non_finite_or_excessive_live_timeout():
    for raw in ("inf", "nan", -1, 0, 999):
        cfg = _swarm_config({"swarm_operator": {"live_delegation_timeout_seconds": raw}})
        assert cfg["live_delegation_timeout_seconds"] == 30.0


def test_swarm_config_clamps_max_children():
    assert _swarm_config({"swarm_operator": {"max_children": -5}})["max_children"] == 1
    assert _swarm_config({"swarm_operator": {"max_children": 999}})["max_children"] == 10


def test_load_gateway_config_bridges_top_level_swarm_operator_yaml():
    config_path = get_hermes_home() / "config.yaml"
    config_path.write_text("swarm_operator:\n  enabled: true\n  max_children: 2\n", encoding="utf-8")

    gateway_config = load_gateway_config()

    assert gateway_config.swarm_operator["enabled"] is True
    assert _swarm_config(gateway_config)["max_children"] == 2
