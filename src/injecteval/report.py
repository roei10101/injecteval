"""Render a run as Markdown (for READMEs and PRs) and as a self-contained HTML page."""
from __future__ import annotations

import html
import json
from typing import Any

CATEGORY_LABELS = {
    "data_exfiltration": "Data exfiltration",
    "unauthorized_action": "Unauthorized action",
    "goal_hijack": "Goal hijack",
    "benign_control": "Benign control",
}


def _pct(rate: dict[str, Any]) -> str:
    if not rate["total"]:
        return "n/a"
    low, high = rate["ci95"]
    return f"{rate['rate'] * 100:.0f}% ({rate['successes']}/{rate['total']}, 95% CI {low * 100:.0f}-{high * 100:.0f}%)"


def _fraction(successes: int | None, total: int) -> str:
    return "-" if successes is None else f"{successes}/{total}"


def to_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        f"# injecteval report - `{report['model']}`",
        "",
        f"- **Defense:** `{report['defense']['name']}` - {report['defense']['description']}",
        f"- **Trials per scenario:** {report['trials_per_scenario']}",
        f"- **Generated:** {report['generated_at']} (injecteval {report['version']})",
        "",
        "## Summary",
        "",
        "| Metric | Result |",
        "| :--- | :--- |",
        f"| Attack success rate (lower is better) | {_pct(summary['attack_success'])} |",
        f"| Task success while under attack | {_pct(summary['utility_under_attack'])} |",
        f"| Task success on benign controls | {_pct(summary['benign_utility'])} |",
        f"| Trials that errored | {summary['errors']} of {summary['trials']} |",
        "",
        "## By attack category",
        "",
        "| Category | Attack success | Task success |",
        "| :--- | :--- | :--- |",
    ]
    for category, row in summary["by_category"].items():
        lines.append(f"| {CATEGORY_LABELS.get(category, category)} | {_pct(row['attack_success'])} | {_pct(row['utility'])} |")
    lines += ["", "## By scenario", "", "| Scenario | Category | Attack succeeded | Task succeeded | Errors |",
              "| :--- | :--- | :---: | :---: | :---: |"]
    for scenario_id, row in summary["by_scenario"].items():
        done = row["trials"] - row["errors"]
        lines.append(
            f"| `{scenario_id}` | {CATEGORY_LABELS.get(row['category'], row['category'])} | "
            f"{_fraction(row['attack_successes'], done)} | {_fraction(row['utility_successes'], done)} | {row['errors']} |"
        )
    lines.append("")
    return "\n".join(lines)


def _short(rate: dict[str, Any]) -> str:
    return "n/a" if not rate["total"] else f"{rate['rate'] * 100:.0f}% ({rate['successes']}/{rate['total']})"


def comparison_markdown(reports: list[dict[str, Any]]) -> str:
    """One row per defense, so the cost and the benefit of each mitigation sit next to each other."""
    first = reports[0]
    lines = [
        f"# injecteval defense comparison - `{first['model']}`",
        "",
        f"{first['trials_per_scenario']} trial(s) per scenario - generated {first['generated_at']}",
        "",
        "| Defense | Attack success (lower is better) | Task success under attack | Task success on controls | Errors |",
        "| :--- | :--- | :--- | :--- | :---: |",
    ]
    for report in reports:
        summary = report["summary"]
        lines.append(
            f"| `{report['defense']['name']}` | {_short(summary['attack_success'])} | "
            f"{_short(summary['utility_under_attack'])} | {_short(summary['benign_utility'])} | {summary['errors']} |"
        )
    lines += ["", "Per-defense reports with full traces are in the sub-directories next to this file.", ""]
    return "\n".join(lines)


_CSS = """
:root { color-scheme: light dark; --bg:#fff; --fg:#1b1f24; --muted:#59636e; --line:#d1d9e0; --card:#f6f8fa;
        --bad:#b42318; --good:#1a7f37; }
@media (prefers-color-scheme: dark) { :root { --bg:#0d1117; --fg:#e6edf3; --muted:#9198a1; --line:#3d444d;
        --card:#151b23; --bad:#ff7b72; --good:#3fb950; } }
body { font: 15px/1.5 system-ui, -apple-system, Segoe UI, sans-serif; background:var(--bg); color:var(--fg);
       max-width: 980px; margin: 2rem auto; padding: 0 1rem; }
h1 { font-size: 1.5rem; } h2 { font-size: 1.15rem; margin-top: 2rem; }
.meta { color: var(--muted); }
.cards { display:grid; grid-template-columns: repeat(auto-fit, minmax(200px,1fr)); gap: .75rem; margin: 1rem 0; }
.card { background:var(--card); border:1px solid var(--line); border-radius:8px; padding:.9rem 1rem; }
.card .value { font-size: 1.7rem; font-weight: 650; } .card .label { color: var(--muted); font-size:.85rem; }
table { border-collapse: collapse; width: 100%; } th, td { border-bottom:1px solid var(--line); padding:.45rem .6rem;
       text-align:left; vertical-align: top; } th { color: var(--muted); font-weight: 600; font-size: .85rem; }
.bad { color: var(--bad); font-weight: 600; } .good { color: var(--good); font-weight: 600; }
details { border:1px solid var(--line); border-radius:8px; padding:.5rem .8rem; margin:.5rem 0; }
summary { cursor: pointer; } pre { background:var(--card); padding:.6rem; border-radius:6px; overflow-x:auto;
       white-space: pre-wrap; word-break: break-word; font-size: .82rem; }
"""


def _card(label: str, rate: dict[str, Any]) -> str:
    value = "n/a" if not rate["total"] else f"{rate['rate'] * 100:.0f}%"
    detail = "" if not rate["total"] else f"{rate['successes']}/{rate['total']} - 95% CI {rate['ci95'][0] * 100:.0f}-{rate['ci95'][1] * 100:.0f}%"
    return f'<div class="card"><div class="value">{value}</div><div class="label">{html.escape(label)}<br>{detail}</div></div>'


def to_html(report: dict[str, Any]) -> str:
    e = html.escape
    summary = report["summary"]
    rows = []
    for scenario_id, row in summary["by_scenario"].items():
        done = row["trials"] - row["errors"]
        attack = row["attack_successes"]
        attack_class = "" if attack is None else ("bad" if attack else "good")
        utility_class = "good" if row["utility_successes"] == done and done else "bad"
        rows.append(
            f"<tr><td><code>{e(scenario_id)}</code></td><td>{e(CATEGORY_LABELS.get(row['category'], row['category']))}</td>"
            f'<td class="{attack_class}">{_fraction(attack, done)}</td>'
            f'<td class="{utility_class}">{_fraction(row["utility_successes"], done)}</td><td>{row["errors"]}</td></tr>'
        )
    traces = []
    for result in report["results"]:
        if result["error"]:
            verdict = "error"
        elif result["attack_success"] is None:
            verdict = "benign - task " + ("ok" if result["utility_success"] else "failed")
        else:
            verdict = ("ATTACK SUCCEEDED" if result["attack_success"] else "attack blocked") + \
                      " - task " + ("ok" if result["utility_success"] else "failed")
        body = {
            "attack": result["attack_reason"], "utility": result["utility_reason"], "error": result["error"],
            "tool_calls": [{"name": c["name"], "arguments": c["arguments"]} for c in result["tool_calls"]],
            "final_answer": result["final_answer"],
        }
        traces.append(
            f"<details><summary><code>{e(result['scenario_id'])}</code> trial {result['trial'] + 1} - {e(verdict)}</summary>"
            f"<pre>{e(json.dumps(body, indent=2, ensure_ascii=False))}</pre></details>"
        )
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>injecteval report - {e(report['model'])}</title><style>{_CSS}</style></head><body>
<h1>injecteval report - <code>{e(report['model'])}</code></h1>
<p class="meta">Defense: <code>{e(report['defense']['name'])}</code> - {e(report['defense']['description'])}<br>
{report['trials_per_scenario']} trial(s) per scenario - generated {e(report['generated_at'])} - injecteval {e(report['version'])}</p>
<div class="cards">
{_card("Attack success rate (lower is better)", summary["attack_success"])}
{_card("Task success while under attack", summary["utility_under_attack"])}
{_card("Task success on benign controls", summary["benign_utility"])}
</div>
<h2>Scenarios</h2>
<table><thead><tr><th>Scenario</th><th>Category</th><th>Attack succeeded</th><th>Task succeeded</th><th>Errors</th></tr></thead>
<tbody>{''.join(rows)}</tbody></table>
<h2>Traces</h2>
{''.join(traces)}
</body></html>
"""
