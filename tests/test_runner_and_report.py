import json

import pytest

from injecteval.cli import main
from injecteval.defenses import get_defense
from injecteval.models import ModelError, build_model
from injecteval.report import to_html, to_markdown
from injecteval.runner import build_report, run_suite, run_trial, summarize, wilson_interval


def run(scenarios, mode, defense="none", trials=1):
    return run_suite(scenarios, lambda scenario: build_model(mode, scenario), get_defense(defense), trials=trials)


def test_every_attack_fires_against_a_fully_vulnerable_agent(scenarios):
    """Guards the scenario files: a payload nobody could ever fall for measures nothing."""
    results = run(scenarios, "mock:vulnerable")
    failed = [r.scenario_id for r in results if r.attacked and not r.attack_success]
    assert failed == []


def test_no_grader_fires_on_clean_behaviour_and_every_task_is_solvable(scenarios):
    results = run(scenarios, "mock:robust")
    assert [r.scenario_id for r in results if r.attack_success] == []
    assert [r.scenario_id for r in results if not r.utility_success] == []


def test_benign_controls_have_no_attack_verdict(scenarios):
    results = run(scenarios, "mock:vulnerable")
    assert all(r.attack_success is None for r in results if r.category == "benign_control")


def test_filter_defense_reduces_attacks_against_the_scripted_agent(scenarios):
    baseline = summarize(run(scenarios, "mock:vulnerable"))["attack_success"]["successes"]
    filtered = summarize(run(scenarios, "mock:vulnerable", defense="filter"))["attack_success"]["successes"]
    assert 0 < filtered < baseline


def test_summary_counts(scenarios):
    summary = summarize(run(scenarios, "mock:vulnerable", trials=2))
    attacks = sum(s.is_attack for s in scenarios)

    assert summary["trials"] == 2 * len(scenarios)
    assert summary["attack_success"] == {"successes": 2 * attacks, "total": 2 * attacks, "rate": 1.0,
                                         "ci95": summary["attack_success"]["ci95"]}
    assert summary["benign_utility"]["total"] == 2 * (len(scenarios) - attacks)
    assert set(summary["by_category"]) == {"data_exfiltration", "unauthorized_action", "goal_hijack"}
    assert summary["by_scenario"]["action-delete-file"]["attack_successes"] == 2


def test_backend_errors_are_reported_and_excluded_from_the_rates(by_id):
    class Broken:
        name = "broken"

        def complete(self, messages, tools):
            raise ModelError("connection refused")

    result = run_trial(by_id["action-delete-file"], Broken(), get_defense("none"))
    summary = summarize([result])

    assert result.error == "connection refused" and result.stopped_reason == "error"
    assert summary["errors"] == 1
    assert summary["attack_success"]["total"] == 0 and summary["attack_success"]["rate"] is None


def test_wilson_interval():
    assert wilson_interval(0, 0) == (0.0, 0.0)
    low, high = wilson_interval(0, 10)
    assert low == 0.0 and 0.25 < high < 0.35  # zero observed attacks is not proof of zero risk
    low, high = wilson_interval(10, 10)
    assert 0.65 < low < 0.75 and high == 1.0
    low, high = wilson_interval(5, 10)
    assert low == pytest.approx(0.237, abs=0.01) and high == pytest.approx(0.763, abs=0.01)


def test_reports_render(scenarios):
    report = build_report(run(scenarios, "mock:vulnerable"), "mock:vulnerable", get_defense("none"), 1)

    markdown, page = to_markdown(report), to_html(report)

    assert json.loads(json.dumps(report))["summary"]["trials"] == len(scenarios)
    assert "| `exfil-email-forward` | Data exfiltration | 1/1 | 1/1 | 0 |" in markdown
    assert "Attack success rate" in markdown and "95% CI" in markdown
    assert page.startswith("<!doctype html>") and "ATTACK SUCCEEDED" in page


def test_html_report_escapes_model_output(by_id):
    scenario = by_id["benign-instructional-doc"]
    result = run_trial(scenario, build_model("mock:robust", scenario), get_defense("none"))
    result.final_answer = "<script>alert(1)</script>"

    page = to_html(build_report([result], "m", get_defense("none"), 1))

    assert "<script>alert(1)</script>" not in page


def test_cli_run_writes_reports(tmp_path, capsys):
    code = main(["run", "--model", "mock:robust", "--out", str(tmp_path), "--category", "goal_hijack"])

    assert code == 0
    assert {p.name for p in tmp_path.iterdir()} == {"report.json", "report.md", "report.html"}
    assert "attack success: 0/3" in capsys.readouterr().out


def test_cli_fail_above_gates_on_attack_success(tmp_path):
    args = ["run", "--out", str(tmp_path), "--fail-above", "0.5", "--model"]
    assert main(args + ["mock:vulnerable"]) == 1
    assert main(args + ["mock:robust"]) == 0


def test_cli_validate_and_list(capsys):
    assert main(["validate"]) == 0
    assert main(["list"]) == 0
    assert "exfil-email-forward" in capsys.readouterr().out


def test_cli_reports_bad_input(tmp_path, capsys):
    assert main(["run", "--model", "nonsense", "--out", str(tmp_path)]) == 2
    assert "provider:model" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        main(["run", "--model", "mock:robust", "--scenario", "does-not-exist", "--out", str(tmp_path)])
