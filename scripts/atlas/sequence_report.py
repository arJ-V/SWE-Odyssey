#!/usr/bin/env python3
"""Check whole-sequence Harbor runs (oracle and nop) and pool every start state seen per ticket.

    python3 scripts/atlas/sequence_report.py --jobs <jobs dir>... [--reports <validate *.report.json>...] \\
        [--sequence sequences/atlas-commerce] [--json out.json]

For every ordering trial run with Harbor's `oracle` agent every step must score 1; with `nop`
every step must score 0 with a clean failure (every fail_to_pass test failed, every pass_to_pass
test passed, from details.json); every step must have run, with no grader error and no tampering
flag. Validation reports (validate_ticket.py) are folded in as further observations.

Independence: for each ticket, the distinct start states (the set of other tickets' references
deployed before it) under which the reference and do-nothing were graded, and whether every one
gave the expected verdict. Exit 1 if any step disagrees.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))


def _load(path: Path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--jobs", nargs="*", default=[])
    parser.add_argument("--reports", nargs="*", default=[])
    parser.add_argument("--sequence", type=Path, default=ROOT / "sequences" / "atlas-commerce")
    parser.add_argument("--json", type=Path)
    args = parser.parse_args()
    import compile_atlas
    atlas = compile_atlas.load(args.sequence)
    grading = {tid: at.grading for tid, at in atlas.tickets.items()}

    # (ticket, action) -> start state -> list of (reward ok, source)
    seen: dict[tuple[str, str], dict[tuple[str, ...], list]] = defaultdict(lambda: defaultdict(list))
    problems_total = 0
    trial_rows = []
    for root in args.jobs:
        for result_path in sorted(Path(root).rglob("result.json")):
            result = _load(result_path)
            if not isinstance(result, dict) or "trial_name" not in result:
                continue
            trial = result_path.parent
            manifest = _load(Path(((_load(trial / "config.json") or {}).get("task") or {}).get("path", ""))
                             / "odyssey.json") or {}
            if "validation" in manifest or not manifest.get("steps"):
                continue
            agent = (result.get("agent_info") or {}).get("name")
            if agent not in ("oracle", "nop"):
                continue
            action, expected = ("reference", 1) if agent == "oracle" else ("nothing", 0)
            by_name = {s.get("step_name"): s for s in result.get("step_results") or []}
            problems, rewards = [], []
            ordering = [s["ticket"] for s in manifest["steps"]]
            for step in manifest["steps"]:
                sr = by_name.get(step["name"])
                tid, pos = step["ticket"], step["position"]
                state = tuple(sorted(ordering[:pos - 1]))
                if sr is None:
                    problems.append(f"{step['name']}: did not run")
                    rewards.append(None)
                    continue
                r = (sr.get("verifier_result") or {}).get("rewards") or {}
                rewards.append(r.get("reward"))
                step_problems = []
                if sr.get("exception_info"):
                    step_problems.append(f"exception {sr['exception_info'].get('exception_type')}")
                if r.get("error"):
                    step_problems.append("grader error")
                if r.get("tampered"):
                    step_problems.append("tampered")
                if r.get("reward") != expected:
                    step_problems.append(f"reward {r.get('reward')} != {expected}")
                if agent == "nop":
                    details = _load(trial / "steps" / step["name"] / "verifier" / "details.json") or {}
                    outcomes = {}
                    for group in ("fail_to_pass", "pass_to_pass", "other"):
                        outcomes.update((details.get(group) or {}).get("outcomes") or {})
                    bad_f2p = [t for t in grading[tid]["fail_to_pass"] if outcomes.get(t) != "failed"]
                    bad_p2p = [t for t in grading[tid]["pass_to_pass"] if outcomes.get(t) != "passed"]
                    if bad_f2p or bad_p2p:
                        step_problems.append("unclean failure: " + ", ".join(
                            f"{t}={outcomes.get(t)}" for t in bad_f2p + bad_p2p))
                seen[(tid, action)][state].append((not step_problems, f"{trial.name}/{step['name']}"))
                problems += [f"{step['name']}: {p}" for p in step_problems]
            if result.get("exception_info"):
                problems.append(f"trial exception {result['exception_info'].get('exception_type')}")
            problems_total += len(problems)
            trial_rows.append({"trial": trial.name, "job": result_path.parent.parent.name, "agent": agent,
                               "ordering_id": manifest.get("ordering_id"), "ordering": ordering,
                               "rewards": rewards, "problems": problems})
            print(f"{'ok  ' if not problems else 'FAIL'} {agent:6} {manifest.get('ordering_id'):18} "
                  f"{','.join(ordering):48} rewards {rewards}" + ("" if not problems else "\n     " + "; ".join(problems)))

    for report_path in args.reports:
        report = _load(Path(report_path)) or {}
        tid = report.get("ticket")
        for row in report.get("steps", []):
            if row.get("action") not in ("reference", "nothing"):
                continue
            seen[(tid, row["action"])][tuple(sorted(row.get("state") or []))].append(
                (bool(row.get("ok")), f"{Path(report_path).name}/{row['step']}"))
            problems_total += 0 if row.get("ok") else 1

    print("\n== independence: distinct start states per ticket (other tickets' references deployed) ==")
    summary = {}
    for tid in sorted(atlas.tickets):
        others = [t for t in atlas.tickets if t != tid]
        possible = 2 ** len(others)
        line = []
        for action in ("reference", "nothing"):
            states = seen.get((tid, action), {})
            bad = sorted(s for s, obs in states.items() if not all(ok for ok, _ in obs))
            n_obs = sum(len(obs) for obs in states.values())
            singles = sum(1 for s in states if len(s) == 1)
            has_all = tuple(sorted(others)) in states
            summary.setdefault(tid, {})[action] = {
                "distinct_states": len(states), "of_possible": possible, "observations": n_obs,
                "singles": singles, "all_others": has_all, "states_with_wrong_verdict": [list(s) for s in bad]}
            line.append(f"{'reference' if action == 'reference' else 'nop':9} {len(states):2}/{possible} states "
                        f"({n_obs} gradings; {singles}/{len(others)} singles; all-others {'yes' if has_all else 'no'})"
                        + (f" WRONG in {bad}" if bad else ""))
        print(f"{tid}: " + " | ".join(line))
    if args.json:
        args.json.write_text(json.dumps({"trials": trial_rows, "independence": summary}, indent=1))
    print(f"\n{'PASS' if problems_total == 0 else 'FAIL'}: {len(trial_rows)} trials, {problems_total} problem(s)")
    return 0 if problems_total == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
