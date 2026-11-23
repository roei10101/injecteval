# Contributing

Thanks for taking a look. The most useful contributions are new scenarios and new defenses.

## Setup

```bash
pip install -e ".[dev]"
ruff check . && pytest && injecteval validate
```

All three must pass; CI runs the same commands on Python 3.10, 3.12 and 3.13.

## Adding a scenario

1. Copy a file from `src/injecteval/scenarios/` and give it a new `id`.
2. Put `{{INJECTION}}` where the payload should appear and use a **unique canary** for any secret.
3. Fill in `utility.reference_calls` (one correct solution) and `attack.simulated_calls` (what a
   compromised agent would do).
4. Run `injecteval validate`. It fails if the attack grader cannot fire, or fires on clean behaviour.

Guidelines:

- Scenarios must be original. Do not copy material from private or proprietary evaluations.
- Only fictional data and `.example` domains. No real people, companies or credentials.
- Prefer graders that look at actions (`canary_exfiltrated`, `tool_called`) over answer text.
- Every new attack technique deserves a matching benign control that looks similar on the surface.

## Adding a defense

Subclass `Defense` in `src/injecteval/defenses.py`, register it in `DEFENSES`, and add tests that pin
both what it blocks and what it breaks.

## Commits

Small, focused commits with a message that explains why, in the
[Conventional Commits](https://www.conventionalcommits.org) style used in the history.
