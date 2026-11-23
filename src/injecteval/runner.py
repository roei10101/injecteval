"""Run scenarios against a model and aggregate the results."""
from __future__ import annotations

import math
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

from . import __version__
from .agent import run_agent
from .defenses import Defense
from .environment import Environment
from .graders import grade
from .models import Model, ModelError
from .scenario import Scenario


@dataclass
class TrialResult:
    scenario_id: str
    category: str
    trial: int
    attacked: bool
    attack_success: bool | None  # None for benign controls
    attack_reason: str
    utility_success: bool
    utility_reason: str
    steps: int
    stopped_reason: str
    error: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    final_answer: str = ""


def run_trial(scenario: Scenario, model: Model, defense: Defense, trial: int = 0, max_steps: int = 12) -> TrialResult:
    env = Environment.from_dict(scenario.build_environment(attacked=True))
    result = TrialResult(
        scenario_id=scenario.id, category=scenario.category, trial=trial, attacked=scenario.is_attack,
        attack_success=None, attack_reason="", utility_success=False, utility_reason="", steps=0,
        stopped_reason="completed",
    )
    try:
        transcript = run_agent(model, env, scenario.user_task, defense, max_steps=max_steps)
    except ModelError as error:
        result.error = str(error)
        result.stopped_reason = "error"
        result.tool_calls = [call.to_dict() for call in env.calls]
        return result

    result.steps, result.stopped_reason = transcript.steps, transcript.stopped_reason
    result.final_answer = transcript.final_answer
    result.tool_calls = [call.to_dict() for call in env.calls]

    utility = grade(scenario.utility["grader"], env, transcript.final_answer)
    result.utility_success, result.utility_reason = utility.passed, utility.reason
    if scenario.is_attack:
        attack = grade(scenario.attack["grader"], env, transcript.final_answer)
        result.attack_success, result.attack_reason = attack.passed, attack.reason
    return result


def run_suite(
    scenarios: Iterable[Scenario],
    model_factory: Callable[[Scenario], Model],
    defense: Defense,
    trials: int = 1,
    max_steps: int = 12,
    on_result: Callable[[TrialResult], None] | None = None,
) -> list[TrialResult]:
    results = []
    for scenario in scenarios:
        for trial in range(trials):
            # A fresh model per trial: mock models are stateful and real ones keep no state anyway.
            result = run_trial(scenario, model_factory(scenario), defense, trial=trial, max_steps=max_steps)
            results.append(result)
            if on_result:
                on_result(result)
    return results


def wilson_interval(successes: int, total: int, z: float = 1.96) -> tuple[float, float]:
    """95% Wilson score interval - behaves sensibly for the small samples typical of eval runs."""
    if total == 0:
        return (0.0, 0.0)
    p = successes / total
    denominator = 1 + z * z / total
    centre = (p + z * z / (2 * total)) / denominator
    margin = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denominator
    return (max(0.0, centre - margin), min(1.0, centre + margin))


def _rate(successes: int, total: int) -> dict[str, Any]:
    low, high = wilson_interval(successes, total)
    return {"successes": successes, "total": total, "rate": (successes / total) if total else None,
            "ci95": [round(low, 4), round(high, 4)]}


def summarize(results: list[TrialResult]) -> dict[str, Any]:
    valid = [r for r in results if not r.error]
    attacked = [r for r in valid if r.attacked]
    benign = [r for r in valid if not r.attacked]

    by_category: dict[str, dict[str, Any]] = {}
    for category in sorted({r.category for r in attacked}):
        rows = [r for r in attacked if r.category == category]
        by_category[category] = {
            "attack_success": _rate(sum(bool(r.attack_success) for r in rows), len(rows)),
            "utility": _rate(sum(r.utility_success for r in rows), len(rows)),
        }

    by_scenario: dict[str, dict[str, Any]] = {}
    for scenario_id in dict.fromkeys(r.scenario_id for r in results):
        rows = [r for r in results if r.scenario_id == scenario_id]
        ok = [r for r in rows if not r.error]
        by_scenario[scenario_id] = {
            "category": rows[0].category,
            "trials": len(rows),
            "errors": len(rows) - len(ok),
            "attack_successes": sum(bool(r.attack_success) for r in ok) if rows[0].attacked else None,
            "utility_successes": sum(r.utility_success for r in ok),
        }

    return {
        "trials": len(results),
        "errors": len(results) - len(valid),
        "attack_success": _rate(sum(bool(r.attack_success) for r in attacked), len(attacked)),
        "utility_under_attack": _rate(sum(r.utility_success for r in attacked), len(attacked)),
        "benign_utility": _rate(sum(r.utility_success for r in benign), len(benign)),
        "by_category": by_category,
        "by_scenario": by_scenario,
    }


def build_report(results: list[TrialResult], model: str, defense: Defense, trials: int) -> dict[str, Any]:
    return {
        "tool": "injecteval",
        "version": __version__,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "model": model,
        "defense": {"name": defense.name, "description": defense.description},
        "trials_per_scenario": trials,
        "summary": summarize(results),
        "results": [asdict(result) for result in results],
    }
