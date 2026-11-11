#!/usr/bin/env python3
"""Cross-check `odyssey account` rows of atlas trials against the trials and the sequence.

    uv run python -m odyssey.cli account <jobs>... --out segments.csv
    python3 scripts/atlas/check_account.py segments.csv <jobs>... [--sequence sequences/atlas-commerce]

For every ordering trial step (validation tasks are skipped) there must be exactly one row, and
its ordering_id, position, ticket, cluster, role and ordering_length must match the task's
odyssey.json and sequence.toml. Its reward must be the trusted verdict: the reward test.sh wrote to
grade.json (its own record, written before reward.json) and reward.json, never a planted file;
grader_error must be test.sh's error flag. Exit 1 on any mismatch.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))


def _load(path: Path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("csv", type=Path)
    parser.add_argument("jobs", nargs="+")
    parser.add_argument("--sequence", type=Path, default=ROOT / "sequences" / "atlas-commerce")
    args = parser.parse_args()
    from odyssey import spec
    labels = {t.id: (t.cluster, t.role) for t in spec.load_sequence(args.sequence).tickets}
    rows = list(csv.DictReader(args.csv.open()))
    by_key: dict[tuple[str, str], list[dict]] = {}
    for row in rows:
        by_key.setdefault((row["trial"], row["step_name"]), []).append(row)

    problems, checked = [], 0
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
            for step in manifest["steps"]:
                key = (trial.name, step["name"])
                found = by_key.get(key, [])
                where = f"{trial.name}/{step['name']}"
                if len(found) != 1:
                    problems.append(f"{where}: {len(found)} rows")
                    continue
                row = found[0]
                checked += 1
                cluster, role = labels[step["ticket"]]
                expect = {"ordering_id": manifest["ordering_id"], "position": str(step["position"]),
                          "ticket": step["ticket"], "cluster": cluster, "role": role,
                          "ordering_length": str(len(manifest["steps"])), "sequence": manifest["sequence"],
                          "arm": manifest["arm"]}
                for field, value in expect.items():
                    if row.get(field) != value:
                        problems.append(f"{where}: {field} = {row.get(field)!r}, expected {value!r}")
                vdir = trial / "steps" / step["name"] / "verifier"
                grade, reward = _load(vdir / "grade.json") or {}, _load(vdir / "reward.json") or {}
                trusted = grade.get("reward")
                if trusted is None or reward.get("reward") != trusted:
                    problems.append(f"{where}: grade.json {trusted} vs reward.json {reward.get('reward')}")
                try:
                    row_reward = float(row["reward"]) if row["reward"] != "" else None
                except ValueError:
                    row_reward = None
                if row_reward is None or trusted is None or row_reward != float(trusted):
                    problems.append(f"{where}: row reward {row['reward']!r} != trusted {trusted}")
                row_error = row.get("grader_error", "").lower() in ("true", "1")
                if row_error != bool(reward.get("error")):
                    problems.append(f"{where}: grader_error {row.get('grader_error')!r} vs test.sh error {reward.get('error')}")
    for p in problems:
        print("FAIL", p)
    print(f"{checked} segment rows checked against {len(rows)} rows in {args.csv}; {len(problems)} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
