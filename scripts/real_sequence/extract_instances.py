#!/usr/bin/env python3
"""Dump one repo's SWE-bench-Live (or SWE-bench-style) rows into per-instance directories.

Usage (run with -I from outside the data directory; the parquet is untrusted data):
    uv run --python 3.12 --with pyarrow python -I extract_instances.py \
        --parquet <split>.parquet --repo owner/name [--since 2026-06-01] [--until 2026-08-01] --out <dir>

Per instance it writes <out>/<instance_id>/:
    meta.json          every scalar/list column except the big text ones
    patch.diff         the upstream source patch, as in the dataset
    test_patch.diff    the upstream test patch
    problem.md         problem_statement, verbatim
and <out>/index.tsv with id, date, files in patch, lines in patch, F2P count, P2P count, image.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pyarrow.parquet as pq

BIG = {"patch", "test_patch", "problem_statement"}


def as_plain(value):
    if hasattr(value, "tolist"):
        value = value.tolist()
    if isinstance(value, (list, tuple)):
        return [as_plain(v) for v in value]
    return value


def files_and_lines(patch: str) -> tuple[int, int]:
    files = sum(1 for line in patch.splitlines() if line.startswith("diff --git "))
    lines = sum(1 for line in patch.splitlines()
                if line[:1] in "+-" and not line.startswith(("+++", "---")))
    return files, lines


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--parquet", required=True)
    ap.add_argument("--repo", required=True)
    ap.add_argument("--since", default="")
    ap.add_argument("--until", default="9999")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    rows = pq.read_table(args.parquet).to_pylist()
    rows = [r for r in rows if r["repo"] == args.repo and args.since <= (r["created_at"] or "") < args.until]
    rows.sort(key=lambda r: (r["created_at"], r["instance_id"]))
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    index = ["id\tdate\tsrc_files\tsrc_lines\tf2p\tp2p\timage"]
    for r in rows:
        d = out / r["instance_id"]
        d.mkdir(exist_ok=True)
        meta = {k: as_plain(v) for k, v in r.items() if k not in BIG}
        (d / "meta.json").write_text(json.dumps(meta, indent=2) + "\n")
        (d / "patch.diff").write_text(r["patch"] or "")
        (d / "test_patch.diff").write_text(r["test_patch"] or "")
        (d / "problem.md").write_text(r["problem_statement"] or "")
        f, n = files_and_lines(r["patch"] or "")
        index.append(f"{r['instance_id']}\t{r['created_at'][:10]}\t{f}\t{n}\t"
                     f"{len(meta.get('FAIL_TO_PASS') or [])}\t{len(meta.get('PASS_TO_PASS') or [])}\t{r.get('docker_image', '')}")
    (out / "index.tsv").write_text("\n".join(index) + "\n")
    print("\n".join(index))


if __name__ == "__main__":
    main()
