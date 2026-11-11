#!/usr/bin/env python3
"""Summarize atlas timings from finished trials: reset and redeploy by start state, grader time per
ticket, and whole-trial wall time.

    python3 scripts/atlas/timings.py <jobs dir>... [--json out.json]

Sources per step: steps/<name>/verifier/atlas-step.json (the controller's reset phases and
setup.sh's own timings), result.json (Harbor's agent and verifier windows, the reward), and the
task's odyssey.json (ticket, position; for validation tasks the number of merged references).
"merged" = how many other tickets' references the step's start state holds, i.e. position - 1 in
an ordering; deploy time grows with it because setup redeploys every merged component.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path


def _load(path: Path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def _secs(window: dict | None) -> float | None:
    if not window or not window.get("started_at") or not window.get("finished_at"):
        return None
    parse = lambda s: dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    return (parse(window["finished_at"]) - parse(window["started_at"])).total_seconds()


def collect(roots: list[str]) -> tuple[list[dict], list[dict]]:
    steps, trials = [], []
    for root in roots:
        for result_path in sorted(Path(root).rglob("result.json")):
            result = _load(result_path)
            if not isinstance(result, dict) or "trial_name" not in result:
                continue
            trial = result_path.parent
            task_path = ((_load(trial / "config.json") or {}).get("task") or {}).get("path", "")
            manifest = _load(Path(task_path) / "odyssey.json") or {}
            by_name = {s.get("name"): s for s in manifest.get("steps", [])}
            agent = (result.get("agent_info") or {}).get("name")
            trials.append({"trial": trial.name, "agent": agent, "steps": len(result.get("step_results") or []),
                           "seconds": _secs(result), "design": manifest.get("design"),
                           "validation": "validation" in manifest})
            for sr in result.get("step_results") or []:
                name = sr.get("step_name")
                meta = by_name.get(name, {})
                record = _load(trial / "steps" / name / "verifier" / "atlas-step.json") or {}
                reset, setup = record.get("reset") or {}, record.get("setup") or {}
                merged = len(meta["state_tickets"]) if "state_tickets" in meta else (
                    meta["position"] - 1 if "position" in meta else None)
                rewards = (sr.get("verifier_result") or {}).get("rewards") or {}
                steps.append({
                    "trial": trial.name, "agent": agent, "step": name, "ticket": meta.get("ticket"),
                    "action": meta.get("action", "reference" if agent == "oracle" else agent),
                    "merged": merged, "reward": rewards.get("reward"),
                    "reset_total": reset.get("total"),
                    "localstack_restart": reset.get("localstack_restart_to_healthy"),
                    "proxy_guard_restart": reset.get("proxy_and_guard_restart"),
                    "bootstrap": reset.get("credential_bootstrap"),
                    "setup_reset": setup.get("reset_sec"), "tree": setup.get("tree_sec"),
                    "deploy": setup.get("deploy_sec"), "setup_total": setup.get("setup_sec"),
                    "lambda_containers_removed": len(record.get("lambda_containers_removed") or []),
                    "deployed": setup.get("deployed"),
                    "agent_sec": _secs(sr.get("agent_execution")), "verifier_sec": _secs(sr.get("verifier")),
                })
    return steps, trials


def _stat(values) -> str:
    values = [v for v in values if isinstance(v, (int, float))]
    if not values:
        return "n=0"
    return f"min {min(values):7.1f}  median {statistics.median(values):7.1f}  max {max(values):7.1f}  (n={len(values)})"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("jobs", nargs="+")
    parser.add_argument("--json", type=Path)
    args = parser.parse_args()
    steps, trials = collect(args.jobs)
    print(f"{len(steps)} steps in {len(trials)} trials")
    print("\n== reset and setup (all steps, seconds) ==")
    for key in ("reset_total", "localstack_restart", "proxy_guard_restart", "bootstrap", "setup_reset", "tree",
                "deploy", "setup_total", "lambda_containers_removed"):
        print(f"{key:26s} {_stat([s[key] for s in steps])}")

    print("\n== setup by start state (merged references = position - 1) ==")
    by_merged = defaultdict(list)
    for s in steps:
        if s["merged"] is not None:
            by_merged[s["merged"]].append(s)
    for merged in sorted(by_merged):
        group = by_merged[merged]
        print(f"merged {merged}: deploy {_stat([s['deploy'] for s in group])}")
        print(f"{'':10}setup  {_stat([s['setup_total'] for s in group])}")

    print("\n== grader (Harbor verifier phase) per ticket ==")
    by_ticket = defaultdict(lambda: defaultdict(list))
    for s in steps:
        if s["ticket"]:
            kind = "pass" if s["reward"] == 1 else "fail"
            by_ticket[s["ticket"]][kind].append(s["verifier_sec"])
    for ticket in sorted(by_ticket):
        for kind in ("pass", "fail"):
            if by_ticket[ticket][kind]:
                print(f"{ticket:8} reward {'1' if kind == 'pass' else '0'}: {_stat(by_ticket[ticket][kind])}")

    print("\n== oracle agent phase (apply the reference + deploy its components) per ticket ==")
    oracle = defaultdict(list)
    for s in steps:
        if s["agent"] == "oracle" and s["action"] == "reference" and s["ticket"]:
            oracle[s["ticket"]].append(s["agent_sec"])
    for ticket in sorted(oracle):
        print(f"{ticket:8} {_stat(oracle[ticket])}")

    print("\n== whole trials (ordering tasks only) ==")
    for agent in sorted({t["agent"] or "?" for t in trials}):
        group = [t for t in trials if (t["agent"] or "?") == agent and not t["validation"]]
        if group:
            by_len = defaultdict(list)
            for t in group:
                by_len[t["steps"]].append(t["seconds"])
            for n in sorted(by_len):
                print(f"{agent:14} {n} step(s): {_stat(by_len[n])}")
    if args.json:
        args.json.write_text(json.dumps({"steps": steps, "trials": trials}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
