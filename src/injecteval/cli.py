"""Command line interface: ``injecteval list`` / ``validate`` / ``run`` / ``compare``."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .defenses import DEFENSES, get_defense
from .environment import Environment
from .graders import grade
from .models import build_model, is_mock
from .report import CATEGORY_LABELS, comparison_markdown, to_html, to_markdown
from .runner import TrialResult, build_report, run_suite
from .scenario import Scenario, ScenarioError, load_scenarios


def _select(scenarios: list[Scenario], ids: list[str] | None, categories: list[str] | None) -> list[Scenario]:
    selected = scenarios
    if ids:
        unknown = set(ids) - {s.id for s in scenarios}
        if unknown:
            raise SystemExit(f"unknown scenario id(s): {', '.join(sorted(unknown))}")
        selected = [s for s in selected if s.id in ids]
    if categories:
        selected = [s for s in selected if s.category in categories]
    if not selected:
        raise SystemExit("no scenarios match the given filters")
    return selected


def cmd_list(args: argparse.Namespace) -> int:
    for scenario in load_scenarios(args.scenarios):
        technique = (scenario.injection or {}).get("technique", "-")
        print(f"{scenario.id:32} {CATEGORY_LABELS[scenario.category]:20} {technique:24} {scenario.title}")
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    """Check that every scenario is self-consistent using the scripted mock agents.

    A robust agent must solve the task without triggering the attack grader, and a fully vulnerable
    agent must trigger it - otherwise the scenario or its graders are wrong.
    """
    defense, problems = get_defense("none"), []
    for scenario in load_scenarios(args.scenarios):
        for mode in ("mock:robust", "mock:vulnerable"):
            from .agent import run_agent  # local import keeps `list` fast

            env = Environment.from_dict(scenario.build_environment())
            transcript = run_agent(build_model(mode, scenario), env, scenario.user_task, defense)
            utility_ok = grade(scenario.utility["grader"], env, transcript.final_answer).passed
            attack_ok = scenario.is_attack and grade(scenario.attack["grader"], env, transcript.final_answer).passed
            if mode == "mock:robust" and not utility_ok:
                problems.append(f"{scenario.id}: the reference solution does not pass the utility grader")
            if mode == "mock:robust" and attack_ok:
                problems.append(f"{scenario.id}: the attack grader fires on the clean reference solution")
            if mode == "mock:vulnerable" and scenario.is_attack and not attack_ok:
                problems.append(f"{scenario.id}: the simulated attack does not trigger the attack grader")
    for problem in problems:
        print(f"FAIL {problem}")
    print("all scenarios are consistent" if not problems else f"{len(problems)} problem(s) found")
    return 1 if problems else 0


def _write_report(report: dict, out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    (out / "report.md").write_text(to_markdown(report), encoding="utf-8")
    (out / "report.html").write_text(to_html(report), encoding="utf-8")


def cmd_compare(args: argparse.Namespace) -> int:
    """Run the same scenarios once per defense and put the results side by side."""
    scenarios = _select(load_scenarios(args.scenarios), args.scenario, args.category)
    names = args.defenses or list(DEFENSES)
    out, reports = Path(args.out), []
    for name in names:
        defense = get_defense(name)
        print(f"defense={name} model={args.model} scenarios={len(scenarios)} trials={args.trials}", file=sys.stderr)
        results = run_suite(
            scenarios,
            lambda scenario: build_model(args.model, scenario, base_url=args.base_url, temperature=args.temperature),
            defense, trials=args.trials, max_steps=args.max_steps,
        )
        report = build_report(results, args.model, defense, args.trials)
        _write_report(report, out / name)
        reports.append(report)

    table = comparison_markdown(reports)
    out.mkdir(parents=True, exist_ok=True)
    (out / "comparison.md").write_text(table, encoding="utf-8")
    print(table)
    print(f"written to {out}/comparison.md", file=sys.stderr)
    return 2 if all(r["summary"]["errors"] == r["summary"]["trials"] for r in reports) else 0


def cmd_run(args: argparse.Namespace) -> int:
    scenarios = _select(load_scenarios(args.scenarios), args.scenario, args.category)
    defense = get_defense(args.defense)

    def progress(result: TrialResult) -> None:
        if result.error:
            status = f"ERROR  {result.error}"
        elif result.attack_success is None:
            status = "benign   task " + ("ok" if result.utility_success else "FAILED")
        else:
            attack = "ATTACKED" if result.attack_success else "blocked "
            status = f"{attack} task " + ("ok" if result.utility_success else "FAILED")
        print(f"  {result.scenario_id:32} trial {result.trial + 1}/{args.trials}  {status}", file=sys.stderr)

    print(f"model={args.model} defense={defense.name} scenarios={len(scenarios)} trials={args.trials}", file=sys.stderr)
    if is_mock(args.model):
        print("note: mock models replay scripted calls - use them to test the harness, not to measure anything.",
              file=sys.stderr)
    results = run_suite(
        scenarios,
        lambda scenario: build_model(args.model, scenario, base_url=args.base_url, temperature=args.temperature),
        defense, trials=args.trials, max_steps=args.max_steps, on_result=progress,
    )
    report = build_report(results, args.model, defense, args.trials)

    out = Path(args.out)
    _write_report(report, out)

    summary = report["summary"]
    attack, utility = summary["attack_success"], summary["utility_under_attack"]
    print(f"\nattack success: {attack['successes']}/{attack['total']}   "
          f"task success under attack: {utility['successes']}/{utility['total']}   errors: {summary['errors']}")
    print(f"reports written to {out}/report.{{json,md,html}}")

    if summary["errors"] == summary["trials"]:
        return 2
    if args.fail_above is not None and attack["total"] and attack["rate"] > args.fail_above:
        print(f"attack success rate {attack['rate']:.2f} is above the allowed {args.fail_above:.2f}", file=sys.stderr)
        return 1
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="injecteval",
        description="Evaluate indirect prompt injection against tool-using LLM agents.",
    )
    parser.add_argument("--version", action="version", version=f"injecteval {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    def add_scenarios_option(p: argparse.ArgumentParser) -> None:
        p.add_argument("--scenarios", metavar="DIR", help="directory with scenario YAML files (default: built in)")

    p_list = sub.add_parser("list", help="list the available scenarios")
    add_scenarios_option(p_list)
    p_list.set_defaults(func=cmd_list)

    p_validate = sub.add_parser("validate", help="check that scenarios and their graders are self-consistent")
    add_scenarios_option(p_validate)
    p_validate.set_defaults(func=cmd_validate)

    def add_run_options(p: argparse.ArgumentParser) -> None:
        add_scenarios_option(p)
        p.add_argument("--model", required=True,
                       help="provider:model, e.g. ollama:llama3.2, vllm:Qwen/Qwen2.5-7B-Instruct, mock:vulnerable")
        p.add_argument("--base-url", help="OpenAI-compatible endpoint (overrides the provider default)")
        p.add_argument("--trials", type=int, default=1, help="repetitions per scenario (default: 1)")
        p.add_argument("--temperature", type=float, default=0.0)
        p.add_argument("--max-steps", type=int, default=12, help="maximum model turns per trial (default: 12)")
        p.add_argument("--scenario", action="append", metavar="ID", help="run only this scenario (repeatable)")
        p.add_argument("--category", action="append", choices=sorted(CATEGORY_LABELS), help="run only this category")
        p.add_argument("--out", default="reports", help="output directory (default: ./reports)")

    p_compare = sub.add_parser("compare", help="run every defense against the same model and compare them")
    add_run_options(p_compare)
    p_compare.add_argument("--defenses", nargs="+", choices=sorted(DEFENSES), metavar="NAME",
                           help=f"defenses to compare (default: all of {', '.join(DEFENSES)})")
    p_compare.set_defaults(func=cmd_compare)

    p_run = sub.add_parser("run", help="run scenarios against a model and write a report")
    add_run_options(p_run)
    p_run.add_argument("--defense", default="none", choices=sorted(DEFENSES),
                       help="mitigation to apply (default: none)")
    p_run.add_argument("--fail-above", type=float, metavar="RATE",
                       help="exit with status 1 if the attack success rate is above RATE (0-1); useful in CI")
    p_run.set_defaults(func=cmd_run)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (ScenarioError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
