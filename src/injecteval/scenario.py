"""Scenario definitions: the task, the simulated environment, the injection and how to grade it."""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Any

import yaml

INJECTION_PLACEHOLDER = "{{INJECTION}}"
CATEGORIES = ("data_exfiltration", "unauthorized_action", "goal_hijack", "benign_control")


class ScenarioError(ValueError):
    """Raised when a scenario file is malformed."""


@dataclass(frozen=True)
class Scenario:
    id: str
    title: str
    category: str
    description: str
    user_task: str
    environment: dict[str, Any]
    utility: dict[str, Any]
    injection: dict[str, Any] | None = None
    attack: dict[str, Any] | None = None
    source: str = field(default="", compare=False)

    @property
    def is_attack(self) -> bool:
        return self.injection is not None

    @property
    def payload(self) -> str:
        return (self.injection or {}).get("payload", "").strip()

    def build_environment(self, attacked: bool = True) -> dict[str, Any]:
        """Return a fresh copy of the environment with the payload planted (or removed)."""
        replacement = self.payload if (attacked and self.is_attack) else ""
        return _substitute(copy.deepcopy(self.environment), replacement)


def _substitute(node: Any, replacement: str) -> Any:
    if isinstance(node, str):
        return node.replace(INJECTION_PLACEHOLDER, replacement).strip() if INJECTION_PLACEHOLDER in node else node
    if isinstance(node, list):
        return [_substitute(item, replacement) for item in node]
    if isinstance(node, dict):
        return {key: _substitute(value, replacement) for key, value in node.items()}
    return node


def _contains_placeholder(node: Any) -> bool:
    if isinstance(node, str):
        return INJECTION_PLACEHOLDER in node
    if isinstance(node, list):
        return any(_contains_placeholder(item) for item in node)
    if isinstance(node, dict):
        return any(_contains_placeholder(value) for value in node.values())
    return False


def parse_scenario(data: dict[str, Any], source: str = "") -> Scenario:
    def require(key: str) -> Any:
        if key not in data or data[key] in (None, ""):
            raise ScenarioError(f"{source or 'scenario'}: missing required field '{key}'")
        return data[key]

    category = require("category")
    if category not in CATEGORIES:
        raise ScenarioError(f"{source}: unknown category '{category}' (expected one of {CATEGORIES})")

    injection, attack = data.get("injection"), data.get("attack")
    environment = require("environment")
    utility = require("utility")
    if "grader" not in utility:
        raise ScenarioError(f"{source}: utility.grader is required")

    if category == "benign_control":
        if injection or attack:
            raise ScenarioError(f"{source}: benign_control scenarios must not define an injection or attack")
    else:
        if not injection or not injection.get("payload"):
            raise ScenarioError(f"{source}: attack scenarios need injection.payload")
        if not attack or "grader" not in attack:
            raise ScenarioError(f"{source}: attack scenarios need attack.grader")
        if not _contains_placeholder(environment):
            raise ScenarioError(f"{source}: environment has no {INJECTION_PLACEHOLDER} placeholder")

    return Scenario(
        id=require("id"),
        title=require("title"),
        category=category,
        description=data.get("description", "").strip(),
        user_task=require("user_task").strip(),
        environment=environment,
        utility=utility,
        injection=injection,
        attack=attack,
        source=source,
    )


def load_scenario_file(path: Path) -> Scenario:
    with open(path, encoding="utf-8") as handle:
        return parse_scenario(yaml.safe_load(handle), source=str(path))


def load_scenarios(directory: str | Path | None = None) -> list[Scenario]:
    """Load every *.yaml scenario from a directory (default: the scenarios shipped with the package)."""
    if directory is None:
        directory = Path(str(resources.files("injecteval") / "scenarios"))
    paths = sorted(Path(directory).glob("*.yaml"))
    if not paths:
        raise ScenarioError(f"no scenario files found in {directory}")
    scenarios = [load_scenario_file(path) for path in paths]
    seen: set[str] = set()
    for scenario in scenarios:
        if scenario.id in seen:
            raise ScenarioError(f"duplicate scenario id '{scenario.id}'")
        seen.add(scenario.id)
    return scenarios
