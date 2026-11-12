"""Contracts for the observability plane: alert rules, scrape and routing config."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

from incidentops.domain.catalog import load_catalog

REPO_ROOT = Path(__file__).resolve().parents[2]
INFRA = REPO_ROOT / "infra"
RULES = INFRA / "prometheus" / "rules" / "lab.rules.yml"


def _compose_image(service: str) -> str:
    """Use exactly the digest-pinned image the stack runs, so tests cannot drift."""
    services = yaml.safe_load((INFRA / "compose.yaml").read_text(encoding="utf-8"))["services"]
    return str(services[service]["image"])


PROMETHEUS_IMAGE = _compose_image("prometheus")
ALERTMANAGER_IMAGE = _compose_image("alertmanager")


# Root-cause identifiers are benchmark ground truth (benchmark.md §5) and must
# never appear in alert labels or annotations visible to the agent.
def _ground_truth_ids() -> set[str]:
    from incidentops.agent.prompt import ROOT_CAUSE_TAXONOMY

    return set(ROOT_CAUSE_TAXONOMY)


GROUND_TRUTH_IDS = _ground_truth_ids()


def _alert_rules() -> list[dict[str, Any]]:
    groups = yaml.safe_load(RULES.read_text(encoding="utf-8"))["groups"]
    return [rule for group in groups for rule in group["rules"] if "alert" in rule]


@pytest.mark.parametrize("rule", _alert_rules(), ids=lambda r: r["alert"])
def test_alert_rules_carry_required_labels(rule: dict[str, Any]) -> None:
    labels = rule["labels"]
    assert {"service_id", "environment", "severity"} <= set(labels)
    assert labels["environment"] == "fault-lab"
    assert labels["severity"] in {"info", "warning", "critical"}
    assert load_catalog().is_registered(labels["service_id"])


@pytest.mark.parametrize("rule", _alert_rules(), ids=lambda r: r["alert"])
def test_alert_names_are_valid_incident_identifiers(rule: dict[str, Any]) -> None:
    assert re.fullmatch(r"[a-z][a-z0-9_]{0,63}", rule["alert"])


@pytest.mark.parametrize("rule", _alert_rules(), ids=lambda r: r["alert"])
def test_alert_metadata_never_leaks_ground_truth(rule: dict[str, Any]) -> None:
    visible = yaml.safe_dump({"labels": rule["labels"], "annotations": rule["annotations"]})
    assert not [token for token in GROUND_TRUTH_IDS if token in visible]


def test_fault_controller_is_never_scraped() -> None:
    config = (INFRA / "prometheus" / "prometheus.yml").read_text(encoding="utf-8")
    assert "fault-controller" not in config


def test_alertmanager_routes_to_incidentops_webhook_with_file_credentials() -> None:
    config = yaml.safe_load((INFRA / "alertmanager" / "alertmanager.yml").read_text("utf-8"))
    (receiver,) = config["receivers"]
    (webhook,) = receiver["webhook_configs"]
    assert webhook["url"].endswith("/api/v1/alerts/alertmanager")
    authorization = webhook["http_config"]["authorization"]
    assert authorization["type"] == "Bearer"
    assert "credentials" not in authorization, "tokens must come from a file, not git"
    assert authorization["credentials_file"].startswith("/etc/alertmanager/secrets/")


def test_infra_compose_isolation_rules() -> None:
    services = yaml.safe_load((INFRA / "compose.yaml").read_text("utf-8"))["services"]
    for name, service in services.items():
        assert "@sha256:" in service["image"], f"{name} image is not digest-pinned"
        for port in service.get("ports", []):
            assert str(port).startswith("127.0.0.1:"), f"{name} exposes {port}"
        for volume in service.get("volumes", []):
            assert "docker.sock" not in str(volume), f"{name} mounts the Docker socket"


def _docker_available() -> bool:
    if shutil.which("docker") is None:
        return False
    probe = subprocess.run(
        ["docker", "info"],
        capture_output=True,
        check=False,
    )
    return probe.returncode == 0


def _require_docker() -> None:
    if not _docker_available():
        if os.environ.get("INCIDENTOPS_REQUIRE_DOCKER") == "1":
            pytest.fail("Docker is required for observability config tests")
        pytest.skip("Docker not available")


def _run(image: str, entrypoint: str, mount: str, target: str, *args: str) -> str:
    result = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "-v",
            f"{mount}:{target}:ro",
            "--entrypoint",
            entrypoint,
            image,
            *args,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout + result.stderr


def test_promtool_rule_unit_tests_pass() -> None:
    _require_docker()
    output = _run(
        PROMETHEUS_IMAGE,
        "promtool",
        str(INFRA / "prometheus"),
        "/p",
        "test",
        "rules",
        "/p/tests/lab.rules.test.yml",
    )
    assert "SUCCESS" in output


def test_prometheus_config_and_rules_are_valid() -> None:
    _require_docker()
    _run(
        PROMETHEUS_IMAGE,
        "promtool",
        str(INFRA / "prometheus"),
        "/etc/prometheus",
        "check",
        "config",
        "/etc/prometheus/prometheus.yml",
    )


def test_alertmanager_config_is_valid() -> None:
    _require_docker()
    _run(
        ALERTMANAGER_IMAGE,
        "amtool",
        str(INFRA / "alertmanager"),
        "/a",
        "check-config",
        "/a/alertmanager.yml",
    )
