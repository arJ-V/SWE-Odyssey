#!/usr/bin/env python3
"""Pick text marks unique to each ticket's reference, for the sequence leak probe.

    python3 scripts/atlas/probe_marks.py sequences/atlas-commerce --out marks.json [--per-ticket 2]

A mark is a line the ticket's gold.patch adds (stripped, at least 30 characters) that occurs in no
file of the base tree, in no other ticket's gold.patch, and in no ticket's text or tests. The probe
agent (probe_agent.py, `--ak marks_file=marks.json`) greps the whole main container for every mark
at every step; check_probe_seq.py then requires the marks of the tickets merged so far to be in
/app and the marks of every other ticket (the current one and every later one) to be nowhere.
Output: {"ATL-1": ["...", "..."], ...}.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def added_lines(patch: str) -> list[str]:
    return [line[1:].strip() for line in patch.splitlines() if line.startswith("+") and not line.startswith("+++")]


def pick(seq_dir: Path, per_ticket: int = 2) -> dict[str, list[str]]:
    import compile_atlas
    atlas = compile_atlas.load(seq_dir)
    base = "\n".join(p.read_text(errors="replace") for p in sorted(atlas.seq.environment.base_tree.rglob("*"))
                     if p.is_file())
    golds = {tid: at.ticket.gold_patch.read_text() for tid, at in atlas.tickets.items()}
    visible = "\n".join(at.ticket.ticket_md.read_text() for at in atlas.tickets.values())
    tests = "\n".join(p.read_text(errors="replace") for at in atlas.tickets.values()
                      for p in sorted(at.ticket.tests_dir.rglob("*")) if p.is_file())
    marks: dict[str, list[str]] = {}
    for tid, gold in golds.items():
        others = "\n".join(g for t, g in golds.items() if t != tid)
        candidates = []
        for line in added_lines(gold):
            if len(line) < 30 or len(line) > 160 or not re.search(r"[A-Za-z]{4}", line):
                continue
            if line in base or line in others or line in visible or line in tests or line in candidates:
                continue
            candidates.append(line)
        # Prefer the most distinctive (longest) lines.
        marks[tid] = sorted(candidates, key=len, reverse=True)[:per_ticket]
        if not marks[tid]:
            raise SystemExit(f"no unique mark found for {tid}")
    return marks


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("sequence", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--per-ticket", type=int, default=2)
    args = parser.parse_args()
    marks = pick(args.sequence, args.per_ticket)
    args.out.write_text(json.dumps(marks, indent=1) + "\n")
    for tid, lines in marks.items():
        print(f"{tid}: {lines}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
