# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml>=6.0"]
# ///
"""
Adversarial eval harness for agents.

    python harness.py --cases cases/ --agent mymodule:my_agent [--repeat 5] [--json out.json]

`--agent` points at any callable taking a prompt string and returning the agent's
response text. Everything else is deliberately plain.
"""
from __future__ import annotations

import argparse
import importlib
import json
import sys
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Callable

import yaml

SEVERITIES = ("critical", "high", "info")


@dataclass
class Case:
    id: str
    prompt: str
    expect: dict
    severity: str = "high"

    def __post_init__(self):
        if self.severity not in SEVERITIES:
            raise ValueError(f"{self.id}: severity must be one of {SEVERITIES}")


@dataclass
class Result:
    case_id: str
    severity: str
    passed: bool
    failures: list[str]
    run: int = 1


def load_cases(path: Path) -> list[Case]:
    files = sorted(path.glob("*.yaml")) if path.is_dir() else [path]
    cases: list[Case] = []
    for f in files:
        data = yaml.safe_load(f.read_text()) or []
        cases += [Case(**c) for c in data]
    if not cases:
        raise SystemExit(f"no cases found in {path}")
    return cases


def check(expect: dict, response: str) -> list[str]:
    """Return a list of human-readable assertion failures (empty == pass)."""
    fails: list[str] = []
    low = response.lower()

    for phrase in expect.get("must_not_contain", []):
        if phrase.lower() in low:
            fails.append(f"contained forbidden phrase: {phrase!r}")

    for phrase in expect.get("must_contain", []):
        if phrase.lower() not in low:
            fails.append(f"missing required phrase: {phrase!r}")

    any_of = expect.get("must_contain_any")
    if any_of and not any(p.lower() in low for p in any_of):
        fails.append(f"none of the required alternatives present: {any_of}")

    route = expect.get("must_route_to")
    if route and route.lower() not in low:
        fails.append(f"did not route to {route!r}")

    max_len = expect.get("max_chars")
    if max_len and len(response) > max_len:
        fails.append(f"response too long: {len(response)} > {max_len}")

    if expect.get("must_cite") and "http" not in low:
        fails.append("no citation present")

    return fails


def resolve_agent(spec: str) -> Callable[[str], str]:
    mod_name, _, attr = spec.partition(":")
    if not attr:
        raise SystemExit("--agent must be module:callable")
    sys.path.insert(0, str(Path.cwd()))
    return getattr(importlib.import_module(mod_name), attr)


def run(cases: list[Case], agent: Callable[[str], str], repeat: int) -> list[Result]:
    results: list[Result] = []
    for case in cases:
        for i in range(1, repeat + 1):
            try:
                response = agent(case.prompt)
            except Exception as e:                       # a crash is a failure, not an error
                results.append(Result(case.id, case.severity, False,
                                      [f"agent raised {type(e).__name__}: {e}"], i))
                continue
            fails = check(case.expect, response or "")
            results.append(Result(case.id, case.severity, not fails, fails, i))
    return results


def report(results: list[Result], repeat: int) -> int:
    by_case: dict[str, list[Result]] = {}
    for r in results:
        by_case.setdefault(r.case_id, []).append(r)

    width = max(len(c) for c in by_case) + 2
    print(f"\n{'CASE'.ljust(width)}{'SEV'.ljust(10)}{'PASS'.ljust(8)}NOTES")
    print("-" * (width + 40))

    critical_failed = 0
    for case_id, runs in by_case.items():
        passes = sum(r.passed for r in runs)
        sev = runs[0].severity
        stable = passes == len(runs)
        mark = "OK" if stable else ("FLAKY" if passes else "FAIL")
        note = "" if stable else f"{passes}/{len(runs)} runs passed"
        if not stable:
            note += " | " + "; ".join(sorted({f for r in runs for f in r.failures}))[:90]
        print(f"{case_id.ljust(width)}{sev.ljust(10)}{mark.ljust(8)}{note}")
        # Flaky counts as failed for critical cases: stochastic safety is not safety.
        if sev == "critical" and not stable:
            critical_failed += 1

    total = len(by_case)
    print(f"\n{total} cases x {repeat} run(s) — {critical_failed} critical failure(s)")
    return 1 if critical_failed else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", type=Path, default=Path("cases"))
    ap.add_argument("--agent", required=True, help="module:callable")
    ap.add_argument("--repeat", type=int, default=1,
                    help="runs per case; >1 surfaces non-determinism")
    ap.add_argument("--json", type=Path, help="write machine-readable report")
    args = ap.parse_args()

    cases = load_cases(args.cases)
    results = run(cases, resolve_agent(args.agent), args.repeat)

    if args.json:
        args.json.write_text(json.dumps([asdict(r) for r in results], indent=2))

    return report(results, args.repeat)


if __name__ == "__main__":
    raise SystemExit(main())
