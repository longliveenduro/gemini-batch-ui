"""Hält die CI-Konfiguration (GitHub Actions) in Übereinstimmung mit dem Projekt."""
import re

import pytest
import yaml

from helpers import ROOT

WORKFLOW = ROOT / ".github" / "workflows" / "tests.yml"
README = (ROOT / "README.md").read_text("utf-8")


@pytest.fixture(scope="module")
def workflow():
    return yaml.safe_load(WORKFLOW.read_text("utf-8"))


def triggers(workflow):
    return workflow.get("on", workflow.get(True))  # YAML 1.1 liest den Schlüssel „on“ als True


def steps(workflow):
    return workflow["jobs"]["pytest"]["steps"]


def test_workflow_is_documented_in_the_readme():
    assert ".github/workflows/tests.yml" in README


def test_workflow_runs_on_push_pull_request_and_by_hand(workflow):
    assert {"push", "pull_request", "workflow_dispatch"} <= set(triggers(workflow))


def test_workflow_installs_the_dev_requirements_and_runs_pytest(workflow):
    commands = [s["run"] for s in steps(workflow) if "run" in s]
    assert any("pip install -r requirements-dev.txt" in c for c in commands)
    assert any(c.strip() == "python -m pytest" for c in commands)


def test_workflow_tests_the_python_versions_named_in_the_readme(workflow):
    versions = set(workflow["jobs"]["pytest"]["strategy"]["matrix"]["python-version"])
    minimum = re.search(r"mindestens Python (3\.\d+)", README).group(1)
    used = re.search(r"Python (3\.\d+) \(vom Besitzer genutzt\)", README).group(1)
    assert {minimum, used} <= versions, f"README nennt {minimum} und {used}, der Workflow testet {sorted(versions)}"


def test_workflow_uses_least_privilege_and_versioned_actions(workflow):
    assert workflow["permissions"] == {"contents": "read"}
    uses = [s["uses"] for s in steps(workflow) if "uses" in s]
    assert uses and all(re.search(r"@v\d+", u) for u in uses), uses


def test_files_the_workflow_relies_on_exist():
    text = WORKFLOW.read_text("utf-8")
    for name in ("requirements.txt", "requirements-dev.txt"):
        assert name in text and (ROOT / name).exists(), f"{name} fehlt"
    assert (ROOT / "pytest.ini").exists()
