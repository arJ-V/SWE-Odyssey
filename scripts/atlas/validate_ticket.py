#!/usr/bin/env python3
"""Validate ONE atlas-commerce ticket on a Docker host, through Harbor (no model calls).

    python3 scripts/atlas/validate_ticket.py ATL-2 --work ~/atlas/<label>/validate \\
        [--sequence sequences/atlas-commerce] [--extra-tickets DIR] [--others ATL-1,ATL-4] \\
        [--subset-sample K] [--seed S] [--repeat-reference N] [--offline]
    python3 scripts/atlas/validate_ticket.py --report-only <trial dir>      # re-read a finished trial

It compiles the ticket's validation plan (compile_atlas.py validate-plan) into ONE Harbor task
whose steps are the conditions, runs it with Harbor's oracle agent (each step's solution applies
nothing, the reference or a known-bad patch, then deploys like a person would), and checks:

  nop at the base            reward 0; every fail_to_pass test fails, every pass_to_pass test passes
  reference at the base      reward 1 (repeat with --repeat-reference)
  each known-bad at the base reward 0, and every test in its must_fail list failed
  for each subset S of the other tickets (all, or a stated sample of K):
    nop with S               reward 0 with the same clean failure (S cannot pass this ticket)
    reference with S         reward 1 (the grader needs nothing but references)
  every step                 no grader error, no tampering flag

Every step resets the platform first, so this also exercises the reset on every condition. Exit
status 0 when every check passes. Raw Harbor output stays under --work; Docker leftovers of the
trial (containers, networks, volumes, the controller image, Lambda containers) are removed.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))


def find_trial(job_dir: Path) -> Path | None:
    for p in sorted(job_dir.iterdir()) if job_dir.is_dir() else []:
        if p.is_dir() and (p / "result.json").is_file():
            try:
                if "trial_name" in json.loads((p / "result.json").read_text()):
                    return p
            except (OSError, ValueError):
                continue
    return None


def _read(path: Path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def evaluate(trial: Path, manifest: dict) -> tuple[list[dict], bool]:
    result = _read(trial / "result.json") or {}
    by_name = {s.get("step_name"): s for s in result.get("step_results") or []}
    grading = manifest["validation"]["grading"]
    rows, all_ok = [], True
    for step in manifest["steps"]:
        name = step["name"]
        sr = by_name.get(name) or {}
        rewards = ((sr.get("verifier_result") or {}).get("rewards")) or {}
        vdir = trial / "steps" / name / "verifier"
        details = _read(vdir / "details.json") or {}
        grade = _read(vdir / "grade.json") or {}
        timing = _read(vdir / "atlas-step.json") or {}
        problems = []
        if not sr:
            problems.append("step did not run")
        if sr.get("exception_info"):
            problems.append(f"exception {sr['exception_info'].get('exception_type')}")
        if rewards.get("error"):
            problems.append(f"grader error: {grade.get('error')}")
        if rewards.get("tampered"):
            problems.append(f"tampered: {grade.get('tamper_reasons')}")
        expect = step["expect"]
        if rewards.get("reward") != expect["reward"]:
            problems.append(f"reward {rewards.get('reward')} != expected {expect['reward']}")
        outcomes = {}
        for group in ("fail_to_pass", "pass_to_pass", "other"):
            outcomes.update((details.get(group) or {}).get("outcomes") or {})
        if expect.get("clean_failure"):
            bad_f2p = [t for t in grading["fail_to_pass"] if outcomes.get(t) != "failed"]
            bad_p2p = [t for t in grading["pass_to_pass"] if outcomes.get(t) != "passed"]
            if bad_f2p:
                problems.append("F2P not cleanly failed: " + ", ".join(f"{t}={outcomes.get(t)}" for t in bad_f2p))
            if bad_p2p:
                problems.append("P2P not passing: " + ", ".join(f"{t}={outcomes.get(t)}" for t in bad_p2p))
        for test in expect.get("must_fail", []):
            if outcomes.get(test) != "failed":
                problems.append(f"must_fail {test} = {outcomes.get(test)}")
        reset = (timing.get("reset") or {}).get("total")
        setup = timing.get("setup") or {}
        verifier_t = sr.get("verifier") or {}
        rows.append({
            "step": name, "condition": step["condition"], "action": step["action"],
            "state": step["state_tickets"], "reward": rewards.get("reward"), "expected": expect["reward"],
            "ok": not problems, "problems": problems,
            "failed_tests": sorted(t for t, o in outcomes.items() if o != "passed"),
            "reset_sec": reset, "setup_sec": setup.get("setup_sec"), "deploy_sec": setup.get("deploy_sec"),
            "deployed": setup.get("deployed"),
            "lambda_containers_removed": len(timing.get("lambda_containers_removed") or []),
            "verifier_window": [verifier_t.get("started_at"), verifier_t.get("finished_at")],
        })
        all_ok = all_ok and not problems
    return rows, all_ok


def cleanup_docker(trial_name: str) -> dict:
    """Remove every container, network, volume and image of the trial's compose project.

    Compose names them <project>_<...> or <project>-<...> (project = the lowercased trial name);
    LocalStack names its Lambda containers after its own container with '_' replaced by '-'.
    """
    project = re.sub(r"[^a-z0-9_-]", "-", trial_name.lower())
    prefixes = (project, project.replace("_", "-"))
    removed = {}
    def names(cmd):
        out = subprocess.run(cmd, capture_output=True, text=True).stdout
        return [n for n in out.split() if n.startswith(prefixes)]
    for kind, list_cmd, rm_cmd in (
        ("containers", ["docker", "ps", "-a", "--format", "{{.Names}}"], ["docker", "rm", "-f", "-v"]),
        ("networks", ["docker", "network", "ls", "--format", "{{.Name}}"], ["docker", "network", "rm"]),
        ("volumes", ["docker", "volume", "ls", "--format", "{{.Name}}"], ["docker", "volume", "rm", "-f"]),
        ("images", ["docker", "images", "--format", "{{.Repository}}"], ["docker", "rmi", "-f"]),
    ):
        found = names(list_cmd)
        if found:
            subprocess.run(rm_cmd + found, capture_output=True, text=True)
        removed[kind] = found
    return removed


def print_rows(rows: list[dict]) -> None:
    # The condition is printed whole (a fixed-width column used to cut "reference with <all six>"
    # down to five tickets, which read as a wrong pairing; the report's "state" was always right).
    width = max([44] + [len(r["condition"]) for r in rows])
    print(f"{'step':10} {'condition':{width}} {'reward':>6} {'exp':>4} {'reset':>6} {'setup':>6}  result")
    for r in rows:
        status = "ok" if r["ok"] else "FAIL: " + "; ".join(r["problems"])
        print(f"{r['step']:10} {r['condition']:{width}} {str(r['reward']):>6} {r['expected']:>4} "
              f"{str(r['reset_sec']):>6} {str(r['setup_sec']):>6}  {status}")
        if r["failed_tests"]:
            print(f"{'':10} failed: {', '.join(r['failed_tests'])}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("ticket", nargs="?")
    parser.add_argument("--work", type=Path)
    parser.add_argument("--sequence", type=Path, default=ROOT / "sequences" / "atlas-commerce")
    parser.add_argument("--extra-tickets", type=Path, action="append", default=[])
    parser.add_argument("--others")
    parser.add_argument("--subset-sample", type=int)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--repeat-reference", type=int, default=1)
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--harbor", default=str(Path.home() / ".local" / "bin" / "harbor"))
    parser.add_argument("--keep-docker", action="store_true")
    parser.add_argument("--report-only", type=Path, help="trial dir of a finished validation run")
    args = parser.parse_args()

    if args.report_only:
        trial = args.report_only
        manifest = _read(Path((_read(trial / "config.json") or {}).get("task", {}).get("path", "")) / "odyssey.json")
        if manifest is None:
            raise SystemExit("cannot find the task's odyssey.json from the trial's config.json")
        rows, ok = evaluate(trial, manifest)
        print_rows(rows)
        return 0 if ok else 1

    if not args.ticket or not args.work:
        parser.error("ticket and --work are required")
    import compile_atlas

    work = args.work.expanduser().resolve()
    (work / "tasks").mkdir(parents=True, exist_ok=True)
    atlas = compile_atlas.load(args.sequence, args.extra_tickets)
    others = [t for t in args.others.split(",") if t] if args.others else None
    task = compile_atlas.compile_validation(atlas, args.ticket, work / "tasks", subset_k=args.subset_sample,
                                            seed=args.seed, repeat_reference=args.repeat_reference,
                                            offline=args.offline, others=others)
    manifest = json.loads((task / "odyssey.json").read_text())
    job_name = f"atlasval-{args.ticket.lower()}-{time.strftime('%m%d-%H%M%S')}"
    cmd = [args.harbor, "run", "-p", str(task), "-a", "oracle", "-n", "1", "-o", str(work / "jobs"),
           "--job-name", job_name, "-y"]
    print(f"[validate] {len(manifest['steps'])} conditions; {' '.join(cmd)}", flush=True)
    start = time.time()
    env = dict(os.environ, PATH=f"{Path.home() / '.local' / 'bin'}:{os.environ.get('PATH', '')}")
    rc = subprocess.run(cmd, env=env).returncode
    elapsed = time.time() - start
    trial = find_trial(work / "jobs" / job_name)
    if trial is None:
        print(f"[validate] no trial result (harbor exit {rc})")
        return 2
    if not args.keep_docker:
        removed = cleanup_docker(trial.name)
        print(f"[validate] docker leftovers removed: { {k: len(v) for k, v in removed.items()} }")
    rows, ok = evaluate(trial, manifest)
    print_rows(rows)
    report = {"ticket": args.ticket, "task": str(task), "trial": str(trial), "harbor_exit": rc,
              "elapsed_sec": round(elapsed, 1), "ok": ok, "steps": rows}
    (work / f"{job_name}.report.json").write_text(json.dumps(report, indent=1))
    print(f"[validate] {'PASS' if ok else 'FAIL'} in {elapsed / 60:.1f} min; report {work / (job_name + '.report.json')}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
