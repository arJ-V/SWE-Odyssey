#!/usr/bin/env python3
"""Decide a step's reward from the grader's own outputs (called by test.sh, never by run.sh).

    verdict.py --results DIR --exit CODE [--junit-required 0|1]

Prints {"reward": 0|1, "tampered": 0|1, "reason": "..."}. Reward 1 needs BOTH run.sh's exit code 0
AND its JUnit reports (DIR/junit-*.xml, written by atlas_run.py) to show at least one test, every
test passed, none failed, errored or skipped. A grader that exited 0 while its reports disagree
(missing, empty or failing) is flagged as tampered: only something other than the grader can
produce that, since test.sh runs it with nothing of the agent's left running.
"""

from __future__ import annotations

import argparse
import json
import xml.etree.ElementTree as ET
from pathlib import Path


def summarize(results: Path) -> tuple[dict, list[str]]:
    totals = {"tests": 0, "passed": 0, "failed": 0, "errors": 0, "skipped": 0}
    problems = []
    reports = sorted(results.glob("junit-*.xml"))
    for path in reports:
        try:
            root = ET.parse(path).getroot()
        except ET.ParseError as exc:
            problems.append(f"{path.name} unreadable: {exc}")
            continue
        for case in root.iter("testcase"):
            totals["tests"] += 1
            if case.find("error") is not None:
                totals["errors"] += 1
            elif case.find("failure") is not None:
                totals["failed"] += 1
            elif case.find("skipped") is not None:
                totals["skipped"] += 1
            else:
                totals["passed"] += 1
    totals["reports"] = len(reports)
    return totals, problems


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--results", required=True, type=Path)
    parser.add_argument("--exit", required=True, type=int)
    parser.add_argument("--junit-required", type=int, default=1)
    args = parser.parse_args()

    totals, problems = summarize(args.results)
    reports_pass = (totals["reports"] > 0 and totals["tests"] > 0 and not problems
                    and totals["passed"] == totals["tests"])
    reward, tampered, reason = 0, 0, ""
    if args.exit == 0:
        if reports_pass or (not args.junit_required and totals["reports"] == 0):
            reward, reason = 1, "grader passed"
        else:
            tampered = 1
            reason = (f"run.sh exited 0 but its reports do not show a pass: {totals}"
                      + (f"; {'; '.join(problems)}" if problems else ""))
    else:
        reason = f"run.sh exited {args.exit}; reports {totals}"
    print(json.dumps({"reward": reward, "tampered": tampered, "reason": reason, "junit": totals}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
